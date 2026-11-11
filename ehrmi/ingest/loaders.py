"""Readers for the two export formats. They only reshape rows into one raw dict layout;
all value cleaning happens in the pipeline."""

from __future__ import annotations

import csv
import json
from pathlib import Path


def load_json_export(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data["records"] if isinstance(data, dict) else data
    out = []
    for row in rows:
        p = row.get("patient") or {}
        out.append({
            "source": f"json:{Path(path).name}",
            "day_first": False,
            "record_id": row.get("record_id"),
            "mrn": p.get("mrn"),
            "given_name": p.get("first_name"),
            "family_name": p.get("last_name"),
            "dob": p.get("dob"),
            "sex": p.get("sex"),
            "doc_type": row.get("doc_type"),
            "date": row.get("date"),
            "title": row.get("title"),
            "text": row.get("text"),
            "encounter_id": row.get("encounter_id"),
        })
    return out


def load_csv_notes(path: Path) -> list[dict]:
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            family, _, given = (row.get("patient_name") or "").partition(",")
            out.append({
                "source": f"csv:{Path(path).name}",
                "day_first": True,  # UK lab scans
                "record_id": row.get("record_id"),
                "mrn": row.get("mrn") or None,
                "given_name": given.strip() or None,
                "family_name": family.strip() or None,
                "dob": row.get("dob") or None,
                "sex": row.get("sex") or None,
                "doc_type": row.get("doc_type"),
                "date": row.get("date") or None,
                "title": row.get("title"),
                "text": row.get("text"),
                "encounter_id": row.get("encounter_id") or None,
            })
    return out


LOADERS = {".json": load_json_export, ".csv": load_csv_notes, ".txt": load_csv_notes}


def load_any(path: Path) -> list[dict]:
    path = Path(path)
    try:
        loader = LOADERS[path.suffix.lower()]
    except KeyError:
        raise ValueError(f"unsupported input format: {path.suffix}") from None
    return loader(path)
