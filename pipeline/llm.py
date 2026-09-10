"""LLM calls: Nemotron 3 Ultra, DeepSeek V4 Flash, Gemma 4, then agy/Gemini."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time

from pipeline.config import load_config

log = logging.getLogger(__name__)

NVIDIA_DEFAULT_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
NVIDIA_FALLBACK_MODELS = [
    "deepseek-ai/deepseek-v4-flash-0731",
    "google/gemma-4-31b-it",
]


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


def nvidia_api_key() -> str:
    return (os.environ.get("NVIDIA_API_KEY") or "").strip()


def primary_provider(cfg=None) -> str:
    cfg = cfg or load_config()
    raw = (cfg.get("pipeline.provider") or "").strip().lower()
    if raw in {"nvidia", "agy"}:
        return raw
    model = str(cfg.get("pipeline.model") or "")
    if _looks_like_nvidia_model(model):
        return "nvidia"
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


def _looks_like_nvidia_model(model: str) -> bool:
    lower = model.lower()
    return (
        lower.startswith("nvidia/")
        or lower.startswith("deepseek-ai/")
        or "nemotron" in lower
        or "deepseek" in lower
    )


_last_used_model: str = ""


def get_last_used_model() -> str:
    global _last_used_model
    return _last_used_model


def complete_prompt(prompt: str, *, effort: str = "high") -> str:
    """Return model text. Tries Nemotron 3 Ultra, then DeepSeek V4 Flash, then Nemotron 3.5 Lightning, then agy."""
    global _last_used_model
    cfg = load_config()
    timeout = int(cfg.get("pipeline.llm_timeout_seconds", 600))
    primary = primary_provider(cfg)
    last = (cfg.get("pipeline.fallback_provider") or "agy").strip().lower()
    if primary == "nvidia":
        text, model_name = _try_nvidia_models_with_model(prompt, cfg, timeout=timeout, effort=effort)
        if text.strip():
            _last_used_model = model_name
            return text
        if last == "agy":
            log.warning("NVIDIA models failed; falling back to agy")
            _last_used_model = str(cfg.get("pipeline.fallback_model") or "gemini-3.1-pro")
            return call_agy(prompt, effort=effort)
        return ""
    try:
        _last_used_model = str(cfg.get("pipeline.model") or "gemini-3.1-pro")
        return call_agy(prompt, effort=effort)
    except Exception as exc:
        log.warning("agy failed (%s)", exc)
        if last == "nvidia" or nvidia_api_key():
            text, model_name = _try_nvidia_models_with_model(prompt, cfg, timeout=timeout, effort=effort)
            _last_used_model = model_name
            return text
        raise


def _is_tailor_prompt_truncated(prompt: str, text: str) -> bool:
    """Check if tailor prompt response was truncated before finishing all sections."""
    if not text or not text.strip():
        return True
    if "<TITLE>" in prompt or "Return ONLY these tagged blocks" in prompt:
        if "<TITLE>" in text and not any(tag in text for tag in ("</ANALYSIS>", "</WHY_I_FIT>", "</COVER_LETTER>")):
            return True
    return False


def _try_nvidia_models_with_model(prompt: str, cfg, *, timeout: int, effort: str) -> tuple[str, str]:
    for model in nvidia_model_chain(cfg):
        try:
            text = _call_nvidia(prompt, cfg, timeout=timeout, effort=effort, model=model)
            if text.strip():
                if _is_tailor_prompt_truncated(prompt, text):
                    log.warning("NVIDIA %s returned truncated output (missing closing tags) — falling back to next model", model)
                    continue
                return text, model
            log.warning("NVIDIA %s returned empty output", model)
        except Exception as exc:
            log.warning("NVIDIA %s failed (%s)", model, exc)
    return "", ""


def _try_nvidia_models(prompt: str, cfg, *, timeout: int, effort: str) -> str:
    text, _ = _try_nvidia_models_with_model(prompt, cfg, timeout=timeout, effort=effort)
    return text


def call_agy(prompt: str, effort: str = "high") -> str:
    cfg = load_config()
    model = cfg.get("pipeline.model") or "gemini-3.1-pro"
    if primary_provider(cfg) == "nvidia":
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
            name = type(exc).__name__
            if "RateLimit" in name or "429" in str(exc):
                wait = 2.0 * (attempt + 1)
                log.warning("NVIDIA rate limit, retry in %.1fs", wait)
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
