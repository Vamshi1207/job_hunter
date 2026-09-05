(function jobDeskFillEngine(global) {
  const SKIP =
    /password|search|gender|race|veteran|disability|hispanic|lgbt|ethnicity|sexual|demographic|eeo|salary|compensation|captcha|otp|token/i;

  function blobToFile(blob, name, type) {
    return new File([blob], name, { type: type || blob.type || "application/pdf" });
  }

  function escapeRegExp(string) {
    return String(string).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function getCountryAliases(country) {
    const c = (country || "").trim().toLowerCase();
    if (!c) return [];
    const aliases = [country.trim()];
    if (c === "canada" || c === "ca") {
      aliases.push("Canada", "CA", "CAN");
    } else if (c === "united states" || c === "usa" || c === "us" || c === "u.s." || c === "u.s.a.") {
      aliases.push("United States", "USA", "US", "U.S.", "United States of America");
    } else if (c === "united kingdom" || c === "uk" || c === "u.k." || c === "great britain" || c === "gb") {
      aliases.push("United Kingdom", "UK", "U.K.", "Great Britain", "England", "GB", "GBR");
    } else if (c === "india" || c === "in") {
      aliases.push("India", "IN", "IND");
    } else if (c === "australia" || c === "au") {
      aliases.push("Australia", "AU", "AUS");
    } else if (c === "germany" || c === "de") {
      aliases.push("Germany", "Deutschland", "DE", "DEU");
    } else if (c === "france" || c === "fr") {
      aliases.push("France", "FR", "FRA");
    }
    return aliases;
  }

  function expandCandidates(candidates) {
    const cleaned = (candidates || []).map((c) => String(c || "").trim()).filter(Boolean);
    const added = [];
    for (const c of cleaned) {
      const lower = c.toLowerCase();
      if (/\b(yes|authorized|eligible|citizen|permanent resident|will not require)\b/i.test(lower)) {
        added.push("Yes", "True", "Authorized", "Yes / Oui", "Eligible");
      } else if (/\b(no|require sponsorship|not authorized|false)\b/i.test(lower)) {
        added.push("No", "False", "No / Non", "Require sponsorship");
      } else if (lower.includes("canada") || lower === "ca") {
        added.push("Canada", "CA", "CAN", "+1");
      } else if (lower.includes("united states") || lower === "usa" || lower === "us") {
        added.push("United States", "USA", "US", "+1");
      } else if (lower.includes("bachelor")) {
        added.push("Bachelor's Degree", "Bachelor", "B.S.", "B.A.", "Undergraduate");
      } else if (lower.includes("master")) {
        added.push("Master's Degree", "Master", "M.S.", "M.A.", "Graduate");
      }
    }
    return Array.from(new Set([...cleaned, ...added]));
  }

  function chooseSelectOption(selectEl, candidates) {
    if (!selectEl || selectEl.tagName !== "SELECT" || !selectEl.options) return false;
    const cleaned = expandCandidates(candidates);
    if (!cleaned.length) return false;

    let bestOpt = null;
    let bestScore = -1;

    for (let i = 0; i < selectEl.options.length; i++) {
      const opt = selectEl.options[i];
      if (!opt || opt.disabled) continue;
      const optText = (opt.text || "").trim();
      const optVal = (opt.value || "").trim();
      if (!optText && !optVal) continue;
      if (/^(select|choose|please select|--|none|any|\s*)$/i.test(optText)) continue;

      const optTextLower = optText.toLowerCase();
      const optValLower = optVal.toLowerCase();
      const blob = (optTextLower + " " + optValLower).trim();

      for (let cIdx = 0; cIdx < cleaned.length; cIdx++) {
        const candidate = cleaned[cIdx];
        const candLower = candidate.toLowerCase();
        const candWeight = (cleaned.length - cIdx) * 100;

        let score = -1;
        if (optTextLower === candLower || optValLower === candLower) {
          score = candWeight + 60;
        } else if (new RegExp("(^|\\b)" + escapeRegExp(candLower) + "(\\b|$)", "i").test(optTextLower)) {
          score = candWeight + 45;
        } else if (optTextLower.startsWith(candLower)) {
          score = candWeight + 35;
        } else if (blob.includes(candLower)) {
          score = candWeight + 20;
        }

        if (score > bestScore) {
          bestScore = score;
          bestOpt = opt;
        }
      }
    }

    if (bestOpt && bestScore > 0) {
      selectEl.selectedIndex = bestOpt.index;
      selectEl.value = bestOpt.value;
      selectEl.dispatchEvent(new Event("input", { bubbles: true }));
      selectEl.dispatchEvent(new Event("change", { bubbles: true }));
      selectEl.dispatchEvent(new Event("blur", { bubbles: true }));
      return true;
    }
    return false;
  }

  function isDropdownElement(el) {
    if (!el) return false;
    if (el.tagName === "SELECT") return true;
    if (el.list && el.list.options && el.list.options.length) return true;
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (role === "combobox" || role === "listbox") return true;
    if (el.hasAttribute("aria-autocomplete") || el.hasAttribute("aria-haspopup")) return true;
    const cls = ((el.className || "") + " " + (el.parentElement ? el.parentElement.className || "" : "")).toLowerCase();
    if (cls.includes("combobox") || cls.includes("select") || cls.includes("autocomplete")) return true;
    if (el.closest('[role="combobox"], [class*="select"], [class*="combobox"], [class*="dropdown"]')) return true;
    return false;
  }

  async function chooseComboboxOption(el, candidates) {
    if (!el) return false;
    if (el.tagName === "SELECT") return chooseSelectOption(el, candidates);
    const cleaned = expandCandidates(candidates);
    if (!cleaned.length) return false;

    // 1. Attached datalist
    if (el.list && el.list.options && el.list.options.length) {
      for (const cand of cleaned) {
        const candLower = cand.toLowerCase();
        for (const opt of el.list.options) {
          const optText = (opt.text || opt.value || "").trim().toLowerCase();
          if (optText === candLower || optText.includes(candLower)) {
            const proto = HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, "value");
            if (desc && desc.set) desc.set.call(el, opt.value || opt.text);
            else el.value = opt.value || opt.text;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            return true;
          }
        }
      }
    }

    // 2. Custom combobox / dropdown (Ashby, React-Select, MUI, etc.)
    try {
      el.focus();
      el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
      el.click();
    } catch (_) {}

    // If input element, simulate typing the candidate to query options
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      const searchVal = cleaned[0];
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, "value");
      if (desc && desc.set) desc.set.call(el, searchVal);
      else el.value = searchVal;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", keyCode: 40, bubbles: true }));
    }

    // Wait for popover menu to appear
    await new Promise((r) => setTimeout(r, 260));

    const optionSelectors = [
      '[role="option"]',
      '.select2-results__option',
      '.choices__item--choice',
      '.pac-item',
      '[class*="suggestion"]',
      '[class*="dropdown-item"]',
      '[class*="select-option"]',
      '.ashby-select-option',
      '[role="listbox"] > *',
      '[role="listbox"] li',
      'ul[class*="menu"] > li',
      'div[id*="option"]',
      'li[data-value]',
    ].join(", ");

    const options = queryAllDeep(optionSelectors).filter((opt) => {
      if (!opt || opt.disabled) return false;
      const text = (opt.innerText || opt.textContent || "").trim();
      if (!text || /^(select|choose|please select|--|none|any|\s*)$/i.test(text)) return false;
      return true;
    });

    if (options.length) {
      let bestOpt = null;
      let bestScore = -1;

      for (let i = 0; i < options.length; i++) {
        const opt = options[i];
        const optText = (opt.innerText || opt.textContent || "").trim().toLowerCase();

        for (let cIdx = 0; cIdx < cleaned.length; cIdx++) {
          const cand = cleaned[cIdx];
          const candLower = cand.toLowerCase();
          const candWeight = (cleaned.length - cIdx) * 100;

          let score = -1;
          if (optText === candLower) {
            score = candWeight + 60;
          } else if (new RegExp("(^|\\b)" + escapeRegExp(candLower) + "(\\b|$)", "i").test(optText)) {
            score = candWeight + 45;
          } else if (optText.startsWith(candLower) || candLower.startsWith(optText)) {
            score = candWeight + 35;
          } else if (optText.includes(candLower) || candLower.includes(optText)) {
            score = candWeight + 20;
          }

          if (score > bestScore) {
            bestScore = score;
            bestOpt = opt;
          }
        }
      }

      if (bestOpt) {
        try {
          bestOpt.scrollIntoView({ block: "nearest" });
          bestOpt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
          bestOpt.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
          bestOpt.click();
          bestOpt.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        } catch (_) {}
      }
    }

    // Try pressing Enter to confirm any highlighted dropdown option
    el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", keyCode: 13, bubbles: true }));
    return false;
  }

  function chooseFromDropdown(el, candidates) {
    if (!el) return false;
    if (el.tagName === "SELECT") {
      return chooseSelectOption(el, candidates);
    }
    chooseComboboxOption(el, candidates);
    return true;
  }

  function fillLocationField(el, values, kind) {
    if (!el || !values) return false;
    const city = (values.city || "").trim();
    const country = (values.country || "").trim();
    const location = (values.location || "").trim();
    const countryAliases = getCountryAliases(country);

    let candidates = [];
    if (kind === "country") {
      candidates = countryAliases;
    } else if (kind === "city") {
      candidates = [city, location, ...countryAliases];
    } else {
      candidates = [
        location,
        city && country ? `${city}, ${country}` : "",
        city,
        ...countryAliases,
      ].filter(Boolean);
    }

    if (el.tagName === "SELECT") {
      return chooseSelectOption(el, candidates);
    }
    if (isDropdownElement(el)) {
      chooseComboboxOption(el, candidates);
      return true;
    }

    const valToSet =
      kind === "country"
        ? (country || location)
        : kind === "city"
          ? (city || location)
          : (location || [city, country].filter(Boolean).join(", "));

    if (!valToSet) return false;
    const ok = setValue(el, valToSet);
    chooseComboboxOption(el, candidates);
    return ok;
  }

  function setValue(el, value, force) {
    if (value == null || value === "") return false;
    if (isDropdownElement(el)) {
      return chooseFromDropdown(el, [String(value)]);
    }
    if (!force && el.value && String(el.value).trim() && el.type !== "file") return false;
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
      el.getAttribute("data-automation-id"),
    ];
    if (el.labels && el.labels[0]) bits.push(el.labels[0].innerText);
    if (el.id) {
      const safe = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(el.id) : String(el.id).replace(/"/g, "");
      const lab = document.querySelector('label[for="' + safe + '"]');
      if (lab) bits.push(lab.innerText);
    }
    const parent = el.closest("label, .field, .form-group, [class*='field'], [class*='input']");
    if (parent && parent !== el) bits.push(parent.innerText.slice(0, 180));
    return bits.filter(Boolean).join(" ").toLowerCase().replace(/\s+/g, " ");
  }

  function isSubmitish(el) {
    const text = ((el.getAttribute("aria-label") || "") + " " + (el.innerText || "") + " " + (el.value || "")).toLowerCase();
    return /\b(submit application|send application|submit|send)\b/.test(text);
  }

  function collectRoots(root) {
    const start = root || document;
    const roots = [start];
    const stack = [start];
    while (stack.length) {
      const node = stack.pop();
      let els = [];
      try {
        els = node.querySelectorAll ? [...node.querySelectorAll("*")] : [];
      } catch (_) {
        els = [];
      }
      for (const el of els) {
        if (el.shadowRoot) {
          roots.push(el.shadowRoot);
          stack.push(el.shadowRoot);
        }
      }
    }
    return roots;
  }

  function queryAllDeep(selector, root) {
    const out = [];
    for (const r of collectRoots(root)) {
      try {
        out.push(...r.querySelectorAll(selector));
      } catch (_) {}
    }
    return out;
  }

  function fields(root) {
    return queryAllDeep("input, textarea, select, [role='combobox'], [role='listbox'], button[aria-haspopup='listbox']", root).filter((el) => {
      if (el.disabled || el.readOnly) return false;
      if (el.type === "hidden" || el.type === "submit" || el.type === "image") return false;
      if (el.type === "button" && !el.hasAttribute("aria-haspopup") && el.getAttribute("role") !== "combobox") return false;
      return !SKIP.test(labelOf(el));
    });
  }

  function fileInputs(root) {
    return queryAllDeep("input[type=file]", root).filter((el) => !el.disabled);
  }

  function looksLikeForm() {
    if (fileInputs().length || fields().length) return true;
    const text = ((document.body && document.body.innerText) || "").slice(0, 8000).toLowerCase();
    return /\b(easy apply|apply now)\b/.test(text);
  }

  function pageWantsResume() {
    if (fileInputs().length) return true;
    const text = ((document.body && document.body.innerText) || "").slice(0, 12000).toLowerCase();
    return /\bresume\b|\bcv\b|curriculum vitae|attach (a |your )?(file|resume|cv)|upload (a |your )?(file|resume|cv)|drop (your )?(resume|cv|file)|choose file|add attachment/.test(
      text
    );
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
    if (key === "resume") return /\bresume\b|\bcv\b|\bcurriculum\b/.test(t);
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

  async function fileFromPayload(fileInfo) {
    if (!fileInfo) return null;

    // 1. If base64 / dataUrl is provided (preloaded by background worker to bypass page CSP/Mixed Content)
    if (fileInfo.base64 || fileInfo.dataUrl) {
      try {
        const raw = fileInfo.base64 || fileInfo.dataUrl.split(",")[1];
        const byteCharacters = atob(raw);
        const byteNumbers = new Array(byteCharacters.length);
        for (let i = 0; i < byteCharacters.length; i++) {
          byteNumbers[i] = byteCharacters.charCodeAt(i);
        }
        const byteArray = new Uint8Array(byteNumbers);
        const blob = new Blob([byteArray], { type: fileInfo.type || "application/pdf" });
        return blobToFile(blob, fileInfo.name || "resume.pdf", fileInfo.type || "application/pdf");
      } catch (_) {}
    }

    // 2. Request background service worker to fetch (extension context has host permissions)
    if (fileInfo.url && typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const res = await new Promise((resolve) => {
          chrome.runtime.sendMessage({ type: "job-desk-fetch-file", url: fileInfo.url }, (resp) => {
            if (chrome.runtime.lastError) resolve(null);
            else resolve(resp);
          });
        });
        if (res && res.ok && res.base64) {
          const byteCharacters = atob(res.base64);
          const byteNumbers = new Array(byteCharacters.length);
          for (let i = 0; i < byteCharacters.length; i++) {
            byteNumbers[i] = byteCharacters.charCodeAt(i);
          }
          const byteArray = new Uint8Array(byteNumbers);
          const blob = new Blob([byteArray], { type: fileInfo.type || "application/pdf" });
          return blobToFile(blob, fileInfo.name || "resume.pdf", fileInfo.type || "application/pdf");
        }
      } catch (_) {}
    }

    // 3. Fallback direct fetch
    if (fileInfo.url) {
      const res = await fetch(fileInfo.url);
      if (!res.ok) throw new Error("Could not download " + (fileInfo.name || "the CV") + " from the desk");
      const blob = await res.blob();
      return blobToFile(blob, fileInfo.name || "resume.pdf", fileInfo.type);
    }

    return null;
  }

  function assignFiles(input, file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    try {
      input.files = dt.files;
    } catch (_) {
      try {
        Object.defineProperty(input, "files", { configurable: true, value: dt.files });
      } catch (__) {
        return false;
      }
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    const zone = input.closest("[class*='drop'], [class*='upload'], [class*='attach'], label") || input.parentElement;
    if (zone && zone !== input) {
      try {
        zone.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
      } catch (_) {}
    }
    return !!(input.files && input.files.length);
  }

  async function setFile(input, fileInfo) {
    if (!fileInfo || !fileInfo.url || (input.files && input.files.length)) return false;
    try {
      const file = await fileFromPayload(fileInfo);
      if (!file) return false;
      return assignFiles(input, file);
    } catch (_) {
      return false;
    }
  }

  async function offerResumeFallback(payload, reason) {
    const fileInfo = payload && payload.files && payload.files.resume;
    if (!fileInfo || !fileInfo.url) {
      toast(reason);
      return;
    }
    activePayload = payload;

    statusHUD.show({
      state: "skipped",
      title: "Point to Attach Resume",
      company: payload.company || "",
      role: payload.role || "",
      message: (reason || "Could not find the resume upload box automatically.") + " Click below to point the extension to the attach button or dropzone on this page.",
      detail: "Or right-click the page's 'Attach' button / dropzone and select 'Attach resume here'.",
      actionText: "🎯 Point to Attach Button / Dropzone",
      onAction: () => {
        startAttachPointerMode(payload);
      },
      autoDismiss: 18000,
    });
  }

  async function openEasyApply() {
    const visibleForm = fields().some((el) => /email|phone|first/.test(labelOf(el)) || el.type === "file");
    if (visibleForm) return;
    const btn = queryAllDeep("button, a[role='button'], a").find((el) => {
      const text = ((el.getAttribute("aria-label") || "") + " " + (el.innerText || "")).trim();
      if (isSubmitish(el)) return false;
      return /^(easy apply|apply now|apply)$/i.test(text) || /easy apply/i.test(text);
    });
    if (btn) {
      btn.click();
      await new Promise((r) => setTimeout(r, 900));
    }
  }

  const statusHUD = (function () {
    let host = null;
    let shadow = null;
    let timerInterval = null;
    let dismissTimeout = null;
    let activeKeyHandler = null;
    let isMinimized = false;

    const tasks = new Map(); // id -> task object
    const highlightedElements = new Map(); // el -> { originalOutline, originalBoxShadow, originalTransition, timeout }

    function clearKeyHandler() {
      if (activeKeyHandler) {
        window.removeEventListener("keydown", activeKeyHandler, true);
        activeKeyHandler = null;
      }
    }

    function ensureHost() {
      if (host && host.isConnected) return host;
      host = document.getElementById("job-desk-status-hud-host");
      if (!host) {
        host = document.createElement("div");
        host.id = "job-desk-status-hud-host";
        host.style.cssText =
          "all: initial; position: fixed; top: 18px; right: 18px; z-index: 2147483647; pointer-events: none;";
        shadow = host.attachShadow({ mode: "open" });
        (document.body || document.documentElement).appendChild(host);
      } else {
        shadow = host.shadowRoot;
      }
      return host;
    }

    function clearTimer() {
      if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
      }
    }

    function clearDismiss() {
      if (dismissTimeout) {
        clearTimeout(dismissTimeout);
        dismissTimeout = null;
      }
    }

    function highlight(el, state) {
      if (!el || !el.style) return;
      let data = highlightedElements.get(el);
      if (!data) {
        data = {
          originalOutline: el.style.outline || "",
          originalBoxShadow: el.style.boxShadow || "",
          originalTransition: el.style.transition || "",
          timeout: null,
        };
        highlightedElements.set(el, data);
      }
      if (data.timeout) {
        clearTimeout(data.timeout);
        data.timeout = null;
      }
      el.style.transition = "box-shadow 0.25s ease, outline 0.25s ease";
      if (state === "active") {
        el.style.outline = "2px solid #3b82f6";
        el.style.boxShadow = "0 0 0 4px rgba(59, 130, 246, 0.35)";
        try {
          el.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (_) {}
      } else if (state === "success") {
        el.style.outline = "2px solid #10b981";
        el.style.boxShadow = "0 0 0 4px rgba(16, 185, 129, 0.4)";
        data.timeout = setTimeout(() => {
          clearHighlight(el);
        }, 3000);
      }
    }

    function clearHighlight(el) {
      if (el) {
        const data = highlightedElements.get(el);
        if (data) {
          if (data.timeout) clearTimeout(data.timeout);
          try {
            el.style.outline = data.originalOutline;
            el.style.boxShadow = data.originalBoxShadow;
            el.style.transition = data.originalTransition;
          } catch (_) {}
          highlightedElements.delete(el);
        }
      } else {
        for (const [elem, data] of highlightedElements.entries()) {
          if (data.timeout) clearTimeout(data.timeout);
          try {
            elem.style.outline = data.originalOutline;
            elem.style.boxShadow = data.originalBoxShadow;
            elem.style.transition = data.originalTransition;
          } catch (_) {}
        }
        highlightedElements.clear();
      }
    }

    function cleanOldTasks() {
      const now = Date.now();
      const hasInFlight = Array.from(tasks.values()).some(
        (t) => t.state === "thinking" || t.state === "loading"
      );
      if (!hasInFlight) {
        for (const [id, t] of tasks.entries()) {
          if (t.finishedAt && now - t.finishedAt > 45000) {
            tasks.delete(id);
          }
        }
      }
    }

    function escapeHtml(str) {
      if (!str) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function formatModelName(name) {
      if (!name) return "Nemotron-3 (NVIDIA)";
      const low = String(name).toLowerCase();
      if (low === "cache" || low.includes("cache")) return "Local Cache (Instant)";
      if (low.includes("nemotron")) return "Nemotron-3 (NVIDIA)";
      if (low.includes("gemini")) return "Gemini 3.1 Pro";
      if (low.includes("gpt-oss")) return "GPT-OSS 120B";
      if (low.includes("claude")) return "Claude 3.5 Sonnet";
      return String(name).replace(/^[^/]+\//, "");
    }

    function startTimerTicker() {
      const hasThinking = Array.from(tasks.values()).some((t) => t.state === "thinking");
      if (!hasThinking) {
        clearTimer();
        return;
      }
      if (timerInterval) return;

      timerInterval = setInterval(() => {
        let stillThinking = false;
        for (const task of tasks.values()) {
          if (task.state === "thinking") {
            stillThinking = true;
            const start = task.startTime || task.createdAt || Date.now();
            task.elapsed = Math.max(1, Math.round((Date.now() - start) / 1000));

            // Single task view updates
            const singleTimerText = shadow && shadow.getElementById("hud-timer-text");
            if (singleTimerText) {
              singleTimerText.textContent = `Querying LLM (${task.elapsed}s)… please wait`;
            }
            const singleLiveElapsed = shadow && shadow.getElementById("hud-live-elapsed");
            if (singleLiveElapsed) {
              singleLiveElapsed.textContent = `${task.elapsed}s`;
            }

            // Multi task view updates
            const multiBadge = shadow && shadow.getElementById(`hud-task-badge-${task.id}`);
            if (multiBadge) {
              multiBadge.innerHTML = `<span class="hud-pulse-dot-sm"></span> THINKING (${task.elapsed}s)`;
            }
            const multiTimer = shadow && shadow.getElementById(`hud-task-timer-${task.id}`);
            if (multiTimer) {
              multiTimer.textContent = `(${task.elapsed}s)`;
            }
          }
        }
        if (!stillThinking) {
          clearTimer();
        }
      }, 1000);
    }

    const baseStyles = `
      <style>
        :host {
          all: initial;
        }
        * {
          box-sizing: border-box;
          margin: 0;
          padding: 0;
        }
        .hud-card {
          pointer-events: auto;
          width: min(440px, calc(100vw - 32px));
          background: rgba(15, 23, 42, 0.96);
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
          border: 1px solid rgba(255, 255, 255, 0.14);
          border-radius: 14px;
          box-shadow: 0 20px 42px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(255, 255, 255, 0.08);
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
          color: #f8fafc;
          padding: 14px 16px;
          animation: hudSlideIn 0.25s cubic-bezier(0.16, 1, 0.3, 1) forwards;
          overflow: hidden;
          position: relative;
        }
        .hud-card.hud-card-multi {
          width: min(475px, calc(100vw - 32px));
        }
        @keyframes hudSlideIn {
          from {
            opacity: 0;
            transform: translateY(-8px) scale(0.97);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
        .hud-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 8px;
        }
        .hud-header-left {
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
        }
        .hud-header-right {
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .hud-icon-wrap {
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
        }
        .hud-spin {
          animation: hudSpin 0.9s linear infinite;
        }
        @keyframes hudSpin {
          100% { transform: rotate(360deg); }
        }
        .hud-title {
          font-size: 13px;
          font-weight: 700;
          letter-spacing: -0.01em;
          color: #f1f5f9;
        }
        .hud-badge {
          font-size: 10px;
          font-weight: 700;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          padding: 2px 7px;
          border-radius: 4px;
        }
        .hud-close {
          background: transparent;
          border: none;
          color: #94a3b8;
          font-size: 18px;
          line-height: 1;
          cursor: pointer;
          padding: 2px 6px;
          border-radius: 4px;
          transition: color 0.15s, background 0.15s;
        }
        .hud-close:hover {
          color: #f8fafc;
          background: rgba(255, 255, 255, 0.08);
        }
        .hud-minimize-btn {
          background: transparent;
          border: none;
          color: #94a3b8;
          font-size: 16px;
          line-height: 1;
          cursor: pointer;
          padding: 2px 6px;
          border-radius: 4px;
          transition: color 0.15s, background 0.15s;
        }
        .hud-minimize-btn:hover {
          color: #f8fafc;
          background: rgba(255, 255, 255, 0.08);
        }
        .hud-minimized-pill {
          pointer-events: auto;
          display: inline-flex;
          align-items: center;
          gap: 10px;
          background: rgba(15, 23, 42, 0.95);
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
          border: 1px solid rgba(59, 130, 246, 0.4);
          box-shadow: 0 12px 28px rgba(0, 0, 0, 0.5);
          border-radius: 30px;
          padding: 6px 12px;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
          color: #f8fafc;
          font-size: 11.5px;
          animation: hudSlideIn 0.2s ease forwards;
        }
        .hud-pill-dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          background: #10b981;
          display: inline-block;
          flex-shrink: 0;
        }
        .hud-pill-dot.thinking {
          background: #60a5fa;
          animation: hudPulse 1.2s ease-in-out infinite;
        }
        .hud-pill-title {
          font-weight: 600;
          color: #f1f5f9;
        }
        .hud-pill-btn {
          background: rgba(255, 255, 255, 0.1);
          border: 1px solid rgba(255, 255, 255, 0.18);
          color: #cbd5e1;
          font-size: 10.5px;
          font-weight: 600;
          padding: 3px 8px;
          border-radius: 12px;
          cursor: pointer;
          transition: all 0.15s ease;
        }
        .hud-pill-btn:hover {
          background: rgba(255, 255, 255, 0.2);
          color: #ffffff;
        }
        .hud-pill-btn-copy {
          background: rgba(16, 185, 129, 0.2);
          border-color: rgba(16, 185, 129, 0.45);
          color: #6ee7b7;
        }
        .hud-pill-btn-copy:hover {
          background: rgba(16, 185, 129, 0.35);
          color: #a7f3d0;
        }
        .hud-pill-close {
          background: transparent;
          border: none;
          color: #94a3b8;
          font-size: 16px;
          line-height: 1;
          cursor: pointer;
          padding: 0 4px;
        }
        .hud-pill-close:hover {
          color: #f8fafc;
        }
        .hud-cancel-all-btn {
          background: rgba(239, 68, 68, 0.15);
          border: 1px solid rgba(239, 68, 68, 0.35);
          color: #fca5a5;
          font-size: 10px;
          font-weight: 600;
          padding: 3px 8px;
          border-radius: 4px;
          cursor: pointer;
          transition: all 0.15s ease;
        }
        .hud-cancel-all-btn:hover {
          background: rgba(239, 68, 68, 0.28);
          border-color: rgba(239, 68, 68, 0.6);
          color: #fee2e2;
        }
        .hud-meta-row {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 6px;
          margin-bottom: 8px;
        }
        .hud-meta-pill {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-size: 10.5px;
          font-weight: 500;
          padding: 2px 7px;
          border-radius: 4px;
          background: rgba(255, 255, 255, 0.07);
          border: 1px solid rgba(255, 255, 255, 0.1);
          color: #cbd5e1;
          max-width: 100%;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .hud-pill-company {
          background: rgba(59, 130, 246, 0.14);
          border-color: rgba(59, 130, 246, 0.32);
          color: #bfdbfe;
        }
        .hud-pill-kind {
          text-transform: uppercase;
          font-size: 9.5px;
          letter-spacing: 0.04em;
          background: rgba(148, 163, 184, 0.12);
          color: #94a3b8;
        }
        .hud-field-box {
          background: rgba(0, 0, 0, 0.32);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 6px;
          padding: 6px 9px;
          margin-bottom: 8px;
        }
        .hud-field-label {
          font-size: 9px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: #94a3b8;
          margin-bottom: 2px;
        }
        .hud-field-val {
          font-size: 11.5px;
          line-height: 1.35;
          color: #e2e8f0;
          font-weight: 500;
          word-break: break-word;
        }
        .hud-message {
          font-size: 12px;
          line-height: 1.4;
          color: #e2e8f0;
          margin-bottom: 8px;
        }
        .hud-timer-row {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 11.5px;
          color: #93c5fd;
          font-weight: 500;
          margin-bottom: 8px;
        }
        .hud-pulse-dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          background: #60a5fa;
          animation: hudPulse 1.3s ease-in-out infinite;
          flex-shrink: 0;
        }
        .hud-pulse-dot-sm {
          width: 5.5px;
          height: 5.5px;
          border-radius: 50%;
          background: #60a5fa;
          animation: hudPulse 1.2s ease-in-out infinite;
          display: inline-block;
          flex-shrink: 0;
        }
        @keyframes hudPulse {
          0%, 100% { opacity: 0.3; transform: scale(0.85); }
          50% { opacity: 1; transform: scale(1.2); }
        }
        .hud-stats-grid {
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 6px;
          margin-bottom: 8px;
        }
        .hud-stat-card {
          background: rgba(255, 255, 255, 0.04);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 6px;
          padding: 6px 8px;
        }
        .hud-stat-lbl {
          font-size: 9px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          color: #94a3b8;
          margin-bottom: 2px;
        }
        .hud-stat-num {
          font-size: 12.5px;
          font-weight: 700;
          color: #f1f5f9;
          letter-spacing: -0.01em;
        }
        .hud-sources-row {
          display: flex;
          flex-wrap: wrap;
          gap: 5px;
          margin-bottom: 8px;
        }
        .hud-source-pill {
          font-size: 10px;
          font-weight: 500;
          padding: 2px 6px;
          border-radius: 4px;
          background: rgba(16, 185, 129, 0.12);
          border: 1px solid rgba(16, 185, 129, 0.25);
          color: #a7f3d0;
        }
        .hud-preview-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 4px;
        }
        .hud-preview-title {
          font-size: 9px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: #94a3b8;
        }
        .hud-copy-btn {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          background: rgba(255, 255, 255, 0.08);
          border: 1px solid rgba(255, 255, 255, 0.16);
          color: #cbd5e1;
          padding: 2px 7px;
          border-radius: 4px;
          font-size: 10px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s ease;
        }
        .hud-copy-btn:hover {
          background: rgba(255, 255, 255, 0.18);
          color: #ffffff;
          border-color: rgba(255, 255, 255, 0.3);
        }
        .hud-copy-btn.copied {
          background: rgba(16, 185, 129, 0.25);
          border-color: rgba(16, 185, 129, 0.5);
          color: #6ee7b7;
        }
        .hud-manual-copy-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          width: 100%;
          padding: 8px 12px;
          margin-top: 6px;
          margin-bottom: 6px;
          background: #059669;
          color: #ffffff;
          border: 1px solid rgba(16, 185, 129, 0.4);
          border-radius: 6px;
          font-size: 11.5px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s ease;
        }
        .hud-manual-copy-btn:hover {
          background: #047857;
          transform: translateY(-1px);
        }
        .hud-manual-copy-btn.copied {
          background: #065f46;
          color: #a7f3d0;
        }
        .hud-preview-box {
          background: rgba(0, 0, 0, 0.35);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: 6px;
          padding: 7px 9px;
          font-size: 11.5px;
          line-height: 1.45;
          color: #cbd5e1;
          max-height: 80px;
          overflow-y: auto;
          word-break: break-word;
          margin-bottom: 6px;
        }
        .hud-detail {
          font-size: 11.5px;
          line-height: 1.4;
          color: #94a3b8;
          background: rgba(0, 0, 0, 0.28);
          padding: 6px 8px;
          border-radius: 6px;
          border: 1px solid rgba(255, 255, 255, 0.06);
          max-height: 75px;
          overflow-y: auto;
          word-break: break-word;
          margin-bottom: 6px;
        }
        .hud-guardrail-box {
          background: rgba(245, 158, 11, 0.08);
          border: 1px solid rgba(245, 158, 11, 0.25);
          border-radius: 6px;
          padding: 7px 9px;
          margin-bottom: 8px;
        }
        .hud-guardrail-title {
          font-size: 9.5px;
          font-weight: 700;
          color: #fbbf24;
          margin-bottom: 2px;
          letter-spacing: 0.03em;
        }
        .hud-guardrail-text {
          font-size: 11px;
          line-height: 1.35;
          color: #fde68a;
        }
        .hud-footer-hint {
          font-size: 10.5px;
          color: #94a3b8;
          display: flex;
          align-items: center;
          gap: 4px;
        }
        .hud-action-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          width: 100%;
          padding: 8px 12px;
          margin-top: 8px;
          margin-bottom: 4px;
          background: #2563eb;
          color: #ffffff;
          border: 1px solid rgba(255, 255, 255, 0.2);
          border-radius: 6px;
          font-size: 12px;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.15s, transform 0.1s;
        }
        .hud-action-btn:hover {
          background: #1d4ed8;
          transform: translateY(-1px);
        }
        .hud-cancel-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          width: 100%;
          padding: 8px 12px;
          margin-top: 10px;
          background: rgba(239, 68, 68, 0.15);
          color: #fca5a5;
          border: 1px solid rgba(239, 68, 68, 0.35);
          border-radius: 6px;
          font-size: 11.5px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s ease;
        }
        .hud-cancel-btn:hover {
          background: rgba(239, 68, 68, 0.28);
          border-color: rgba(239, 68, 68, 0.6);
          color: #fee2e2;
          transform: translateY(-1px);
        }
        .hud-progress-line {
          position: absolute;
          bottom: 0;
          left: 0;
          height: 2px;
          width: 100%;
        }
        .hud-progress-animated {
          animation: hudBar 2s ease-in-out infinite;
        }
        @keyframes hudBar {
          0% { transform: translateX(-100%); }
          50% { transform: translateX(0%); }
          100% { transform: translateX(100%); }
        }

        /* Adaptive Multi-Task Styles */
        .hud-multi-list {
          display: flex;
          flex-direction: column;
          gap: 7px;
          max-height: 380px;
          overflow-y: auto;
          margin-bottom: 8px;
          padding-right: 2px;
        }
        .hud-multi-list::-webkit-scrollbar {
          width: 5px;
        }
        .hud-multi-list::-webkit-scrollbar-track {
          background: rgba(0, 0, 0, 0.2);
          border-radius: 4px;
        }
        .hud-multi-list::-webkit-scrollbar-thumb {
          background: rgba(255, 255, 255, 0.18);
          border-radius: 4px;
        }
        .hud-task-card {
          background: rgba(0, 0, 0, 0.32);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 8px;
          padding: 8px 10px;
          transition: border-color 0.2s ease, background 0.2s ease;
        }
        .hud-task-card-thinking {
          border-color: rgba(59, 130, 246, 0.35);
          background: rgba(30, 58, 138, 0.16);
        }
        .hud-task-card-success {
          border-color: rgba(16, 185, 129, 0.28);
          background: rgba(6, 78, 59, 0.14);
        }
        .hud-task-card-skipped {
          border-color: rgba(245, 158, 11, 0.25);
          background: rgba(120, 53, 15, 0.14);
        }
        .hud-task-card-error {
          border-color: rgba(239, 68, 68, 0.3);
          background: rgba(127, 29, 29, 0.14);
        }
        .hud-task-card-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          margin-bottom: 6px;
        }
        .hud-task-header-left {
          display: flex;
          align-items: center;
          gap: 6px;
          min-width: 0;
          flex: 1;
        }
        .hud-task-num {
          font-size: 9.5px;
          font-weight: 700;
          color: #94a3b8;
          background: rgba(255, 255, 255, 0.08);
          padding: 1px 5px;
          border-radius: 4px;
          flex-shrink: 0;
        }
        .hud-task-qname {
          font-size: 11.5px;
          font-weight: 600;
          color: #f1f5f9;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .hud-task-badge {
          font-size: 9.5px;
          font-weight: 700;
          padding: 2px 6px;
          border-radius: 4px;
          letter-spacing: 0.03em;
          white-space: nowrap;
          flex-shrink: 0;
        }
        .hud-task-badge.thinking {
          background: rgba(59, 130, 246, 0.2);
          color: #60a5fa;
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .hud-task-badge.success {
          background: rgba(16, 185, 129, 0.2);
          color: #34d399;
        }
        .hud-task-badge.skipped {
          background: rgba(245, 158, 11, 0.2);
          color: #fbbf24;
        }
        .hud-task-badge.error {
          background: rgba(239, 68, 68, 0.2);
          color: #f87171;
        }
        .hud-task-thinking-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          font-size: 11px;
          color: #93c5fd;
          padding-top: 2px;
        }
        .hud-task-thinking-info {
          display: flex;
          align-items: center;
          gap: 6px;
          min-width: 0;
        }
        .hud-task-btn-cancel {
          background: rgba(239, 68, 68, 0.15);
          border: 1px solid rgba(239, 68, 68, 0.35);
          color: #fca5a5;
          font-size: 10px;
          font-weight: 600;
          padding: 2px 7px;
          border-radius: 4px;
          cursor: pointer;
          transition: all 0.15s ease;
          flex-shrink: 0;
        }
        .hud-task-btn-cancel:hover {
          background: rgba(239, 68, 68, 0.3);
          border-color: rgba(239, 68, 68, 0.6);
          color: #fee2e2;
        }
        .hud-task-answer-box {
          background: rgba(0, 0, 0, 0.28);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 5px;
          padding: 6px 8px;
          font-size: 11px;
          line-height: 1.4;
          color: #cbd5e1;
          max-height: 58px;
          overflow-y: auto;
          word-break: break-word;
          margin-bottom: 6px;
        }
        .hud-task-action-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .hud-task-meta-txt {
          font-size: 10px;
          color: #94a3b8;
        }
        .hud-task-skipped-msg {
          font-size: 11px;
          color: #fde68a;
          line-height: 1.35;
          padding-top: 2px;
        }
        .hud-task-error-msg {
          font-size: 11px;
          color: #fca5a5;
          line-height: 1.35;
          padding-top: 2px;
        }
      </style>
    `;

    function renderSingle(opts) {
      const state = opts.state || "loading";
      const title = opts.title || "Job Desk AI";
      const message = opts.message || "";
      const detail = opts.detail || "";
      const stats = opts.stats || null;

      let badgeText = "IN PROGRESS";
      let badgeBg = "#1e293b";
      let badgeColor = "#93c5fd";
      let iconHtml = `
        <svg class="hud-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10" stroke="rgba(59,130,246,0.2)"></circle>
          <path d="M12 2a10 10 0 0 1 10 10"></path>
        </svg>
      `;

      if (state === "thinking") {
        badgeText = "THINKING";
        badgeBg = "rgba(59, 130, 246, 0.2)";
        badgeColor = "#60a5fa";
        iconHtml = `
          <svg class="hud-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"></path>
          </svg>
        `;
      } else if (state === "success") {
        const latSec =
          stats && stats.latency_ms
            ? (stats.latency_ms / 1000).toFixed(1) + "s"
            : opts.elapsed
            ? `${opts.elapsed}s`
            : "";
        badgeText = latSec ? `FILLED (${latSec})` : "FILLED";
        badgeBg = "rgba(16, 185, 129, 0.2)";
        badgeColor = "#34d399";
        iconHtml = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
            <polyline points="22 4 12 14.01 9 11.01"></polyline>
          </svg>
        `;
      } else if (state === "skipped") {
        badgeText = "SKIPPED";
        badgeBg = "rgba(245, 158, 11, 0.2)";
        badgeColor = "#fbbf24";
        iconHtml = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="16" x2="12" y2="12"></line>
            <line x1="12" y1="8" x2="12.01" y2="8"></line>
          </svg>
        `;
      } else if (state === "ready") {
        badgeText = "MATCHED";
        badgeBg = "rgba(59, 130, 246, 0.25)";
        badgeColor = "#60a5fa";
        iconHtml = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
            <line x1="16" y1="13" x2="8" y2="13"></line>
            <line x1="16" y1="17" x2="8" y2="17"></line>
          </svg>
        `;
      } else if (state === "error") {
        badgeText = "ERROR";
        badgeBg = "rgba(239, 68, 68, 0.2)";
        badgeColor = "#f87171";
        iconHtml = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
        `;
      }

      shadow.innerHTML = `
        ${baseStyles}
        <div class="hud-card">
          <div class="hud-header">
            <div class="hud-header-left">
              <div class="hud-icon-wrap">${iconHtml}</div>
              <span class="hud-title">${escapeHtml(title)}</span>
              <span class="hud-badge" style="background: ${badgeBg}; color: ${badgeColor};">${badgeText}</span>
            </div>
            <div class="hud-header-right">
              <button class="hud-minimize-btn" id="hud-minimize-btn" title="Minimize to small floating pill">—</button>
              <button class="hud-close" id="hud-close-btn" title="Close / Dismiss">&times;</button>
            </div>
          </div>

          ${
            opts.company || opts.role || opts.questionKind
              ? `
            <div class="hud-meta-row">
              ${
                opts.company || opts.role
                  ? `
                <span class="hud-meta-pill hud-pill-company">
                  🏢 ${escapeHtml([opts.company, opts.role].filter(Boolean).join(" • "))}
                </span>
              `
                  : ""
              }
              ${
                opts.questionKind
                  ? `<span class="hud-meta-pill hud-pill-kind">${escapeHtml(opts.questionKind)}</span>`
                  : ""
              }
            </div>
          `
              : ""
          }

          ${
            opts.targetField
              ? `
            <div class="hud-field-box">
              <div class="hud-field-label">TARGET QUESTION</div>
              <div class="hud-field-val">${escapeHtml(opts.targetField)}</div>
            </div>
          `
              : ""
          }

          ${
            state === "thinking"
              ? `
            <div class="hud-message">${escapeHtml(message)}</div>
            <div class="hud-timer-row">
              <span class="hud-pulse-dot"></span>
              <span id="hud-timer-text">Querying LLM (${opts.elapsed || 0}s)… please wait</span>
            </div>
            <div class="hud-stats-grid">
              <div class="hud-stat-card">
                <div class="hud-stat-lbl">AI MODEL</div>
                <div class="hud-stat-num">${escapeHtml(
                  formatModelName((stats && stats.model) || opts.model)
                )}</div>
              </div>
              <div class="hud-stat-card">
                <div class="hud-stat-lbl">LIVE ELAPSED</div>
                <div class="hud-stat-num" id="hud-live-elapsed">${opts.elapsed || 0}s</div>
              </div>
              <div class="hud-stat-card">
                <div class="hud-stat-lbl">CONTEXT FED</div>
                <div class="hud-stat-num">CV + Memory + JD</div>
              </div>
              <div class="hud-stat-card">
                <div class="hud-stat-lbl">GROUNDING</div>
                <div class="hud-stat-num" style="color: #34d399;">Zero-Hallucination</div>
              </div>
            </div>
            ${detail ? `<div class="hud-detail">${escapeHtml(detail)}</div>` : ""}
            <button type="button" class="hud-cancel-btn" id="hud-cancel-btn">✕ Cancel Request</button>
          `
              : ""
          }

          ${
            state === "success"
              ? `
            <div class="hud-message">${escapeHtml(message)}</div>
            ${
              stats
                ? `
              <div class="hud-stats-grid">
                <div class="hud-stat-card">
                  <div class="hud-stat-lbl">LATENCY</div>
                  <div class="hud-stat-num">${
                    stats.latency_ms
                      ? stats.latency_ms < 1000
                        ? Math.round(stats.latency_ms) + " ms"
                        : (stats.latency_ms / 1000).toFixed(2) + "s"
                      : opts.elapsed
                      ? opts.elapsed + "s"
                      : "—"
                  }</div>
                </div>
                <div class="hud-stat-card">
                  <div class="hud-stat-lbl">OUTPUT SIZE</div>
                  <div class="hud-stat-num">${stats.words_generated || 0} words <span style="font-size:10px; font-weight:normal; opacity:0.8;">(${
                    stats.chars_generated || 0
                  } ch)</span></div>
                </div>
                <div class="hud-stat-card">
                  <div class="hud-stat-lbl">PROMPT SIZE</div>
                  <div class="hud-stat-num">${
                    stats.prompt_chars
                      ? stats.prompt_chars > 1024
                        ? (stats.prompt_chars / 1024).toFixed(1) + " KB"
                        : stats.prompt_chars + " ch"
                      : "—"
                  }</div>
                </div>
                <div class="hud-stat-card">
                  <div class="hud-stat-lbl">AI MODEL</div>
                  <div class="hud-stat-num">${escapeHtml(
                    formatModelName((stats && stats.model) || opts.model)
                  )}</div>
                </div>
              </div>
              <div class="hud-sources-row">
                <span class="hud-source-pill">📄 CV: ${(
                  (stats.sources && stats.sources.cv_chars) ||
                  stats.cv_chars ||
                  0
                ).toLocaleString()} ch</span>
                <span class="hud-source-pill">🧠 Memory: ${(
                  (stats.sources && stats.sources.memory_chars) ||
                  stats.memory_chars ||
                  0
                ).toLocaleString()} ch</span>
                ${
                  (stats.sources && stats.sources.jd_chars) || stats.jd_chars
                    ? `<span class="hud-source-pill">📋 JD: ${(
                        (stats.sources && stats.sources.jd_chars) ||
                        stats.jd_chars
                      ).toLocaleString()} ch</span>`
                    : ""
                }
                ${
                  (stats.sources && stats.sources.rules_chars) || stats.rules_chars
                    ? `<span class="hud-source-pill">✍️ Rules: ${(
                        (stats.sources && stats.sources.rules_chars) ||
                        stats.rules_chars
                      ).toLocaleString()} ch</span>`
                    : ""
                }
              </div>
            `
                : ""
            }
            ${
              opts.answerPreview
                ? `
              <div class="hud-preview-header">
                <span class="hud-preview-title">INSERTED ANSWER:</span>
                <button type="button" class="hud-copy-btn" id="hud-copy-btn" data-copy-text="${escapeHtml(
                  opts.answerPreview
                )}" title="Copy answer to clipboard">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                  </svg>
                  <span>Copy</span>
                </button>
              </div>
              <div class="hud-preview-box">${escapeHtml(opts.answerPreview)}</div>
            `
                : detail
                ? `<div class="hud-detail">${escapeHtml(detail)}</div>`
                : ""
            }
            <div class="hud-footer-hint">✓ Inserted into form field. Stays open so you can copy at your own pace.</div>
          `
              : ""
          }

          ${
            state === "skipped"
              ? `
            <div class="hud-message">${escapeHtml(message)}</div>
            ${
              opts.isGuardrail
                ? `
              <div class="hud-guardrail-box">
                <div class="hud-guardrail-title">🛡️ GROUNDING GUARDRAIL TRIGGERED</div>
                <div class="hud-guardrail-text">${escapeHtml(
                  detail ||
                    "No factual verification found in your CV or Memory profile for this question. Left blank to avoid hallucination."
                )}</div>
              </div>
              <div class="hud-footer-hint" style="color: #fbbf24;">You can enter an answer manually if you wish.</div>
            `
                : detail
                ? `<div class="hud-detail">${escapeHtml(detail)}</div>`
                : ""
            }
            ${
              stats
                ? `
              <div class="hud-sources-row">
                <span class="hud-source-pill" style="background: rgba(245, 158, 11, 0.12); border-color: rgba(245, 158, 11, 0.3); color: #fcd34d;">⏱️ ${(
                  stats.latency_ms / 1000
                ).toFixed(2)}s</span>
                <span class="hud-source-pill" style="background: rgba(245, 158, 11, 0.12); border-color: rgba(245, 158, 11, 0.3); color: #fcd34d;">Checked ${(
                  (stats.cv_chars || 0) + (stats.memory_chars || 0)
                ).toLocaleString()} chars profile</span>
              </div>
            `
                : ""
            }
          `
              : ""
          }

          ${
            state === "ready"
              ? `
            <div class="hud-message" style="font-size: 12.5px; font-weight: 600; color: #f8fafc; margin-bottom: 6px;">
              ${escapeHtml(message)}
            </div>
            ${
              detail
                ? `
              <div class="hud-detail" style="color: #cbd5e1; font-size: 11.5px; line-height: 1.5; white-space: pre-line; margin-bottom: 8px;">
                ${escapeHtml(detail)}
              </div>
            `
                : ""
            }
          `
              : ""
          }

          ${
            state === "error"
              ? `
            <div class="hud-message">${escapeHtml(message)}</div>
            ${
              opts.answerPreview
                ? `
              <div class="hud-preview-header">
                <span class="hud-preview-title" style="color: #fca5a5;">READY ANSWER:</span>
                <button type="button" class="hud-copy-btn" id="hud-copy-btn" data-copy-text="${escapeHtml(
                  opts.answerPreview
                )}" title="Copy answer to clipboard">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                  </svg>
                  <span>Copy</span>
                </button>
              </div>
              <div class="hud-preview-box" style="border-color: rgba(239, 68, 68, 0.3); background: rgba(239, 68, 68, 0.08);">${escapeHtml(
                opts.answerPreview
              )}</div>
              <button type="button" class="hud-manual-copy-btn" id="hud-manual-copy-btn" data-copy-text="${escapeHtml(
                opts.answerPreview
              )}">
                📋 Copy Answer (Click &amp; Paste into Field)
              </button>
            `
                : ""
            }
            ${
              detail
                ? `<div class="hud-detail" style="border-color: rgba(239, 68, 68, 0.3); background: rgba(239, 68, 68, 0.08);">${escapeHtml(
                    detail
                  )}</div>`
                : ""
            }
            <div class="hud-footer-hint" style="color: #f87171;">Ensure Job Desk backend is running on http://127.0.0.1:8000.</div>
          `
              : ""
          }

          ${
            opts.actionText
              ? `
            <button class="hud-action-btn" id="hud-action-btn">${escapeHtml(opts.actionText)}</button>
          `
              : ""
          }

          <div class="hud-progress-line ${
            state === "loading" || state === "thinking" ? "hud-progress-animated" : ""
          }" style="background: ${
        state === "success"
          ? "#10b981"
          : state === "skipped"
          ? "#f59e0b"
          : state === "error"
          ? "#ef4444"
          : "#3b82f6"
      };"></div>
        </div>
      `;
    }

    function renderMulti(taskList) {
      const thinkingTasks = taskList.filter((t) => t.state === "thinking" || t.state === "loading");
      const successTasks = taskList.filter((t) => t.state === "success");
      const errorTasks = taskList.filter((t) => t.state === "error");
      const skippedTasks = taskList.filter((t) => t.state === "skipped");

      const thinkingCount = thinkingTasks.length;
      const successCount = successTasks.length;
      const totalCount = taskList.length;

      let badgeBg = "rgba(59, 130, 246, 0.2)";
      let badgeColor = "#60a5fa";
      let badgeText = `${thinkingCount} ACTIVE • ${successCount} FILLED`;

      let iconHtml = `
        <svg class="hud-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"></path>
        </svg>
      `;

      if (thinkingCount === 0) {
        if (successCount === totalCount) {
          badgeBg = "rgba(16, 185, 129, 0.2)";
          badgeColor = "#34d399";
          badgeText = `ALL ${totalCount} FILLED`;
          iconHtml = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
          `;
        } else {
          badgeBg = "rgba(245, 158, 11, 0.2)";
          badgeColor = "#fbbf24";
          badgeText = `${successCount}/${totalCount} COMPLETED`;
          iconHtml = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="16" x2="12" y2="12"></line>
              <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
          `;
        }
      }

      const sampleTask = taskList.find((t) => t.company || t.role) || taskList[0] || {};
      const companyRoleText = [sampleTask.company, sampleTask.role].filter(Boolean).join(" • ");

      const taskCardsHtml = taskList
        .map((task, idx) => {
          const isThinking = task.state === "thinking" || task.state === "loading";
          const isSuccess = task.state === "success";
          const isSkipped = task.state === "skipped";
          const isErr = task.state === "error";

          let statusBadgeHtml = "";
          if (isThinking) {
            statusBadgeHtml = `
              <span class="hud-task-badge thinking" id="hud-task-badge-${task.id}">
                <span class="hud-pulse-dot-sm"></span> THINKING (${task.elapsed || 0}s)
              </span>
            `;
          } else if (isSuccess) {
            const latStr =
              task.stats && task.stats.latency_ms
                ? (task.stats.latency_ms / 1000).toFixed(1) + "s"
                : task.elapsed
                ? `${task.elapsed}s`
                : "";
            statusBadgeHtml = `<span class="hud-task-badge success">✓ FILLED${latStr ? " (" + latStr + ")" : ""}</span>`;
          } else if (isSkipped) {
            statusBadgeHtml = `<span class="hud-task-badge skipped">SKIPPED</span>`;
          } else if (isErr) {
            statusBadgeHtml = `<span class="hud-task-badge error">ERROR</span>`;
          }

          let bodyHtml = "";
          if (isThinking) {
            bodyHtml = `
              <div class="hud-task-thinking-row">
                <div class="hud-task-thinking-info">
                  <span class="hud-pulse-dot-sm"></span>
                  <span>Querying ${escapeHtml(formatModelName(task.model))}… <span id="hud-task-timer-${task.id}">(${task.elapsed || 0}s)</span></span>
                </div>
                <button type="button" class="hud-task-btn-cancel" data-cancel-id="${task.id}" title="Cancel this question">✕ Cancel</button>
              </div>
            `;
          } else if (isSuccess) {
            bodyHtml = `
              <div>
                ${
                  task.answerPreview
                    ? `<div class="hud-task-answer-box">${escapeHtml(task.answerPreview)}</div>`
                    : ""
                }
                <div class="hud-task-action-row">
                  <span class="hud-task-meta-txt">${
                    task.stats && task.stats.words_generated
                      ? `${task.stats.words_generated} words generated`
                      : "Answer inserted into field"
                  }</span>
                  ${
                    task.answerPreview
                      ? `
                    <button type="button" class="hud-copy-btn hud-task-copy-btn" data-copy-text="${escapeHtml(
                      task.answerPreview
                    )}" title="Copy this answer to clipboard">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                      </svg>
                      <span>Copy</span>
                    </button>
                  `
                      : ""
                  }
                </div>
              </div>
            `;
          } else if (isSkipped) {
            bodyHtml = `
              <div class="hud-task-skipped-msg">${escapeHtml(
                task.detail || task.message || "Grounding guardrail: Left blank to avoid hallucination."
              )}</div>
            `;
          } else if (isErr) {
            bodyHtml = `
              <div class="hud-task-error-msg">${escapeHtml(task.message || "Could not generate answer.")}</div>
              ${
                task.answerPreview
                  ? `
                <div class="hud-task-answer-box" style="border-color: rgba(239, 68, 68, 0.3); background: rgba(239, 68, 68, 0.08); margin-top: 4px;">${escapeHtml(
                  task.answerPreview
                )}</div>
                <div class="hud-task-action-row" style="margin-top: 4px;">
                  <span class="hud-task-meta-txt" style="color: #fca5a5;">Ready to paste</span>
                  <button type="button" class="hud-copy-btn hud-task-copy-btn" data-copy-text="${escapeHtml(
                    task.answerPreview
                  )}">
                    <span>📋 Copy</span>
                  </button>
                </div>
              `
                  : ""
              }
            `;
          }

          return `
            <div class="hud-task-card hud-task-card-${task.state || "loading"}" id="hud-task-card-${task.id}">
              <div class="hud-task-card-header">
                <div class="hud-task-header-left">
                  <span class="hud-task-num">#${idx + 1}</span>
                  <span class="hud-task-qname" title="${escapeHtml(task.targetField)}">${escapeHtml(
            task.targetField || "Question " + (idx + 1)
          )}</span>
                </div>
                ${statusBadgeHtml}
              </div>
              ${bodyHtml}
            </div>
          `;
        })
        .join("");

      shadow.innerHTML = `
        ${baseStyles}
        <div class="hud-card hud-card-multi">
          <div class="hud-header">
            <div class="hud-header-left">
              <div class="hud-icon-wrap">${iconHtml}</div>
              <span class="hud-title">Job Desk AI</span>
              <span class="hud-badge" style="background: ${badgeBg}; color: ${badgeColor};">${badgeText}</span>
            </div>
            <div class="hud-header-right">
              ${
                thinkingCount > 0
                  ? `<button type="button" class="hud-cancel-all-btn" id="hud-cancel-all-btn" title="Cancel all in-flight requests">✕ Cancel All</button>`
                  : ""
              }
              <button class="hud-minimize-btn" id="hud-minimize-btn" title="Minimize to small floating pill">—</button>
              <button class="hud-close" id="hud-close-btn" title="Close / Dismiss">&times;</button>
            </div>
          </div>

          <div class="hud-meta-row">
            ${
              companyRoleText
                ? `<span class="hud-meta-pill hud-pill-company">🏢 ${escapeHtml(companyRoleText)}</span>`
                : ""
            }
            <span class="hud-meta-pill hud-pill-kind" style="color: #93c5fd; background: rgba(59, 130, 246, 0.15); border-color: rgba(59, 130, 246, 0.3);">
              ⚡ PARALLEL PIPELINE (${taskList.length} QUESTIONS)
            </span>
          </div>

          <div class="hud-multi-list">
            ${taskCardsHtml}
          </div>

          <div class="hud-footer-hint">
            ${
              thinkingCount > 0
                ? `⚡ Querying ${thinkingCount} question${
                    thinkingCount > 1 ? "s" : ""
                  } in parallel… right-click more fields anytime.`
                : `✓ All questions processed. Stays open so you can copy at your own pace.`
            }
          </div>

          <div class="hud-progress-line ${
            thinkingCount > 0 ? "hud-progress-animated" : ""
          }" style="background: ${
        thinkingCount > 0
          ? "#3b82f6"
          : successCount === totalCount
          ? "#10b981"
          : errorTasks.length > 0
          ? "#ef4444"
          : "#f59e0b"
      };"></div>
        </div>
      `;
    }

    function renderMinimized(taskList) {
      const thinkingTasks = taskList.filter((t) => t.state === "thinking" || t.state === "loading");
      const answerTasks = taskList.filter((t) => !!t.answerPreview);
      const isThinking = thinkingTasks.length > 0;
      const latestAnswer = answerTasks.length ? answerTasks[answerTasks.length - 1].answerPreview : "";

      let label = "Job Desk AI";
      if (isThinking) {
        label = `${thinkingTasks.length} Generating…`;
      } else if (answerTasks.length) {
        label = `${answerTasks.length} Answer${answerTasks.length > 1 ? "s" : ""} Ready`;
      }

      shadow.innerHTML = `
        ${baseStyles}
        <div class="hud-minimized-pill">
          <span class="hud-pill-dot ${isThinking ? "thinking" : ""}"></span>
          <span class="hud-pill-title">${escapeHtml(label)}</span>
          ${
            latestAnswer
              ? `
            <button type="button" class="hud-pill-btn hud-pill-btn-copy hud-copy-btn" data-copy-text="${escapeHtml(
              latestAnswer
            )}" title="Copy latest generated answer">
              📋 Copy
            </button>
          `
              : ""
          }
          <button type="button" class="hud-pill-btn" id="hud-expand-btn" title="Open full status card">
            ↗ Open
          </button>
          <button type="button" class="hud-pill-close" id="hud-close-btn" title="Close / Dismiss">&times;</button>
        </div>
      `;
    }

    function wireCommonEvents() {
      // 1. Copy Buttons
      const copyButtons = shadow.querySelectorAll(".hud-copy-btn, .hud-manual-copy-btn");
      copyButtons.forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.preventDefault();
          e.stopPropagation();
          const text = btn.getAttribute("data-copy-text");
          if (!text) return;
          try {
            await navigator.clipboard.writeText(text);
          } catch (_) {
            try {
              const ta = document.createElement("textarea");
              ta.value = text;
              ta.style.position = "fixed";
              ta.style.opacity = "0";
              document.body.appendChild(ta);
              ta.select();
              document.execCommand("copy");
              ta.remove();
            } catch (_) {}
          }
          const origHtml = btn.innerHTML;
          btn.classList.add("copied");
          btn.innerHTML = `✓ Copied!`;
          setTimeout(() => {
            btn.classList.remove("copied");
            btn.innerHTML = origHtml;
          }, 2500);
        });
      });

      // 2. Individual Task Cancel Buttons
      const cancelButtons = shadow.querySelectorAll(".hud-task-btn-cancel");
      cancelButtons.forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          const id = btn.getAttribute("data-cancel-id");
          if (id && tasks.has(id)) {
            const task = tasks.get(id);
            if (typeof task.onCancel === "function") {
              task.onCancel();
            }
          }
        });
      });

      // 3. Single Card Cancel Button
      const singleCancelBtn = shadow.getElementById("hud-cancel-btn");
      if (singleCancelBtn) {
        singleCancelBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          const task = tasks.values().next().value;
          if (task && typeof task.onCancel === "function") {
            task.onCancel();
          } else {
            hide();
          }
        });
      }

      // 4. Cancel All Button
      const cancelAllBtn = shadow.getElementById("hud-cancel-all-btn");
      if (cancelAllBtn) {
        cancelAllBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          for (const task of tasks.values()) {
            if (task.state === "thinking" && typeof task.onCancel === "function") {
              task.onCancel();
            }
          }
        });
      }

      // 5. Minimize / Expand Buttons
      const minBtn = shadow.getElementById("hud-minimize-btn");
      if (minBtn) {
        minBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          isMinimized = true;
          render();
        });
      }

      const expBtn = shadow.getElementById("hud-expand-btn");
      if (expBtn) {
        expBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          isMinimized = false;
          render();
        });
      }

      // 6. Close Button
      const closeBtn = shadow.getElementById("hud-close-btn");
      if (closeBtn) {
        closeBtn.addEventListener("click", () => {
          hide();
        });
      }

      // 7. Action Button (single view)
      const actionBtn = shadow.getElementById("hud-action-btn");
      if (actionBtn) {
        const task = tasks.values().next().value;
        if (task) {
          actionBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (typeof task.onAction === "function") {
              task.onAction();
            } else if (
              task.actionType === "fill-now" ||
              (task.actionText && task.actionText.includes("Fill"))
            ) {
              const payload = task.actionPayload || activePayload;
              if (payload && typeof globalThis.jobDeskFill === "function") {
                globalThis.jobDeskFill(payload);
              }
            } else if (typeof globalThis.jobDeskStartPointerMode === "function") {
              globalThis.jobDeskStartPointerMode();
            }
          });
        }
      }

      // 8. Escape Key
      clearKeyHandler();
      const anyThinking = Array.from(tasks.values()).some((t) => t.state === "thinking");
      if (anyThinking) {
        activeKeyHandler = (e) => {
          if (e.key === "Escape") {
            clearKeyHandler();
            for (const task of tasks.values()) {
              if (task.state === "thinking" && typeof task.onCancel === "function") {
                task.onCancel();
              }
            }
          }
        };
        window.addEventListener("keydown", activeKeyHandler, true);
      }

      // 9. Auto-dismiss (ONLY for transient messages without answers!)
      clearDismiss();
      const hasInFlight = Array.from(tasks.values()).some(
        (t) => t.state === "thinking" || t.state === "loading"
      );
      const hasAnswers = Array.from(tasks.values()).some(
        (t) => !!t.answerPreview || t.state === "success"
      );

      // CRITICAL: If an answer was generated, NEVER auto-dismiss.
      // The user must be able to review and copy it without feeling rushed.
      if (!hasInFlight && !hasAnswers && tasks.size > 0) {
        let maxDismiss = 14000;
        for (const t of tasks.values()) {
          if (t.autoDismiss && t.autoDismiss > maxDismiss) {
            maxDismiss = t.autoDismiss;
          }
        }
        dismissTimeout = setTimeout(() => {
          hide();
        }, maxDismiss);
      }

      const card = shadow.querySelector(".hud-card, .hud-minimized-pill");
      if (card) {
        card.addEventListener("mouseenter", () => {
          clearDismiss();
        });
        card.addEventListener("mouseleave", () => {
          const stillInFlight = Array.from(tasks.values()).some(
            (t) => t.state === "thinking" || t.state === "loading"
          );
          if (!hasAnswers && !stillInFlight && tasks.size > 0) {
            clearDismiss();
            dismissTimeout = setTimeout(() => {
              hide();
            }, 8000);
          }
        });
      }
    }

    function render() {
      ensureHost();
      const taskList = Array.from(tasks.values());
      if (taskList.length === 0) {
        hide();
        return;
      }

      if (isMinimized) {
        renderMinimized(taskList);
      } else if (taskList.length === 1) {
        renderSingle(taskList[0]);
      } else {
        renderMulti(taskList);
      }

      wireCommonEvents();
    }

    function show(opts) {
      cleanOldTasks();
      clearDismiss();

      // Guard: if caller sends a generic loading HUD without an id while tasks are running, ignore
      if (!opts.id && opts.state === "loading" && tasks.size > 0) {
        return;
      }

      // When a new thinking request arrives, ensure the HUD is expanded
      if (opts.state === "thinking") {
        isMinimized = false;
      }

      const id = opts.id || "global_" + (opts.state === "loading" ? "loading" : Date.now());
      let task = tasks.get(id);

      if (!task) {
        task = {
          id,
          createdAt: Date.now(),
          startTime: opts.timer ? Date.now() : null,
          elapsed: opts.elapsed || 0,
        };
        tasks.set(id, task);
      }

      Object.assign(task, opts);
      if (opts.timer && !task.startTime) {
        task.startTime = Date.now();
      }
      if (task.state === "success" || task.state === "error" || task.state === "skipped") {
        task.finishedAt = Date.now();
      }

      render();
      startTimerTicker();
    }

    function clearTasks() {
      clearKeyHandler();
      clearTimer();
      clearDismiss();
      clearHighlight();
      tasks.clear();
      isMinimized = false;
    }

    function hide() {
      clearTasks();
      if (host && host.isConnected) {
        const card = shadow && shadow.querySelector(".hud-card");
        if (card) {
          card.style.transition = "opacity 0.2s ease, transform 0.2s ease";
          card.style.opacity = "0";
          card.style.transform = "translateY(-6px)";
          setTimeout(() => {
            if (host && host.isConnected) host.remove();
          }, 220);
        } else {
          host.remove();
        }
      }
    }

    return {
      show,
      hide,
      clearTasks,
      highlight,
      clearHighlight,
      getTasks: () => tasks,
      getStartTime: () => (tasks.size ? tasks.values().next().value.startTime : null),
    };
  })();

  function toast(text, kind, detail) {
    statusHUD.show({
      state: kind || "loading",
      title: "Job Desk",
      message: text,
      detail: detail || "",
      autoDismiss: kind === "error" ? 8000 : 5000,
    });
  }

  function markFill(el, ok) {
    if (ok) el.setAttribute("data-job-desk-filled", "1");
    return ok;
  }

  function visibleQuestion(el) {
    if (!el) return "";
    if (el.labels && el.labels[0]) {
      const lab = (el.labels[0].innerText || el.labels[0].textContent || "").replace(/\s+/g, " ").trim();
      if (lab.length > 3) return lab;
    }
    const wrap = el.closest("label, fieldset, [class*='question'], [class*='field'], [class*='input'], [class*='form-group']");
    if (wrap) {
      const clone = wrap.cloneNode(true);
      clone.querySelectorAll("input, textarea, select, button, svg").forEach((node) => node.remove());
      const text = (clone.innerText || clone.textContent || "").replace(/\s+/g, " ").trim();
      if (text.length > 3) return text;
    }
    const standardLabel = labelOf(el);
    if (standardLabel && standardLabel.length > 3) return standardLabel;
    const aria = el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || "";
    if (aria && aria.trim().length > 3) return aria.trim();
    if (el.placeholder && el.placeholder.trim().length > 3) return el.placeholder.trim();
    if (el.title && el.title.trim().length > 3) return el.title.trim();
    if (el.name && el.name.trim().length > 3) return el.name.replace(/[-_]/g, " ").trim();
    return "";
  }

  function isKnownField(text) {
    return (
      pick(text, "first_name") ||
      pick(text, "last_name") ||
      pick(text, "full_name") ||
      pick(text, "email") ||
      pick(text, "phone") ||
      pick(text, "linkedin") ||
      pick(text, "github") ||
      pick(text, "website") ||
      pick(text, "city") ||
      pick(text, "country") ||
      pick(text, "location") ||
      pick(text, "cover_letter") ||
      pick(text, "why_i_fit") ||
      pick(text, "heard_about") ||
      pick(text, "work_authorization") ||
      pick(text, "sponsorship_now") ||
      pick(text, "sponsorship_future") ||
      pick(text, "resume")
    );
  }

  let pickedField = null;
  let lastInteractedElement = null;

  function fieldFromEventTarget(target) {
    if (!target || !target.closest) return null;
    const control = target.closest("input, textarea, select");
    if (control) return control;
    const lab = target.closest("label");
    if (!lab) return null;
    if (lab.control) return lab.control;
    const forId = lab.getAttribute("for");
    if (forId) return document.getElementById(forId);
    return lab.querySelector("input, textarea, select");
  }

  function findForwardControl(startEl) {
    if (!startEl) return null;

    // Check if startEl itself is a control
    const control = startEl.closest("input, textarea, select");
    if (control && isAnswerTarget(control)) return control;

    // Check if startEl contains a control (e.g. wrapper label or question container)
    const inside = startEl.querySelectorAll("textarea, [contenteditable='true'], [role='textbox'], input:not([type=hidden]):not([type=file]):not([type=submit]):not([type=button]), select");
    if (inside.length > 0) {
      for (const el of inside) {
        if (isAnswerTarget(el)) return el;
      }
    }

    // Check immediate single-field parent wrapper (e.g. .field, .form-group)
    let parent = startEl.parentElement;
    for (let i = 0; i < 3 && parent && parent !== document.body && parent !== document.documentElement; i++) {
      if (parent.tagName === "FORM" || parent.tagName === "FIELDSET") break;
      const parentTextarea = parent.querySelector("textarea, [contenteditable='true'], [role='textbox']");
      if (parentTextarea && isAnswerTarget(parentTextarea)) return parentTextarea;
      const allInputs = parent.querySelectorAll("textarea, select, input:not([type=hidden]):not([type=file]):not([type=submit]):not([type=button])");
      if (allInputs.length === 1 && isAnswerTarget(allInputs[0])) {
        return allInputs[0];
      }
      parent = parent.parentElement;
    }

    // TreeWalker to walk strictly FORWARD down the DOM tree from startEl
    try {
      const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_ELEMENT,
        {
          acceptNode(node) {
            if (node.matches && node.matches("textarea, [contenteditable='true'], [role='textbox'], select, input:not([type=hidden]):not([type=file]):not([type=submit]):not([type=button])")) {
              if (isAnswerTarget(node)) return NodeFilter.FILTER_ACCEPT;
            }
            return NodeFilter.FILTER_SKIP;
          }
        }
      );
      walker.currentNode = startEl;
      let next = walker.nextNode();
      let firstCandidate = next;
      let steps = 0;
      // If there is a textarea in the next 3 controls, prefer it over checkboxes/radios
      while (next && steps < 4) {
        if (next.tagName === "TEXTAREA" || next.getAttribute("role") === "textbox" || next.isContentEditable) {
          return next;
        }
        next = walker.nextNode();
        steps++;
      }
      if (firstCandidate && isAnswerTarget(firstCandidate)) return firstCandidate;
    } catch (_) {}

    return null;
  }

  function findControlNearElement(startEl) {
    if (!startEl) return null;
    return findForwardControl(startEl);
  }

  function fieldNearSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    // Use the end of the selection so we scan forward towards the answer box
    const endNode = range.endContainer;
    const endEl = endNode && endNode.nodeType === 1 ? endNode : (endNode && endNode.parentElement);
    if (endEl) {
      const found = findForwardControl(endEl);
      if (found && isAnswerTarget(found)) return found;
    }
    const node = sel.anchorNode;
    const start = node && node.nodeType === 1 ? node : (node && node.parentElement);
    if (!start) return null;
    return findControlNearElement(start);
  }

  function findControlByText(text) {
    if (!text || text.trim().length < 5) return null;
    const query = text.trim().slice(0, 45).toLowerCase();
    const candidates = queryAllDeep("label, h1, h2, h3, h4, h5, h6, p, div, span, legend");
    for (const el of candidates) {
      if (el.children.length > 5) continue;
      const content = (el.textContent || "").trim().toLowerCase();
      if (content.includes(query)) {
        const found = findControlNearElement(el);
        if (found) return found;
      }
    }
    return null;
  }

  function bindQuestionPicker() {
    if (global.__jobDeskPickBound) return;
    global.__jobDeskPickBound = true;
    document.addEventListener(
      "pointerdown",
      (e) => {
        global.__jobDeskLastTarget = e.target;
        lastInteractedElement = e.target;
        const el = fieldFromEventTarget(e.target);
        if (el) pickedField = el;
      },
      true
    );
    document.addEventListener(
      "contextmenu",
      (e) => {
        global.__jobDeskLastTarget = e.target;
        lastInteractedElement = e.target;
        const el = fieldFromEventTarget(e.target);
        if (el) pickedField = el;
      },
      true
    );
    document.addEventListener(
      "focusin",
      (e) => {
        global.__jobDeskLastTarget = e.target;
        if (isAnswerTarget(e.target)) pickedField = e.target;
      },
      true
    );
  }

  function resolvePickedField(selectedText) {
    // 1. If text was specifically highlighted or passed via context menu
    if (selectedText && selectedText.trim()) {
      const nearSel = fieldNearSelection();
      if (nearSel && isAnswerTarget(nearSel)) return nearSel;
      const foundByText = findControlByText(selectedText);
      if (foundByText && isAnswerTarget(foundByText)) return foundByText;
    }

    // 2. Direct active element (if user is currently typing/focused in a form control)
    const active = document.activeElement;
    if (active && isAnswerTarget(active) && (active.tagName === "TEXTAREA" || active.tagName === "INPUT" || active.tagName === "SELECT")) {
      return active;
    }

    // 3. Element right-clicked / interacted with directly
    if (lastInteractedElement && lastInteractedElement.isConnected) {
      if (isAnswerTarget(lastInteractedElement)) return lastInteractedElement;
      const nearClicked = findControlNearElement(lastInteractedElement);
      if (nearClicked && isAnswerTarget(nearClicked)) return nearClicked;
    }

    // 4. Saved pickedField from pointerdown/focus/contextmenu
    if (pickedField && pickedField.isConnected && isAnswerTarget(pickedField)) return pickedField;

    // 5. Control near active DOM selection if any
    const near = fieldNearSelection();
    if (near && isAnswerTarget(near)) return near;

    return null;
  }

  function isAnswerTarget(el) {
    if (!el || el.disabled || el.readOnly) return false;
    if (el.type === "hidden" || el.type === "file" || el.type === "submit" || el.type === "button" || el.type === "image") {
      return false;
    }
    return !SKIP.test(labelOf(el));
  }

  function resolvePickedFields(selectedText) {
    const single = resolvePickedField(selectedText);
    return single ? [single] : [];
  }

  function questionFromField(el, selectedText, customKey) {
    if (!el) return null;
    const cleanSel = (selectedText || "").trim();
    let label = "";
    if (cleanSel.length >= 5) {
      // User explicitly highlighted this question: ALWAYS use it!
      label = cleanSel;
    } else {
      label = visibleQuestion(el);
      if (!label || label.trim().length === 0) {
        label = el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("title") || el.name || "Form Question";
      }
    }
    const key = customKey || "q0";
    const group =
      el.type === "radio" && el.name
        ? queryAllDeep('input[type="radio"][name="' + el.name.replace(/"/g, "") + '"]')
        : [el];
    group.forEach((node) => node.setAttribute("data-job-desk-q", key));
    let options =
      el.tagName === "SELECT"
        ? [...el.options].map((opt) => (opt.text || opt.value || "").trim()).filter(Boolean)
        : group.length > 1
          ? group.map((node) => (node.value || labelOf(node)).trim()).filter(Boolean)
          : [];
    if (!options.length && isDropdownElement(el)) {
      const optEls = queryAllDeep('[role="option"], .select2-results__option, [class*="suggestion"], [class*="dropdown-item"], li[data-value]');
      options = optEls.map((o) => (o.innerText || o.textContent || "").trim()).filter(Boolean);
    }
    return {
      key,
      label,
      kind: (el.tagName === "SELECT" || isDropdownElement(el)) ? "select" : el.type || "text",
      options: options.slice(0, 20),
    };
  }

  function applyAnswer(el, value, force) {
    if (!el || value == null || value === "") return false;
    if (el.tagName === "SELECT" || isDropdownElement(el)) {
      const candidates = [String(value)];
      const text = labelOf(el);
      if (pick(text, "location") || pick(text, "city") || pick(text, "country")) {
        candidates.push(...getCountryAliases(String(value)));
      }
      return markFill(el, chooseFromDropdown(el, candidates));
    }
    if (el.type === "checkbox" || el.type === "radio") {
      if (chooseChoice(el, value)) return markFill(el, true);
      const name = el.name;
      const nodes = name
        ? queryAllDeep('input[type="radio"][name="' + name.replace(/"/g, "") + '"]')
        : [el];
      const want = String(value).toLowerCase();
      for (const node of nodes) {
        const blob = (node.value + " " + labelOf(node)).toLowerCase();
        if (blob.includes(want) || want.includes(String(node.value || "").toLowerCase())) {
          node.checked = true;
          node.dispatchEvent(new Event("change", { bubbles: true }));
          node.dispatchEvent(new Event("click", { bubbles: true }));
          return markFill(node, true);
        }
      }
      return false;
    }
    return markFill(el, setValue(el, value, force));
  }

  async function answerSelected(payload) {
    const rawSel = (payload && payload.selectionText) || "";
    const domSel = (window.getSelection && String(window.getSelection()).trim()) || "";
    const selectionText = rawSel.trim() || domSel;
    const fields = resolvePickedFields(selectionText);
    if (!fields || fields.length === 0) {
      statusHUD.show({
        state: "error",
        title: "No Input Field Found",
        message: "Could not find a text box for this question.",
        detail: "Click inside the answer box or highlight the question text, then right-click → Answer this question.",
        autoDismiss: 8000,
      });
      return 0;
    }

    const validFields = fields.filter(isAnswerTarget);
    if (validFields.length === 0) {
      statusHUD.show({
        state: "skipped",
        title: "Field Skipped",
        message: "This field is intentionally skipped.",
        detail: "Password, salary, and EEO demographic fields are never filled automatically.",
        autoDismiss: 6000,
      });
      return 0;
    }

    const questions = validFields.map((f, i) =>
      questionFromField(f, validFields.length === 1 ? selectionText : "", "q" + i)
    );
    const isMulti = questions.length > 1;

    validFields.forEach((f) => statusHUD.highlight(f, "active"));

    const targetField = isMulti
      ? `${questions.length} Questions (${questions.map((q) => q.label).slice(0, 2).join("; ")}${questions.length > 2 ? "…" : ""})`
      : questions[0].label || selectionText || validFields[0].name || validFields[0].placeholder || "Form Question";
    const questionKind = isMulti ? "multi-question" : questions[0].kind;

    const requestId = "req_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
    let isCancelled = false;

    const doCancel = () => {
      if (isCancelled) return;
      isCancelled = true;
      validFields.forEach((f) => statusHUD.clearHighlight(f));
      try {
        chrome.runtime.sendMessage({
          type: "job-desk-cancel-answer",
          requestId,
        });
        chrome.runtime.sendMessage({
          type: "job-desk-badge",
          text: "—",
          color: "#94a3b8",
        });
      } catch (_) {}
      statusHUD.show({
        id: requestId,
        state: "skipped",
        title: "Request Cancelled",
        company: payload.company || "",
        role: payload.role || "",
        targetField,
        questionKind,
        message: "Question answering was cancelled.",
        detail: "No answer was requested or inserted into this field.",
        autoDismiss: 5000,
      });
    };

    const startTs = Date.now();
    statusHUD.show({
      id: requestId,
      state: "thinking",
      title: isMulti ? `Job Desk AI (${questions.length} Questions)` : "Job Desk AI",
      company: payload.company || "",
      role: payload.role || "",
      targetField,
      questionKind,
      message: isMulti
        ? `Consulting LLM for ${questions.length} questions…`
        : "Consulting LLM with your profile & role context…",
      detail: isMulti
        ? `Synthesizing answers across ${questions.length} questions`
        : "Synthesizing answer grounded in resume experience & writing guidelines",
      timer: true,
      onCancel: doCancel,
    });

    let data = {};
    try {
      data = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          {
            type: "job-desk-answer-question",
            requestId,
            body: {
              url: location.href,
              package_id: payload.package_id || "",
              questions,
            },
          },
          (response) => {
            if (isCancelled) return resolve({ cancelled: true });
            if (chrome.runtime.lastError) {
              return reject(new Error(chrome.runtime.lastError.message));
            }
            if (!response) {
              return reject(new Error("No response from extension background worker"));
            }
            if (response.cancelled) {
              return resolve({ cancelled: true });
            }
            if (!response.ok) {
              return reject(new Error(response.error || "Desk did not answer this question"));
            }
            resolve(response.data || {});
          }
        );
      });
    } catch (netErr) {
      if (isCancelled) return 0;
      validFields.forEach((f) => statusHUD.clearHighlight(f));
      statusHUD.show({
        id: requestId,
        state: "error",
        title: "Could Not Generate Answer",
        company: payload.company || "",
        role: payload.role || "",
        targetField,
        message: netErr.message || "Could not reach Job Desk backend.",
        detail: "Ensure the local Job Desk server is running on http://127.0.0.1:8000 and this role was opened via Apply.",
        autoDismiss: 9000,
      });
      throw netErr;
    }

    if (isCancelled || data.cancelled) {
      return 0;
    }

    const elapsed = Math.max(1, Math.round((Date.now() - startTs) / 1000));
    const stats = data.stats || null;
    const answers = data.answers || [];

    if (!answers.length || answers.every((a) => a.skip || !a.value)) {
      validFields.forEach((f) => statusHUD.clearHighlight(f));
      statusHUD.show({
        id: requestId,
        state: "skipped",
        title: "Question Skipped",
        company: payload.company || "",
        role: payload.role || "",
        targetField,
        questionKind,
        isGuardrail: true,
        message: "Grounding guardrail: Question skipped by LLM.",
        detail: "No factual verification found in your CV or Memory profile for this question (e.g. salary expectation, undisclosed tool, or demographics). Left blank to avoid hallucination.",
        stats,
        elapsed,
        autoDismiss: 10000,
      });
      return 0;
    }

    let filledCount = 0;
    const previewSnippets = [];

    for (let i = 0; i < questions.length; i++) {
      const q = questions[i];
      const ans = answers.find((a) => a.key === q.key) || answers[i];
      const field = queryAllDeep('[data-job-desk-q="' + q.key + '"]')[0] || validFields[i];
      if (ans && !ans.skip && ans.value) {
        if (applyAnswer(field, ans.value, true)) {
          filledCount++;
          statusHUD.highlight(field, "success");
          previewSnippets.push(isMulti ? `Q: ${q.label}\nA: ${ans.value}` : String(ans.value));
        } else {
          previewSnippets.push(isMulti ? `Q: ${q.label} (Not inserted)\nA: ${ans.value}` : String(ans.value));
        }
      }
    }

    const fullPreview = previewSnippets.join("\n\n");

    if (filledCount === 0) {
      validFields.forEach((f) => statusHUD.clearHighlight(f));
      statusHUD.show({
        id: requestId,
        state: "error",
        title: "Answer Ready (Not Inserted)",
        company: payload.company || "",
        role: payload.role || "",
        targetField,
        message: "Generated an answer but could not put it into this field automatically.",
        detail: "Click 'Copy Answer' below and press ⌘V (or Ctrl+V) to paste it directly.",
        answerPreview: fullPreview,
        stats,
        elapsed,
      });
      return 0;
    }

    const isCached = Boolean(stats && stats.from_cache);
    const who = [payload.company, payload.role].filter(Boolean).join(" — ");
    statusHUD.show({
      id: requestId,
      state: "success",
      title: isCached
        ? (isMulti ? `${filledCount} Answers (Cached)` : "Question Answered (Cached)")
        : (isMulti ? `${filledCount} Questions Answered` : "Question Answered"),
      company: payload.company || "",
      role: payload.role || "",
      targetField,
      questionKind,
      message: (who ? who + ": " : "") + (isCached
        ? (isMulti ? `${filledCount} answers loaded from cache & inserted.` : "Answer loaded from cache & inserted into form field.")
        : (isMulti ? `${filledCount} answers verified & inserted.` : "Answer verified & inserted into form field.")),
      answerPreview: fullPreview,
      stats,
      elapsed,
    });
    return filledCount;
  }

  async function applyFields(payload) {
    const values = payload.fields || {};
    const files = payload.files || {};
    let filled = 0;
    let uploaded = 0;
    for (const el of fileInputs()) {
      const text = labelOf(el);
      const kind = pick(text, "cover_letter") && files.cover_letter ? "cover_letter" : "resume";
      if (await setFile(el, files[kind] || files.resume)) {
        uploaded += 1;
        filled += 1;
        markFill(el, true);
      }
    }
    for (const el of fields()) {
      if (el.type === "file") continue;
      const text = labelOf(el);
      if (pick(text, "first_name") && markFill(el, setValue(el, values.first_name))) filled += 1;
      else if (pick(text, "last_name") && markFill(el, setValue(el, values.last_name))) filled += 1;
      else if (pick(text, "email") && markFill(el, setValue(el, values.email))) filled += 1;
      else if (pick(text, "phone")) {
        const phoneGroup = el.closest(".form-group, .field, [class*='phone'], [class*='field'], [class*='input']") || el.parentElement;
        if (phoneGroup) {
          const countryDrop = phoneGroup.querySelector("select, [role='combobox'], button[aria-haspopup='listbox'], [class*='country']");
          if (countryDrop && countryDrop !== el) {
            chooseFromDropdown(countryDrop, ["Canada", "+1", "CA", values.country || ""]);
          }
        }
        if (markFill(el, setValue(el, values.phone))) filled += 1;
      }
      else if (pick(text, "linkedin") && markFill(el, setValue(el, values.linkedin))) filled += 1;
      else if (pick(text, "github") && markFill(el, setValue(el, values.github))) filled += 1;
      else if (pick(text, "website") && markFill(el, setValue(el, values.website))) filled += 1;
      else if (pick(text, "city") && markFill(el, fillLocationField(el, values, "city"))) filled += 1;
      else if (pick(text, "country") && markFill(el, fillLocationField(el, values, "country"))) filled += 1;
      else if (pick(text, "location") && markFill(el, fillLocationField(el, values, "location"))) filled += 1;
      else if (pick(text, "cover_letter") && markFill(el, setValue(el, values.cover_letter))) filled += 1;
      else if (pick(text, "why_i_fit") && markFill(el, setValue(el, values.why_i_fit))) filled += 1;
      else if (pick(text, "heard_about") && markFill(el, setValue(el, values.heard_about))) filled += 1;
      else if (pick(text, "work_authorization") && markFill(el, setValue(el, values.work_authorization))) filled += 1;
      else if (pick(text, "sponsorship_future") && markFill(el, chooseChoice(el, values.sponsorship_future))) filled += 1;
      else if (pick(text, "sponsorship_now") && markFill(el, chooseChoice(el, values.sponsorship_now))) filled += 1;
      else if (pick(text, "full_name") && markFill(el, setValue(el, values.full_name))) filled += 1;
      else if (Array.isArray(payload.cached_answers) && payload.cached_answers.length > 0 && !el.value) {
        const cleanElLabel = text.trim().toLowerCase().replace(/^[\s*#\-•]+|[\s*:?]+$/g, "");
        if (cleanElLabel) {
          const match = payload.cached_answers.find((ca) => {
            if (ca.skip || !ca.value) return false;
            const caText = (ca.normalized_label || ca.label || "").trim().toLowerCase().replace(/^[\s*#\-•]+|[\s*:?]+$/g, "");
            return caText && (caText === cleanElLabel || cleanElLabel.includes(caText) || caText.includes(cleanElLabel));
          });
          if (match && markFill(el, setValue(el, match.value))) filled += 1;
        }
      }
    }
    return { filled, uploaded };
  }

  let activePayload = null;

  async function ensurePayload(payload) {
    if (payload && payload.files) {
      activePayload = payload;
      return payload;
    }
    if (activePayload && activePayload.files) {
      return activePayload;
    }
    try {
      const payloadData = await new Promise((resolve) => {
        chrome.runtime.sendMessage(
          { type: "job-desk-for-page", url: location.href },
          (response) => {
            if (chrome.runtime.lastError || !response || !response.ok) {
              resolve(null);
            } else {
              resolve(response.data);
            }
          }
        );
      });
      if (payloadData) {
        activePayload = payloadData;
        return activePayload;
      }
    } catch (_) {}
    return activePayload;
  }

  let activePointerCleanup = null;

  function startAttachPointerMode(payload) {
    if (activePointerCleanup) {
      activePointerCleanup();
      activePointerCleanup = null;
    }

    statusHUD.show({
      state: "thinking",
      title: "Point & Click Upload Target",
      company: (payload && payload.company) || "",
      role: (payload && payload.role) || "",
      message: "Hover and click the 'Attach', 'Upload', or Drag & Drop box on the page.",
      detail: "The extension will attach your tailored resume to that element.\nPress Escape to cancel.",
    });

    let currentHovered = null;
    let prevOutline = "";
    let prevCursor = "";

    function clearHover() {
      if (currentHovered) {
        try {
          currentHovered.style.outline = prevOutline;
          currentHovered.style.cursor = prevCursor;
        } catch (_) {}
        currentHovered = null;
      }
    }

    function onPointerOver(e) {
      const target = e.target;
      if (!target) return;
      if (target.closest("#job-desk-status-hud-host") || (target.getRootNode && target.getRootNode() instanceof ShadowRoot)) return;
      clearHover();
      currentHovered = target;
      prevOutline = target.style.outline || "";
      prevCursor = target.style.cursor || "";
      target.style.outline = "2px dashed #3b82f6";
      target.style.cursor = "crosshair";
    }

    async function onClick(e) {
      const target = e.target;
      if (!target) return;
      if (target.closest("#job-desk-status-hud-host") || (target.getRootNode && target.getRootNode() instanceof ShadowRoot)) return;

      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();

      cleanup();
      await attachResumeToTarget(target, payload);
    }

    function onKeyDown(e) {
      if (e.key === "Escape") {
        cleanup();
        statusHUD.show({
          state: "skipped",
          title: "Pointer Mode Cancelled",
          message: "Cancelled pointer selection. You can right-click any upload button → 'Attach resume here' at any time.",
          autoDismiss: 4000,
        });
      }
    }

    function cleanup() {
      clearHover();
      document.removeEventListener("pointerover", onPointerOver, true);
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("keydown", onKeyDown, true);
      activePointerCleanup = null;
    }

    activePointerCleanup = cleanup;
    document.addEventListener("pointerover", onPointerOver, true);
    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKeyDown, true);
  }

  function findUploadInput(el) {
    if (!el) return null;

    // 1. Direct input[type=file]
    if (el.tagName === "INPUT" && el.type === "file") return el;

    // 2. Nearest label (handles clicking or selecting text inside <label for="...">)
    const label = el.closest ? el.closest("label") : null;
    if (label) {
      const forId = label.htmlFor || label.getAttribute("for");
      if (forId) {
        const forInput = document.getElementById(forId);
        if (forInput && forInput.type === "file") return forInput;
      }
      const inLabel = label.querySelector("input[type='file']");
      if (inLabel) return inLabel;
    }

    // 3. Child input[type=file]
    const inside = el.querySelector ? el.querySelector("input[type='file']") : null;
    if (inside) return inside;

    // 4. Modal / Dialog container (LinkedIn Easy Apply modal, Greenhouse modal, Ashby modal)
    const modal = el.closest ? el.closest("[role='dialog'], .jobs-easy-apply-modal, .artdeco-modal, form, [data-test-modal], section") : null;
    if (modal) {
      const modalInputs = queryAllDeep("input[type='file']", modal);
      const resumeInput = modalInputs.find((inp) => {
        const str = ((inp.id || "") + " " + (inp.name || "") + " " + (inp.getAttribute("aria-label") || "")).toLowerCase();
        return /resume|cv|document/i.test(str);
      });
      if (resumeInput) return resumeInput;
      if (modalInputs.length > 0) return modalInputs[0];
    }

    // 5. Ancestor search up to 8 levels
    let curr = el;
    for (let i = 0; i < 8 && curr && curr !== document.body; i++) {
      const found = curr.querySelector ? curr.querySelector("input[type='file']") : null;
      if (found) return found;
      curr = curr.parentElement;
    }

    // 6. Global page file inputs (e.g. LinkedIn Easy Apply or standard application step)
    const allInputs = queryAllDeep("input[type='file']");
    const globalResumeInput = allInputs.find((inp) => {
      const str = ((inp.id || "") + " " + (inp.name || "") + " " + (inp.getAttribute("aria-label") || "")).toLowerCase();
      return /resume|cv|document/i.test(str);
    });
    if (globalResumeInput) return globalResumeInput;
    if (allInputs.length > 0) return allInputs[0];

    return null;
  }

  async function attachResumeToTarget(targetElement, payload) {
    if (!targetElement) return false;
    payload = await ensurePayload(payload);
    const fileInfo = payload && payload.files && payload.files.resume;
    if (!fileInfo) {
      statusHUD.show({
        state: "error",
        title: "No Resume File",
        message: "No tailored CV file was found in the package for this page.",
        autoDismiss: 6000,
      });
      return false;
    }

    statusHUD.show({
      state: "thinking",
      title: "Attaching Resume…",
      company: payload.company || "",
      role: payload.role || "",
      message: "Loading tailored CV (" + (fileInfo.name || "resume.pdf") + ")…",
    });

    let file = null;
    try {
      file = await fileFromPayload(fileInfo);
    } catch (err) {
      statusHUD.show({
        state: "error",
        title: "Download Failed",
        message: "Could not fetch resume file from local desk: " + (err.message || err),
        autoDismiss: 6000,
      });
      return false;
    }
    if (!file) {
      statusHUD.show({
        state: "error",
        title: "Download Failed",
        message: "Failed to read the resume file binary from Desk.",
        autoDismiss: 6000,
      });
      return false;
    }

    const dt = new DataTransfer();
    dt.items.add(file);

    function markSuccess(element) {
      const visibleEl = (element && element.offsetParent ? element : null) ||
        (targetElement && targetElement.closest ? targetElement.closest("label, button, [role='button'], [class*='upload'], div") : null) ||
        targetElement;
      statusHUD.highlight(visibleEl, "success");
      statusHUD.show({
        state: "success",
        title: "Resume Attached!",
        company: payload.company || "",
        role: payload.role || "",
        message: "Successfully attached " + (fileInfo.name || "tailored CV") + "!",
        detail: "Highlighted target in green. Verify the attachment on the page before submitting.",
        autoDismiss: 7000,
      });
      return true;
    }

    // 1. Direct or nearby input[type=file]
    const fileInput = findUploadInput(targetElement);
    if (fileInput) {
      if (assignFiles(fileInput, file)) return markSuccess(fileInput);
    }

    // 2. Dropzone / Upload button container
    const dropzone = targetElement.closest("[class*='drop'], [class*='upload'], [class*='attach'], [class*='file'], label, button, [role='button']") || targetElement;
    const dropTargets = [dropzone, targetElement, targetElement.parentElement].filter(Boolean);

    let dropAccepted = false;
    for (const dtTarget of dropTargets) {
      try {
        dtTarget.dispatchEvent(new DragEvent("dragenter", { bubbles: true, cancelable: true, dataTransfer: dt }));
        dtTarget.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt }));
        dtTarget.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
        dropAccepted = true;
      } catch (_) {}
    }

    // 3. Intercept programmatic input.click() if clicking the element triggers a hidden input
    let interceptedInput = null;
    const origClick = HTMLInputElement.prototype.click;
    try {
      HTMLInputElement.prototype.click = function () {
        if (this.type === "file") {
          interceptedInput = this;
          assignFiles(this, file);
          return;
        }
        return origClick.apply(this, arguments);
      };
      const btn = targetElement.closest("button, [role='button'], label") || targetElement;
      if (typeof btn.click === "function") {
        btn.click();
      }
    } catch (_) {
    } finally {
      setTimeout(() => {
        HTMLInputElement.prototype.click = origClick;
      }, 500);
    }

    if (interceptedInput) {
      return markSuccess(interceptedInput);
    }

    await new Promise((r) => setTimeout(r, 400));

    // Check if any nearby file input was populated
    const container = targetElement.closest("form, section, fieldset, tr, div") || document;
    const anyFilled = queryAllDeep("input[type='file']", container).find((inp) => inp.files && inp.files.length);
    if (anyFilled) {
      return markSuccess(anyFilled);
    }

    // If target was recognized as a dropzone / attach button and received drop events
    const isAttachIsh = /\b(attach|upload|drop|resume|cv|file)\b/i.test(
      (dropzone.innerText || "") + " " + (dropzone.className || "") + " " + (dropzone.getAttribute("aria-label") || "")
    );
    if (isAttachIsh && dropAccepted) {
      return markSuccess(dropzone);
    }

    return false;
  }

  async function jobDeskAttachSelected(payload) {
    if (payload) activePayload = payload;
    bindQuestionPicker();
    payload = await ensurePayload(payload);

    let target = global.__jobDeskLastTarget || lastInteractedElement;
    if (!target || !target.isConnected) {
      if (document.activeElement && document.activeElement !== document.body) {
        target = document.activeElement;
      }
    }

    if (!target) {
      target = findUploadInput(document.body);
    }

    if (!target) {
      startAttachPointerMode(payload);
      return { ok: false, filled: 0, reason: "No target element detected. Pointer mode activated." };
    }

    let ok = await attachResumeToTarget(target, payload);
    if (!ok) {
      const modalOrBody = (target.closest && target.closest("[role='dialog'], .jobs-easy-apply-modal, .artdeco-modal, form")) || document.body;
      const fallbackInput = findUploadInput(modalOrBody);
      if (fallbackInput && fallbackInput !== target) {
        ok = await attachResumeToTarget(fallbackInput, payload);
      }
    }

    if (!ok) {
      return { ok: false, filled: 0, reason: "The right-clicked element does not accept file attachments." };
    }
    return { ok: true, filled: 1 };
  }

  async function jobDeskFill(payload) {
    if (payload) activePayload = payload;
    bindQuestionPicker();
    if (!payload || !payload.fields) return 0;

    statusHUD.clearTasks();

    if (payload.apply_kind === "easy_apply" || /linkedin\.com|indeed\./i.test(location.hostname)) {
      await openEasyApply();
    }
    statusHUD.show({
      id: "form_fill",
      isFormFill: true,
      state: "thinking",
      title: "Filling Form…",
      company: payload.company || "",
      role: payload.role || "",
      message: "Applying form answers and attaching tailored resume…",
    });
    if (!looksLikeForm()) return 0;
    let total = 0;
    let uploaded = 0;
    for (let i = 0; i < 6; i += 1) {
      const result = await applyFields(payload);
      total = Math.max(total, result.filled);
      uploaded = Math.max(uploaded, result.uploaded);
      if (uploaded) break;
      if (i < 5) await new Promise((r) => setTimeout(r, 700));
    }
    const who = [payload.company, payload.role].filter(Boolean).join(" — ");
    const cv = (payload.files && payload.files.resume && payload.files.resume.name) || "the tailored CV";
    if (!uploaded && payload.files && payload.files.resume && pageWantsResume()) {
      await offerResumeFallback(
        payload,
        (who ? who + ". " : "") +
          (total ? "Filled " + total + " field(s). " : "") +
          "Could not attach the CV to a file field on this page."
      );
      return total;
    }
    if (total || uploaded) {
      statusHUD.show({
        id: "form_fill",
        isFormFill: true,
        state: "success",
        title: "Form Filled",
        company: payload.company || "",
        role: payload.role || "",
        message: (who ? who + ": " : "") + "Filled " + total + " field(s)" + (uploaded ? ", uploaded " + cv : "") + "!",
        detail: "Please review the form entries before submitting.",
        autoDismiss: 9000,
      });
      if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: "job-desk-badge", text: "OK", color: "#16a34a" });
      }
    } else if (looksLikeForm()) {
      statusHUD.show({
        id: "form_fill",
        isFormFill: true,
        state: "skipped",
        title: "No Matching Fields",
        company: payload.company || "",
        role: payload.role || "",
        message: "No matching fields on this page step yet. Stay on the form step that has the fields or upload, then click Fill again.",
        autoDismiss: 8000,
      });
    }
    return total;
  }

  async function jobDeskAnswerSelected(payload) {
    if (payload) activePayload = payload;
    bindQuestionPicker();
    if (!payload) return 0;
    return answerSelected(payload);
  }

  bindQuestionPicker();
  global.jobDeskFill = jobDeskFill;
  global.jobDeskAnswerSelected = jobDeskAnswerSelected;
  global.jobDeskAttachSelected = jobDeskAttachSelected;
  global.jobDeskStartPointerMode = () => startAttachPointerMode(activePayload);
  global.jobDeskStatusHUD = statusHUD;
})(typeof globalThis !== "undefined" ? globalThis : window);
