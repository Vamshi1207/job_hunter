"""LLM calls: OpenCode Go (Muse Spark, subscription), then NVIDIA NIM, then agy/Gemini."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
import uuid

from pipeline.config import load_config

log = logging.getLogger(__name__)

NVIDIA_DEFAULT_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
NVIDIA_FALLBACK_MODELS = [
    "deepseek-ai/deepseek-v4.1-flash",
    "google/gemma-4-31b-it",
]

OPENCODE_DEFAULT_URL = "https://opencode.ai/zen/go/v1"
# Go subscription model IDs (NOT Zen pay-per-use IDs: no -free suffix here,
# and muse-spark-1.3-contributor, not muse-spark-1.3). Go requires the
# x-opencode-session header and bills the subscription, not Zen credits.
OPENCODE_DEFAULT_MODEL = "muse-spark-1.3-contributor"
OPENCODE_FALLBACK_MODELS = [
    "deepseek-v4-flash",
    "minimax-m3",
]

# Stable session per process so Go can route + cache our repeated tailor prompts.
_SESSION_ID = os.environ.get("OPENCODE_SESSION_ID") or f"jobsearch-{uuid.uuid4().hex[:12]}"
_USER_AGENT = "job-search-pipeline/1.0"

# Zen model IDs served over the Responses API (not chat/completions).
_OPENCODE_RESPONSES_PREFIXES = ("muse-spark", "gpt-", "grok-")


class RpmLimiter:
    """Process-wide sliding window so parallel workers stay under the API RPM cap."""

    def __init__(self, rpm: int):
        self.rpm = max(1, int(rpm))
        self._times: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            sleep_for = 0.0
            with self._lock:
                now = time.monotonic()
                window = now - 60.0
                self._times = [t for t in self._times if t > window]
                if len(self._times) >= self.rpm:
                    sleep_for = 60.0 - (now - self._times[0]) + 0.05
                else:
                    self._times.append(now)
                    return
            time.sleep(max(sleep_for, 0.05))


_limiter: RpmLimiter | None = None
_opencode_limiter: RpmLimiter | None = None
_limiter_lock = threading.Lock()


def worker_count(cfg=None) -> int:
    cfg = cfg or load_config()
    try:
        return max(1, min(8, int(cfg.get("pipeline.workers", 4))))
    except (TypeError, ValueError):
        return 4


def _rpm(cfg) -> int:
    try:
        return max(1, int(cfg.get("pipeline.nvidia.rpm", 40)))
    except (TypeError, ValueError):
        return 40


def _limiter_for(cfg) -> RpmLimiter:
    global _limiter
    rpm = _rpm(cfg)
    with _limiter_lock:
        if _limiter is None or _limiter.rpm != rpm:
            _limiter = RpmLimiter(rpm)
        return _limiter


def _opencode_rpm(cfg) -> int:
    try:
        return max(1, int(cfg.get("pipeline.opencode.rpm", 40)))
    except (TypeError, ValueError):
        return 40


def _opencode_limiter_for(cfg) -> RpmLimiter:
    global _opencode_limiter
    rpm = _opencode_rpm(cfg)
    with _limiter_lock:
        if _opencode_limiter is None or _opencode_limiter.rpm != rpm:
            _opencode_limiter = RpmLimiter(rpm)
        return _opencode_limiter


def nvidia_api_key() -> str:
    return (os.environ.get("NVIDIA_API_KEY") or "").strip()


def opencode_api_key() -> str:
    return (os.environ.get("OPENCODE_API_KEY") or "").strip()


def _provider_key(provider: str) -> str:
    if provider == "nvidia":
        return nvidia_api_key()
    if provider == "opencode":
        return opencode_api_key()
    return ""


def primary_provider(cfg=None) -> str:
    cfg = cfg or load_config()
    raw = (cfg.get("pipeline.provider") or "").strip().lower()
    if raw in {"nvidia", "agy", "opencode"}:
        return raw
    model = str(cfg.get("pipeline.model") or "")
    if _looks_like_nvidia_model(model):
        return "nvidia"
    if _looks_like_opencode_model(model):
        return "opencode"
    return "agy"


def nvidia_model_chain(cfg=None) -> list[str]:
    """Primary model (nemotron-3-ultra), then deepseek-v4-flash, then gemma-4-31b."""
    cfg = cfg or load_config()
    primary = str(cfg.get("pipeline.model") or NVIDIA_DEFAULT_MODEL).strip()
    if not _looks_like_nvidia_model(primary):
        primary = NVIDIA_DEFAULT_MODEL
    chain = [primary]

    configured_fallbacks = cfg.get("pipeline.nvidia.fallback_models")
    if isinstance(configured_fallbacks, list) and configured_fallbacks:
        fallbacks = [str(m).strip() for m in configured_fallbacks if str(m).strip()]
    else:
        single = str(cfg.get("pipeline.nvidia.fallback_model") or "").strip()
        if single and _looks_like_nvidia_model(single):
            fallbacks = [single]
            for m in NVIDIA_FALLBACK_MODELS:
                if m not in fallbacks:
                    fallbacks.append(m)
        else:
            fallbacks = NVIDIA_FALLBACK_MODELS

    for m in fallbacks:
        if m and m not in chain and "gpt-oss" not in m.lower():
            chain.append(m)
    return chain


def opencode_model_chain(cfg=None) -> list[str]:
    """Primary Go model (muse-spark contributor), then configured Go fallbacks."""
    cfg = cfg or load_config()
    primary = str(cfg.get("pipeline.model") or OPENCODE_DEFAULT_MODEL).strip()
    if not _looks_like_opencode_model(primary):
        primary = OPENCODE_DEFAULT_MODEL
    chain = [primary]

    configured_fallbacks = cfg.get("pipeline.opencode.fallback_models")
    if isinstance(configured_fallbacks, list) and configured_fallbacks:
        fallbacks = [str(m).strip() for m in configured_fallbacks if str(m).strip()]
    else:
        fallbacks = list(OPENCODE_FALLBACK_MODELS)

    for m in fallbacks:
        if m and m not in chain:
            chain.append(m)
    return chain


def _looks_like_nvidia_model(model: str) -> bool:
    lower = (model or "").lower()
    if "/" not in lower:
        # NVIDIA NIM IDs are org-prefixed (nvidia/..., deepseek-ai/...).
        # Bare IDs (deepseek-v4-flash, muse-spark-...) belong to Zen/agy.
        return False
    return (
        lower.startswith("nvidia/")
        or lower.startswith("deepseek-ai/")
        or lower.startswith("google/")
        or "nemotron" in lower
        or "deepseek" in lower
    )


def _looks_like_opencode_model(model: str) -> bool:
    lower = (model or "").strip().lower()
    if not lower or "/" in lower or lower.startswith("gemini-"):
        return False
    prefixes = (
        "muse-spark",
        "deepseek",
        "minimax",
        "glm-",
        "kimi-",
        "mistral",
        "qwen",
        "big-pickle",
        "space-bunny",
        "longcat-",
        "step-",
        "exo-",
        "mimo-",
        "ling-",
        "nemotron-",
        "grok-",
        "gpt-",
        "claude-",
    )
    return lower.startswith(prefixes)


_last_used_model: str = ""


def _transient_error(exc: Exception) -> bool:
    """True for rate limits and temporary upstream wobbles (HTTP 429/5xx,
    NIM 'Service temporarily overloaded', timeouts) — worth a backoff retry."""
    text = f"{type(exc).__name__} {exc}".lower()
    markers = (
        "ratelimit",
        "429",
        "500",
        "502",
        "503",
        "529",
        "overload",
        "temporarily",
        "try again",
        "timeout",
        "timed out",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
    )
    return any(m in text for m in markers)


def get_last_used_model() -> str:
    global _last_used_model
    return _last_used_model


def complete_prompt(prompt: str, *, effort: str = "high") -> str:
    """Return model text. Tries the primary provider first, then the other
    OpenAI-compatible gateway when its key is set, then the fallback provider.

    The recommended wiring is primary ``opencode`` (Muse Spark) with the
    NVIDIA chain following and agy/Gemini as the last backup.
    """
    global _last_used_model
    cfg = load_config()
    timeout = int(cfg.get("pipeline.llm_timeout_seconds", 600))
    primary = primary_provider(cfg)
    fallback = (cfg.get("pipeline.fallback_provider") or "agy").strip().lower()

    order = [primary]
    for candidate in ("opencode", "nvidia"):
        if candidate != primary and candidate != fallback and _provider_key(candidate):
            order.append(candidate)
    if fallback and fallback not in order:
        order.append(fallback)

    for provider in order:
        if provider in ("nvidia", "opencode"):
            text, model_name = _try_provider_models(
                provider, prompt, cfg, timeout=timeout, effort=effort
            )
            if text.strip():
                _last_used_model = model_name
                return text
            log.warning("%s models failed; trying next provider", provider)
        elif provider == "agy":
            try:
                if provider == primary:
                    _last_used_model = str(cfg.get("pipeline.model") or "gemini-3.1-pro")
                else:
                    _last_used_model = str(
                        cfg.get("pipeline.fallback_model") or "gemini-3.1-pro"
                    )
                return call_agy(prompt, effort=effort)
            except Exception as exc:
                log.warning("agy failed (%s)", exc)
        else:
            log.warning("Unknown provider %r; skipping", provider)
    return ""


def _try_provider_models(
    provider: str, prompt: str, cfg, *, timeout: int, effort: str
) -> tuple[str, str]:
    if provider == "opencode":
        chain = opencode_model_chain(cfg)
        caller = _call_opencode
    else:
        chain = nvidia_model_chain(cfg)
        caller = _call_nvidia
    for model in chain:
        try:
            text = caller(prompt, cfg, timeout=timeout, effort=effort, model=model)
            if text.strip():
                if _is_tailor_prompt_truncated(prompt, text):
                    log.warning(
                        "%s %s returned truncated output (missing closing tags) — falling back to next model",
                        provider,
                        model,
                    )
                    continue
                return text, model
            log.warning("%s %s returned empty output", provider, model)
        except Exception as exc:
            log.warning("%s %s failed (%s)", provider, model, exc)
    return "", ""


def _is_tailor_prompt_truncated(prompt: str, text: str) -> bool:
    """Check if tailor prompt response was truncated before finishing all sections."""
    if not text or not text.strip():
        return True
    if "<TITLE>" in prompt or "Return ONLY these tagged blocks" in prompt:
        if "<TITLE>" in text and not any(tag in text for tag in ("</ANALYSIS>", "</WHY_I_FIT>", "</COVER_LETTER>")):
            return True
    return False


def _try_nvidia_models_with_model(prompt: str, cfg, *, timeout: int, effort: str) -> tuple[str, str]:
    return _try_provider_models("nvidia", prompt, cfg, timeout=timeout, effort=effort)


def _try_opencode_models_with_model(prompt: str, cfg, *, timeout: int, effort: str) -> tuple[str, str]:
    return _try_provider_models("opencode", prompt, cfg, timeout=timeout, effort=effort)


def _try_nvidia_models(prompt: str, cfg, *, timeout: int, effort: str) -> str:
    text, _ = _try_nvidia_models_with_model(prompt, cfg, timeout=timeout, effort=effort)
    return text


def call_agy(prompt: str, effort: str = "high") -> str:
    cfg = load_config()
    model = cfg.get("pipeline.model") or "gemini-3.1-pro"
    if primary_provider(cfg) in ("nvidia", "opencode"):
        model = cfg.get("pipeline.fallback_model") or "gemini-3.1-pro"
    agy = shutil.which("agy") or "/root/.local/bin/agy"
    result = subprocess.run(
        [agy, "--print", prompt, "--model", model, "--effort", effort],
        capture_output=True,
        text=True,
        check=True,
        timeout=int(cfg.get("pipeline.llm_timeout_seconds", 600)),
    )
    return result.stdout


def _call_opencode(prompt: str, cfg, *, timeout: int, effort: str, model: str | None = None) -> str:
    """Call OpenCode Zen. Muse Spark / GPT / Grok IDs use the Responses API,
    everything else on the gateway uses chat/completions."""
    key = opencode_api_key()
    if not key:
        raise RuntimeError("OPENCODE_API_KEY is not set")
    from openai import OpenAI

    _opencode_limiter_for(cfg).acquire()
    model = (model or str(cfg.get("pipeline.model") or OPENCODE_DEFAULT_MODEL)).strip()
    base_url = str(cfg.get("pipeline.opencode.base_url") or OPENCODE_DEFAULT_URL).rstrip("/")
    temperature = float(cfg.get("pipeline.opencode.temperature", 1.0 if effort == "high" else 0.3))
    top_p = float(cfg.get("pipeline.opencode.top_p", 0.95))
    max_tokens = int(cfg.get("pipeline.opencode.max_tokens", 16384))

    client = OpenAI(
        base_url=base_url,
        api_key=key,
        timeout=timeout,
        default_headers={
            "User-Agent": _USER_AGENT,
            "x-opencode-session": _SESSION_ID,
        },
    )
    log.info("opencode %s (%s)", model, effort)
    last_error = None
    for attempt in range(3):
        try:
            if model.lower().startswith(_OPENCODE_RESPONSES_PREFIXES):
                # Muse Spark always runs at max reasoning effort.
                reasoning = (
                    {"effort": "xhigh"} if model.lower().startswith("muse-spark") else None
                )
                resp = client.responses.create(
                    model=model,
                    input=prompt,
                    temperature=temperature,
                    top_p=top_p,
                    max_output_tokens=max_tokens,
                    **({"reasoning": reasoning} if reasoning else {}),
                )
                return _responses_text(resp)
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
            return _nvidia_message_text(completion)
        except Exception as exc:
            last_error = exc
            if _transient_error(exc):
                wait = 2.0 * (attempt + 1)
                log.warning("opencode transient error, retry in %.1fs", wait)
                time.sleep(wait)
                continue
            raise
    raise last_error or RuntimeError("opencode request failed")


def _responses_text(resp) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return text
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            chunk = getattr(block, "text", None)
            if chunk:
                parts.append(chunk)
    return "".join(parts)


def _call_nvidia(prompt: str, cfg, *, timeout: int, effort: str, model: str | None = None) -> str:
    key = nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set")
    from openai import OpenAI

    _limiter_for(cfg).acquire()
    model = (model or str(cfg.get("pipeline.model") or NVIDIA_DEFAULT_MODEL)).strip()
    is_deepseek = "deepseek" in model.lower()
    base_url = str(cfg.get("pipeline.nvidia.base_url") or NVIDIA_DEFAULT_URL).rstrip("/")
    temperature = float(cfg.get("pipeline.nvidia.temperature", 1.0 if effort == "high" else 0.3))
    top_p = float(cfg.get("pipeline.nvidia.top_p", 0.95))
    max_tokens = int(cfg.get("pipeline.nvidia.max_tokens", 16384))

    if is_deepseek:
        stream = False
        extra_body = {"chat_template_kwargs": {"thinking": True, "reasoning_effort": "high"}}
    else:
        # Nemotron 3 Ultra (default) and Gemma 4 both use stream=True and enable_thinking=True
        stream = True
        extra_body = {"chat_template_kwargs": {"enable_thinking": True}}

    client = OpenAI(base_url=base_url, api_key=key, timeout=timeout)
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": stream,
        "extra_body": extra_body,
    }
    log.info("NVIDIA %s (%s)", model, effort)
    last_error = None
    for attempt in range(3):
        try:
            completion = client.chat.completions.create(**kwargs)
            if stream:
                return _nvidia_stream_text(completion)
            return _nvidia_message_text(completion)
        except Exception as exc:
            last_error = exc
            if _transient_error(exc):
                wait = 2.0 * (attempt + 1)
                log.warning("NVIDIA transient error, retry in %.1fs", wait)
                time.sleep(wait)
                continue
            raise
    raise last_error or RuntimeError("NVIDIA request failed")


def _nvidia_stream_text(completion) -> str:
    parts: list[str] = []
    for chunk in completion:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if getattr(delta, "content", None):
            parts.append(delta.content)
    return "".join(parts)


def _nvidia_message_text(completion) -> str:
    if not completion.choices:
        return ""
    message = completion.choices[0].message
    return (getattr(message, "content", None) or "")
