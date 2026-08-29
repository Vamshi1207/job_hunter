const $ = (id) => document.getElementById(id);

const state = {
  packages: [],
  pending: [],
  activeId: null,
  runId: null,
  events: null,
  hunt: { max_jobs: 5, roles: [], markets: [] },
};

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function setStrip(text, append) {
  const el = $("strip");
  if (append) el.textContent += (el.textContent.endsWith("\n") ? "" : "\n") + text;
  else el.textContent = text;
  el.scrollTop = el.scrollHeight;
}

function setLamp(mode) {
  $("lamp").className = "lamp" + (mode ? " " + mode : "");
}

async function loadMe() {
  const me = await api("/api/me");
  $("who").textContent = me.name || "Job desk";
  state.hunt = me.hunt || state.hunt;
  const roles = (state.hunt.roles || []).join(", ") || "your target roles";
  const markets = (state.hunt.markets || []).join(", ");
  const where = [me.city, me.country].filter(Boolean).join(", ");
  const years = state.hunt.years_experience;
  const skip = (state.hunt.exclude_levels || []).filter((level) =>
    ["principal", "staff"].includes(String(level).toLowerCase())
  );
  const reject = (state.hunt.reject_skills || []).join(", ");
  $("hunt-line").textContent =
    `Hunt looks for ${roles}` +
    (markets || where ? ` in ${markets || where}` : "") +
    ` (up to ${state.hunt.max_jobs} jobs)` +
    (years ? `, ~${years} years experience` : "") +
    (skip.length ? `, skipping ${skip.join("/")}` : "") +
    (reject ? `, not ${reject}` : "") +
    `. Camoufox opens for LinkedIn/Indeed/ATS. LinkedIn needs a one-time sign-in. Apply is never clicked.`;
}

function resumeCell(pkg) {
  if (!pkg.pdf_name) return "—";
  const href = `/api/packages/${encodeURIComponent(pkg.id)}/file/${encodeURIComponent(pkg.pdf_name)}`;
  const title = pkg.pdf_path ? escapeAttr(pkg.pdf_path) : "Open PDF";
  return `<a class="file-link" href="${href}" target="_blank" rel="noopener" title="${title}">Open PDF</a>`;
}

function jobLinkCell(pkg) {
  if (!pkg.url) return "—";
  return `<a class="file-link" href="${escapeAttr(pkg.url)}" target="_blank" rel="noopener">Posting</a>`;
}

function escapeAttr(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function loadPackages(selectId) {
  const data = await api("/api/packages");
  state.packages = data.packages || [];
  renderBoard(selectId || state.activeId);
}

function renderBoard(active) {
  const body = $("package-list");
  body.innerHTML = "";
  if (!state.packages.length && !state.pending.length) {
    body.innerHTML =
      '<tr class="empty-row"><td colspan="4">No packages yet. Hunt from your profile, or paste job URLs.</td></tr>';
    return;
  }
  for (const row of state.pending) {
    const tr = document.createElement("tr");
    tr.className = "pending-row";
    tr.innerHTML = `
      <td>${escapeHtml(row.role || "Role")}</td>
      <td>${escapeHtml(row.company || "")}</td>
      <td>Working…</td>
      <td>—</td>
    `;
    body.appendChild(tr);
  }
  for (const pkg of state.packages) {
    const tr = document.createElement("tr");
    tr.dataset.id = pkg.id;
    if (pkg.id === active) tr.classList.add("active");
    tr.innerHTML = `
      <td>${escapeHtml(pkg.role || "Role")}</td>
      <td>${escapeHtml(pkg.company)}</td>
      <td>${resumeCell(pkg)}</td>
      <td>${jobLinkCell(pkg)}</td>
    `;
    tr.tabIndex = 0;
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a")) return;
      openPackage(pkg.id);
    });
    tr.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        openPackage(pkg.id);
      }
    });
    body.appendChild(tr);
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function openPackage(id) {
  state.activeId = id;
  await loadPackages(id);
  const detail = await api("/api/packages/" + encodeURIComponent(id));
  $("report").hidden = false;
  $("report-title").textContent = `${detail.company} · ${detail.role}`;
  $("report-meta").textContent = [detail.date, detail.has_pdf ? "PDF ready" : "No PDF", detail.url ? "Posting saved" : ""]
    .filter(Boolean)
    .join(" · ");
  const ev = detail.evaluation || {};
  $("scores").innerHTML = [
    metric("Score", ev.score),
    metric("Honesty", ev.honesty),
    metric("Keywords", ev.keyword_coverage),
  ].join("");

  const tabs = [
    { id: "feedback", label: "Feedback" },
    { id: "resume", label: "Resume" },
    { id: "cover", label: "Cover letter" },
    { id: "why", label: "Why I fit" },
    { id: "playbook", label: "Playbook" },
    { id: "analysis", label: "Analysis" },
  ];
  const nav = $("tabs");
  nav.innerHTML = "";
  for (const tab of tabs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = tab.label;
    btn.addEventListener("click", () => showTab(detail, tab.id, btn));
    nav.appendChild(btn);
  }
  showTab(detail, "feedback", nav.firstChild);
  $("report").scrollIntoView({ block: "nearest" });
}

