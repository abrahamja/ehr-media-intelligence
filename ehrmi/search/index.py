"""Chroma vector index over FHIR document text and AI summaries."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

import chromadb

from ..fhir.store import FhirStore
from ..summarize.cache import SummaryCache
from .embeddings import Embedder

INDEXED_TYPES = ("DocumentReference", "DiagnosticReport")
SUMMARY_TYPE = "AISummary"
COLLECTION = "ehr_records"


@dataclass
class SearchHit:
    id: str
    mrn: str
    patient_name: str
    resource_type: str
    record_date: str | None
    title: str
    snippet: str
    summary_snippet: str | None
    score: float


def _date_int(value: str | None) -> int:
    return int(value.replace("-", "")) if value else 0


class SearchIndex:
    def __init__(self, embedder: Embedder, path: str | None = None):
        self.embedder = embedder
        self.client = chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        self.collection = self.client.get_or_create_collection(
            COLLECTION, embedding_function=None, metadata={"hnsw:space": "cosine", "embedder": embedder.name})

    def rebuild(self, store: FhirStore, summaries: SummaryCache | None = None) -> int:
        self.client.delete_collection(COLLECTION)
        self.collection = self.client.create_collection(
            COLLECTION, embedding_function=None, metadata={"hnsw:space": "cosine", "embedder": self.embedder.name})

        ids, docs, metas = [], [], []
        names = {}
        for row in store.resources():
            if row["resource_type"] == "Patient":
                names[row["mrn"]] = row["title"]
        summary_text = {}
        if summaries:
            for mrn in store.mrns():
                s = summaries.latest(mrn)
                if s:
                    summary_text[mrn] = s
                    ids.append(f"summary-{mrn}")
                    docs.append(f"AI summary. {s.chief_concern} {' '.join(s.key_diagnoses)} "
                                f"{' '.join(s.flagged_anomalies)} {s.summary}")
                    metas.append({"mrn": mrn, "resource_type": SUMMARY_TYPE, "date": 0, "date_str": "",
                                  "title": "AI clinical summary", "patient_name": names.get(mrn, mrn),
                                  "record_key": f"summary|{mrn}"})
        for row in store.resources():
            if row["resource_type"] not in INDEXED_TYPES:
                continue
            ids.append(row["id"])
            # title carries a lot of signal for short notes ("MRI left knee")
            docs.append(f"{row['title']}. {row['text']}")
            metas.append({"mrn": row["mrn"], "resource_type": row["resource_type"],
                          # a lab/imaging record is indexed twice (DocumentReference + DiagnosticReport);
                          # record_key lets search collapse them into one result
                          "record_key": f"{row['mrn']}|{row['record_date']}|{row['title']}",
                          "date": _date_int(row["record_date"]), "date_str": row["record_date"] or "",
                          "title": row["title"], "patient_name": names.get(row["mrn"], row["mrn"])})
        self._summary_snippets = {m: s.summary for m, s in summary_text.items()}
        if ids:
            self.collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=self.embedder.embed(docs))
        return len(ids)

    def search(self, query: str, top_k: int = 5, resource_type: str | None = None,
               date_from: date | None = None, date_to: date | None = None) -> tuple[list[SearchHit], float]:
        started = time.perf_counter()
        clauses = []
        if resource_type:
            clauses.append({"resource_type": resource_type})
        if date_from:
            clauses.append({"date": {"$gte": int(date_from.strftime("%Y%m%d"))}})
        if date_to:
            clauses.append({"date": {"$lte": int(date_to.strftime("%Y%m%d"))}})
        where = clauses[0] if len(clauses) == 1 else ({"$and": clauses} if clauses else None)

        total = self.collection.count()
        if total == 0:
            return [], 0.0
        res = self.collection.query(query_embeddings=self.embedder.embed([query]), n_results=min(top_k * 3, total),
                                    where=where, include=["documents", "metadatas", "distances"])
        hits, seen = [], set()
        for id_, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            if meta["record_key"] in seen:
                continue
            seen.add(meta["record_key"])
            if len(hits) == top_k:
                break
            text = doc.split(". ", 1)[1] if ". " in doc else doc
            hits.append(SearchHit(
                id=id_, mrn=meta["mrn"], patient_name=meta["patient_name"], resource_type=meta["resource_type"],
                record_date=meta["date_str"] or None, title=meta["title"], snippet=text[:220],
                summary_snippet=getattr(self, "_summary_snippets", {}).get(meta["mrn"]),
                score=round(1.0 - float(dist), 4)))
        return hits, (time.perf_counter() - started) * 1000
