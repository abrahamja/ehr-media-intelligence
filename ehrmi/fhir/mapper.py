"""Map cleaned records to FHIR resources and build one collection Bundle per patient.

Resources are built as plain dicts and validated by fhir.resources (see validate.py).
Ids are deterministic (uuid5 of MRN / record id) so re-runs produce identical Bundles.
"""

from __future__ import annotations

import base64
import uuid
from collections import defaultdict

from ..ingest.models import CleanRecord, DocType, Patient

NS = uuid.UUID("6f1c8e8e-5b8a-4c39-9b0e-1d7c2f0e9a11")
MRN_SYSTEM = "urn:oid:2.16.840.1.113883.19.5.99999.1"  # placeholder OID for the hospital MRN namespace

# LOINC document / report codes used for the doc types in the exports
DOC_CODES = {
    DocType.lab: ("11502-2", "Laboratory report"),
    DocType.imaging: ("18748-4", "Diagnostic imaging study"),
    DocType.discharge_summary: ("18842-5", "Discharge summary"),
    DocType.clinical_note: ("11506-3", "Progress note"),
    DocType.scanned_document: ("34133-9", "Summary of episode note"),
}
# DiagnosticReport.category from the v2-0074 diagnostic service section table
REPORT_CATEGORY = {DocType.lab: ("LAB", "Laboratory"), DocType.imaging: ("RAD", "Radiology")}


def _id(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(parts)))


def _urn(resource_id: str) -> str:
    return f"urn:uuid:{resource_id}"


def patient_resource(p: Patient) -> dict:
    res = {
        "resourceType": "Patient",
        "id": _id("patient", p.mrn),
        "identifier": [{
            "use": "usual",
            "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR",
                                 "display": "Medical record number"}]},
            "system": MRN_SYSTEM,
            "value": p.mrn,
        }],
        "name": [{"use": "official", "family": p.family_name, "given": [p.given_name]}],
        "gender": p.gender.value,
    }
    if p.birth_date:
        res["birthDate"] = p.birth_date.isoformat()
    return res


def encounter_resource(encounter_id: str, patient_id: str) -> dict:
    return {
        "resourceType": "Encounter",
        "id": _id("encounter", encounter_id),
        "identifier": [{"system": "urn:ehr:encounter", "value": encounter_id}],
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB", "display": "ambulatory"},
        "subject": {"reference": _urn(patient_id)},
    }


def document_reference(rec: CleanRecord, patient_id: str, encounter_id: str | None) -> dict:
    code, display = DOC_CODES[rec.doc_type]
    res = {
        "resourceType": "DocumentReference",
        "id": _id("docref", rec.record_id),
        "masterIdentifier": {"system": "urn:ehr:media", "value": rec.record_id},
        "status": "current",
        "type": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}], "text": rec.title},
        "subject": {"reference": _urn(patient_id)},
        "description": rec.title,
        "content": [{"attachment": {
            "contentType": "text/plain; charset=utf-8",
            "language": "en",
            "data": base64.b64encode(rec.text.encode()).decode(),
            "title": rec.title,
        }}],
    }
    if rec.record_date:
        res["date"] = f"{rec.record_date.isoformat()}T00:00:00Z"
    if encounter_id:
        res["context"] = {"encounter": [{"reference": _urn(encounter_id)}]}
    return res


def diagnostic_report(rec: CleanRecord, patient_id: str, encounter_id: str | None, docref_id: str) -> dict:
    code, display = DOC_CODES[rec.doc_type]
    cat_code, cat_display = REPORT_CATEGORY[rec.doc_type]
    res = {
        "resourceType": "DiagnosticReport",
        "id": _id("report", rec.record_id),
        "identifier": [{"system": "urn:ehr:media", "value": rec.record_id}],
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                                  "code": cat_code, "display": cat_display}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}], "text": rec.title},
        "subject": {"reference": _urn(patient_id)},
        "conclusion": rec.text,
        "presentedForm": [{"contentType": "text/plain; charset=utf-8",
                           "data": base64.b64encode(rec.text.encode()).decode(), "title": rec.title}],
        # not a core element link, but lets a reader jump from the report to its source document
        "extension": [{"url": "urn:ehrmi:source-document", "valueReference": {"reference": _urn(docref_id)}}],
    }
    if rec.record_date:
        res["effectiveDateTime"] = rec.record_date.isoformat()
        res["issued"] = f"{rec.record_date.isoformat()}T00:00:00Z"
    if encounter_id:
        res["encounter"] = {"reference": _urn(encounter_id)}
    return res


def build_bundles(records: list[CleanRecord]) -> dict[str, dict]:
    """Return {mrn: Bundle dict}. Every record becomes a DocumentReference; labs and imaging
    also get a DiagnosticReport. Encounters referenced by records are included so that
    every reference resolves inside the Bundle."""
    by_mrn: dict[str, list[CleanRecord]] = defaultdict(list)
    for rec in records:
        by_mrn[rec.patient.mrn].append(rec)

    bundles = {}
    for mrn, recs in sorted(by_mrn.items()):
        patient = patient_resource(recs[0].patient)
        entries = [patient]
        encounters: dict[str, str] = {}
        for rec in sorted(recs, key=lambda r: (r.record_date is None, r.record_date)):
            enc_id = None
            if rec.encounter_id:
                if rec.encounter_id not in encounters:
                    enc = encounter_resource(rec.encounter_id, patient["id"])
                    encounters[rec.encounter_id] = enc["id"]
                    entries.append(enc)
                enc_id = encounters[rec.encounter_id]
            doc = document_reference(rec, patient["id"], enc_id)
            entries.append(doc)
            if rec.doc_type in REPORT_CATEGORY:
                entries.append(diagnostic_report(rec, patient["id"], enc_id, doc["id"]))

        bundles[mrn] = {
            "resourceType": "Bundle",
            "id": _id("bundle", mrn),
            "type": "collection",
            "timestamp": "2026-11-01T00:00:00Z",
            "entry": [{"fullUrl": _urn(r["id"]), "resource": r} for r in entries],
        }
    return bundles
