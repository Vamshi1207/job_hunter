"""LLM calls: Nemotron, then NVIDIA gpt-oss, then agy/Gemini."""

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
NVIDIA_FALLBACK_MODEL = "openai/gpt-oss-120b"


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
    if model.startswith("nvidia/") or "nemotron" in model.lower():
        return "nvidia"
    return "agy"


def nvidia_model_chain(cfg=None) -> list[str]:
    """Primary NIM model, then gpt-oss, skipping duplicates."""
    cfg = cfg or load_config()
    primary = str(cfg.get("pipeline.model") or NVIDIA_DEFAULT_MODEL).strip()
    if not _looks_like_nvidia_model(primary):
        primary = NVIDIA_DEFAULT_MODEL
    mid = str(cfg.get("pipeline.nvidia.fallback_model") or NVIDIA_FALLBACK_MODEL).strip()
    models = [primary]
    if mid and mid != primary:
        models.append(mid)
    return models


def _looks_like_nvidia_model(model: str) -> bool:
    lower = model.lower()
    return (
        lower.startswith("nvidia/")
        or lower.startswith("openai/")
        or "nemotron" in lower
        or "gpt-oss" in lower
    )


def complete_prompt(prompt: str, *, effort: str = "high") -> str:
    """Return model text. Tries Nemotron, then NVIDIA gpt-oss, then agy."""
    cfg = load_config()
    timeout = int(cfg.get("pipeline.llm_timeout_seconds", 600))
    primary = primary_provider(cfg)
    last = (cfg.get("pipeline.fallback_provider") or "agy").strip().lower()
    if primary == "nvidia":
        text = _try_nvidia_models(prompt, cfg, timeout=timeout, effort=effort)
        if text.strip():
            return text
        if last == "agy":
            log.warning("NVIDIA models failed; falling back to agy")
            return call_agy(prompt, effort=effort)
        return ""
    try:
        return call_agy(prompt, effort=effort)
    except Exception as exc:
        log.warning("agy failed (%s)", exc)
        if last == "nvidia" or nvidia_api_key():
            return _try_nvidia_models(prompt, cfg, timeout=timeout, effort=effort)
        raise


def _try_nvidia_models(prompt: str, cfg, *, timeout: int, effort: str) -> str:
    for model in nvidia_model_chain(cfg):
        try:
            text = _call_nvidia(prompt, cfg, timeout=timeout, effort=effort, model=model)
            if text.strip():
                return text
            log.warning("NVIDIA %s returned empty output", model)
        except Exception as exc:
            log.warning("NVIDIA %s failed (%s)", model, exc)
    return ""


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
    gpt_oss = "gpt-oss" in model.lower()
    base_url = str(cfg.get("pipeline.nvidia.base_url") or NVIDIA_DEFAULT_URL).rstrip("/")
    temperature = float(cfg.get("pipeline.nvidia.temperature", 1 if effort == "high" else 0.3))
    if gpt_oss:
        top_p = float(cfg.get("pipeline.nvidia.fallback_top_p", 1))
        raw_max = cfg.get("pipeline.nvidia.fallback_max_tokens")
        max_tokens = int(raw_max if raw_max is not None else cfg.get("pipeline.nvidia.max_tokens", 16384))
        stream = False
        thinking = False
    else:
        top_p = float(cfg.get("pipeline.nvidia.top_p", 0.95))
        max_tokens = int(cfg.get("pipeline.nvidia.max_tokens", 16384))
        stream = True
        thinking = bool(cfg.get("pipeline.nvidia.enable_thinking", True))
    client = OpenAI(base_url=base_url, api_key=key, timeout=timeout)
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if thinking:
        kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}
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
