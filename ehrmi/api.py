"""FastAPI app: search endpoint, patient detail and the static frontend.

    uvicorn ehrmi.api:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .fhir.store import FhirStore
from .search.embeddings import default_embedder
from .search.index import INDEXED_TYPES, SUMMARY_TYPE, SearchIndex
from .summarize.cache import SummaryCache

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "var" / "ehrmi.sqlite3"
CHROMA_PATH = ROOT / "var" / "chroma"
FRONTEND = ROOT / "frontend"


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchResult(BaseModel):
    id: str
    mrn: str
    patient_name: str
    resource_type: str
    record_date: str | None
    title: str
    snippet: str
    summary_snippet: str | None
    score: float


class SearchResponse(BaseModel):
    query: str
    took_ms: float
    results: list[SearchResult]


def create_app(store: FhirStore | None = None, summaries: SummaryCache | None = None,
               index: SearchIndex | None = None) -> FastAPI:
    state = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["store"] = store or FhirStore(DB_PATH)
        state["summaries"] = summaries or SummaryCache(DB_PATH)
        if index is None:
            idx = SearchIndex(default_embedder(), str(CHROMA_PATH))
            idx.rebuild(state["store"], state["summaries"])
            state["index"] = idx
        else:
            state["index"] = index
        yield

    app = FastAPI(title="EHR Media Intelligence", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "indexed": state["index"].collection.count()}

    @app.post("/search", response_model=SearchResponse)
    def search(body: SearchRequest,
               resource_type: str | None = Query(None, description="DocumentReference, DiagnosticReport or AISummary"),
               date_from: date | None = Query(None), date_to: date | None = Query(None)):
        if resource_type and resource_type not in (*INDEXED_TYPES, SUMMARY_TYPE):
            raise HTTPException(422, f"resource_type must be one of {[*INDEXED_TYPES, SUMMARY_TYPE]}")
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from is after date_to")
        hits, took = state["index"].search(body.query, body.top_k, resource_type, date_from, date_to)
        return SearchResponse(query=body.query, took_ms=round(took, 1),
                              results=[SearchResult(**h.__dict__) for h in hits])

    @app.get("/patients")
    def patients():
        rows = [r for r in state["store"].resources() if r["resource_type"] == "Patient"]
        return [{"mrn": r["mrn"], "name": r["title"]} for r in rows]

    @app.get("/patients/{mrn}")
    def patient(mrn: str):
        bundle = state["store"].bundle(mrn)
        if bundle is None:
            raise HTTPException(404, "patient not found")
        summary = state["summaries"].latest(mrn)
        resources = [{"id": r["id"], "resource_type": r["resource_type"], "date": r["record_date"],
                      "title": r["title"], "text": r["text"]}
                     for r in state["store"].resources(mrn) if r["resource_type"] != "Patient"]
        p = bundle["entry"][0]["resource"]
        return {"mrn": mrn, "name": f"{' '.join(p['name'][0]['given'])} {p['name'][0]['family']}",
                "birth_date": p.get("birthDate"), "gender": p.get("gender"),
                "summary": summary.model_dump() if summary else None, "resources": resources}

    if FRONTEND.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

        @app.get("/", include_in_schema=False)
        def index_page():
            return FileResponse(FRONTEND / "index.html")

    return app


app = create_app()
