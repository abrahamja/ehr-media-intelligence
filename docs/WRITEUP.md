# EHR Media Intelligence – write-up

**Repository:** https://github.com/abrahamja/ehr-media-intelligence · Jyothish Abraham

## What was built

A Python pipeline and a small web app. Two synthetic exports (an Epic-style JSON file and a scanned-notes CSV, 15 rows with duplicates, mixed date formats, four MRN spellings and conflicting demographics) are cleaned into 13 records with a per-record audit log, mapped to five FHIR Bundles that all pass validation, summarised per patient, and indexed for natural-language search behind a FastAPI endpoint and a Tailwind UI. 40 pytest tests run offline.

## Trade-offs and why

- **R4B models instead of strict R4.** fhir.resources 8 (pydantic v2, which the stack requires) ships R4B. For the four resource types used, R4B and R4 are the same. The alternative was pydantic v1.
- **Resolve demographic conflicts automatically, but flag them.** The most common value wins and every record is warned. A real system should send these to a person.
- **Reject rather than guess.** Records without an MRN or name are listed with a reason instead of being attached to a patient.
- **SQLite and embedded Chroma.** One process, no services to run; enough for this volume.
- **Rule-based fallback summariser.** The pipeline and tests run without an API key, and a bad LLM answer is never shown unchecked.

## What I'd improve with more time

OCR for real scanned PDFs; validation with the official HL7 validator and US Core profiles; hybrid keyword plus vector search and a clinical embedding model; a labelled evaluation set for summaries; authentication and access auditing before any real data.

## FHIR and clinical concepts I had to look up

- Which resource holds what: DocumentReference for the document itself, DiagnosticReport for a lab or imaging result with a conclusion, Encounter for the visit.
- v2-0203 `MR` identifier type for MRNs and v2-0074 section codes (LAB, RAD) for report categories.
- LOINC document codes for the document types, for example 11502-2 for a laboratory report and 18748-4 for diagnostic imaging.
- Required value-set bindings (Patient.gender, DocumentReference.status, DiagnosticReport.status) that the Python models don't enforce.
- Clinical meaning of the synthetic findings so the anomaly rules make sense: HbA1c, ferritin, EF, BNP and NYHA class.

## How I validated AI summary quality

1. **Structure:** the model must return JSON matching a pydantic schema; anything else is retried once and then replaced by the rule-based summary.
2. **Length:** at most 200 words in total, checked after generation, not just requested in the prompt.
3. **Grounding:** every number in the summary must appear in the patient's records, and every "recent media" item must name a real record. A failure sets confidence to low and is shown in the UI.
4. **Prompt rules:** only stated facts, "suspected" where the record says so, values copied with units, and disagreements between records called out.
5. **Tests:** mocked model responses cover a good answer, invented numbers, an over-long answer followed by invalid JSON, and cache invalidation when records change.
6. **Manual review (live run, claude-haiku-4-5, 13 Nov 2026):** I read all five summaries against the source records. Every lab value, imaging finding, medication and dose matched. The grounding check marked all five as low confidence, for two reasons: the model stated patient ages it worked out from date of birth (three right, two a year out: 57 instead of 58, 35 instead of 36), and two media titles dropped the "(scanned)" suffix, which is a false positive in the check. One summary also called a platelet count of 410 x10^9/L "normal", which the record does not say. Next fixes: pass the computed age to the model, normalise titles before matching, and forbid reference-range judgements the record does not make.
