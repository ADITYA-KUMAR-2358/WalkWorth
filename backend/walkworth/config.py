"""Loads walkworth.config.toml from the repo root, plus secrets from .env.

Keys never live in the TOML file. They come from the environment, which a
`.env` file at the repo root fills in for local runs (Render sets them in its
dashboard instead). Real environment variables win over `.env`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "walkworth.config.toml"
ENV_PATH = REPO_ROOT / ".env"


def load_dotenv(path: Path = ENV_PATH) -> None:
    """Minimal KEY=value reader: comments, blank lines and quotes handled."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    llm_model: str
    vision_model: str
    stt_model: str
    # Any OpenAI-compatible chat endpoint: Gemini's, or a local Ollama.
    llm_base_url: str
    llm_timeout_s: int
    llm_max_rpm: int          # 0 = no client-side limit
    llm_max_wait_s: int       # longest we wait out 429s before giving up
    llm_thinking_level: str   # "" = provider default
    llm_max_per_day: int      # 0 = no daily cap
    submit_limit: int         # quota-spending requests per visitor per 10 min; 0 = off
    elevenlabs_base_url: str
    tts_model: str
    tts_voice_id: str
    temporal_address: str
    temporal_namespace: str
    data_dir: Path
    database: Path
    timezone: str
    trim_ends_m: int
    publish_base_url: str
    # Secrets: kept out of repr so they never land in a log line.
    llm_api_key: str = field(default="", repr=False)
    elevenlabs_api_key: str = field(default="", repr=False)
    temporal_api_key: str = field(default="", repr=False)
    # Optional: when set, starting a walk or an import needs this code.
    access_code: str = field(default="", repr=False)


@lru_cache
def load() -> Config:
    load_dotenv()
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    env = os.environ.get
    data_dir = Path(env("WALKWORTH_DATA_DIR") or REPO_ROOT / raw["storage"]["data_dir"])
    llm_model = env("LLM_MODEL") or raw["models"]["llm"]
    return Config(
        llm_model=llm_model,
        vision_model=env("LLM_VISION_MODEL") or env("LLM_MODEL") or raw["models"]["vision"],
        stt_model=env("ELEVENLABS_STT_MODEL") or raw["models"]["stt"],
        llm_base_url=(env("LLM_BASE_URL") or raw["llm"]["base_url"]).rstrip("/"),
        llm_timeout_s=_int("LLM_TIMEOUT", 240),
        llm_max_rpm=_int("LLM_MAX_RPM", 0),
        llm_max_wait_s=_int("LLM_MAX_WAIT", 45),
        llm_thinking_level=(env("LLM_THINKING_LEVEL") or "").strip(),
        llm_max_per_day=_int("LLM_MAX_PER_DAY", 0),
        submit_limit=_int("SUBMIT_LIMIT", 0),
        elevenlabs_base_url=raw["elevenlabs"]["base_url"].rstrip("/"),
        tts_model=env("ELEVENLABS_TTS_MODEL") or "",
        tts_voice_id=env("ELEVENLABS_VOICE_ID") or "",
        temporal_address=env("TEMPORAL_ADDRESS") or raw["temporal"]["address"],
        temporal_namespace=env("TEMPORAL_NAMESPACE") or raw["temporal"]["namespace"],
        data_dir=data_dir,
        database=data_dir / "walkworth.sqlite3",
        timezone=raw["display"]["timezone"],
        trim_ends_m=raw["publish"]["trim_ends_m"],
        publish_base_url=(env("PUBLIC_BASE_URL") or raw["publish"].get("base_url") or env("RENDER_EXTERNAL_URL") or "").rstrip("/"),
        # GEMINI_API_KEY still works, from before LLM_* existed.
        llm_api_key=env("LLM_API_KEY") or env("GEMINI_API_KEY") or "",
        elevenlabs_api_key=env("ELEVENLABS_API_KEY", ""),
        temporal_api_key=env("TEMPORAL_API_KEY", ""),
        access_code=(env("ACCESS_CODE") or "").strip(),
    )


# Media paths are stored as text. Files under the data directory are stored as
# "data/<rest>" whatever WALKWORTH_DATA_DIR is; everything else (the sample's
# fixtures) is relative to the repo root.
DATA_PREFIX = "data/"


def store_path(path: Path) -> str:
    path = path.resolve()
    data = load().data_dir.resolve()
    if path.is_relative_to(data):
        return DATA_PREFIX + path.relative_to(data).as_posix()
    return path.relative_to(REPO_ROOT).as_posix()


def media_path(stored: str) -> Path:
    if stored.startswith(DATA_PREFIX):
        return load().data_dir / stored[len(DATA_PREFIX):]
    return REPO_ROOT / stored
