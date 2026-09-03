const $ = (id) => document.getElementById(id);

const state = {
  packages: [],
  jobs: [],
  activeId: null,
  runId: null,
  events: null,
  hunt: { max_jobs: 0, roles: [], markets: [], search_locations: [], preferred_city: "" },
  huntStage: "",
  camoufoxUrl: "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale",
  helperPath: "web/apply-helper",
  board: { query: "", sortKey: "modified", sortDir: "desc", tab: "queue" },
  heldUntilRefresh: new Map(),
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
  const markets = (state.hunt.search_locations || state.hunt.markets || []).join(", ");
  const preferred = state.hunt.preferred_city || me.city || "";
  const where = [me.city, me.country].filter(Boolean).join(", ");
  const years = state.hunt.years_experience;
  const skip = (state.hunt.exclude_levels || []).filter((level) =>
    ["principal", "staff"].includes(String(level).toLowerCase())
  );
  const reject = (state.hunt.reject_skills || []).join(", ");
  $("hunt-line").textContent =
    `Hunt looks for ${roles}` +
    (markets || where ? ` in ${markets || where}` : "") +
    (preferred ? ` (${preferred} preferred)` : "") +
    ". US postings are kept only when they are open to Canada applicants" +
    (state.hunt.max_jobs
      ? ` (safety cap ${state.hunt.max_jobs} matches)`
      : ", and tailors every posting that matches") +
    (years ? `, ~${years} years experience` : "") +
    (skip.length ? `, skipping ${skip.join("/")}` : "") +
    (reject ? `, not ${reject}` : "") +
    `. The browser panel appears only if a board asks you to sign in, complete 2FA, or solve a CAPTCHA. Apply is never clicked.`;
  if (me.camoufox && me.camoufox.vnc) state.camoufoxUrl = me.camoufox.vnc;
  if (me.apply_helper && me.apply_helper.extension_path) {
    state.helperPath = me.apply_helper.extension_path;
    const pathEl = $("helper-path");
    if (pathEl) pathEl.textContent = me.apply_helper.extension_path;
  }
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
    apply_url: data.apply_url || "",
    apply_kind: data.apply_kind || "",
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
      apply_url: incoming.apply_url || prev.apply_url,
      apply_kind: incoming.apply_kind || prev.apply_kind,
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
    apply_url: row.apply_url || "",
    apply_kind: row.apply_kind || "",
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
  const hunt = line === undefined ? progressFromJobs() : line;
  if (hunt) {
    el.hidden = false;
    el.textContent = hunt;
    title.textContent = state.jobs.length ? "Jobs" : "Ready packages";
    return;
  }
  el.hidden = true;
  el.textContent = "";
  title.textContent = "Ready packages";
}

function setBoardHeading({ ready, applied, progress, total, shown, hunt, queueTotal, appliedTotal }) {
  const title = $("board-title");
  const el = $("board-progress");
  const tab = state.board.tab || "queue";
  if (tab === "applied") {
    title.textContent = applied ? `${applied} applied` : "Applied";
  } else if (ready || applied || progress) {
    title.textContent = `${ready} ready to apply`;
  } else if (state.jobs.length) {
    title.textContent = "Jobs";
  } else {
    title.textContent = "Ready packages";
  }
  const bits = [];
  if (tab === "queue" && applied) bits.push(`${applied} applied`);
  if (progress) bits.push(`${progress} in progress`);
  if (total != null && shown != null && shown !== total) bits.push(`showing ${shown} of ${total}`);
  if (tab === "queue" && hunt) bits.push(hunt);
  updateBoardTabs(queueTotal ?? ready + progress, appliedTotal ?? applied);
  if (!bits.length) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = bits.join(" · ");
}

function updateBoardTabs(queueCount, appliedCount) {
  const tab = state.board.tab || "queue";
  const queueBtn = $("tab-queue");
  const appliedBtn = $("tab-applied");
  if (queueBtn) {
    queueBtn.classList.toggle("active", tab === "queue");
    queueBtn.setAttribute("aria-selected", tab === "queue" ? "true" : "false");
  }
  if (appliedBtn) {
    appliedBtn.classList.toggle("active", tab === "applied");
    appliedBtn.setAttribute("aria-selected", tab === "applied" ? "true" : "false");
  }
  const queueEl = $("queue-count");
  const appliedEl = $("applied-count");
  if (queueEl) queueEl.textContent = queueCount ? String(queueCount) : "";
  if (appliedEl) appliedEl.textContent = appliedCount ? String(appliedCount) : "";
}

