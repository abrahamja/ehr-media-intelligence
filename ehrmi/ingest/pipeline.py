"""Ingestion pipeline: load -> clean each row -> drop duplicates -> reconcile patients."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .cleaning import (
    CleaningError,
    clean_text,
    normalise_doc_type,
    normalise_gender,
    normalise_mrn,
    normalise_name,
    parse_date,
)
from .loaders import load_any
from .models import Change, CleanRecord, Gender, IngestResult, Patient, RejectedRecord


def clean_row(raw: dict) -> CleanRecord:
    audit: list[Change] = []

    def keep(pair):
        value, change = pair
        if change:
            audit.append(change)
        return value

    if not raw.get("record_id"):
        raise CleaningError("missing record_id")
    day_first = raw.get("day_first", False)
    patient = Patient(
        mrn=keep(normalise_mrn(raw.get("mrn"))),
        given_name=keep(normalise_name(raw.get("given_name"), "patient.given_name")),
        family_name=keep(normalise_name(raw.get("family_name"), "patient.family_name")),
        birth_date=keep(parse_date(raw.get("dob"), "patient.birth_date", day_first)),
        gender=keep(normalise_gender(raw.get("sex"))),
    )
    warnings = []
    record_date = keep(parse_date(raw.get("date"), "record_date", day_first))
    if record_date is None:
        warnings.append("record date missing")
    return CleanRecord(
        record_id=str(raw["record_id"]).strip(),
        source=raw.get("source", "unknown"),
        patient=patient,
        doc_type=keep(normalise_doc_type(raw.get("doc_type"))),
        record_date=record_date,
        title=(raw.get("title") or "Untitled document").strip(),
        text=keep(clean_text(raw.get("text"))),
        encounter_id=(raw.get("encounter_id") or None),
        audit=audit,
        warnings=warnings,
    )


def _reconcile_patients(records: list[CleanRecord]) -> None:
    """Records for one MRN can disagree (DOB typo, 'U' vs 'F'). Pick one canonical
    demographic per MRN: most frequent known value, ties broken by the newest record.
    Every record that gets overwritten is audited and a conflict warning is kept."""
    by_mrn: dict[str, list[CleanRecord]] = defaultdict(list)
    for rec in records:
        by_mrn[rec.patient.mrn].append(rec)

    for mrn, recs in by_mrn.items():
        ordered = sorted(recs, key=lambda r: (r.record_date is not None, r.record_date), reverse=True)
        canonical = {}
        for field in ("given_name", "family_name", "birth_date", "gender"):
            values = [getattr(r.patient, field) for r in ordered]
            known = [v for v in values if v not in (None, Gender.unknown)]
            if not known:
                canonical[field] = values[0]
                continue
            counts = Counter(known)
            top = max(counts.values())
            canonical[field] = next(v for v in known if counts[v] == top)  # newest among the most common
            if len(counts) > 1:
                for r in recs:
                    r.warnings.append(
                        f"conflicting {field} for {mrn}: {sorted(map(str, counts))} -> kept {canonical[field]}")
        for r in recs:
            for field, value in canonical.items():
                old = getattr(r.patient, field)
                if old != value:
                    r.audit.append(Change(field=f"patient.{field}", before=None if old is None else str(getattr(old, 'value', old)),
                                          after=str(getattr(value, 'value', value)), reason="reconciled across records for same MRN"))
            r.patient = Patient(mrn=mrn, **canonical)


def ingest(paths: list[Path]) -> IngestResult:
    rows = []
    for p in paths:
        rows.extend(load_any(p))

    records: list[CleanRecord] = []
    rejected: list[RejectedRecord] = []
    seen: dict[str, CleanRecord] = {}
    dupes = 0
    for raw in rows:
        try:
            rec = clean_row(raw)
        except (CleaningError, ValueError) as exc:
            rejected.append(RejectedRecord(record_id=raw.get("record_id"), source=raw.get("source", "?"),
                                           reason=str(exc).splitlines()[0], raw={k: v for k, v in raw.items() if k != "day_first"}))
            continue
        prior = seen.get(rec.record_id)
        if prior is not None:
            if prior.content_hash == rec.content_hash:
                dupes += 1
                continue
            rec.warnings.append(f"record_id {rec.record_id} reused with different content; kept both")
            rec.record_id = f"{rec.record_id}-{rec.content_hash[:6]}"
        seen[rec.record_id] = rec
        records.append(rec)

    _reconcile_patients(records)
    return IngestResult(records=records, rejected=rejected, duplicates_dropped=dupes)
