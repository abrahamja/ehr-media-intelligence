"""Canonical intermediate representation produced by the ingestion step."""

from __future__ import annotations

import hashlib
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class Gender(str, Enum):
    male = "male"
    female = "female"
    other = "other"
    unknown = "unknown"


class DocType(str, Enum):
    lab = "lab"
    imaging = "imaging"
    discharge_summary = "discharge_summary"
    clinical_note = "clinical_note"
    scanned_document = "scanned_document"


class Change(BaseModel):
    field: str
    before: str | None
    after: str | None
    reason: str


class Patient(BaseModel):
    mrn: str = Field(pattern=r"^MRN-\d{6}$")
    given_name: str
    family_name: str
    birth_date: date | None = None
    gender: Gender = Gender.unknown


class CleanRecord(BaseModel):
    record_id: str
    source: str
    patient: Patient
    doc_type: DocType
    record_date: date | None
    title: str
    text: str
    encounter_id: str | None = None
    audit: list[Change] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def content_hash(self) -> str:
        payload = f"{self.record_id}|{self.record_date}|{self.title}|{self.text}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class RejectedRecord(BaseModel):
    record_id: str | None
    source: str
    reason: str
    raw: dict


class IngestResult(BaseModel):
    records: list[CleanRecord]
    rejected: list[RejectedRecord]
    duplicates_dropped: int = 0

    def patients(self) -> dict[str, Patient]:
        return {r.patient.mrn: r.patient for r in self.records}
