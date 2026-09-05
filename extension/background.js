const DESK = "http://127.0.0.1:8000";
const activeAnswerRequests = new Map();

function ensureMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "job-desk-answer",
      title: "Answer this question",
      contexts: ["editable", "selection"],
    });
    chrome.contextMenus.create({
      id: "job-desk-attach-resume",
      title: "Attach resume here",
      contexts: ["all"],
    });
  });
}

ensureMenus();
chrome.runtime.onInstalled.addListener(ensureMenus);

function setBadge(tabId, text, color, clearMs) {
  if (!tabId) return;
  try {
    chrome.action.setBadgeText({ text: text || "", tabId });
    if (color) chrome.action.setBadgeBackgroundColor({ color, tabId });
    if (clearMs) {
      setTimeout(() => {
        try {
          chrome.action.setBadgeText({ text: "", tabId });
        } catch (_) {}
      }, clearMs);
    }
  } catch (_) {}
}

async function showTabHUD(tab, frameId, opts) {
  if (!tab || !tab.id) return;
  // Always target frame 0 (main viewport) so the Status HUD is guaranteed visible on screen
  const target = { tabId: tab.id, frameIds: [0] };
  try {
    await chrome.scripting.executeScript({
      target,
      files: ["fill.js"],
    });
    await chrome.scripting.executeScript({
      target,
      func: (hudOpts) => {
        if (globalThis.jobDeskStatusHUD && typeof globalThis.jobDeskStatusHUD.show === "function") {
          globalThis.jobDeskStatusHUD.show(hudOpts);
        }
      },
      args: [opts],
    });
  } catch (_) {}
}

async function payloadForUrl(url) {
  let res;
  try {
    res = await fetch(DESK + "/api/apply/for-page?url=" + encodeURIComponent(url));
  } catch (err) {
    throw new Error(
      "Cannot connect to Job Desk at " +
        DESK +
        ". Please ensure the local desk server is running (e.g. 'python3 -m web.app')."
    );
  }
  let body = {};
  try {
    body = await res.json();
  } catch (_) {}
  if (!res.ok) {
    throw new Error(body.detail || res.statusText || "Desk did not match this form");
  }
  if (!body || !body.fields) {
    throw new Error("Desk returned an empty fill payload");
  }

  // Preload resume file binary in background worker (immune to webpage Mixed-Content and CSP restrictions)
  if (body.files && body.files.resume && body.files.resume.url) {
    try {
      const fileRes = await fetch(body.files.resume.url);
      if (fileRes.ok) {
        const buf = await fileRes.arrayBuffer();
        let binary = "";
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.byteLength; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        body.files.resume.base64 = btoa(binary);
      }
    } catch (_) {}
  }

  return body;
}

