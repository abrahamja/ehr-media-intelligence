"""Command line entry point.

    python -m ehrmi.cli ingest          # Task 1: clean records + audit log
    python -m ehrmi.cli fhir            # Task 2: FHIR Bundles, validation report, SQLite store
    python -m ehrmi.cli summarize       # Task 3: cached clinical summaries per patient
    python -m ehrmi.cli serve           # Task 4/5: build the vector index and start the API + UI
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fhir.mapper import build_bundles
from .fhir.store import FhirStore
from .fhir.validate import format_report, validate_all
from .ingest.pipeline import ingest
from .summarize.cache import SummaryCache
from .summarize.summarizer import Summarizer, docs_from_bundle

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


def cmd_summarize(args) -> None:
    store = FhirStore(DB_PATH)
    if not store.mrns():
        cmd_fhir(args)
    cache = SummaryCache(DB_PATH)
    summarizer = Summarizer(provider=args.provider)
    out = VAR / "summaries"
    out.mkdir(parents=True, exist_ok=True)
    for mrn in store.mrns():
        summary = cache.get_or_create(docs_from_bundle(mrn, store.bundle(mrn)), summarizer)
        (out / f"{mrn}.json").write_text(summary.model_dump_json(indent=2))
        notes = f"  [{'; '.join(summary.quality_notes)}]" if summary.quality_notes else ""
        print(f"{mrn}  {summary.provider}/{summary.model}  confidence={summary.confidence}  "
              f"{summary.word_count()} words{notes}")
    print(f"cache hits={cache.hits} misses={cache.misses}")


def cmd_serve(args) -> None:
    import uvicorn

    store = FhirStore(DB_PATH)
    if not store.mrns():
        cmd_summarize(argparse.Namespace(provider=None))
    uvicorn.run("ehrmi.api:app", host=args.host, port=args.port)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="ehrmi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").set_defaults(func=cmd_ingest)
    sub.add_parser("fhir").set_defaults(func=cmd_fhir)
    p_sum = sub.add_parser("summarize")
    p_sum.add_argument("--provider", choices=["anthropic", "openai", "extractive"], default=None,
                       help="default: anthropic if ANTHROPIC_API_KEY is set, else openai, else extractive")
    p_sum.set_defaults(func=cmd_summarize)
    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