function statusLabel(row) {
  const status = typeof row === "string" ? row : row && row.status;
  const detail = typeof row === "string" ? "" : (row && row.detail) || "";
  if (status === "found") return "Found";
  if (status === "queued") return "Waiting";
  if (status === "working") return detail || "Tailoring";
  if (status === "ready") return "Ready";
  if (status === "applied") return "Applied";
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

function boardCells(role, company, location, mode, ats, statusHtml, resume, edit, link, apply, del) {
  return `
      <td class="col-role">${escapeHtml(role || "Role")}</td>
      <td class="col-company">${escapeHtml(company || "")}</td>
      <td class="col-location">${escapeHtml(location)}</td>
      <td class="col-mode">${escapeHtml(mode)}</td>
      <td class="col-ats ats-cell">${escapeHtml(ats)}</td>
      <td class="col-status">${statusHtml}</td>
      <td class="col-resume">${resume}</td>
      <td class="col-edit">${edit}</td>
      <td class="col-link">${link}</td>
      <td class="col-apply">${apply}</td>
      <td class="col-delete">${del}</td>
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
  const files = links.length ? `<span class="edit-files">${links.join("")}</span>` : "";
  const rebuild = pkg.html_name
    ? `<button type="button" class="rebuild-pdf ghost" data-id="${escapeAttr(pkg.id)}" title="Rebuild PDF from the HTML on disk">Rebuild PDF</button>`
    : "";
  if (!files && !rebuild) return "—";
  return `<span class="edit-links">${files}${rebuild}</span>`;
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

function applyHost(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (_) {
    return "";
  }
}

function isAggregatorHost(url) {
  const host = applyHost(url);
  return /linkedin\.com$|indeed\.|glassdoor\.com|ziprecruiter\.com|simplyhired\.com|monster\.com/.test(host);
}

function applyKindLabel(kind) {
  return kind === "easy_apply" ? "Easy Apply" : "Apply";
}

function applyCell(row, pkg) {
  const applyUrl = (row && row.apply_url) || (pkg && pkg.apply_url) || "";
  const posting = (row && row.url) || (pkg && pkg.url) || "";
  const kind = (row && row.apply_kind) || (pkg && pkg.apply_kind) || "";
  const id = (row && row.package_id) || (pkg && pkg.id) || "";
  if (!applyUrl && !posting && !id) return "—";
  const form = applyUrl || posting;
  const host = applyHost(form);
  const resolved = Boolean(applyUrl) && kind !== "aggregator";
  const caption = resolved
    ? `<a class="apply-form-url" href="${escapeAttr(applyUrl)}" target="_blank" rel="noopener" title="${escapeAttr(applyUrl)}">${escapeHtml(host)}</a>`
    : host
      ? `<span class="apply-form-url muted" title="Form URL is resolved when you click Apply">${escapeHtml(host)}</span>`
      : "";
  const applied = Boolean(pkg && pkg.applied);
  const mark = id
    ? `<label class="applied-stamp${applied ? " is-applied" : ""}">
        <input type="checkbox" class="mark-applied" data-id="${escapeAttr(id)}" ${applied ? "checked" : ""} aria-label="${applied ? "Undo applied" : "Mark applied"}">
        <span class="applied-box" aria-hidden="true"><span class="applied-tick"></span></span>
        <span class="applied-text">Applied</span>
      </label>`
    : "";
  return `<div class="apply-cell">
    <button type="button" class="apply-btn" data-id="${escapeAttr(id)}" data-url="${escapeAttr(posting)}" data-apply-url="${escapeAttr(applyUrl)}" data-kind="${escapeAttr(kind)}" data-company="${escapeAttr((row && row.company) || (pkg && pkg.company) || "")}" data-role="${escapeAttr((row && row.role) || (pkg && pkg.role) || "")}">${escapeHtml(applyKindLabel(kind))}</button>
    ${caption}
    ${mark}
  </div>`;
}

function bindApply(tr) {
  const btn = tr.querySelector(".apply-btn");
  if (!btn) return;
  btn.addEventListener("click", async (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Finding form…";
    try {
      const data = await api("/api/apply/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          package_id: btn.dataset.id || "",
          url: btn.dataset.url || "",
          company: btn.dataset.company || "",
          role: btn.dataset.role || "",
        }),
      });
      const kind = data.apply_kind || btn.dataset.kind;
      const target = data.apply_url || "";
      const canOpen = target && (kind === "easy_apply" || !isAggregatorHost(target));
      if (canOpen) {
        window.open(target, "_blank", "noopener");
        const caption = tr.querySelector(".apply-form-url");
        const host = applyHost(target);
        if (caption && host) {
          caption.textContent = host;
          caption.classList.remove("muted");
          if (caption.tagName === "A") {
            caption.href = target;
            caption.title = target;
          }
        }
        btn.textContent = applyKindLabel(kind);
        setStrip(
          kind === "easy_apply"
            ? "This posting is LinkedIn Easy Apply — that is the form. If the helper is installed, matching fields fill themselves. You click Submit."
            : "Opened the company form, not the LinkedIn posting. If the helper is installed, matching fields fill themselves. You click Submit. Mark applied when you have submitted."
        );
      } else {
        btn.textContent = label;
        setStrip(
          "No company form URL yet. LinkedIn hid it on the public page. Open Posting while signed in, or hunt again so Camoufox can read the Apply link. Apply never opens the LinkedIn listing."
        );
      }
    } catch (err) {
      btn.textContent = label;
      setStrip(err.message);
    } finally {
      btn.disabled = false;
      if (btn.textContent === "Finding form…" || btn.textContent === "Opening…") btn.textContent = label;
    }
  });
}

function appliedMotionMs(checking) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return 0;
  return checking ? 280 : 180;
}

function bindMarkApplied(tr) {
  const input = tr.querySelector(".mark-applied");
  if (!input) return;
  const stamp = input.closest(".applied-stamp");
  input.addEventListener("click", (ev) => ev.stopPropagation());
  input.addEventListener("change", async (ev) => {
    ev.stopPropagation();
    const id = input.dataset.id;
    if (!id) {
      input.checked = !input.checked;
      return;
    }
    const next = input.checked;
    input.disabled = true;
    if (stamp) {
      stamp.classList.toggle("is-checking", next);
      stamp.classList.toggle("is-unchecking", !next);
      stamp.classList.toggle("is-applied", next);
    }
    try {
      const updated = await api("/api/packages/" + encodeURIComponent(id) + "/applied", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ applied: next }),
      });
      const idx = state.packages.findIndex((item) => item.id === id);
      if (idx >= 0) state.packages[idx] = { ...state.packages[idx], ...updated };
      else state.packages.unshift(updated);
      state.heldUntilRefresh.set(id, state.board.tab || "queue");
      const status = tr.querySelector(".job-status");
      if (status) {
        status.textContent = next ? "Applied" : "Ready";
        status.className = "job-status job-status-" + (next ? "applied" : "ready");
      }
      input.setAttribute("aria-label", next ? "Undo applied" : "Mark applied");
      await new Promise((resolve) => setTimeout(resolve, appliedMotionMs(next)));
    } catch (err) {
      input.checked = !next;
      if (stamp) {
        stamp.classList.toggle("is-applied", !next);
        stamp.classList.remove("is-checking", "is-unchecking");
      }
      setStrip(err.message);
    } finally {
      input.disabled = false;
      if (stamp) stamp.classList.remove("is-checking", "is-unchecking");
    }
  });
}

function escapeAttr(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function loadPackages(selectId) {
  if (!state.jobs.length && !state.packages.length) {
    $("package-list").innerHTML =
      '<tr class="empty-row"><td colspan="11">Loading packages…</td></tr>';
  }
  const data = await api("/api/packages");
  state.packages = data.packages || [];
  renderBoard(selectId || state.activeId);
}

function boardItem(row, pkg) {
  const live = Boolean(row);
  const packageId = (row && row.package_id) || (pkg && pkg.id) || "";
  const heldTab = packageId ? state.heldUntilRefresh.get(packageId) : "";
  const applied = Boolean((pkg && pkg.applied) || (row && row.applied));
  const status = live ? row.status || "found" : applied ? "applied" : "ready";
  const inProgress = ["found", "queued", "working"].includes(status);
  const resumeText = pkg && pkg.pdf_name ? "PDF" : "—";
  const hasHtml = Boolean(pkg && (pkg.html_name || pkg.docx_name || pkg.pages_name));
  const posting = (row && row.url) || (pkg && pkg.url) || "";
  const applyKind = (row && row.apply_kind) || (pkg && pkg.apply_kind) || "";
  const atsRaw = row && row.ats_score != null ? row.ats_score : pkg && (pkg.ats_score != null ? pkg.ats_score : pkg.score);
  const atsNum = atsRaw == null || atsRaw === "" ? null : Number(atsRaw);
  const committedApplied = applied && !heldTab;
  return {
    row,
    pkg,
    live,
    applied,
    group: inProgress ? "progress" : committedApplied ? "applied" : "ready",
    role: (row && row.role) || (pkg && pkg.role) || "",
    company: (row && row.company) || (pkg && pkg.company) || "",
    location: locationLabel(row, pkg),
    mode: workModeLabel((row && row.work_mode) || (pkg && pkg.work_mode)),
    ats: atsLabel(row, pkg),
    atsNum: Number.isFinite(atsNum) ? atsNum : -1,
    status,
    statusLabel: applied && !inProgress ? "Applied" : statusLabel(row || status),
    resume: resumeText,
    hasPdf: Boolean(pkg && pkg.pdf_name),
    hasEdit: hasHtml,
    hasLink: Boolean(posting),
    applyKind: applyKind || (posting || (pkg && pkg.apply_url) ? "apply" : ""),
    modified: (pkg && pkg.modified) || 0,
    packageId,
    heldTab: heldTab || "",
  };
}

function collectBoardItems() {
  const items = [];
  for (const row of state.jobs) {
    const pkg = row.package_id ? state.packages.find((item) => item.id === row.package_id) : null;
    items.push(boardItem(row, pkg));
  }
  for (const pkg of state.packages) {
    if (packageHiddenByLive(pkg)) continue;
    items.push(boardItem(null, pkg));
  }
  return items;
}

function displayTab(item) {
  if (item.heldTab) return item.heldTab;
  return item.group === "applied" ? "applied" : "queue";
}

function statusTone(item) {
  if (item.applied && item.group !== "progress") return "applied";
  return item.status || "found";
}

function itemMatchesSearch(item) {
  const query = (state.board.query || "").trim().toLowerCase();
  if (!query) return true;
  const hay = `${item.role} ${item.company} ${item.location}`.toLowerCase();
  return hay.includes(query);
}

function sortItems(items) {
  const key = state.board.sortKey || "modified";
  const dir = state.board.sortDir === "asc" ? 1 : -1;
  const value = (item) => {
    if (key === "ats") return item.atsNum;
    if (key === "modified") return item.modified;
    if (key === "resume") return item.hasPdf ? 1 : 0;
    if (key === "edit") return item.hasEdit ? 1 : 0;
    if (key === "link") return item.hasLink ? 1 : 0;
    if (key === "apply") return item.applyKind || "";
    if (key === "del") return item.company;
    return String(item[key] || "").toLowerCase();
  };
  return items.slice().sort((a, b) => {
    const left = value(a);
    const right = value(b);
    if (left < right) return -1 * dir;
    if (left > right) return 1 * dir;
    return String(a.company).localeCompare(String(b.company));
  });
}

function updateSortHeaders() {
  document.querySelectorAll(".th-sort").forEach((btn) => {
    const key = btn.dataset.key;
    if (key === state.board.sortKey) {
      btn.setAttribute("aria-sort", state.board.sortDir === "asc" ? "ascending" : "descending");
    } else {
      btn.removeAttribute("aria-sort");
    }
  });
}

function appendBoardRow(body, item, active) {
  const tr = document.createElement("tr");
  const row = item.row;
  const pkg = item.pkg;
  if (item.live) {
    tr.className = "job-row-live job-row-" + (item.status || "found");
  } else if (pkg && pkg.id === active) {
    tr.classList.add("active");
  }
  if (item.applied) tr.classList.add("is-applied-row");
  if (pkg && pkg.id) tr.dataset.id = pkg.id;
  const statusHtml = `<span class="job-status job-status-${escapeAttr(statusTone(item))}">${escapeHtml(item.statusLabel)}</span>`;
  tr.innerHTML = boardCells(
    item.role,
    item.company,
    item.location,
    item.mode,
    item.ats,
    statusHtml,
    item.live ? liveResumeCell(row) : resumeCell(pkg),
    item.live ? liveEditCell(row) : editCell(pkg),
    item.live ? liveJobLink(row) : jobLinkCell(pkg),
    applyCell(row, pkg),
    deleteCell(item.packageId, item.company, item.role)
  );
  bindDelete(tr, item.packageId, row);
  bindRebuild(tr);
  bindApply(tr);
  bindMarkApplied(tr);
  if (item.packageId) {
    tr.tabIndex = 0;
    tr.style.cursor = "pointer";
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a, button, label, input")) return;
      openPackage(item.packageId);
    });
    tr.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        openPackage(item.packageId);
      }
    });
  }
  body.appendChild(tr);
}

function appendGroup(body, label, items, active) {
  if (!items.length) return;
  const header = document.createElement("tr");
  header.className = "group-row";
  header.innerHTML = `<th scope="colgroup" colspan="11">${escapeHtml(label)} <span class="group-count">${items.length}</span></th>`;
  body.appendChild(header);
  for (const item of items) appendBoardRow(body, item, active);
}

function renderBoard(active) {
  const body = $("package-list");
  body.innerHTML = "";
  const tab = state.board.tab || "queue";
  if (!state.packages.length && !state.jobs.length) {
    body.innerHTML =
      '<tr class="empty-row"><td colspan="11">No packages yet. Hunt from your profile, or paste job URLs.</td></tr>';
    setBoardHeading({ ready: 0, applied: 0, progress: 0, hunt: "", queueTotal: 0, appliedTotal: 0 });
    return;
  }
  const all = collectBoardItems();
  const queueTotal = all.filter((item) => displayTab(item) === "queue").length;
  const appliedTotal = all.filter((item) => displayTab(item) === "applied").length;
  const matched = sortItems(all.filter(itemMatchesSearch).filter((item) => displayTab(item) === tab));
  const progress = matched.filter((item) => item.group === "progress");
  const ready = matched.filter((item) => item.group === "ready");
  if (!matched.length) {
    const empty =
      tab === "applied" && !(state.board.query || "").trim()
        ? "No applied jobs yet. Mark a job applied from the queue; it moves here on refresh."
        : "No jobs match this search.";
    body.innerHTML = `<tr class="empty-row"><td colspan="11">${empty}</td></tr>`;
    setBoardHeading({
      ready: 0,
      applied: appliedTotal,
      progress: 0,
      total: tab === "applied" ? appliedTotal : queueTotal,
      shown: 0,
      hunt: progressFromJobs(),
      queueTotal,
      appliedTotal,
    });
    updateSortHeaders();
    return;
  }
  if (tab === "applied") {
    for (const item of matched) appendBoardRow(body, item, active);
  } else {
    appendGroup(body, "In progress", progress, active);
    appendGroup(body, "Ready to apply", ready, active);
  }
  setBoardHeading({
    ready: ready.length,
    applied: appliedTotal,
    progress: progress.length,
    total: tab === "applied" ? appliedTotal : queueTotal,
    shown: matched.length,
    hunt: progressFromJobs(),
    queueTotal,
    appliedTotal,
  });
  updateSortHeaders();
}

function bindBoardControls() {
  const search = $("board-search");
  if (search && !search.dataset.bound) {
    search.dataset.bound = "1";
    search.addEventListener("input", () => {
      state.board.query = search.value;
      renderBoard(state.activeId);
    });
  }
  document.querySelectorAll(".board-tab").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      if (!tab || state.board.tab === tab) return;
      state.board.tab = tab;
      renderBoard(state.activeId);
    });
    btn.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
      ev.preventDefault();
      const next = ev.key === "ArrowRight" ? $("tab-applied") : $("tab-queue");
      if (next) next.focus();
      next.click();
    });
  });
  document.querySelectorAll(".th-sort").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const key = btn.dataset.key;
      if (state.board.sortKey === key) {
        state.board.sortDir = state.board.sortDir === "asc" ? "desc" : "asc";
      } else {
        state.board.sortKey = key;
        state.board.sortDir = "asc";
      }
      renderBoard(state.activeId);
    });
  });
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
  state.heldUntilRefresh.clear();
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
bindBoardControls();
loadPackages().catch((err) => {
  $("package-list").innerHTML = `<tr class="empty-row"><td colspan="11">${escapeHtml(err.message)}</td></tr>`;
});
(function bindFillBookmarklet() {
  const link = $("fill-bookmarklet");
  if (!link) return;
  link.href =
    "javascript:" +
    encodeURIComponent(
      "(function(){var s=document.createElement('script');s.src='http://127.0.0.1:8000/static/fill-helper.js';document.documentElement.appendChild(s)})();"
    );
})();
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
