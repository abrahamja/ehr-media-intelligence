import copy
from pathlib import Path

import pytest

from ehrmi.fhir.mapper import build_bundles
from ehrmi.fhir.store import FhirStore
from ehrmi.fhir.validate import validate_all, validate_bundle
from ehrmi.ingest.pipeline import ingest

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="module")
def bundles():
    result = ingest([RAW / "epic_export.json", RAW / "scanned_notes.csv"])
    return build_bundles(result.records)


def test_one_bundle_per_patient(bundles):
    assert sorted(bundles) == ["MRN-000123", "MRN-000321", "MRN-000456", "MRN-000654", "MRN-000789"]


def test_all_bundles_validate(bundles):
    reports = validate_all(bundles)
    assert all(r.valid for r in reports), [e for r in reports for e in r.errors]


def test_required_resource_types_present(bundles):
    types = {e["resource"]["resourceType"] for b in bundles.values() for e in b["entry"]}
    assert {"Patient", "DocumentReference", "DiagnosticReport", "Encounter"} <= types


def test_references_point_at_bundle_patient_and_encounter(bundles):
    b = bundles["MRN-000456"]
    patient_url = b["entry"][0]["fullUrl"]
    reports = [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "DiagnosticReport"]
    assert reports and all(r["subject"]["reference"] == patient_url for r in reports)
    enc_urls = {e["fullUrl"] for e in b["entry"] if e["resource"]["resourceType"] == "Encounter"}
    assert all(r["encounter"]["reference"] in enc_urls for r in reports)


def test_ids_are_deterministic():
    result = ingest([RAW / "epic_export.json", RAW / "scanned_notes.csv"])
    assert build_bundles(result.records) == build_bundles(result.records)


def test_schema_errors_are_reported(bundles):
    broken = copy.deepcopy(bundles["MRN-000123"])
    broken["entry"][0]["resource"]["gender"] = "woman"          # not in the gender value set
    report = validate_bundle("MRN-000123", broken)
    assert not report.valid
    assert any("gender" in e for e in report.errors)


def test_missing_required_element_is_reported(bundles):
    broken = copy.deepcopy(bundles["MRN-000321"])
    report_entry = next(e for e in broken["entry"] if e["resource"]["resourceType"] == "DiagnosticReport")
    del report_entry["resource"]["code"]                          # DiagnosticReport.code is 1..1
    report = validate_bundle("MRN-000321", broken)
    assert not report.valid
    assert any(e.startswith("schema:") and "code" in e for e in report.errors)


def test_dangling_reference_is_reported(bundles):
    broken = copy.deepcopy(bundles["MRN-000123"])
    broken["entry"] = [e for e in broken["entry"] if e["resource"]["resourceType"] != "Encounter"]
    report = validate_bundle("MRN-000123", broken)
    assert not report.valid
    assert any(e.startswith("reference:") for e in report.errors)


def test_store_round_trip(bundles):
    store = FhirStore(":memory:")
    for mrn, b in bundles.items():
        store.save(mrn, b, True)
    assert store.bundle("MRN-000321") == bundles["MRN-000321"]
    docs = [r for r in store.resources("MRN-000321") if r["resource_type"] == "DocumentReference"]
    assert any("renal cell carcinoma" in r["text"] for r in docs)
