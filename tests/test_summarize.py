import json
from pathlib import Path

import pytest

from ehrmi.fhir.mapper import build_bundles
from ehrmi.ingest.pipeline import ingest
from ehrmi.summarize import summarizer as sm
from ehrmi.summarize.cache import SummaryCache
from ehrmi.summarize.summarizer import DISCLAIMER, Summarizer, docs_from_bundle, rule_flags

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="module")
def bundles():
    return build_bundles(ingest([RAW / "epic_export.json", RAW / "scanned_notes.csv"]).records)


@pytest.fixture
def heart_failure(bundles):
    return docs_from_bundle("MRN-000456", bundles["MRN-000456"])


GOOD = {
    "chief_concern": "Heart failure follow-up after admission for acute on chronic systolic heart failure.",
    "key_diagnoses": ["acute on chronic systolic heart failure, EF 30%"],
    "recent_media": [{"date": "2026-10-28", "type": "clinical_note", "title": "Cardiology follow-up",
                      "finding": "Euvolemic, NYHA class II, spironolactone 25 mg added"}],
    "flagged_anomalies": ["BNP 420 pg/mL", "cardiomegaly with bilateral small pleural effusions"],
    "summary": "Admitted with heart failure, EF 30%, diuresed; now euvolemic at cardiology follow-up.",
}


def fake_llm(responses):
    calls = []

    def _call(system, user, model):
        calls.append(user)
        return responses[min(len(calls), len(responses)) - 1]
    return _call, calls


def test_documents_come_out_of_bundle_oldest_first(heart_failure):
    dates = [d["date"] for d in heart_failure.documents]
    assert dates == sorted(dates)
    assert "furosemide" in heart_failure.corpus


def test_rule_flags_pick_up_lab_markers_and_keywords(bundles):
    flags = rule_flags(docs_from_bundle("MRN-000123", bundles["MRN-000123"]))
    assert any("HbA1c 8.4% (H)" in f for f in flags)
    assert any("retinopathy" in f for f in flags)


def test_llm_summary_is_validated_and_carries_disclaimer(monkeypatch, heart_failure):
    call, calls = fake_llm([json.dumps(GOOD)])
    monkeypatch.setattr(sm, "_call_anthropic", call)
    s = Summarizer(provider="anthropic", model="test-model").summarize(heart_failure)
    assert len(calls) == 1
    assert s.disclaimer == DISCLAIMER
    assert s.word_count() <= sm.MAX_WORDS
    assert s.confidence == "high" and s.quality_notes == []


def test_invented_numbers_lower_confidence(monkeypatch, heart_failure):
    bad = dict(GOOD, summary="EF 27%, weight down 9 kg.")         # neither number is in the records
    monkeypatch.setattr(sm, "_call_anthropic", fake_llm([json.dumps(bad)])[0])
    s = Summarizer(provider="anthropic", model="m").summarize(heart_failure)
    assert s.confidence == "low"
    assert any("27" in n and "9" in n for n in s.quality_notes)


def test_overlong_answer_is_retried_then_falls_back(monkeypatch, heart_failure):
    long = dict(GOOD, summary="word " * 250)
    call, calls = fake_llm([json.dumps(long), "not json at all"])
    monkeypatch.setattr(sm, "_call_anthropic", call)
    s = Summarizer(provider="anthropic", model="m").summarize(heart_failure)
    assert len(calls) == 2 and "rejected" in calls[1]
    assert s.provider == "extractive"
    assert any("rejected twice" in n for n in s.quality_notes)


def test_extractive_fallback_runs_offline(heart_failure):
    s = Summarizer(provider="extractive").summarize(heart_failure)
    assert s.chief_concern.startswith("Acute on chronic systolic heart failure")
    assert s.word_count() <= sm.MAX_WORDS
    assert s.confidence == "medium"


def test_cache_hits_until_records_change(monkeypatch, heart_failure):
    call, calls = fake_llm([json.dumps(GOOD)])
    monkeypatch.setattr(sm, "_call_anthropic", call)
    cache = SummaryCache(":memory:")
    summarizer = Summarizer(provider="anthropic", model="m")

    first = cache.get_or_create(heart_failure, summarizer)
    second = cache.get_or_create(heart_failure, summarizer)
    assert first == second and len(calls) == 1 and cache.hits == 1

    heart_failure.documents.append({"date": "2026-11-09", "type": "lab", "title": "BNP", "text": "BNP 300 pg/mL."})
    cache.get_or_create(heart_failure, summarizer)
    assert len(calls) == 2 and cache.misses == 2