// Background helper for content scripts to fetch files/APIs without page CSP restrictions
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;

  if (msg.type === "job-desk-badge") {
    if (sender && sender.tab && sender.tab.id) {
      setBadge(sender.tab.id, msg.text || "", msg.color || "#16a34a", 5000);
    }
  }

  if (msg.type === "job-desk-trigger-fill") {
    if (sender && sender.tab && sender.tab.id) {
      runInFrames(sender.tab, "job-desk-fill", msg.payload).catch(() => {});
    }
  }

  if (msg.type === "job-desk-fetch-file") {
    (async () => {
      try {
        const res = await fetch(msg.url);
        if (!res.ok) throw new Error("Fetch failed: " + res.status);
        const buf = await res.arrayBuffer();
        let binary = "";
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.byteLength; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        sendResponse({ ok: true, base64: btoa(binary) });
      } catch (err) {
        sendResponse({ ok: false, error: String(err && err.message ? err.message : err) });
      }
    })();
    return true;
  }

  if (msg.type === "job-desk-cancel-answer") {
    const reqId = msg.requestId;
    if (reqId && activeAnswerRequests.has(reqId)) {
      const controller = activeAnswerRequests.get(reqId);
      try {
        controller.abort();
      } catch (_) {}
      activeAnswerRequests.delete(reqId);
    }
    sendResponse({ ok: true, cancelled: true });
    return true;
  }

  if (msg.type === "job-desk-answer-question") {
    (async () => {
      const reqId = msg.requestId;
      const controller = new AbortController();
      if (reqId) {
        activeAnswerRequests.set(reqId, controller);
      }
      try {
        const res = await fetch(DESK + "/api/apply/answer", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(msg.body),
          signal: controller.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          sendResponse({
            ok: false,
            status: res.status,
            error: data.detail || res.statusText || "Desk did not answer this question",
          });
          return;
        }
        sendResponse({ ok: true, data });
      } catch (err) {
        if (controller.signal.aborted) {
          sendResponse({ ok: false, cancelled: true, error: "Cancelled by user" });
          return;
        }
        sendResponse({
          ok: false,
          error: "Cannot connect to Job Desk at " + DESK + ". Please ensure your Job Desk server is running.",
        });
      } finally {
        if (reqId) {
          activeAnswerRequests.delete(reqId);
        }
      }
    })();
    return true;
  }

  if (msg.type === "job-desk-for-page") {
    (async () => {
      try {
        const res = await fetch(DESK + "/api/apply/for-page?url=" + encodeURIComponent(msg.url));
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          sendResponse({ ok: false, status: res.status, error: data.detail || res.statusText });
          return;
        }
        sendResponse({ ok: true, data });
      } catch (err) {
        sendResponse({ ok: false, error: String(err && err.message ? err.message : err) });
      }
    })();
    return true;
  }
});

