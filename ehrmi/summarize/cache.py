"""SQLite cache for summaries.

Key = MRN + hash of the patient's documents + prompt version + model, so a summary is
regenerated only when the records, the prompt or the model change."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .prompt import PROMPT_VERSION
from .summarizer import ClinicalSummary, PatientDocs, Summarizer


def records_hash(docs: PatientDocs) -> str:
    payload = json.dumps(docs.documents, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


class SummaryCache:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS summaries (
            cache_key TEXT PRIMARY KEY, mrn TEXT NOT NULL, summary_json TEXT NOT NULL)""")
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(docs: PatientDocs, summarizer: Summarizer) -> str:
        return f"{docs.mrn}:{records_hash(docs)}:{PROMPT_VERSION}:{summarizer.provider}:{summarizer.model}"

    def get_or_create(self, docs: PatientDocs, summarizer: Summarizer) -> ClinicalSummary:
        key = self.key(docs, summarizer)
        row = self.db.execute("SELECT summary_json FROM summaries WHERE cache_key = ?", (key,)).fetchone()
        if row:
            self.hits += 1
            return ClinicalSummary.model_validate_json(row[0])
        self.misses += 1
        summary = summarizer.summarize(docs)
        with self.db:
            self.db.execute("REPLACE INTO summaries VALUES (?, ?, ?)", (key, docs.mrn, summary.model_dump_json()))
        return summary

    def latest(self, mrn: str) -> ClinicalSummary | None:
        row = self.db.execute("SELECT summary_json FROM summaries WHERE mrn = ? ORDER BY rowid DESC LIMIT 1",
                              (mrn,)).fetchone()
        return ClinicalSummary.model_validate_json(row[0]) if row else None
