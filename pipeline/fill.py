"""Application form fill payload. Never includes a submit action."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path

from pipeline.config import Config
from pipeline.playbook import visa_answers
from pipeline.reports import as_host_path, package_dir


def _text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def fill_fields(cfg: Config, job: dict | None = None) -> dict:
    visa = visa_answers(cfg)
    city = (cfg.get("user.city") or "").strip()
    country = (cfg.get("user.country") or "").strip()
    location = ", ".join(part for part in (city, country) if part)
    company = ((job or {}).get("company") or "").strip()
    return {
        "first_name": cfg.preferred_name,
        "last_name": cfg.last_name,
        "full_name": cfg.full_name,
        "email": (cfg.get("user.email") or "").strip(),
        "phone": str(cfg.get("user.phone") or "").strip(),
        "city": city,
        "country": country,
        "location": location,
        "linkedin": (cfg.get("user.linkedin") or "").strip(),
        "github": (cfg.get("user.github") or "").strip(),
        "website": (cfg.get("user.website") or "").strip(),
        "work_authorization": visa["work_authorization"],
        "sponsorship_now": visa["sponsorship_now"],
        "sponsorship_future": visa["sponsorship_future"],
        "heard_about": f"{company} careers page" if company else "Company careers page",
        "cover_letter": "",
        "why_i_fit": "",
    }


def package_fill_payload(
    cfg: Config,
    *,
    package_id: str = "",
    job: dict | None = None,
    public_base: str = "http://127.0.0.1:8000",
) -> dict:
    """JSON the regular-browser helper uses to fill a form. Never submits."""
    meta = dict(job or {})
    folder: Path | None = None
    if package_id:
        folder = package_dir(cfg, package_id)
        if folder is None:
            raise FileNotFoundError(package_id)
        job_json = folder / "job.json"
        if job_json.exists():
            try:
                loaded = json.loads(job_json.read_text())
                if isinstance(loaded, dict):
                    meta = {**loaded, **{k: v for k, v in meta.items() if v}}
            except json.JSONDecodeError:
                pass
    fields = fill_fields(cfg, meta)
    files: dict = {}
    if folder:
        pdf = next(iter(sorted(folder.glob("*_CV.pdf"))), None)
        cover = folder / "cover_letter.md"
        why = folder / "why_i_fit.txt"
        fields["cover_letter"] = _text(cover)
        fields["why_i_fit"] = _text(why)
        base = public_base.rstrip("/")
        if pdf:
            files["resume"] = {
                "url": f"{base}/api/packages/{package_id}/file/{pdf.name}",
                "name": pdf.name,
                "type": "application/pdf",
                "path": as_host_path(cfg, pdf),
            }
        if cover.exists() and cover.stat().st_size:
            files["cover_letter"] = {
                "url": f"{base}/api/packages/{package_id}/file/{cover.name}",
                "name": cover.name,
                "type": "text/markdown",
                "path": as_host_path(cfg, cover),
            }
    apply_url = (meta.get("apply_url") or "").strip()
    posting = (meta.get("url") or "").strip()
    kind = (meta.get("apply_kind") or "").strip()
    from pipeline.apply_url import is_resolved_apply

    if apply_url and not is_resolved_apply(apply_url, kind):
        apply_url = ""
    elif not apply_url and kind == "easy_apply":
        apply_url = posting
    return {
        "package_id": package_id,
        "company": (meta.get("company") or "").strip(),
        "role": (meta.get("role") or "").strip(),
        "posting_url": posting,
        "apply_url": apply_url,
        "apply_kind": kind,
        "fields": fields,
        "files": files,
        "cached_answers": load_package_answers_cache(cfg, package_id) if package_id else [],
        "never_submit": True,
    }


def _url_ids(url: str) -> set[str]:
    from pipeline.apply_url import indeed_jk, linkedin_job_id

    raw = (url or "").strip()
    if not raw:
        return set()
    ids: set[str] = set()
    job_id = linkedin_job_id(raw)
    if job_id:
        ids.add("li:" + job_id)
    jk = indeed_jk(raw)
    if jk:
        ids.add("in:" + jk)
    for match in re.finditer(r"/jobs/(\d{5,})", raw):
        ids.add("job:" + match.group(1))
    for match in re.finditer(r"/job/(\d{5,})", raw, re.I):
        ids.add("job:" + match.group(1))
    for match in re.finditer(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", raw, re.I
    ):
        ids.add("uuid:" + match.group(0).lower())
    return ids


def page_match_score(page_url: str, *candidates: str) -> int:
    """How well a form page matches a stored posting or apply URL. 0 = no match."""
    from pipeline.apply_url import canonicalize_form_url, host_of

    page = (page_url or "").strip()
    if not page.startswith("http"):
        return 0
    page_host = host_of(page)
    page_ids = _url_ids(page)
    page_canon = canonicalize_form_url(page)
    best = 0
    for raw in candidates:
        cand = (raw or "").strip()
        if not cand.startswith("http"):
            continue
        if cand == page or canonicalize_form_url(cand) == page_canon:
            return 100
        shared = page_ids & _url_ids(cand)
        if shared:
            best = max(best, 80)
            continue
        host = host_of(cand)
        if not host or not page_host:
            continue
        related = (
            host == page_host
            or host.endswith("." + page_host)
            or page_host.endswith("." + host)
            or host.split(".")[-2:] == page_host.split(".")[-2:]
        )
        if related:
            best = max(best, 20)
    return best


def fill_payload_for_page(
    cfg: Config,
    page_url: str,
    *,
    pending: dict | None = None,
    public_base: str = "http://127.0.0.1:8000",
) -> dict | None:
    """Pick the tailored package for the open form URL, preferring a recent Apply click."""
    from pipeline.reports import list_packages

    page = (page_url or "").strip()
    pending_score = 0
    if isinstance(pending, dict) and pending.get("fields"):
        pending_score = page_match_score(
            page, pending.get("apply_url") or "", pending.get("posting_url") or ""
        )
        if pending_score >= 80:
            return pending

    scored: list[tuple[int, float, dict]] = []
    for pkg in list_packages(cfg):
        score = page_match_score(page, pkg.get("apply_url") or "", pkg.get("url") or "")
        if score:
            scored.append((score, float(pkg.get("modified") or 0), pkg))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    strong = [item for item in scored if item[0] >= 80]
    if strong:
        return package_fill_payload(
            cfg, package_id=strong[0][2]["id"], public_base=public_base
        )
    if pending_score >= 20:
        return pending
    if len(scored) == 1:
        return package_fill_payload(
            cfg, package_id=scored[0][2]["id"], public_base=public_base
        )
    return None


def _clip(text: str, limit: int) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 20)].rstrip() + "\n…[truncated]"


def _html_file_text(path: Path) -> str:
    from pipeline.search import html_to_text

    if not path.exists():
        return ""
    return html_to_text(path.read_text())


def _package_answer_context(cfg: Config, package_id: str) -> dict:
    folder = package_dir(cfg, package_id) if package_id else None
    meta: dict = {}
    if folder is not None:
        job_json = folder / "job.json"
        if job_json.exists():
            try:
                loaded = json.loads(job_json.read_text())
                if isinstance(loaded, dict):
                    meta = loaded
            except json.JSONDecodeError:
                meta = {}
    jd = (meta.get("jd") or "").strip()
    if not jd:
        from pipeline.jobs import applied_job_rows, queued_job_rows

        needle = {
            "url": meta.get("url") or meta.get("apply_url") or "",
            "company": meta.get("company") or "",
            "role": meta.get("role") or "",
        }
        for row in queued_job_rows(cfg) + applied_job_rows(cfg):
            if (needle["url"] and (row.get("url") or "") == needle["url"]) or (
                needle["company"]
                and needle["role"]
                and (row.get("company") or "").strip().lower() == needle["company"].strip().lower()
                and (row.get("role") or "").strip().lower() == needle["role"].strip().lower()
            ):
                jd = (row.get("jd") or row.get("jd_text") or "").strip()
                if jd:
                    break
    cv_html = next(iter(sorted(folder.glob("*_CV.html"))), None) if folder is not None else None
    cv_md = folder / "cv.md" if folder is not None else None
    return {
        "company": (meta.get("company") or "").strip(),
        "role": (meta.get("role") or "").strip(),
        "jd": jd,
        "cv": _html_file_text(cv_html) if cv_html else (_text(cv_md) if cv_md and cv_md.exists() else ""),
        "cover_letter": _text(folder / "cover_letter.md") if folder is not None else "",
        "why_i_fit": _text(folder / "why_i_fit.txt") if folder is not None else "",
        "analysis": _text(folder / "analysis.md") if folder is not None else "",
        "memory": _text(cfg.root / "memory" / "project.md"),
        "feedback": _text(cfg.root / "memory" / "feedback.md"),
        "visa": (cfg.get("visa.description") or cfg.get("visa.status") or "").strip(),
    }


def normalize_question_label(label: str) -> str:
    """Normalize question label for robust cache matching."""
    text = re.sub(r"\s+", " ", str(label or "").strip().lower())
    text = re.sub(r"^[\s\*\#\-\•]+|[\s\*\:\?]+$", "", text).strip()
    return text


def _package_answers_cache_path(cfg: Config, package_id: str) -> Path | None:
    if not package_id:
        return None
    folder = package_dir(cfg, package_id)
    if folder is None:
        return None
    return folder / "answers_cache.json"


def load_package_answers_cache(cfg: Config, package_id: str) -> list[dict]:
    """Load cached LLM answers for a package."""
    path = _package_answers_cache_path(cfg, package_id)
    if not path or not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            items = data.get("answers")
            if isinstance(items, list):
                return items
        elif isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_package_answers_cache(cfg: Config, package_id: str, new_answers: list[dict]) -> None:
    """Save or update cached LLM answers for a package."""
    path = _package_answers_cache_path(cfg, package_id)
    if not path:
        return
    existing = load_package_answers_cache(cfg, package_id)
    by_norm: dict[str, dict] = {}
    for item in existing:
        norm = item.get("normalized_label") or normalize_question_label(item.get("label", ""))
        if norm:
            by_norm[norm] = item

    now_iso = datetime.now(timezone.utc).isoformat()
    for item in new_answers:
        label = str(item.get("label") or "").strip()
        val = str(item.get("value") or "").strip()
        if not label or not val or item.get("skip"):
            continue
        norm = normalize_question_label(label)
        by_norm[norm] = {
            "label": label,
            "normalized_label": norm,
            "kind": item.get("kind", "text"),
            "value": val,
            "skip": False,
            "options": list(item.get("options") or []),
            "answered_at": item.get("answered_at") or now_iso,
        }

    blob = {
        "package_id": package_id,
        "updated_at": now_iso,
        "answers": list(by_norm.values()),
    }
    path.write_text(json.dumps(blob, indent=2, ensure_ascii=False))


def clear_package_answers_cache(cfg: Config, package_id: str) -> bool:
    """Clear cached LLM answers when package is marked applied or reset."""
    path = _package_answers_cache_path(cfg, package_id)
    if path and path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            pass
    return False


def _parse_question_answers(raw: str, keys: set[str]) -> list[dict]:
    blob = (raw or "").strip()
    match = re.search(r"\[[\s\S]*\]", blob)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in keys or key in seen:
            continue
        seen.add(key)
        skip = bool(item.get("skip"))
        value = "" if skip else str(item.get("value") or "").strip()
        out.append({"key": key, "value": value, "skip": skip or not value})
    return out


def answer_form_questions(
    cfg: Config,
    questions: list[dict],
    *,
    package_id: str = "",
    page_url: str = "",
    with_stats: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    """Answer leftover application questions from memory + this role's tailored CV."""
    cleaned: list[dict] = []
    for item in questions or []:
        label = str(item.get("label") or "").strip()
        key = str(item.get("key") or "").strip()
        if not key or not label:
            continue
        # Skip demographic, EEO, compensation, and account-password fields (avoiding false positives like "1Password")
        if str(item.get("kind") or "").lower() == "password" or re.search(
            r"\b(gender|race|veteran|disability|hispanic|ethnicity|sexual|demographic|eeo|salary|compensation)\b",
            label,
            re.I,
        ) or (
            re.search(r"\b(create|choose|enter|confirm|new|account)?\s*password\b", label, re.I)
            and not re.search(r"\b1password\b", label, re.I)
        ):
            continue
        cleaned.append(
            {
                "key": key,
                "label": label[:500],
                "kind": str(item.get("kind") or "text"),
                "options": [str(opt).strip() for opt in (item.get("options") or []) if str(opt).strip()][:20],
            }
        )
        if len(cleaned) >= 15:
            break
    if not cleaned:
        empty_stats = {
            "model": cfg.get("pipeline.nvidia.model") or "nvidia/nemotron-3-ultra-550b-a55b",
            "company": "",
            "role": "",
            "sources": {},
            "prompt_chars": 0,
            "questions_count": 0,
            "answers_count": 0,
            "words_generated": 0,
            "chars_generated": 0,
            "from_cache": False,
            "cached_count": 0,
            "new_count": 0,
            "latency_ms": 0,
        }
        return ([], empty_stats) if with_stats else []

    # Check for answers cached previously for this package
    cached_list = load_package_answers_cache(cfg, package_id) if package_id else []
    cached_by_norm = {
        (item.get("normalized_label") or normalize_question_label(item.get("label", ""))): item
        for item in cached_list
        if not item.get("skip") and item.get("value")
    }

    cached_results: dict[str, dict] = {}
    to_query: list[dict] = []

    for item in cleaned:
        norm = normalize_question_label(item["label"])
        cached_entry = cached_by_norm.get(norm)
        if cached_entry:
            cached_results[item["key"]] = {
                "key": item["key"],
                "value": cached_entry["value"],
                "skip": False,
                "from_cache": True,
            }
        else:
            to_query.append(item)

    ctx = _package_answer_context(cfg, package_id)

    # If all requested questions were found in the cache, return immediately with 0 LLM latency
    if not to_query:
        results = [cached_results[item["key"]] for item in cleaned]
        if with_stats:
            words_count = sum(len(str(a.get("value") or "").split()) for a in results if not a.get("skip"))
            chars_count = sum(len(str(a.get("value") or "")) for a in results if not a.get("skip"))
            stats = {
                "model": "cache",
                "company": ctx.get("company") or "",
                "role": ctx.get("role") or "",
                "sources": {
                    "cv_chars": len(ctx.get("cv") or ""),
                    "memory_chars": len(ctx.get("memory") or ""),
                    "jd_chars": len(ctx.get("jd") or ctx.get("analysis") or ""),
                    "rules_chars": len(ctx.get("feedback") or ""),
                    "visa_info": bool(ctx.get("visa")),
                },
                "prompt_chars": 0,
                "questions_count": len(cleaned),
                "answers_count": len(results),
                "words_generated": words_count,
                "chars_generated": chars_count,
                "from_cache": True,
                "cached_count": len(results),
                "new_count": 0,
                "latency_ms": 0,
            }
            return results, stats
        return results

    payload = json.dumps(to_query, ensure_ascii=False)
    company_name = ctx.get("company") or "this company"
    role_name = ctx.get("role") or "this role"

    prompt = f"""You are answering job application form questions as {cfg.full_name}, an experienced Senior Software Engineer applying for '{role_name}' at {company_name}.

### Grounding & Truthfulness:
Answer ONLY from the source materials below (Memory, CV, Projects, Writing rules). Do NOT invent employers, tools, users, revenue, or metrics that do not exist in the source materials. If a question cannot be answered honestly from the profile, set skip=true.

### Reader's View & Voice Guidelines (Sound like an authentic, high-caliber Senior Engineer, not an ATS robot):
1. **Conversational Senior Engineer Voice**:
   - Write in the first person ("I", "my") with a natural, pragmatic, and confident tone.
   - Speak directly to the hiring manager or tech lead as a peer.
   - BANNED CLICHÉS: Never use "mirrors my work", "mirrors the problems I solve", "driving the stakeholder loop", "confirming behavioral constraints", "aligns with my passion", "testament to", or "seamless integration".
   - Do NOT concatenate resume bullet points into single dense sentences. Use natural sentence structure (2 to 4 clear, well-paced sentences; 40 to 80 words for open-ended questions).

2. **Question-Specific Strategies**:
   - **"Why [Company] / Why now?"**:
     - Talk about {company_name}'s product, technology, or mission first (e.g. what makes their technical challenge, platform, or scale exciting right now).
     - Explain why this engineering problem genuinely interests you.
     - Connect 1-2 relevant technical strengths from your background (e.g. building reliable real-time pipelines, developer integrations, distributed systems) to show how you can contribute immediately.
     - NEVER lead with "Your focus matches my experience" or start by listing your past employers in sentence one.
   - **"What is the most impactful thing you've built? / What was your specific contribution?"**:
     - Pick the project from your experience that best matches {company_name}'s technical domain (e.g. for low-latency, streaming, AI, or modern backend systems, prioritize real-time Kafka event streaming at Uber or containerized streaming platform; for enterprise customer integrations, highlight full-lifecycle customer API delivery).
     - Structure as a builder: (1) what the core technical problem was, (2) what you personally architected and implemented (technologies, Python, data flow), and (3) the concrete impact (throughput, latency, user adoption).
   - **"How did you know it worked? / What did success look like?"**:
     - Frame verification like a software engineer:
       - Technical verification: production monitoring, latency under load spikes, zero event loss, stability.
       - Operational/User impact: concrete metric improvements, workflow turnaround reduction, or user adoption.
   - **Short / Factual Questions (e.g. tools, years of experience, URLs, work authorization)**:
     - Provide a direct, concise answer.

Visa / work authorisation: {ctx['visa'] or '(see memory)'}

### Memory
{_clip(ctx['memory'], 16000)}

### Writing rules
{_clip(ctx['feedback'], 4000)}

### Tailored CV for this role
{_clip(ctx['cv'], 12000)}

### Company & Role Context
Company: {company_name}
Role: {role_name}
{_clip(ctx['analysis'] or ctx['why_i_fit'], 3000)}

### Job description
{_clip(ctx['jd'], 6000)}

### Questions to Answer
{payload}

Return JSON only, an array with one object per question:
[{{"key":"q0","value":"concise, authentic answer","skip":false}}]
Use the exact same keys. For selects, copy one of the given options. Set skip=true only when you cannot answer honestly.
"""
    from pipeline.llm import complete_prompt, get_last_used_model

    raw = complete_prompt(prompt, effort="low")
    answers = _parse_question_answers(raw, {item["key"] for item in to_query})
    by_key = {item["key"]: item for item in answers}

    # Save newly generated answers to cache for this package
    new_to_cache = []
    to_query_by_key = {item["key"]: item for item in to_query}
    for ans in answers:
        if not ans.get("skip") and ans.get("value"):
            q_info = to_query_by_key.get(ans["key"])
            if q_info:
                new_to_cache.append({
                    "label": q_info["label"],
                    "value": ans["value"],
                    "kind": q_info.get("kind", "text"),
                    "options": q_info.get("options", []),
                })
    if new_to_cache and package_id:
        save_package_answers_cache(cfg, package_id, new_to_cache)

    results = []
    for item in cleaned:
        if item["key"] in cached_results:
            results.append(cached_results[item["key"]])
        else:
            results.append(by_key.get(item["key"]) or {"key": item["key"], "value": "", "skip": True})

    if with_stats:
        words_count = sum(len(str(a.get("value") or "").split()) for a in results if not a.get("skip"))
        chars_count = sum(len(str(a.get("value") or "")) for a in results if not a.get("skip"))
        stats = {
            "model": get_last_used_model() or cfg.get("pipeline.model") or "nvidia/nemotron-3-ultra-550b-a55b",
            "company": ctx.get("company") or "",
            "role": ctx.get("role") or "",
            "sources": {
                "cv_chars": len(ctx.get("cv") or ""),
                "memory_chars": len(ctx.get("memory") or ""),
                "jd_chars": len(ctx.get("jd") or ctx.get("analysis") or ""),
                "rules_chars": len(ctx.get("feedback") or ""),
                "visa_info": bool(ctx.get("visa")),
            },
            "prompt_chars": len(prompt),
            "questions_count": len(cleaned),
            "answers_count": sum(1 for a in results if not a.get("skip") and a.get("value")),
            "words_generated": words_count,
            "chars_generated": chars_count,
            "from_cache": len(cached_results) > 0 and len(to_query) == 0,
            "cached_count": len(cached_results),
            "new_count": len(to_query),
        }
        return results, stats
    return results
