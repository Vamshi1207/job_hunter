const $ = (id) => document.getElementById(id);

const state = {
  packages: [],
  jobs: [],
  activeId: null,
  runId: null,
  events: null,
  hunt: { max_jobs: 0, roles: [], markets: [] },
  huntStage: "",
  camoufoxUrl: "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale",
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

function setControls(mode) {
  const idle = mode === "idle";
  const running = mode === "running";
  $("hunt").disabled = !idle;
  $("run").disabled = !idle;
  $("stop").hidden = idle;
  $("stop").disabled = !running;
  $("stop").textContent = running || idle ? "Stop" : "Stopping…";
  if (idle) hideCamoufox();
}

function applyCamoufoxStage(needsAction, hint) {
  if (needsAction) {
    if (hint) {
      const el = $("camoufox-hint");
      if (el) el.textContent = hint;
    }
    showCamoufox(true);
    return;
  }
  hideCamoufox();
}

function showCamoufox(expand) {
  const panel = $("camoufox-panel");
  const wrap = $("camoufox-wrap");
  const frame = $("camoufox-frame");
  if (!panel || !wrap || !frame) return;
  panel.hidden = false;
  if (expand) wrap.open = true;
  if (state.camoufoxUrl && frame.getAttribute("src") !== state.camoufoxUrl) {
    frame.src = state.camoufoxUrl;
  }
}

function hideCamoufox() {
  const panel = $("camoufox-panel");
  const wrap = $("camoufox-wrap");
  const frame = $("camoufox-frame");
  if (wrap) wrap.open = false;
  if (panel) panel.hidden = true;
  if (frame) frame.removeAttribute("src");
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
    (state.hunt.max_jobs
      ? ` (safety cap ${state.hunt.max_jobs} matches)`
      : ", and tailors every posting that matches") +
    (years ? `, ~${years} years experience` : "") +
    (skip.length ? `, skipping ${skip.join("/")}` : "") +
    (reject ? `, not ${reject}` : "") +
    `. The browser panel appears only if a board asks you to sign in, complete 2FA, or solve a CAPTCHA. Apply is never clicked.`;
  if (me.camoufox && me.camoufox.vnc) state.camoufoxUrl = me.camoufox.vnc;
}

function jobKey(row) {
  return `${row.company || ""}|${row.role || ""}|${row.url || ""}`;
}

function upsertJob(data) {
  const incoming = {
    company: data.company || "",
    role: data.role || "Role",
    url: data.url || "",
    status: data.status || "found",
    package_id: data.package_id || "",
    location: data.location || "",
    work_mode: data.work_mode || "",
    ats_score: data.ats_score,
    detail: data.status === "working" ? (data.detail || "") : "",
  };
  const key = jobKey(incoming);
  const idx = state.jobs.findIndex((row) => jobKey(row) === key);
  if (idx >= 0) {
    const prev = state.jobs[idx];
    state.jobs[idx] = {
      ...prev,
      ...incoming,
      package_id: incoming.package_id || prev.package_id,
      location: incoming.location || prev.location,
      work_mode: incoming.work_mode || prev.work_mode,
      ats_score: incoming.ats_score == null ? prev.ats_score : incoming.ats_score,
    };
  } else {
    state.jobs.push(incoming);
  }
}

function setQueue(jobs) {
  state.jobs = (jobs || []).map((row) => ({
    company: row.company || "",
    role: row.role || "Role",
    url: row.url || "",
    status: row.status || "queued",
    package_id: row.package_id || "",
    location: row.location || "",
    work_mode: row.work_mode || "",
    ats_score: row.ats_score,
    detail: "",
  }));
}

function progressFromJobs() {
  const jobs = state.jobs;
  if (!jobs.length) return state.huntStage || "";
  let ready = 0;
  let working = 0;
  let waiting = 0;
  let skipped = 0;
  let failed = 0;
  let stopped = 0;
  const workingDetails = [];
  for (const row of jobs) {
    if (row.status === "ready") ready += 1;
    else if (row.status === "working") {
      working += 1;
      const detail = (row.detail || "").trim();
      if (detail && !workingDetails.includes(detail)) workingDetails.push(detail);
    }
    else if (row.status === "queued" || row.status === "found") waiting += 1;
    else if (row.status === "skipped") skipped += 1;
    else if (row.status === "failed") failed += 1;
    else if (row.status === "stopped") stopped += 1;
  }
  const processed = ready + skipped + failed + stopped;
  const parts = [`Found ${jobs.length}`, `${processed} processed`];
  if (workingDetails.length) parts.push(workingDetails.join(" · "));
  else if (working) parts.push(`${working} tailoring`);
  if (waiting) parts.push(`${waiting} waiting`);
  if (skipped) parts.push(`${skipped} skipped`);
  if (failed) parts.push(`${failed} failed`);
  if (stopped) parts.push(`${stopped} stopped`);
  return parts.join(" · ");
}

function renderProgress(line) {
  const el = $("board-progress");
  const title = $("board-title");
  const text = line === undefined ? progressFromJobs() : line;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    title.textContent = "Ready packages";
    return;
  }
  el.hidden = false;
  el.textContent = text;
  title.textContent = state.jobs.length ? "Jobs" : "Ready packages";
}

