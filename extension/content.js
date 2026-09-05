chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg) return;
  if (msg.type === "job-desk-fill") {
    const fill = globalThis.jobDeskFill;
    if (typeof fill !== "function") {
      sendResponse({ ok: false, error: "Fill script is not loaded on this page." });
      return;
    }
    Promise.resolve(fill(msg.payload))
      .then((filled) => sendResponse({ ok: true, filled }))
      .catch((err) => sendResponse({ ok: false, error: String(err && err.message ? err.message : err) }));
    return true;
  }
  if (msg.type === "job-desk-answer") {
    const answer = globalThis.jobDeskAnswerSelected;
    if (typeof answer !== "function") {
      sendResponse({ ok: false, error: "Fill script is not loaded on this page." });
      return;
    }
    Promise.resolve(answer(msg.payload))
      .then((filled) => sendResponse({ ok: true, filled }))
      .catch((err) => sendResponse({ ok: false, error: String(err && err.message ? err.message : err) }));
    return true;
  }
});