function metric(label, value) {
  if (value == null || value === "") return "";
  return `<span><b>${label}</b> ${escapeHtml(value)}</span>`;
}

function showTab(detail, tabId, btn) {
  for (const el of $("tabs").children) el.classList.remove("active");
  if (btn) btn.classList.add("active");
  const body = $("report-body");
  const files = detail.files || {};
  const ev = detail.evaluation || {};
  if (tabId === "feedback") {
    const gaps = (ev.gaps || []).map((g) => "· " + g).join("\n");
    body.textContent = [
      ev.critique || "No critique stored.",
      gaps ? "\nGaps\n" + gaps : "",
      ev.retry_history ? "\nRetry history\n" + ev.retry_history : "",
    ].join("\n").trim();
    return;
  }
  if (tabId === "resume") {
    if (!detail.pdf_name) {
      body.textContent = "No PDF in this package.";
      return;
    }
    const src = `/api/packages/${encodeURIComponent(detail.id)}/file/${encodeURIComponent(detail.pdf_name)}`;
    const pathNote = detail.pdf_path ? `<p class="hint">${escapeHtml(detail.pdf_path)}</p>` : "";
    body.innerHTML = `${pathNote}<iframe title="Resume PDF" src="${src}"></iframe>`;
    return;
  }
  const map = {
    cover: files.cover_letter,
    playbook: files.playbook,
    analysis: files.analysis,
    why: files.why_i_fit,
  };
  body.textContent = map[tabId] || "Nothing in this file yet.";
}

function watchRun(runId) {
  if (state.events) state.events.close();
  setLamp("on");
  $("run-state").textContent = "running";
  setStrip("— strip open —\n");
  const src = new EventSource("/api/runs/" + runId + "/stream");
  state.events = src;
  src.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.line) setStrip(data.line, true);
      if (data.type === "processing" && (data.company || data.role)) {
        const key = `${data.company}|${data.role}`;
        if (!state.pending.some((row) => `${row.company}|${row.role}` === key)) {
          state.pending.unshift({ company: data.company, role: data.role });
        }
        renderBoard(state.activeId);
      }
      if (data.type === "package" && data.package_id) {
        state.pending = state.pending.filter(
          (row) => row.company !== data.company || row.role !== data.role
        );
        loadPackages(data.package_id);
      }
    } catch (_) {}
  };
  src.addEventListener("done", async (ev) => {
    src.close();
    state.events = null;
    state.pending = [];
    let payload = {};
    try { payload = JSON.parse(ev.data); } catch (_) {}
    setLamp(payload.status === "done" ? "done" : "");
    $("run-state").textContent = payload.status || "done";
    const first = payload.package_id || (payload.packages && payload.packages[0]);
    await loadPackages(first);
    if (first) openPackage(first);
    if (payload.error) setStrip("ERROR: " + payload.error, true);
    $("hunt").disabled = false;
    $("run").disabled = false;
  });
  src.onerror = () => {
    $("run-state").textContent = "stream interrupted";
  };
}

$("refresh").addEventListener("click", () => loadPackages());
$("hunt").addEventListener("click", async () => {
  const huntBtn = $("hunt");
  huntBtn.disabled = true;
  try {
    const run = await api("/api/hunt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_jobs: state.hunt.max_jobs }),
    });
    state.runId = run.id;
    watchRun(run.id);
  } catch (err) {
    setStrip(err.message);
    setLamp("");
    huntBtn.disabled = false;
  }
});
$("intake").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const runBtn = $("run");
  runBtn.disabled = true;
  try {
    const body = {
      urls: $("urls").value,
      jd: $("jd").value,
    };
    const run = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.runId = run.id;
    watchRun(run.id);
  } catch (err) {
    $("inspect-note").hidden = false;
    $("inspect-note").textContent = err.message;
    setLamp("");
    runBtn.disabled = false;
  }
});

loadMe().catch(() => {});
loadPackages().catch((err) => {
  $("package-list").innerHTML = `<tr class="empty-row"><td colspan="4">${escapeHtml(err.message)}</td></tr>`;
});
