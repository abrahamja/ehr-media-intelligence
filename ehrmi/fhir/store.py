"""SQLite store for Bundles plus a flattened resource table used by search and the UI."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bundles (
    mrn TEXT PRIMARY KEY,
    bundle_json TEXT NOT NULL,
    valid INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    mrn TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    record_date TEXT,
    title TEXT,
    text TEXT,
    resource_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_resources_mrn ON resources(mrn);
"""


class FhirStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def save(self, mrn: str, bundle: dict, valid: bool) -> None:
        with self.db:
            self.db.execute("REPLACE INTO bundles VALUES (?, ?, ?)", (mrn, json.dumps(bundle), int(valid)))
            self.db.execute("DELETE FROM resources WHERE mrn = ?", (mrn,))
            for entry in bundle["entry"]:
                res = entry["resource"]
                self.db.execute("INSERT INTO resources VALUES (?, ?, ?, ?, ?, ?, ?)", (
                    res["id"], mrn, res["resourceType"], resource_date(res), resource_title(res),
                    resource_text(res), json.dumps(res)))

    def bundle(self, mrn: str) -> dict | None:
        row = self.db.execute("SELECT bundle_json FROM bundles WHERE mrn = ?", (mrn,)).fetchone()
        return json.loads(row[0]) if row else None

    def mrns(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT mrn FROM bundles ORDER BY mrn")]

    def resources(self, mrn: str | None = None) -> list[sqlite3.Row]:
        if mrn:
            return list(self.db.execute("SELECT * FROM resources WHERE mrn = ? ORDER BY record_date", (mrn,)))
        return list(self.db.execute("SELECT * FROM resources ORDER BY mrn, record_date"))


def _decode(data: str | None) -> str:
    return base64.b64decode(data).decode() if data else ""


def resource_date(res: dict) -> str | None:
    value = res.get("effectiveDateTime") or res.get("date") or res.get("birthDate")
    return value[:10] if value else None


def resource_title(res: dict) -> str:
    rt = res["resourceType"]
    if rt == "Patient":
        n = res["name"][0]
        return f"{' '.join(n.get('given', []))} {n.get('family', '')}".strip()
    if rt == "Encounter":
        return f"Encounter {res['identifier'][0]['value']}"
    if rt == "DocumentReference":
        return res.get("description") or res["type"].get("text", "Document")
    if rt == "DiagnosticReport":
        return res["code"].get("text", "Report")
    return rt


def resource_text(res: dict) -> str:
    rt = res["resourceType"]
    if rt == "DocumentReference":
        return _decode(res["content"][0]["attachment"].get("data"))
    if rt == "DiagnosticReport":
        return res.get("conclusion", "")
    return ""
