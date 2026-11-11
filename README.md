# EHR Media Intelligence

Ingests messy EHR media records (lab PDFs, imaging notes, discharge summaries, scanned documents), cleans them, maps them to HL7 FHIR R4 Bundles, writes an AI clinical summary per patient, and lets clinicians search everything in plain language.

All data in `data/raw/` is synthetic. No real patient data is used anywhere.

```
data/raw (JSON + CSV) ──► ingest/clean ──► FHIR Bundles ──► SQLite
                                                │
                                   LLM summaries (cached)
                                                │
                        embeddings ──► Chroma ──► POST /search ──► Tailwind UI
```

## Setup

Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# optional: LLM summaries (without a key a rule-based summariser is used)
export ANTHROPIC_API_KEY=...          # or OPENAI_API_KEY=...
export EHRMI_MODEL=claude-haiku-4-5   # any Claude model id
```

## Run

```bash
python -m ehrmi.cli ingest      # Task 1 -> var/ingest/records.json, audit.jsonl, rejected.json
python -m ehrmi.cli fhir        # Task 2 -> var/fhir/*.bundle.json, validation_report.txt, SQLite
python -m ehrmi.cli summarize   # Task 3 -> var/summaries/*.json (second run is all cache hits)
python -m ehrmi.cli serve       # Task 4/5 -> http://127.0.0.1:8000
```

`serve` builds the Chroma index at startup with `all-MiniLM-L6-v2` (downloaded on first run). Set `EHRMI_EMBEDDER=hash` to use the offline hashing embedder instead.

Search from the command line:

```bash
curl -X POST 'http://127.0.0.1:8000/search?resource_type=DiagnosticReport&date_from=2026-10-01' \
     -H 'content-type: application/json' -d '{"query": "low iron anaemia"}'
```

## Tests

```bash
pytest
```

40 tests cover the cleaning edge cases, FHIR mapping and validation, summary validation and caching (the LLM call is mocked), search ranking and filters, the API, and a 50-record latency check. They run offline: no API key and no model download needed.

## Project layout

| Path | What it does |
| --- | --- |
| `ehrmi/ingest/` | Loaders for the JSON export and the CSV notes, field normalisers, duplicate removal, per-MRN reconciliation, audit log |
| `ehrmi/fhir/` | Mapping to Patient, Encounter, DocumentReference and DiagnosticReport, Bundle validation, SQLite store |
| `ehrmi/summarize/` | Prompt, Claude/OpenAI clients, rule-based fallback, quality checks, summary cache |
| `ehrmi/search/` | Embedders and the Chroma index |
| `ehrmi/api.py` | FastAPI app: `POST /search`, `GET /patients`, `GET /patients/{mrn}`, static UI |
| `frontend/` | `index.html` + `app.js`, Tailwind compiled to `tailwind.css` |

## Design decisions

**Cleaning.** Every normaliser returns the new value and an audit entry, so each record carries the list of what changed and why. Rows that can't be repaired (no MRN, no name, unknown document type) are rejected with a reason rather than guessed. Ambiguous slash dates are read month-first for the US export and day-first for the UK-scanned CSV; a first part above 12 always means day-first.

**Patient identity.** MRNs are canonicalised to `MRN-` plus 6 digits, which merges `123`, `000123` and `MRN-000123`. When records for one MRN disagree (Aisha Bello's DOB is 1990 in one file and 1991 in another), the most common known value wins, ties go to the newest record, and every affected record gets a conflict warning. In production this should go to a data-steward queue, not be silently resolved.

**FHIR.** Every record becomes a `DocumentReference` holding the original text. Labs and imaging also become a `DiagnosticReport` with a v2-0074 category (LAB/RAD) and the text as `conclusion`. Encounters are included so `encounter` references resolve inside the Bundle. Ids are uuid5 values, so re-runs give identical Bundles. Validation has three layers: fhir.resources model validation, required value-set bindings (which the library doesn't check), and reference integrity.

**Summaries.** The prompt asks for JSON only and restricts the model to facts in the records. Before a summary is cached it must parse, stay under 200 words, and pass a grounding check: every number in the summary must appear in the source records. A failure gets one retry with the reason, then the rule-based summary is used. Each summary carries a confidence level, its quality notes, the model id and a disclaimer.

**Search.** Document text and AI summaries go into one Chroma collection with cosine distance. The record title is embedded with the text because short notes depend on it ("MRI left knee"). A lab or imaging record exists as both a DocumentReference and a DiagnosticReport, so results are collapsed per source record.

## Trade-offs

- fhir.resources 8.x supports pydantic v2 but ships R4B rather than R4. R4B is the R4-compatible 4.3 release, and Patient, Encounter, DocumentReference and DiagnosticReport are the same for every element used here. Pinning `fhir.resources<7` would give strict R4 with pydantic v1.
- SQLite and an embedded Chroma keep the project to one process. They are fine for this data size, not for a hospital.
- The hashing embedder makes the tests fast and offline but only matches words. The real model is used whenever it's available.

## With more time

- Real OCR for scanned PDFs (Tesseract or a document AI service) in front of the loaders.
- Validate with the HL7 FHIR validator or a HAPI server, plus US Core profiles.
- A labelled set of clinician-written summaries for evaluation, and faithfulness scoring per sentence.
- Hybrid search (BM25 plus embeddings) and a clinical embedding model.
- Auth, audit logging of who searched what, and PHI controls.
