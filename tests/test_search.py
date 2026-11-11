import random
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ehrmi.api import create_app
from ehrmi.fhir.mapper import build_bundles
from ehrmi.fhir.store import FhirStore
from ehrmi.ingest.models import CleanRecord, DocType, Gender, Patient
from ehrmi.ingest.pipeline import ingest
from ehrmi.search.embeddings import HashingEmbedder
from ehrmi.search.index import SearchIndex
from ehrmi.summarize.cache import SummaryCache
from ehrmi.summarize.summarizer import Summarizer, docs_from_bundle

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="module")
def stack():
    store, cache = FhirStore(":memory:"), SummaryCache(":memory:")
    for mrn, bundle in build_bundles(ingest([RAW / "epic_export.json", RAW / "scanned_notes.csv"]).records).items():
        store.save(mrn, bundle, True)
        cache.get_or_create(docs_from_bundle(mrn, bundle), Summarizer(provider="extractive"))
    index = SearchIndex(HashingEmbedder())
    index.rebuild(store, cache)
    return store, cache, index


@pytest.fixture(scope="module")
def client(stack):
    store, cache, index = stack
    with TestClient(create_app(store, cache, index)) as c:
        yield c


def test_query_finds_the_right_patient(stack):
    hits, _ = stack[2].search("knee ligament tear MRI")
    assert hits[0].mrn == "MRN-000654"
    assert hits[0].score >= hits[-1].score


def test_filter_by_resource_type(stack):
    hits, _ = stack[2].search("anaemia iron", resource_type="DiagnosticReport")
    assert hits and all(h.resource_type == "DiagnosticReport" for h in hits)


def test_filter_by_date_range(stack):
    hits, _ = stack[2].search("heart failure", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))
    assert hits and all("2026-10-01" <= h.record_date <= "2026-10-31" for h in hits)


def test_search_endpoint_returns_top5_with_scores(client):
    r = client.post("/search", json={"query": "kidney lesion suspicious for cancer"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 5
    assert body["results"][0]["mrn"] == "MRN-000321"
    scores = [x["score"] for x in body["results"]]
    assert scores == sorted(scores, reverse=True) and all(-1 <= s <= 1 for s in scores)


def test_search_endpoint_query_params(client):
    r = client.post("/search?resource_type=DocumentReference&date_from=2026-11-01", json={"query": "MRI"})
    assert r.status_code == 200
    assert all(x["resource_type"] == "DocumentReference" and x["record_date"] >= "2026-11-01"
               for x in r.json()["results"])
    assert client.post("/search?resource_type=Observation", json={"query": "MRI"}).status_code == 422
    assert client.post("/search?date_from=2026-12-01&date_to=2026-01-01", json={"query": "MRI"}).status_code == 422


def test_patient_detail(client):
    r = client.get("/patients/MRN-000456")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "James O'Neil"
    assert body["summary"]["disclaimer"]
    assert {x["resource_type"] for x in body["resources"]} >= {"DocumentReference", "DiagnosticReport"}
    assert client.get("/patients/MRN-999999").status_code == 404


def test_fifty_records_under_two_seconds():
    rng = random.Random(7)
    terms = ["chest pain", "fracture", "pneumonia", "migraine", "asthma", "renal stone", "anaemia", "sepsis"]
    store = FhirStore(":memory:")
    recs = []
    for i in range(50):
        p = Patient(mrn=f"MRN-{i % 10:06d}", given_name="Test", family_name=f"Patient{i % 10}",
                    birth_date=date(1980, 1, 1), gender=Gender.unknown)
        recs.append(CleanRecord(record_id=f"SYN-{i}", source="synthetic", patient=p, doc_type=DocType.imaging,
                                record_date=date(2026, 1 + i % 12, 1 + i % 27), title=f"Study {i}",
                                text=f"Findings consistent with {rng.choice(terms)}. Follow-up advised."))
    for mrn, b in build_bundles(recs).items():
        store.save(mrn, b, True)
    index = SearchIndex(HashingEmbedder())
    assert index.rebuild(store) >= 50
    _, took_ms = index.search("lung infection pneumonia")
    assert took_ms < 2000
