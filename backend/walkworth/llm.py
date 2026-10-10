"""Gemma through any OpenAI-compatible chat endpoint.

Default: Google's Gemini API (LLM_BASE_URL=.../v1beta/openai, key in
LLM_API_KEY). A local Ollama works too (http://localhost:11434/v1).

Free tiers are rate limited, so every call goes through a process-wide
throttle (LLM_MAX_RPM), 429s are waited out for at most LLM_MAX_WAIT seconds,
and LLM_MAX_PER_DAY caps the calls a public demo can make in a day.

Structured calls describe the JSON schema in the prompt and ask for
`response_format: json_object`; if the endpoint refuses an option (JSON mode,
reasoning_effort) for this model we drop it and remember that. Callers
validate the JSON with Pydantic.
"""

from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
from datetime import date
from pathlib import Path

import httpx
from PIL import Image

from . import config

_lock = threading.Lock()
_next_slot = 0.0
# Options a given model refused; we stop sending them.
_refused: dict[str, set[str]] = {}


class ModelError(RuntimeError):
    pass


class DailyLimit(ModelError):
    pass


def _log_speed(model: str, task: str, tokens: int | None, seconds: float) -> None:
    """Keep tokens/second per call; the DEV post compares Gemma sizes with it."""
    if not tokens or seconds <= 0:
        return
    path = config.load().data_dir / "model_stats.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "model": model, "task": task, "tokens": tokens,
                            "tokens_per_s": round(tokens / seconds, 2)}) + "\n")


def _check_budget() -> None:
    """Refuse once today's LLM_MAX_PER_DAY answers have been used."""
    cfg = config.load()
    if cfg.llm_max_per_day > 0 and calls_today()["used"] >= cfg.llm_max_per_day:
        raise DailyLimit(f"Walkworth has used today's {cfg.llm_max_per_day} model calls. Try again tomorrow.")


def _spend() -> None:
    """Count one answered call. Refusals and 429s cost no quota, so they don't count."""
    cfg = config.load()
    path = cfg.data_dir / "llm_calls.json"
    today = date.today().isoformat()
    with _lock:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        count = state.get("count", 0) if state.get("date") == today else 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": today, "count": count + 1}), encoding="utf-8")


def calls_today() -> dict:
    cfg = config.load()
    try:
        state = json.loads((cfg.data_dir / "llm_calls.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    used = state.get("count", 0) if state.get("date") == date.today().isoformat() else 0
    return {"used": used, "limit": cfg.llm_max_per_day}


def _throttle() -> None:
    """Space call starts so we never exceed LLM_MAX_RPM, across all threads."""
    global _next_slot
    rpm = config.load().llm_max_rpm
    if rpm <= 0:
        return
    with _lock:
        now = time.monotonic()
        wait = max(0.0, _next_slot - now)
        _next_slot = max(now, _next_slot) + 60.0 / rpm
    if wait:
        time.sleep(wait)


def _retry_after(r: httpx.Response, attempt: int) -> float:
    try:
        return max(1.0, float(r.headers.get("retry-after", "")))
    except ValueError:
        return float(min(30, 4 * 2 ** attempt))


def _json_text(text: str) -> str:
    """The JSON object in a reply, without ```json fences or chatter around it."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else text


def chat(model: str, system: str, user: str, *, schema: dict | None = None,
         images: list[bytes] | None = None, task: str = "chat", temperature: float = 0.3) -> str:
    cfg = config.load()
    if not cfg.llm_api_key:
        raise ModelError("LLM_API_KEY isn't set. Add it to .env (local) or the Render dashboard, then restart.")
    if schema is not None:
        user += ("\n\nAnswer with one JSON object only, no other text, matching this JSON Schema:\n"
                 + json.dumps(schema, separators=(",", ":")))
    content: str | list[dict] = user
    if images:
        content = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(i).decode()}}
                   for i in images] + [{"type": "text", "text": user}]
    body: dict = {"model": model, "temperature": temperature,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    refused = _refused.setdefault(model, set())
    if schema is not None and "response_format" not in refused:
        body["response_format"] = {"type": "json_object"}
    if cfg.llm_thinking_level and "reasoning_effort" not in refused:
        body["reasoning_effort"] = cfg.llm_thinking_level

    url = f"{cfg.llm_base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.llm_api_key}"}
    waited = 0.0
    attempt = 0
    while True:
        _check_budget()
        _throttle()
        started = time.monotonic()
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=cfg.llm_timeout_s)
        except httpx.TimeoutException as e:
            raise ModelError(f"{model} took longer than {cfg.llm_timeout_s} s (LLM_TIMEOUT).") from e
        except httpx.HTTPError as e:
            raise ModelError(f"Couldn't reach {cfg.llm_base_url} ({e.__class__.__name__}). Check LLM_BASE_URL and the connection.") from e

        if r.status_code == 400:
            # An option this model doesn't take: drop it and ask again.
            text = r.text.lower()
            dropped = [k for k, hint in (("response_format", "json"), ("reasoning_effort", "reason")) if k in body and (k in text or hint in text)]
            if dropped:
                for k in dropped:
                    body.pop(k)
                    refused.add(k)
                continue
        if r.status_code in (429, 500, 502, 503) and attempt < 6:
            pause = _retry_after(r, attempt)
            if waited + pause > cfg.llm_max_wait_s:
                if r.status_code == 429:
                    raise ModelError(f"The model's rate limit held for over {cfg.llm_max_wait_s} s (LLM_MAX_WAIT). "
                                     "Temporal will retry this step; or lower LLM_MAX_RPM.")
            else:
                time.sleep(pause)
                waited += pause
                attempt += 1
                continue
        break

    if r.status_code == 404:
        raise ModelError(f"{cfg.llm_base_url} doesn't serve {model}. Check LLM_MODEL.")
    if r.status_code in (401, 403):
        raise ModelError("The model endpoint refused the key. Check LLM_API_KEY.")
    if r.status_code != 200:
        raise ModelError(f"The model endpoint answered {r.status_code}: {r.text[:300]}")

    _spend()
    data = r.json()
    choices = data.get("choices") or []
    if not choices:
        raise ModelError(f"{model} returned no answer.")
    text = choices[0].get("message", {}).get("content") or ""
    # Some endpoints inline the model's thinking; the answer follows it.
    text = re.sub(r"<thought>.*?</thought>|<think>.*?</think>", "", text, flags=re.S).strip()
    _log_speed(model, task, (data.get("usage") or {}).get("completion_tokens"), time.monotonic() - started)
    return _json_text(text) if schema is not None else text


def image_for_model(path: Path, max_px: int = 896) -> bytes:
    """Gemma sees 896 px squares; sending more only costs upload time."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_px, max_px))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def status() -> dict:
    """For /api/health: is a key set (never the key itself), and today's budget."""
    cfg = config.load()
    return {"llm_key": bool(cfg.llm_api_key), "elevenlabs_key": bool(cfg.elevenlabs_api_key),
            "calls_today": calls_today()}
