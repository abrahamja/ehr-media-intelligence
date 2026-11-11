"""Validate Bundles against the FHIR models in fhir.resources and check reference integrity.

fhir.resources 8.x (pydantic v2) ships R4B and STU3 models; R4B (4.3.0) is the
R4-compatible release and Patient, Encounter, DocumentReference and DiagnosticReport
are structurally the same as R4 for every element used here. See README trade-offs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fhir.resources.R4B.bundle import Bundle
from pydantic import ValidationError


@dataclass
class BundleReport:
    mrn: str
    valid: bool
    resource_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# fhir.resources checks structure and datatypes but not value-set bindings, so the
# required bindings used by this project are checked here.
REQUIRED_BINDINGS = {
    ("Patient", "gender"): {"male", "female", "other", "unknown"},
    ("Encounter", "status"): {"planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled",
                              "entered-in-error", "unknown"},
    ("DocumentReference", "status"): {"current", "superseded", "entered-in-error"},
    ("DiagnosticReport", "status"): {"registered", "partial", "preliminary", "final", "amended", "corrected",
                                     "appended", "cancelled", "entered-in-error", "unknown"},
}


def _references(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "reference" and isinstance(v, str):
                yield v
            else:
                yield from _references(v)
    elif isinstance(node, list):
        for item in node:
            yield from _references(item)


def validate_bundle(mrn: str, bundle: dict) -> BundleReport:
    report = BundleReport(mrn=mrn, valid=True)
    try:
        Bundle.model_validate(bundle)
    except ValidationError as exc:
        report.valid = False
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            report.errors.append(f"schema: {loc}: {err['msg']}")

    full_urls = {e.get("fullUrl") for e in bundle.get("entry", [])}
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        report.resource_counts[res.get("resourceType", "?")] = report.resource_counts.get(res.get("resourceType", "?"), 0) + 1
        for (rtype, element), allowed in REQUIRED_BINDINGS.items():
            if res.get("resourceType") == rtype and element in res and res[element] not in allowed:
                report.valid = False
                report.errors.append(f"binding: {rtype}/{res.get('id')}.{element}={res[element]!r} not in required value set")
        for ref in _references(res):
            if ref.startswith("urn:uuid:") and ref not in full_urls:
                report.valid = False
                report.errors.append(f"reference: {res.get('resourceType')}/{res.get('id')} -> {ref} not in bundle")
        if res.get("resourceType") != "Patient" and "subject" in res:
            if res["subject"]["reference"] != bundle["entry"][0]["fullUrl"]:
                report.valid = False
                report.errors.append(f"subject: {res['resourceType']}/{res['id']} does not point at the bundle patient")
    return report


def validate_all(bundles: dict[str, dict]) -> list[BundleReport]:
    return [validate_bundle(mrn, b) for mrn, b in bundles.items()]


def format_report(reports: list[BundleReport]) -> str:
    lines = ["FHIR validation report", "=" * 22]
    for r in reports:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(r.resource_counts.items()))
        lines.append(f"{'OK  ' if r.valid else 'FAIL'} {r.mrn}  ({counts})")
        lines.extend(f"     - {e}" for e in r.errors)
    ok = sum(r.valid for r in reports)
    lines.append(f"\n{ok}/{len(reports)} bundles valid")
    return "\n".join(lines)
