// Search UI: calls POST /search, renders result cards, opens a patient drawer.
const $ = (sel) => document.querySelector(sel);
const form = $("#search-form");
const statusEl = $("#status");
const resultsEl = $("#results");
const drawer = $("#drawer");
const backdrop = $("#drawer-backdrop");
let lastFocus = null;
let debounce = null;
let inflight = null;

const BADGE = {
  DocumentReference: "bg-sky-100 text-sky-800",
  DiagnosticReport: "bg-amber-100 text-amber-800",
  AISummary: "bg-violet-100 text-violet-800",
  Encounter: "bg-slate-100 text-slate-700",
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function setStatus(text) { statusEl.textContent = text; }

async function runSearch() {
  const q = $("#q").value.trim();
  if (q.length < 2) { resultsEl.innerHTML = ""; setStatus("Type a question about the patients' records."); return; }

  const params = new URLSearchParams();
  if ($("#type").value) params.set("resource_type", $("#type").value);
  if ($("#from").value) params.set("date_from", $("#from").value);
  if ($("#to").value) params.set("date_to", $("#to").value);

  if (inflight) inflight.abort();
  inflight = new AbortController();
  setStatus("Searching…");
  resultsEl.setAttribute("aria-busy", "true");
  resultsEl.innerHTML = Array.from({ length: 3 }, () =>
    '<li class="animate-pulse rounded-lg border border-slate-200 bg-white p-4"><div class="h-4 w-1/3 bg-slate-200 rounded"></div><div class="mt-3 h-3 w-full bg-slate-100 rounded"></div><div class="mt-2 h-3 w-2/3 bg-slate-100 rounded"></div></li>').join("");

  try {
    const res = await fetch(`/search?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, top_k: 5 }),
      signal: inflight.signal,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : `Search failed (${res.status})`);
    }
    render(await res.json());
  } catch (e) {
    if (e.name === "AbortError") return;
    resultsEl.innerHTML = "";
    setStatus(e.message);
  } finally {
    resultsEl.removeAttribute("aria-busy");
  }
}

function render(data) {
  if (!data.results.length) {
    resultsEl.innerHTML = '<li class="rounded-lg border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">No matching records. Try different words or remove a filter.</li>';
    setStatus(`No results for “${data.query}”.`);
    return;
  }
  setStatus(`${data.results.length} results in ${data.took_ms} ms. Use Tab to move between results, Enter to open a patient.`);
  resultsEl.innerHTML = data.results.map((r, i) => `
    <li>
      <button type="button" data-mrn="${esc(r.mrn)}"
        class="result w-full text-left rounded-lg border border-slate-200 bg-white p-4 hover:border-sky-400 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
        aria-label="Result ${i + 1}: ${esc(r.title)} for ${esc(r.patient_name)}, relevance ${Math.round(r.score * 100)} percent. Open patient details.">
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-semibold text-slate-900">${esc(r.patient_name)}</span>
          <span class="text-xs text-slate-500">${esc(r.mrn)}</span>
          <span class="rounded-full px-2 py-0.5 text-xs font-medium ${BADGE[r.resource_type] || BADGE.Encounter}">${esc(r.resource_type)}</span>
          <span class="text-xs text-slate-500">${esc(r.record_date || "undated")}</span>
          <span class="ml-auto text-xs font-medium text-slate-600" title="cosine similarity">relevance ${(r.score * 100).toFixed(0)}%</span>
        </div>
        <p class="mt-1 text-sm font-medium">${esc(r.title)}</p>
        <p class="mt-1 text-sm text-slate-600">${esc(r.snippet)}</p>
        ${r.summary_snippet ? `<p class="mt-2 text-xs text-violet-800 bg-violet-50 rounded px-2 py-1"><span class="font-semibold">AI summary:</span> ${esc(r.summary_snippet.slice(0, 180))}${r.summary_snippet.length > 180 ? "…" : ""}</p>` : ""}
      </button>
    </li>`).join("");
}

// ---------------- drawer

async function openPatient(mrn, trigger) {
  lastFocus = trigger;
  $("#drawer-title").textContent = "Loading…";
  $("#drawer-sub").textContent = mrn;
  $("#drawer-body").innerHTML = '<p class="text-sm text-slate-500">Loading patient…</p>';
  backdrop.classList.remove("hidden");
  drawer.classList.remove("translate-x-full");
  drawer.focus();

  try {
    const res = await fetch(`/patients/${encodeURIComponent(mrn)}`);
    if (!res.ok) throw new Error(`Could not load patient (${res.status})`);
    const p = await res.json();
    $("#drawer-title").textContent = p.name;
    $("#drawer-sub").textContent = `${p.mrn} · ${p.gender} · born ${p.birth_date || "unknown"}`;
    $("#drawer-body").innerHTML = summaryHtml(p.summary) + resourcesHtml(p.resources);
  } catch (e) {
    $("#drawer-body").innerHTML = `<p class="text-sm text-red-700">${esc(e.message)}</p>`;
  }
}

function summaryHtml(s) {
  if (!s) return '<section><h3 class="font-semibold">AI summary</h3><p class="text-sm text-slate-500">No summary generated yet.</p></section>';
  const list = (items) => items.length ? `<ul class="list-disc pl-5 text-sm space-y-1">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="text-sm text-slate-500">None</p>';
  const conf = { high: "bg-emerald-100 text-emerald-800", medium: "bg-amber-100 text-amber-800", low: "bg-red-100 text-red-800" }[s.confidence];
  return `
  <section aria-labelledby="sum-h" class="space-y-3">
    <div class="flex items-center gap-2">
      <h3 id="sum-h" class="font-semibold">AI summary</h3>
      <span class="rounded-full px-2 py-0.5 text-xs font-medium ${conf}">confidence: ${esc(s.confidence)}</span>
      <span class="text-xs text-slate-500">${esc(s.provider)} / ${esc(s.model)}</span>
    </div>
    <p class="text-sm"><span class="font-medium">Chief concern:</span> ${esc(s.chief_concern)}</p>
    <p class="text-sm">${esc(s.summary)}</p>
    <div><h4 class="text-sm font-medium">Key diagnoses</h4>${list(s.key_diagnoses)}</div>
    <div><h4 class="text-sm font-medium">Flagged anomalies</h4>${list(s.flagged_anomalies)}</div>
    <div><h4 class="text-sm font-medium">Recent media</h4>${list(s.recent_media.map((m) => `${m.date || "undated"} · ${m.title}: ${m.finding}`))}</div>
    ${s.quality_notes.length ? `<div class="text-xs text-red-700"><span class="font-semibold">Quality checks:</span> ${s.quality_notes.map(esc).join("; ")}</div>` : ""}
    <p class="text-xs text-slate-500 border-l-2 border-slate-300 pl-2">${esc(s.disclaimer)}</p>
  </section>`;
}

function resourcesHtml(resources) {
  return `
  <section aria-labelledby="res-h">
    <h3 id="res-h" class="font-semibold mb-2">Linked FHIR resources (${resources.length})</h3>
    <ul class="divide-y divide-slate-100 rounded border border-slate-200">
      ${resources.map((r) => `
        <li class="p-3">
          <div class="flex items-center gap-2 text-xs">
            <span class="rounded-full px-2 py-0.5 font-medium ${BADGE[r.resource_type] || BADGE.Encounter}">${esc(r.resource_type)}</span>
            <span class="text-slate-500">${esc(r.date || "")}</span>
          </div>
          <p class="mt-1 text-sm font-medium">${esc(r.title)}</p>
          ${r.text ? `<details class="mt-1"><summary class="cursor-pointer text-xs text-sky-700">Show text</summary><p class="mt-1 text-sm text-slate-600">${esc(r.text)}</p></details>` : ""}
        </li>`).join("")}
    </ul>
  </section>`;
}

function closeDrawer() {
  drawer.classList.add("translate-x-full");
  backdrop.classList.add("hidden");
  if (lastFocus) lastFocus.focus();
}

// ---------------- events

form.addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
$("#q").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(runSearch, 300); });
["#type", "#from", "#to"].forEach((id) => $(id).addEventListener("change", runSearch));
$("#clear").addEventListener("click", () => { $("#type").value = ""; $("#from").value = ""; $("#to").value = ""; runSearch(); });

resultsEl.addEventListener("click", (e) => {
  const btn = e.target.closest("button.result");
  if (btn) openPatient(btn.dataset.mrn, btn);
});

// arrow keys move between result cards
resultsEl.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const items = [...resultsEl.querySelectorAll("button.result")];
  const i = items.indexOf(document.activeElement);
  if (i === -1) return;
  e.preventDefault();
  items[Math.max(0, Math.min(items.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))].focus();
});
$("#q").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { const first = resultsEl.querySelector("button.result"); if (first) { e.preventDefault(); first.focus(); } }
});

$("#drawer-close").addEventListener("click", closeDrawer);
backdrop.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (drawer.classList.contains("translate-x-full")) return;
  if (e.key === "Escape") closeDrawer();
  if (e.key === "Tab") {           // keep focus inside the open drawer
    const f = [...drawer.querySelectorAll("button, summary, [href], [tabindex]:not([tabindex='-1'])")];
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  }
});
