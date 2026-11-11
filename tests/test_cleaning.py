from datetime import date
from pathlib import Path

import pytest

from ehrmi.ingest.cleaning import CleaningError, normalise_mrn, normalise_name, parse_date
from ehrmi.ingest.models import Gender
from ehrmi.ingest.pipeline import clean_row, ingest

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def _row(**overrides):
    row = {"source": "test", "record_id": "R1", "mrn": "123", "given_name": "maria", "family_name": "GONZALEZ",
           "dob": "1968-03-14", "sex": "F", "doc_type": "lab_pdf", "date": "2026-10-02", "title": "CMP",
           "text": "Glucose 182", "encounter_id": "E1"}
    row.update(overrides)
    return row


@pytest.mark.parametrize("raw,expected", [
    ("2026-10-05", date(2026, 10, 5)),
    ("10/05/2026", date(2026, 10, 5)),        # US export: month first
    ("2026/10/20", date(2026, 10, 20)),
    ("30 Nov 1955", date(1955, 11, 30)),
    ("2026-09-18T14:22:00", date(2026, 9, 18)),
    ("14/03/1968", date(1968, 3, 14)),        # first part > 12 -> must be day first
])
def test_inconsistent_date_formats(raw, expected):
    value, _ = parse_date(raw, "d")
    assert value == expected


def test_day_first_sources_flip_ambiguous_dates():
    assert parse_date("03/04/2026", "d", day_first=True)[0] == date(2026, 4, 3)
    assert parse_date("03/04/2026", "d", day_first=False)[0] == date(2026, 3, 4)


def test_unparseable_date_rejects():
    with pytest.raises(CleaningError):
        parse_date("sometime last week", "d")


@pytest.mark.parametrize("raw", ["123", "000123", "MRN-000123", "mrn 123", " MRN 0000123 "])
def test_mrn_variants_collapse_to_one_identifier(raw):
    assert normalise_mrn(raw)[0] == "MRN-000123"


def test_missing_mrn_is_rejected_not_guessed():
    with pytest.raises(CleaningError):
        clean_row(_row(mrn=None))


def test_name_capitalisation_keeps_apostrophes():
    assert normalise_name("o'NEIL", "n")[0] == "O'Neil"


def test_missing_fields_are_warned_and_audited():
    rec = clean_row(_row(date="", dob=None, sex="female"))
    assert rec.record_date is None
    assert "record date missing" in rec.warnings
    assert rec.patient.birth_date is None
    assert rec.patient.gender is Gender.female
    fields = {c.field for c in rec.audit}
    assert {"patient.mrn", "patient.given_name", "patient.family_name", "doc_type"} <= fields


def test_end_to_end_duplicates_and_conflicting_identifiers():
    result = ingest([RAW / "epic_export.json", RAW / "scanned_notes.csv"])

    # exact duplicate DOC-1002 is dropped once
    assert result.duplicates_dropped == 1
    assert [r.record_id for r in result.records].count("DOC-1002") == 1

    # '000123', 'MRN-000123' and '123' are the same patient
    maria = [r for r in result.records if r.patient.mrn == "MRN-000123"]
    assert len(maria) == 3
    assert {r.patient.family_name for r in maria} == {"Gonzalez"}

    # Aisha Bello: DOB 1990 vs 1991 and sex 'U' vs 'F' -> reconciled, flagged, audited
    aisha = [r for r in result.records if r.patient.mrn == "MRN-000789"]
    assert {r.patient.gender for r in aisha} == {Gender.female}
    assert len({r.patient.birth_date for r in aisha}) == 1
    assert any("conflicting birth_date" in w for r in aisha for w in r.warnings)
    assert any(c.reason.startswith("reconciled") for r in aisha for c in r.audit)

    # orphan lab with no MRN or name ends up in the rejected list with a reason
    assert [r.record_id for r in result.rejected] == ["NOTE-2005"]
