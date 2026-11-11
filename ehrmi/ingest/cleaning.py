"""Field-level normalisers. Each returns (value, Change | None) so the caller can build an audit log."""

from __future__ import annotations

import re
from datetime import date, datetime

from .models import Change, DocType, Gender

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
)

_GENDER_MAP = {
    "m": Gender.male,
    "male": Gender.male,
    "f": Gender.female,
    "female": Gender.female,
    "o": Gender.other,
    "other": Gender.other,
    "u": Gender.unknown,
    "unknown": Gender.unknown,
}

_DOC_TYPE_MAP = {
    "lab_pdf": DocType.lab,
    "lab_result": DocType.lab,
    "lab": DocType.lab,
    "imaging_note": DocType.imaging,
    "imaging": DocType.imaging,
    "discharge_summary": DocType.discharge_summary,
    "clinic_note": DocType.clinical_note,
    "clinical_note": DocType.clinical_note,
    "scanned_document": DocType.scanned_document,
}


class CleaningError(ValueError):
    """Raised when a record can't be repaired and must be rejected."""


def _change(field: str, before, after, reason: str) -> Change | None:
    if str(before) == str(after):
        return None
    return Change(field=field, before=None if before is None else str(before),
                  after=None if after is None else str(after), reason=reason)


def parse_date(raw, field: str, day_first: bool = False) -> tuple[date | None, Change | None]:
    """Parse the date formats seen in the exports.

    Slash dates are ambiguous (03/04/2026). The JSON export is US-style (month first);
    the scanned CSV comes from a UK lab and is day first, so the loader passes day_first.
    A slash date whose first part is > 12 is treated as day first regardless.
    """
    if raw is None or str(raw).strip() == "":
        return None, None
    text = str(raw).strip()
    formats = list(_DATE_FORMATS)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m and (day_first or int(m.group(1)) > 12):
        formats.insert(0, "%d/%m/%Y")
    for fmt in formats:
        try:
            value = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return value, _change(field, text, value.isoformat(), f"normalised date from '{fmt}'")
    raise CleaningError(f"unparseable {field}: {text!r}")


def normalise_mrn(raw) -> tuple[str, Change | None]:
    """Canonical MRN is 'MRN-' plus 6 digits. Accepts '123', '000123', 'mrn 456', 'MRN-000123'."""
    if raw is None:
        raise CleaningError("missing MRN")
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        raise CleaningError(f"MRN has no digits: {raw!r}")
    digits = digits.lstrip("0") or "0"
    if len(digits) > 6:
        raise CleaningError(f"MRN too long: {raw!r}")
    value = f"MRN-{int(digits):06d}"
    return value, _change("patient.mrn", raw, value, "canonical MRN format")


def normalise_gender(raw) -> tuple[Gender, Change | None]:
    key = str(raw or "").strip().lower()
    value = _GENDER_MAP.get(key, Gender.unknown)
    return value, _change("patient.gender", raw, value.value, "mapped to FHIR administrative gender")


def normalise_name(raw: str | None, field: str) -> tuple[str, Change | None]:
    if not raw or not str(raw).strip():
        raise CleaningError(f"missing {field}")
    text = " ".join(str(raw).split())
    # Title-case but keep apostrophes / hyphens sensible (O'Neil, Smith-Jones)
    value = "-".join("'".join(p.capitalize() for p in part.split("'")) for part in text.split("-"))
    return value, _change(field, raw, value, "normalised capitalisation/whitespace")


def normalise_doc_type(raw) -> tuple[DocType, Change | None]:
    key = str(raw or "").strip().lower()
    if key not in _DOC_TYPE_MAP:
        raise CleaningError(f"unknown document type: {raw!r}")
    value = _DOC_TYPE_MAP[key]
    return value, _change("doc_type", raw, value.value, "mapped to canonical document type")


def clean_text(raw) -> tuple[str, Change | None]:
    text = " ".join(str(raw or "").split())
    if not text:
        raise CleaningError("empty document text")
    return text, (_change("text", "<raw>", "<collapsed>", "collapsed whitespace") if text != raw else None)
