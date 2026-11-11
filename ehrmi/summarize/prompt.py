"""Prompt for the clinical summary. Bump PROMPT_VERSION whenever the text changes so the
cache doesn't serve summaries written under an older prompt."""

PROMPT_VERSION = "v3"

SYSTEM = """You summarise a single patient's EHR media records (lab reports, imaging notes, \
discharge summaries, scanned documents) for a clinician who is about to open the chart.

Rules:
- Use ONLY facts stated in the records. Never infer a diagnosis that is not written down; \
if something is only suspected, say "suspected".
- Keep every numeric value exactly as written, with its unit.
- Mention each record at most once. Prefer the most recent information when records disagree \
and say that they disagree.
- The "summary" field must be under 120 words. Plain clinical English, no markdown.
- Return JSON only, matching the schema you are given. No text outside the JSON."""

SCHEMA_HINT = """{
  "chief_concern": "one sentence: the main problem these records are about",
  "key_diagnoses": ["diagnosis or suspected diagnosis as written in the records"],
  "recent_media": [{"date": "YYYY-MM-DD or null", "type": "lab|imaging|discharge_summary|clinical_note|scanned_document",
                    "title": "record title", "finding": "the key finding in under 20 words"}],
  "flagged_anomalies": ["abnormal value or finding that needs attention, with the value"],
  "summary": "under 120 words"
}"""


def build_user_prompt(patient_line: str, documents: list[dict], rule_flags: list[str]) -> str:
    lines = [f"Patient: {patient_line}", "", "Records (oldest first):"]
    for i, d in enumerate(documents, 1):
        lines.append(f"[{i}] {d['date'] or 'undated'} | {d['type']} | {d['title']}\n{d['text']}")
    lines += ["", "Values already flagged abnormal by the lab (H/L markers) or by keyword rules:"]
    lines += [f"- {f}" for f in rule_flags] or ["- none"]
    lines += ["", "Return JSON with exactly this shape:", SCHEMA_HINT]
    return "\n".join(lines)