async function runInFrames(tab, type, payload, frameId) {
  const target =
    Number.isInteger(frameId) && frameId >= 0
      ? { tabId: tab.id, frameIds: [frameId] }
      : { tabId: tab.id, allFrames: true };
  await chrome.scripting.executeScript({
    target,
    files: ["fill.js"],
  });
  const results = await chrome.scripting.executeScript({
    target,
    func: async (nextPayload, nextType) => {
      try {
        if (nextType === "job-desk-answer") {
          const answer = globalThis.jobDeskAnswerSelected;
          if (typeof answer !== "function") return { ok: false, filled: 0, error: "Answer handler not found" };
          const filled = await answer(nextPayload);
          return { ok: true, filled: Number(filled) || 0 };
        }
        if (nextType === "job-desk-attach-resume") {
          const attach = globalThis.jobDeskAttachSelected;
          if (typeof attach !== "function") return { ok: false, filled: 0, error: "Attach handler not found" };
          const res = await attach(nextPayload);
          if (typeof res === "object") return res;
          return { ok: !!res, filled: res ? 1 : 0 };
        }
        const fill = globalThis.jobDeskFill;
        if (typeof fill !== "function") return { ok: false, filled: 0, error: "Fill handler not found" };
        const filled = await fill(nextPayload);
        return { ok: true, filled: Number(filled) || 0 };
      } catch (err) {
        return { ok: false, error: String(err && err.message ? err.message : err), filled: 0 };
      }
    },
    args: [payload, type],
  });
  let filled = 0;
  let error = "";
  let reason = "";
  for (const item of results || []) {
    const row = item && item.result;
    if (!row) continue;
    filled += Number(row.filled) || 0;
    if (!row.ok && row.error && !error) error = row.error;
    if (!row.ok && row.reason && !reason) reason = row.reason;
  }
  if (error && !filled) throw new Error(error);
  return { ok: !error, filled, reason, error };
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab || !tab.id || !tab.url || !/^https?:\/\//i.test(tab.url)) {
    setBadge(tab ? tab.id : null, "!", "#dc2626", 5000);
    return;
  }
  setBadge(tab.id, "...", "#2563eb");
  try {
    const payload = await payloadForUrl(tab.url);
    const cvName = (payload.files && payload.files.resume && payload.files.resume.name) || "tailored CV";
    const fieldCount = Object.keys(payload.fields || {}).length;

    // Present confirmation card with Role & Company before auto-filling
    await showTabHUD(tab, 0, {
      state: "ready",
      title: "Application Package Matched",
      company: payload.company || "",
      role: payload.role || "",
      message: `Ready to fill application for ${payload.role || "this role"} at ${payload.company || "this company"}.`,
      detail: `• Company: ${payload.company || "—"}\n• Role: ${payload.role || "—"}\n• Tailored CV: ${cvName}\n• Available Answers: ${fieldCount} fields ready`,
      actionText: "⚡ Fill Now",
      actionType: "fill-now",
      actionPayload: payload,
      autoDismiss: 60000,
    });
    setBadge(tab.id, "CV", "#2563eb", 5000);
  } catch (err) {
    setBadge(tab.id, "!", "#dc2626", 8000);
    await showTabHUD(tab, 0, {
      state: "error",
      title: "Fill Error",
      message: String(err && err.message ? err.message : err),
      detail: "Check that this role was launched via Apply from the Job Desk.",
      autoDismiss: 9000,
    });
  }
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!info) return;
  if (!tab || !tab.id || !tab.url || !/^https?:\/\//i.test(tab.url)) {
    setBadge(tab ? tab.id : null, "!", "#dc2626", 5000);
    return;
  }

  if (info.menuItemId === "job-desk-attach-resume") {
    setBadge(tab.id, "CV", "#2563eb");
    await showTabHUD(tab, 0, {
      state: "loading",
      title: "Attaching Resume",
      message: "Looking up tailored CV for this role…",
      detail: "Connecting to Job Desk…",
    });

    try {
      const payload = await payloadForUrl(tab.url);
      const cvName = (payload.files && payload.files.resume && payload.files.resume.name) || "tailored CV";

      await showTabHUD(tab, 0, {
        state: "thinking",
        title: "Attaching Resume…",
        company: payload.company || "",
        role: payload.role || "",
        message: `Attaching ${cvName} to the selected element…`,
      });

      const result = await runInFrames(tab, "job-desk-attach-resume", payload, info.frameId);
      if (result && result.filled > 0) {
        setBadge(tab.id, "OK", "#16a34a", 5000);
        await showTabHUD(tab, 0, {
          state: "success",
          title: "Resume Attached!",
          company: payload.company || "",
          role: payload.role || "",
          message: `Successfully attached ${cvName} to the form!`,
          detail: "Target element highlighted in green. Verify the attachment on the page before submitting.",
          autoDismiss: 6000,
        });
      } else {
        setBadge(tab.id, "—", "#d97706", 6000);
        await showTabHUD(tab, 0, {
          state: "skipped",
          title: "Could Not Attach Here",
          company: payload.company || "",
          role: payload.role || "",
          message: (result && result.reason) || "The right-clicked element does not accept file attachments.",
          detail: "Click 'Point to Attach' below, then click your form's upload button or drag zone.",
          actionText: "🎯 Point to Attach Button / Dropzone",
          autoDismiss: 14000,
        });
      }
    } catch (err) {
      setBadge(tab.id, "!", "#dc2626", 8000);
      await showTabHUD(tab, 0, {
        state: "error",
        title: "Could Not Attach Resume",
        message: String(err && err.message ? err.message : err),
        detail: "Ensure the local Job Desk server is running and this page was opened via Apply.",
        autoDismiss: 9000,
      });
    }
    return;
  }

  if (info.menuItemId === "job-desk-answer") {
    setBadge(tab.id, "AI", "#2563eb");
    try {
      const payload = await payloadForUrl(tab.url);
      payload.selectionText = info.selectionText || "";

      const result = await runInFrames(tab, "job-desk-answer", payload, info.frameId);
      if (result && result.error) throw new Error(result.error);

      if (result && result.filled > 0) {
        setBadge(tab.id, "OK", "#16a34a", 5000);
      } else {
        setBadge(tab.id, "—", "#d97706", 5000);
      }
    } catch (err) {
      setBadge(tab.id, "!", "#dc2626", 8000);
      await showTabHUD(tab, 0, {
        id: "err_" + Date.now(),
        state: "error",
        title: "Could Not Answer",
        message: String(err && err.message ? err.message : err),
        detail: "Ensure the local Job Desk server is running and this page was opened via Apply.",
        autoDismiss: 9000,
      });
    }
  }
});
