"""Command line entry point.

    python -m ehrmi.cli ingest          # Task 1: clean records + audit log
    python -m ehrmi.cli fhir            # Task 2: FHIR Bundles, validation report, SQLite store
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fhir.mapper import build_bundles
from .fhir.store import FhirStore
from .fhir.validate import format_report, validate_all
from .ingest.pipeline import ingest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
VAR = ROOT / "var"
DB_PATH = VAR / "ehrmi.sqlite3"


def default_inputs() -> list[Path]:
    return sorted(p for p in RAW.iterdir() if p.suffix in {".json", ".csv"})


def cmd_ingest(args) -> None:
    result = ingest(default_inputs())
    out = VAR / "ingest"
    out.mkdir(parents=True, exist_ok=True)
    (out / "records.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in result.records], indent=2))
    with open(out / "audit.jsonl", "w") as fh:
        for r in result.records:
            fh.write(json.dumps({"record_id": r.record_id, "changes": [c.model_dump() for c in r.audit],
                                 "warnings": r.warnings}) + "\n")
    (out / "rejected.json").write_text(json.dumps([r.model_dump() for r in result.rejected], indent=2))
    print(f"clean records: {len(result.records)}  duplicates dropped: {result.duplicates_dropped}  "
          f"rejected: {len(result.rejected)}  -> {out}")


def cmd_fhir(args) -> None:
    result = ingest(default_inputs())
    bundles = build_bundles(result.records)
    reports = validate_all(bundles)
    out = VAR / "fhir"
    out.mkdir(parents=True, exist_ok=True)
    store = FhirStore(DB_PATH)
    for report in reports:
        bundle = bundles[report.mrn]
        (out / f"{report.mrn}.bundle.json").write_text(json.dumps(bundle, indent=2))
        store.save(report.mrn, bundle, report.valid)
    text = format_report(reports)
    (out / "validation_report.txt").write_text(text + "\n")
    print(text)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="ehrmi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").set_defaults(func=cmd_ingest)
    sub.add_parser("fhir").set_defaults(func=cmd_fhir)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