function statusLabel(row) {
  const status = typeof row === "string" ? row : row && row.status;
  const detail = typeof row === "string" ? "" : (row && row.detail) || "";
  if (status === "found") return "Found";
  if (status === "queued") return "Waiting";
  if (status === "working") return detail || "Tailoring";
  if (status === "ready") return "Ready";
  if (status === "skipped") return "Skipped";
  if (status === "stopped") return "Stopped";
  if (status === "failed") return "Failed";
  return status || "";
}

function workModeLabel(mode) {
  const value = String(mode || "").toLowerCase();
  if (value === "hybrid") return "Hybrid";
  if (value === "remote") return "Remote";
  if (value === "onsite") return "Onsite";
  return "—";
}

function locationLabel(row, pkg) {
  const text = (row && row.location) || (pkg && pkg.location) || "";
  return text || "—";
}

function atsLabel(row, pkg) {
  const score = row && row.ats_score != null ? row.ats_score : pkg && (pkg.ats_score != null ? pkg.ats_score : pkg.score);
  if (score == null || score === "") return "—";
  return String(score);
}

function boardCells(role, company, location, mode, ats, statusHtml, resume, edit, link, del) {
  return `
      <td>${escapeHtml(role || "Role")}</td>
      <td>${escapeHtml(company || "")}</td>
      <td>${escapeHtml(location)}</td>
      <td>${escapeHtml(mode)}</td>
      <td class="ats-cell">${escapeHtml(ats)}</td>
      <td>${statusHtml}</td>
      <td>${resume}</td>
      <td>${edit}</td>
      <td>${link}</td>
      <td>${del}</td>
    `;
}

function liveJobLink(row) {
  if (!row.url) return "—";
  return `<a class="file-link" href="${escapeAttr(row.url)}" target="_blank" rel="noopener">Posting</a>`;
}

function packageHiddenByLive(pkg) {
  return state.jobs.some(
    (row) =>
      (pkg.url && row.url && pkg.url === row.url) ||
      (row.company === pkg.company && row.role === pkg.role)
  );
}

function resumeCell(pkg) {
  if (!pkg.pdf_name) return "—";
  const href = `/api/packages/${encodeURIComponent(pkg.id)}/file/${encodeURIComponent(pkg.pdf_name)}`;
  const title = pkg.pdf_path ? escapeAttr(pkg.pdf_path) : "Open PDF";
  return `<a class="file-link" href="${href}" target="_blank" rel="noopener" title="${title}">PDF</a>`;
}

function liveResumeCell(row) {
  const pkg = row.package_id ? state.packages.find((item) => item.id === row.package_id) : null;
  if (pkg) return resumeCell(pkg);
  return "—";
}

function editLink(pkg, name, label, title) {
  if (!name) return "";
  const href = `/api/packages/${encodeURIComponent(pkg.id)}/file/${encodeURIComponent(name)}`;
  const tip = title ? ` title="${escapeAttr(title)}"` : "";
  return `<a class="file-link" href="${href}" rel="noopener"${tip}>${escapeHtml(label)}</a>`;
}

function editCell(pkg) {
  if (!pkg) return "—";
  const htmlTip = pkg.html_path
    ? `Edit in Cursor: ${pkg.html_path}`
    : "Editable HTML resume";
  const links = [
    editLink(pkg, pkg.docx_name, "Word", "Editable Word document. Pages on Mac can open this too."),
    editLink(pkg, pkg.html_name, "HTML", htmlTip),
    editLink(pkg, pkg.pages_name, "Pages", "Opens in Pages"),
  ].filter(Boolean);
  if (pkg.html_name) {
    links.push(
      `<button type="button" class="rebuild-pdf ghost" data-id="${escapeAttr(pkg.id)}" title="Rebuild PDF from the HTML on disk">Rebuild PDF</button>`
    );
  }
  if (!links.length) return "—";
  return `<span class="edit-links">${links.join(" ")}</span>`;
}

function liveEditCell(row) {
  const pkg = row.package_id ? state.packages.find((item) => item.id === row.package_id) : null;
  return editCell(pkg);
}

