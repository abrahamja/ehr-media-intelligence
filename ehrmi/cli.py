"""Command line entry point.

    python -m ehrmi.cli ingest          # Task 1: clean records + audit log
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ingest.pipeline import ingest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
VAR = ROOT / "var"


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


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="ehrmi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").set_defaults(func=cmd_ingest)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
