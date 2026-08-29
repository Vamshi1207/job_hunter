const $ = (id) => document.getElementById(id);

const state = {
  packages: [],
  activeId: null,
  runId: null,
  events: null,
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
}

async function loadPackages(selectId) {
  const data = await api("/api/packages");
  state.packages = data.packages || [];
  const list = $("package-list");
  list.innerHTML = "";
  if (!state.packages.length) {
    list.innerHTML = '<li class="empty">No packages yet. Run a tailor to create one.</li>';
    return;
  }
  for (const pkg of state.packages) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "docket" + (pkg.id === (selectId || state.activeId) ? " active" : "");
    btn.innerHTML = `<strong>${escapeHtml(pkg.company)}</strong><span>${escapeHtml(pkg.role || "Role")}${pkg.score != null ? " · " + pkg.score + "/100" : ""}</span>`;
    btn.addEventListener("click", () => openPackage(pkg.id));
    li.appendChild(btn);
    list.appendChild(li);
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
  $("report-meta").textContent = [detail.date, detail.has_pdf ? "PDF ready" : "No PDF"]
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
    body.innerHTML = `<p><a href="${src}" target="_blank" rel="noopener">Open PDF</a></p><iframe title="Resume PDF" src="${src}"></iframe>`;
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

async function inspectUrl() {
  const note = $("inspect-note");
  note.hidden = true;
  try {
    const data = await api("/api/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: $("url").value, jd: $("jd").value }),
    });
    if (data.company && !$("company").value) $("company").value = data.company;
    if (data.role && !$("role").value) $("role").value = data.role;
    if (data.jd && !$("jd").value) $("jd").value = data.jd;
    if (data.blocked || data.needs_jd) {
      note.hidden = false;
      note.textContent = data.blocked
        ? "This host blocks scrapers. Paste the job description, then run."
        : "Could not read that page. Paste the job description, then run.";
    } else {
      note.hidden = false;
      note.textContent = data.fetched ? "Posting loaded from the URL." : "Using the text you pasted.";
    }
  } catch (err) {
    note.hidden = false;
    note.textContent = err.message;
  }
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
    } catch (_) {}
  };
  src.addEventListener("done", async (ev) => {
    src.close();
    state.events = null;
    let payload = {};
    try { payload = JSON.parse(ev.data); } catch (_) {}
    setLamp(payload.status === "done" ? "done" : "");
    $("run-state").textContent = payload.status || "done";
    await loadPackages(payload.package_id);
    if (payload.package_id) openPackage(payload.package_id);
    if (payload.error) setStrip("ERROR: " + payload.error, true);
  });
  src.onerror = () => {
    $("run-state").textContent = "stream interrupted";
  };
}

$("inspect").addEventListener("click", inspectUrl);
$("refresh").addEventListener("click", () => loadPackages());
$("intake").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const runBtn = $("run");
  runBtn.disabled = true;
  try {
    const body = {
      url: $("url").value.trim(),
      company: $("company").value.trim(),
      role: $("role").value.trim(),
      jd: $("jd").value.trim(),
    };
    const run = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.runId = run.id;
    if (run.company) $("company").value = run.company;
    if (run.role) $("role").value = run.role;
    watchRun(run.id);
  } catch (err) {
    $("inspect-note").hidden = false;
    $("inspect-note").textContent = err.message;
    setLamp("");
  } finally {
    runBtn.disabled = false;
  }
});

loadMe().catch(() => {});
loadPackages().catch((err) => {
  $("package-list").innerHTML = `<li class="empty">${escapeHtml(err.message)}</li>`;
});
