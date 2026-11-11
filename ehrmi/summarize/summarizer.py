"""Clinical summaries from FHIR Bundles.

Providers:
  * "anthropic"  - Claude Messages API (set ANTHROPIC_API_KEY; model via EHRMI_MODEL)
  * "openai"     - Chat Completions API (set OPENAI_API_KEY; model via EHRMI_OPENAI_MODEL)
  * "extractive" - no LLM; rule-based fallback so the pipeline and tests run offline

Whatever the provider, the output is checked before it is cached: JSON shape, word
limit, and a grounding check that every number in the summary appears in the records.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field, ValidationError

from .prompt import PROMPT_VERSION, SYSTEM, build_user_prompt

DISCLAIMER = ("AI-generated summary of the patient's documents. It may be incomplete or wrong and is not a "
              "clinical decision. Verify against the source records before acting.")
MAX_WORDS = 200

_ABNORMAL = re.compile(r"([A-Za-z][A-Za-z0-9 /]{1,40}?\s[\d.,]+\s?[A-Za-z/%^\d]*\s\((?:H|L)\))")
_KEYWORDS = ("suspicious", "tear", "lesion", "effusion", "cardiomegaly", "retinopathy", "hematuria", "anaemia",
             "anemia", "exacerbation", "poorly controlled")


class MediaItem(BaseModel):
    date: str | None = None
    type: str
    title: str
    finding: str


class ClinicalSummary(BaseModel):
    mrn: str
    chief_concern: str
    key_diagnoses: list[str] = Field(default_factory=list)
    recent_media: list[MediaItem] = Field(default_factory=list)
    flagged_anomalies: list[str] = Field(default_factory=list)
    summary: str
    provider: str
    model: str
    prompt_version: str = PROMPT_VERSION
    confidence: str = "medium"
    quality_notes: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def word_count(self) -> int:
        return len(self.as_text().split())

    def as_text(self) -> str:
        media = "; ".join(f"{m.date or 'undated'} {m.title}: {m.finding}" for m in self.recent_media)
        return " ".join([self.chief_concern, ", ".join(self.key_diagnoses), media,
                         ", ".join(self.flagged_anomalies), self.summary])


@dataclass
class PatientDocs:
    mrn: str
    patient_line: str
    documents: list[dict]

    @property
    def corpus(self) -> str:
        return " ".join(f"{d['title']} {d['text']} {d['date'] or ''}" for d in self.documents)


def docs_from_bundle(mrn: str, bundle: dict) -> PatientDocs:
    patient = bundle["entry"][0]["resource"]
    name = patient["name"][0]
    patient_line = (f"{' '.join(name.get('given', []))} {name.get('family', '')}, {patient.get('gender', 'unknown')}, "
                    f"born {patient.get('birthDate', 'unknown')}, {mrn}")
    docs = []
    for e in bundle["entry"]:
        r = e["resource"]
        if r["resourceType"] != "DocumentReference":
            continue
        code = r["type"]["coding"][0]["display"]
        docs.append({
            "date": r.get("date", "")[:10] or None,
            "type": _doc_type_from_code(code),
            "title": r.get("description", ""),
            "text": base64.b64decode(r["content"][0]["attachment"]["data"]).decode(),
        })
    docs.sort(key=lambda d: d["date"] or "0000")
    return PatientDocs(mrn=mrn, patient_line=patient_line, documents=docs)


def _doc_type_from_code(display: str) -> str:
    return {"Laboratory report": "lab", "Diagnostic imaging study": "imaging", "Discharge summary": "discharge_summary",
            "Progress note": "clinical_note"}.get(display, "scanned_document")


def rule_flags(docs: PatientDocs) -> list[str]:
    flags = []
    for d in docs.documents:
        for m in _ABNORMAL.finditer(d["text"]):
            flags.append(f"{m.group(1).strip()} ({d['date'] or 'undated'}, {d['title']})")
        low = d["text"].lower()
        for kw in _KEYWORDS:
            if kw in low:
                sentence = next(s for s in re.split(r"(?<=\.)\s", d["text"]) if kw in s.lower())
                flags.append(f"{sentence.strip()} ({d['date'] or 'undated'}, {d['title']})")
                break
    return list(dict.fromkeys(flags))


# ---------------------------------------------------------------- quality checks

_NUM = re.compile(r"\d+(?:\.\d+)?")


def check_quality(s: ClinicalSummary, docs: PatientDocs) -> ClinicalSummary:
    notes = []
    words = s.word_count()
    if words > MAX_WORDS:
        notes.append(f"over word limit ({words} > {MAX_WORDS})")
    source_numbers = set(_NUM.findall(docs.corpus))
    invented = sorted(set(_NUM.findall(s.as_text())) - source_numbers)
    if invented:
        notes.append(f"numbers not found in source records: {', '.join(invented)}")
    known_titles = {d["title"] for d in docs.documents}
    unknown = [m.title for m in s.recent_media if m.title not in known_titles]
    if unknown:
        notes.append(f"recent_media titles not in records: {', '.join(unknown)}")
    s.quality_notes = notes
    s.confidence = "low" if invented or unknown else ("medium" if notes or s.provider == "extractive" else "high")
    return s


# ---------------------------------------------------------------- providers

def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("model did not return JSON")
    return json.loads(match.group(0))


def _call_anthropic(system: str, user: str, model: str) -> str:
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 900, "temperature": 0, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=60,
    )
    resp.raise_for_status()
    return "".join(b.get("text", "") for b in resp.json()["content"])


def _call_openai(system: str, user: str, model: str) -> str:
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def extractive_summary(docs: PatientDocs) -> ClinicalSummary:
    flags = rule_flags(docs)
    latest = docs.documents[-1] if docs.documents else None
    media = [MediaItem(date=d["date"], type=d["type"], title=d["title"],
                       finding=" ".join(d["text"].split(". ")[0].split()[:18]))
             for d in reversed(docs.documents)][:4]
    impressions = [m.group(1).strip().rstrip(".") for d in docs.documents
                   for m in re.finditer(r"(?:Impression|Admitted with)[: ]+([^.]+)", d["text"])]
    concern = impressions[-1] if impressions else (latest["title"] if latest else "No documents")
    return ClinicalSummary(
        mrn=docs.mrn,
        chief_concern=concern[0].upper() + concern[1:],
        key_diagnoses=impressions,
        recent_media=media,
        flagged_anomalies=[f.split(" (")[0] for f in flags][:6],
        summary=f"Most recent record: {latest['title']} ({latest['date'] or 'undated'}). Rule-based summary; set an API key for an LLM summary."
        if latest else "No documents.",
        provider="extractive",
        model="rules",
    )


class Summarizer:
    def __init__(self, provider: str | None = None, model: str | None = None):
        if provider is None:
            provider = ("anthropic" if os.getenv("ANTHROPIC_API_KEY")
                        else "openai" if os.getenv("OPENAI_API_KEY") else "extractive")
        self.provider = provider
        self.model = model or {"anthropic": os.getenv("EHRMI_MODEL", "claude-haiku-4-5"),
                               "openai": os.getenv("EHRMI_OPENAI_MODEL", "gpt-4o-mini"),
                               "extractive": "rules"}[provider]

    def summarize(self, docs: PatientDocs) -> ClinicalSummary:
        if self.provider == "extractive":
            return check_quality(extractive_summary(docs), docs)
        user = build_user_prompt(docs.patient_line, docs.documents, rule_flags(docs))
        call = _call_anthropic if self.provider == "anthropic" else _call_openai
        last_error = None
        for attempt in range(2):
            prompt = user if attempt == 0 else (
                user + f"\n\nYour previous answer was rejected ({last_error}). Return valid JSON, "
                       f"under {MAX_WORDS} words in total.")
            try:
                data = _extract_json(call(SYSTEM, prompt, self.model))
                result = ClinicalSummary(mrn=docs.mrn, provider=self.provider, model=self.model, **data)
            except (ValueError, ValidationError, KeyError) as exc:
                last_error = str(exc).splitlines()[0]
                continue
            result = check_quality(result, docs)
            if result.word_count() <= MAX_WORDS:
                return result
            last_error = f"{result.word_count()} words"
        # two bad answers: fall back rather than show something unchecked
        fallback = check_quality(extractive_summary(docs), docs)
        fallback.quality_notes.append(f"LLM output rejected twice ({last_error}); extractive fallback used")
        return fallback