function deleteCell(pkgId, company, role) {
  if (!pkgId) {
    return `<button type="button" class="row-delete ghost" data-forget="1" aria-label="Remove row">Remove</button>`;
  }
  const label = `Delete ${company || ""} ${role || ""}`.trim();
  return `<button type="button" class="row-delete ghost" data-id="${escapeAttr(pkgId)}" aria-label="${escapeAttr(label)}">Delete</button>`;
}

function bindDelete(tr, packageId, liveRow) {
  const btn = tr.querySelector(".row-delete");
  if (!btn) return;
  btn.addEventListener("click", async (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (btn.dataset.forget) {
      if (liveRow) {
        state.jobs = state.jobs.filter((row) => row !== liveRow && jobKey(row) !== jobKey(liveRow));
      }
      renderBoard(state.activeId);
      return;
    }
    const id = packageId || btn.dataset.id;
    if (!id) return;
    const who = liveRow
      ? `${liveRow.company || ""} — ${liveRow.role || ""}`
      : id;
    if (!window.confirm(`Delete ${who.trim() || id}? This removes the folder from applications/.`)) {
      return;
    }
    try {
      await api("/api/packages/" + encodeURIComponent(id), { method: "DELETE" });
      state.jobs = state.jobs.filter((row) => row.package_id !== id);
      if (state.activeId === id) {
        state.activeId = null;
        $("report").hidden = true;
      }
      await loadPackages();
    } catch (err) {
      setStrip(err.message);
    }
  });
}

