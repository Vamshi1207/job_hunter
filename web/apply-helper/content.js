(async function loadJobDeskFillHelper() {
  const desk = "http://127.0.0.1:8000";
  try {
    const res = await fetch(desk + "/static/fill-helper.js");
    if (!res.ok) return;
    const code = await res.text();
    // Run in the content-script world so page CSP cannot block it.
    // eslint-disable-next-line no-eval
    eval(code);
  } catch (_) {
    // Desk is not running.
  }
})();
