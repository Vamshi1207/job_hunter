(function jobDeskFillHelper() {
  if (window.__jobDeskFillHelper) return;
  window.__jobDeskFillHelper = true;

  const DESK = String(window.JOB_DESK_ORIGIN || "http://127.0.0.1:8000").replace(/\/$/, "");
  const SKIP =
    /password|search|gender|race|veteran|disability|hispanic|lgbt|ethnicity|sexual|demographic|eeo|salary|compensation|captcha|otp|token/i;

  function request(path, options) {
    const gm = typeof GM !== "undefined" && GM.xmlHttpRequest;
    if (gm) {
      return new Promise((resolve, reject) => {
        gm({
          method: (options && options.method) || "GET",
          url: DESK + path,
          headers: { Accept: "application/json" },
          onload: (res) => {
            try {
              resolve(res.responseText ? JSON.parse(res.responseText) : {});
            } catch (err) {
              reject(err);
            }
          },
          onerror: reject,
        });
      });
    }
    return fetch(DESK + path, options || {}).then((res) => {
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    });
  }

  function hostOf(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "").toLowerCase();
    } catch (_) {
      return "";
    }
  }

  function pageMatches(payload) {
    const here = hostOf(location.href);
    if (!here || !payload) return false;
    const targets = [payload.apply_url, payload.posting_url].filter(Boolean);
    return targets.some((url) => hostOf(url) === here);
  }

  function blobToFile(blob, name, type) {
    return new File([blob], name, { type: type || blob.type || "application/pdf" });
  }

  function setValue(el, value) {
    if (value == null || value === "") return false;
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    return true;
  }

  function labelOf(el) {
    const bits = [
      el.getAttribute("aria-label"),
      el.getAttribute("placeholder"),
      el.getAttribute("autocomplete"),
      el.getAttribute("name"),
      el.getAttribute("id"),
      el.getAttribute("data-testid"),
    ];
    if (el.labels && el.labels[0]) bits.push(el.labels[0].innerText);
    if (el.id) {
      const safe = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(el.id) : el.id.replace(/"/g, "");
      const lab = document.querySelector('label[for="' + safe + '"]');
      if (lab) bits.push(lab.innerText);
    }
    const parent = el.closest("label, .field, .form-group, [class*='field']");
    if (parent && parent !== el) bits.push(parent.innerText.slice(0, 180));
    return bits.filter(Boolean).join(" ").toLowerCase().replace(/\s+/g, " ");
  }

  function isSubmitish(el) {
    const text = ((el.getAttribute("aria-label") || "") + " " + (el.innerText || "") + " " + (el.value || "")).toLowerCase();
    return /\b(submit application|send application|submit|send|review)\b/.test(text);
  }

  function fields(root) {
    return [...(root || document).querySelectorAll("input, textarea, select")].filter((el) => {
      if (el.disabled || el.readOnly) return false;
      if (el.type === "hidden" || el.type === "submit" || el.type === "button" || el.type === "image") return false;
      return !SKIP.test(labelOf(el));
    });
  }

  function fileInputs(root) {
    return [...(root || document).querySelectorAll("input[type=file]")].filter((el) => !el.disabled);
  }

  function pick(text, key) {
    const t = text;
    if (key === "first_name") return /\bfirst\b|\bgiven\b|fname|first_name|firstname/.test(t) && !/\blast\b/.test(t);
    if (key === "last_name") return /\blast\b|\bsurname\b|\bfamily\b|lname|last_name|lastname/.test(t);
    if (key === "full_name") return (/\bfull name\b|\byour name\b|^name$|\bname\b/.test(t)) && !/\bfirst\b|\blast\b|\bcompany\b|\buser/.test(t);
    if (key === "email") return /\bemail\b|e-mail/.test(t);
    if (key === "phone") return /\bphone\b|\bmobile\b|\btel\b/.test(t);
    if (key === "linkedin") return /\blinkedin\b/.test(t);
    if (key === "github") return /\bgithub\b/.test(t);
    if (key === "website") return /\bwebsite\b|\bportfolio\b|\bpersonal url\b|\bhomepage\b/.test(t);
    if (key === "city") return /\bcity\b/.test(t) && !/\bcountry\b/.test(t);
    if (key === "country") return /\bcountry\b/.test(t);
    if (key === "location") return /\blocation\b|\baddress\b|\bcity,/.test(t);
    if (key === "cover_letter") return /\bcover letter\b|\bcoverletter\b|\badditional information\b/.test(t);
    if (key === "why_i_fit") return /\bwhy (are you|this)|interest|motivation|why you/.test(t);
    if (key === "heard_about") return /\bhear about\b|\bsource\b|\bhow did you/.test(t);
    if (key === "sponsorship_now") return /\bsponsor/.test(t) && /\bnow\b|\bcurrent|\brequire/.test(t) && !/\bfuture\b/.test(t);
    if (key === "sponsorship_future") return /\bsponsor/.test(t) && /\bfuture\b|\blater\b/.test(t);
    if (key === "work_authorization") return /\bauthori[sz]ed\b|\bwork (status|permit|rights)|eligible to work/.test(t);
    return false;
  }

  function chooseChoice(el, want) {
    const yes = /^(yes|true|y|1)$/i.test(String(want));
    const value = String(el.value || "").toLowerCase();
    const text = labelOf(el);
    const isYes = /^(yes|true|y)$/i.test(value) || /\byes\b/.test(text);
    const isNo = /^(no|false|n)$/i.test(value) || /\bno\b/.test(text);
    if (el.type === "checkbox") {
      el.checked = yes;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    if (el.type === "radio") {
      if ((yes && isYes) || (!yes && isNo)) {
        el.checked = true;
        el.dispatchEvent(new Event("change", { bubbles: true }));
        el.dispatchEvent(new Event("click", { bubbles: true }));
        return true;
      }
    }
    if (el.tagName === "SELECT") {
      const wantText = yes ? "yes" : "no";
      for (const opt of el.options) {
        const blob = (opt.text + " " + opt.value).toLowerCase();
        if (blob.includes(wantText)) {
          el.value = opt.value;
          el.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        }
      }
    }
    return false;
  }

  async function setFile(input, fileInfo) {
    if (!fileInfo || !fileInfo.url) return false;
    try {
      const res = await fetch(fileInfo.url);
      if (!res.ok) return false;
      const blob = await res.blob();
      const file = blobToFile(blob, fileInfo.name || "resume.pdf", fileInfo.type);
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    } catch (_) {
      return false;
    }
  }

  async function openEasyApply() {
    const visibleForm = fields().some((el) => /email|phone|first/.test(labelOf(el)) || el.type === "file");
    if (visibleForm) return;
    const btn = [...document.querySelectorAll("button, a[role='button'], a")].find((el) => {
      const text = ((el.getAttribute("aria-label") || "") + " " + (el.innerText || "")).trim();
      if (isSubmitish(el)) return false;
      return /^(easy apply|apply now|apply)$/i.test(text) || /easy apply/i.test(text);
    });
    if (btn) {
      btn.click();
      await new Promise((r) => setTimeout(r, 900));
    }
  }

  function toast(text) {
    const id = "job-desk-fill-toast";
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement("div");
      el.id = id;
      el.style.cssText =
        "position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:22rem;background:#141c24;color:#f3f6f8;padding:10px 12px;font:13px/1.4 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25)";
      document.documentElement.appendChild(el);
    }
    el.textContent = text;
  }

  async function fill(payload) {
    if (!payload || !payload.fields) return 0;
    if (payload.apply_kind === "easy_apply" || /linkedin\.com|indeed\./.test(hostOf(location.href))) {
      await openEasyApply();
    }
    const values = payload.fields;
    let filled = 0;
    for (const el of fileInputs()) {
      const text = labelOf(el);
      const kind = /\bcover/.test(text) ? "cover_letter" : "resume";
      if (await setFile(el, (payload.files || {})[kind] || (payload.files || {}).resume)) filled += 1;
    }
    for (const el of fields()) {
      const text = labelOf(el);
      if (el.type === "file") continue;
      if (pick(text, "first_name") && setValue(el, values.first_name)) filled += 1;
      else if (pick(text, "last_name") && setValue(el, values.last_name)) filled += 1;
      else if (pick(text, "email") && setValue(el, values.email)) filled += 1;
      else if (pick(text, "phone") && setValue(el, values.phone)) filled += 1;
      else if (pick(text, "linkedin") && setValue(el, values.linkedin)) filled += 1;
      else if (pick(text, "github") && setValue(el, values.github)) filled += 1;
      else if (pick(text, "website") && setValue(el, values.website)) filled += 1;
      else if (pick(text, "city") && setValue(el, values.city)) filled += 1;
      else if (pick(text, "country") && setValue(el, values.country)) filled += 1;
      else if (pick(text, "location") && setValue(el, values.location)) filled += 1;
      else if (pick(text, "cover_letter") && setValue(el, values.cover_letter)) filled += 1;
      else if (pick(text, "why_i_fit") && setValue(el, values.why_i_fit)) filled += 1;
      else if (pick(text, "heard_about") && setValue(el, values.heard_about)) filled += 1;
      else if (pick(text, "work_authorization") && setValue(el, values.work_authorization)) filled += 1;
      else if (pick(text, "sponsorship_future") && chooseChoice(el, values.sponsorship_future)) filled += 1;
      else if (pick(text, "sponsorship_now") && chooseChoice(el, values.sponsorship_now)) filled += 1;
      else if (pick(text, "full_name") && setValue(el, values.full_name)) filled += 1;
    }
    if (filled) {
      toast("Filled " + filled + " field(s) from the job desk. Review, finish the rest, and click Submit yourself.");
    }
    return filled;
  }

  async function run() {
    let payload = null;
    try {
      payload = await request("/api/apply/for-page?url=" + encodeURIComponent(location.href));
    } catch (_) {
      try {
        payload = await request("/api/apply/pending");
      } catch (__) {
        toast("No tailored package matches this form. Open it with Apply on the desk first.");
        return;
      }
    }
    if (!payload || payload.payload === null || !payload.fields) {
      toast("No tailored package matches this form. Open it with Apply on the desk first.");
      return;
    }
    await fill(payload);
  }

  if (typeof GM !== "undefined" && typeof GM.registerMenuCommand === "function") {
    GM.registerMenuCommand("Fill this form", run);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(run, 400));
  } else {
    setTimeout(run, 500);
  }
})();
