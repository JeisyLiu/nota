"""Runtime configuration.

Everything comes from environment variables (optionally loaded from `.env`).
No secret ever lives in the repository.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv is in requirements, but the app must work without it too
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - optional dependency
    pass


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable view of the process configuration."""

    root: Path
    db_path: Path
    data_dir: Path

    llm_provider: str = "scripted"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""

    stt_provider: str = "faster_whisper"
    whisper_model: str = "small"

    free_daily_limit: int = 10
    host: str = "127.0.0.1"
    port: int = 8787
    prompt_version: str = "v2"

    # Tool calls that cannot be undone are parked here and need a human "yes".
    approval_timeout_seconds: int = 600
    max_agent_turns: int = 6
    job_max_attempts: int = 3
    step_timeout_seconds: int = 90
    retry_backoff_seconds: tuple[int, ...] = (1, 4, 15)

    @property
    def demo_mode(self) -> bool:
        """True when no real model is configured."""
        return self.llm_provider == "scripted"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "audio").mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings(root: Path | None = None) -> Settings:
    repo_root = Path(root) if root else Path(__file__).resolve().parent.parent
    db_raw = _env("DB_PATH", "./data/nota.db")
    data_raw = _env("DATA_DIR", "./data")
    db_path = Path(db_raw)
    data_dir = Path(data_raw)
    if not db_path.is_absolute():
        db_path = (repo_root / db_path).resolve()
    if not data_dir.is_absolute():
        data_dir = (repo_root / data_raw).resolve()

    settings = Settings(
        root=repo_root,
        db_path=db_path,
        data_dir=data_dir,
        llm_provider=_env("LLM_PROVIDER", "scripted").lower(),
        openai_base_url=_env("OPENAI_BASE_URL"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_model=_env("OPENAI_MODEL"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL"),
        stt_provider=_env("STT_PROVIDER", "faster_whisper").lower(),
        whisper_model=_env("WHISPER_MODEL", "small"),
        free_daily_limit=_env_int("FREE_DAILY_LIMIT", 10),
        host=_env("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8787),
        prompt_version=_env("PROMPT_VERSION", "v2"),
        approval_timeout_seconds=_env_int("APPROVAL_TIMEOUT_SECONDS", 600),
        max_agent_turns=_env_int("MAX_AGENT_TURNS", 6),
        job_max_attempts=_env_int("JOB_MAX_ATTEMPTS", 3),
        step_timeout_seconds=_env_int("STEP_TIMEOUT_SECONDS", 90),
    )
    settings.ensure_dirs()
    return settings