function bindRebuild(tr) {
  const btn = tr.querySelector(".rebuild-pdf");
  if (!btn) return;
  btn.addEventListener("click", async (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const id = btn.dataset.id;
    if (!id) return;
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Rebuilding…";
    try {
      await api("/api/packages/" + encodeURIComponent(id) + "/rebuild-pdf", { method: "POST" });
      await loadPackages(state.activeId);
      if (state.activeId === id) {
        await openPackage(id);
      }
    } catch (err) {
      setStrip(err.message);
      btn.disabled = false;
      btn.textContent = label;
    }
  });
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
  const readyPackages = state.packages.filter((pkg) => !packageHiddenByLive(pkg));
  if (!state.packages.length && !state.jobs.length) {
    body.innerHTML =
      '<tr class="empty-row"><td colspan="10">No packages yet. Hunt from your profile, or paste job URLs.</td></tr>';
    renderProgress("");
    return;
  }
  renderProgress();
  for (const row of state.jobs) {
    const tr = document.createElement("tr");
    tr.className = "job-row-live job-row-" + (row.status || "found");
    const pkg = row.package_id ? state.packages.find((item) => item.id === row.package_id) : null;
    tr.innerHTML = boardCells(
      row.role,
      row.company,
      locationLabel(row, pkg),
      workModeLabel(row.work_mode || (pkg && pkg.work_mode)),
      atsLabel(row, pkg),
      `<span class="job-status job-status-${escapeAttr(row.status || "found")}">${escapeHtml(statusLabel(row))}</span>`,
      liveResumeCell(row),
      liveEditCell(row),
      liveJobLink(row),
      deleteCell(row.package_id, row.company, row.role)
    );
    bindDelete(tr, row.package_id, row);
    bindRebuild(tr);
    if (row.package_id) {
      tr.tabIndex = 0;
      tr.style.cursor = "pointer";
      tr.addEventListener("click", (ev) => {
        if (ev.target.closest("a") || ev.target.closest("button")) return;
        openPackage(row.package_id);
      });
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openPackage(row.package_id);
        }
      });
    }
    body.appendChild(tr);
  }
  for (const pkg of readyPackages) {
    const tr = document.createElement("tr");
    tr.dataset.id = pkg.id;
    if (pkg.id === active) tr.classList.add("active");
    tr.innerHTML = boardCells(
      pkg.role,
      pkg.company,
      locationLabel(pkg, pkg),
      workModeLabel(pkg.work_mode),
      atsLabel(pkg, pkg),
      `<span class="job-status job-status-ready">Ready</span>`,
      resumeCell(pkg),
      editCell(pkg),
      jobLinkCell(pkg),
      deleteCell(pkg.id, pkg.company, pkg.role)
    );
    bindDelete(tr, pkg.id);
    bindRebuild(tr);
    tr.tabIndex = 0;
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a") || ev.target.closest("button")) return;
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
    const src = `/api/packages/${encodeURIComponent(detail.id)}/file/${encodeURIComponent(detail.pdf_name)}?t=${Date.now()}`;
    const pathNote = detail.pdf_path ? `<p class="hint">${escapeHtml(detail.pdf_path)}</p>` : "";
    const htmlNote = detail.html_path
      ? `<p class="hint">Edit HTML in Cursor: ${escapeHtml(detail.html_path)} then Rebuild PDF.</p>`
      : "";
    body.innerHTML = `${pathNote}${htmlNote}<iframe title="Resume PDF" src="${src}"></iframe>`;
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

function watchRun(runId, mode) {
  if (state.events) state.events.close();
  state.jobs = [];
  state.huntStage = "";
  renderProgress("");
  setLamp("on");
  setControls(mode === "stopping" ? "stopping" : "running");
  $("run-state").textContent = mode === "stopping" ? "stopping" : "running";
  setStrip("— strip open —\n");
  const src = new EventSource("/api/runs/" + runId + "/stream");
  state.events = src;
  src.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.line && data.type !== "progress") setStrip(data.line, true);
      if (data.type === "found" || data.type === "queued") {
        upsertJob({ ...data, status: data.status || (data.type === "queued" ? "queued" : "found") });
        renderBoard(state.activeId);
      }
      if (data.type === "queue") {
        for (const row of data.jobs || []) {
          upsertJob({ ...row, status: row.status || "queued" });
        }
        if ((data.jobs || []).length) $("run-state").textContent = "tailoring";
        renderBoard(state.activeId);
      }
      if (data.type === "hunt_stage") {
        state.huntStage = data.detail || data.line || "";
        const busy = state.jobs.some((row) => row.status === "working" || row.status === "queued");
        if (state.huntStage && !busy) $("run-state").textContent = state.huntStage;
        if (!state.jobs.length && state.huntStage) renderProgress(state.huntStage);
        applyCamoufoxStage(Boolean(data.browser), state.huntStage);
      }
      if (data.type === "processing" && (data.company || data.role)) {
        upsertJob({ ...data, status: data.status || "working" });
        $("run-state").textContent = "tailoring";
        renderBoard(state.activeId);
      }
      if (data.type === "package") {
        upsertJob({
          ...data,
          status: data.status || (data.package_id ? "ready" : "failed"),
        });
        renderBoard(state.activeId);
        if (data.package_id) loadPackages(data.package_id);
      }
      if (data.type === "failed") {
        upsertJob({ ...data, status: "failed" });
        renderBoard(state.activeId);
      }
      if (data.type === "stopped") {
        upsertJob({ ...data, status: "stopped" });
        renderBoard(state.activeId);
      }
      if (data.type === "progress") {
        renderProgress(data.line);
      }
    } catch (_) {}
  };
  src.addEventListener("done", async (ev) => {
    src.close();
    state.events = null;
    let payload = {};
    try { payload = JSON.parse(ev.data); } catch (_) {}
    setLamp(payload.status === "done" ? "done" : "");
    $("run-state").textContent = payload.status || "done";
    setControls("idle");
    const first = payload.package_id || (payload.packages && payload.packages[0]);
    await loadPackages(first);
    renderProgress(progressFromJobs());
    if (first) openPackage(first);
    if (payload.error && payload.status !== "stopped") setStrip("ERROR: " + payload.error, true);
    $("hunt").disabled = false;
    $("run").disabled = false;
    hideCamoufox();
  });
  src.onerror = () => {
    $("run-state").textContent = "stream interrupted";
  };
}

$("refresh").addEventListener("click", () => {
  state.jobs = [];
  state.huntStage = "";
  renderProgress("");
  loadPackages();
});
$("hunt").addEventListener("click", async () => {
  setControls("running");
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
    setControls("idle");
  }
});
$("stop").addEventListener("click", async () => {
  if (!state.runId) return;
  setControls("stopping");
  $("run-state").textContent = "stopping";
  try {
    await api("/api/runs/" + state.runId + "/stop", { method: "POST" });
    setStrip("Stopping hunt…", true);
  } catch (err) {
    setStrip(err.message, true);
    setControls("running");
  }
});
$("intake").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  setControls("running");
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
    setControls("idle");
  }
});

loadMe().catch(() => {});
loadPackages().catch((err) => {
  $("package-list").innerHTML = `<tr class="empty-row"><td colspan="10">${escapeHtml(err.message)}</td></tr>`;
});
(async () => {
  try {
    const active = await api("/api/runs/active");
    if (!active.id) return;
    state.runId = active.id;
    watchRun(active.id, active.status === "stopping" ? "stopping" : "running");
    if (active.browser) applyCamoufoxStage(true, "Sign in or extra verification happens here");
    if (active.status === "stopping") setStrip("Hunt is stopping…", true);
  } catch (_) {}
})();
