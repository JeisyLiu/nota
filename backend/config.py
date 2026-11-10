"""Runtime configuration.

Everything comes from environment variables, optionally loaded from the `.env`
file at the repository root. No secret ever lives in the repository: `.env` is
git-ignored and `.env.example` only carries the variable names.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv is in requirements, but the app must work without it too
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None  # type: ignore[assignment]


class ConfigError(RuntimeError):
    """Configuration is structurally wrong in a way the user must fix."""


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


def _env_float(name: str, default: float) -> float:
    raw = _env(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


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
    llm_timeout_seconds: float = 60.0
    # httpx honours HTTP(S)_PROXY from the environment when this is true. Kept
    # configurable because a machine-wide proxy silently breaks local calls.
    llm_trust_env: bool = True

    stt_provider: str = "faster_whisper"
    whisper_model: str = "small"
    # Model used for the live preview while recording. Smaller than
    # whisper_model on purpose: the preview is re-run every fraction of a second
    # over the audio so far, so it has to be fast enough to keep up with speech.
    stt_stream_model: str = "base"

    free_daily_limit: int = 10
    host: str = "127.0.0.1"
    port: int = 8787
    prompt_version: str = "v3"
    # Seed one admin and five demo users (plus a fortnight of activity) so the
    # console has something to show. Tests and the eval suite switch this off.
    seed_demo_users: bool = True

    # Tool calls that cannot be undone are parked here and need a human "yes".
    approval_timeout_seconds: int = 600
    max_agent_turns: int = 6
    job_max_attempts: int = 3
    step_timeout_seconds: int = 90
    retry_backoff_seconds: tuple[int, ...] = (1, 4, 15)

    # ------------------------------------------------------------------ derived
    @property
    def demo_mode(self) -> bool:
        """True when no real model is configured."""
        return self.llm_provider == "scripted"

    @property
    def api_key_configured(self) -> bool:
        """Whether the credential for the selected provider is present.

        Never returns the key itself, so it is safe to expose over /healthz.
        """
        if self.llm_provider == "openai":
            return bool(self.openai_api_key and self.openai_model)
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return False

    @property
    def model_label(self) -> str:
        """Human-readable model name for logs and the UI badge."""
        if self.llm_provider == "openai":
            return self.openai_model or "(未填写 OPENAI_MODEL)"
        if self.llm_provider == "anthropic":
            return self.anthropic_model or "claude-sonnet-4-5"
        return "local"

    def llm_problems(self) -> list[str]:
        """Return every reason the selected provider cannot run.

        Collected as a list rather than raised one at a time so the user can fix
        all of them in one edit instead of restarting once per missing value.
        """
        p = self.llm_provider
        problems: list[str] = []

        if p not in ("scripted", "openai", "anthropic"):
            problems.append(
                f"LLM_PROVIDER={p!r} 不认识。可选值：scripted | openai | anthropic"
            )
            return problems

        if p == "openai":
            if not self.openai_api_key:
                problems.append("OPENAI_API_KEY 是空的 —— 去 .env 里填你的 key")
            elif not self.openai_api_key.startswith(("sk-", "gsk_", "sess-")):
                # Not fatal (some gateways use custom prefixes) — surfaced as a
                # hint because a pasted-in placeholder is the usual cause.
                problems.append(
                    "OPENAI_API_KEY 看起来不像一个真实 key（通常以 sk- 开头）。"
                    "确认你没有把示例值或空格粘进去"
                )
            if not self.openai_model:
                problems.append("OPENAI_MODEL 是空的 —— 例如 deepseek-chat / gpt-4o-mini")
        elif p == "anthropic":
            if not self.anthropic_api_key:
                problems.append("ANTHROPIC_API_KEY 是空的 —— 去 .env 里填你的 key")

        return problems

    def llm_warnings(self) -> list[str]:
        """Non-fatal things worth printing once at startup."""
        warnings: list[str] = []
        if self.llm_provider == "openai" and not self.openai_base_url:
            warnings.append(
                "OPENAI_BASE_URL 未设置，将请求 https://api.openai.com/v1。"
                "如果你用的是 DeepSeek / 月之暗面 / 智谱 / 通义，请填对应的 base_url"
            )
        if self.llm_provider == "scripted":
            warnings.append(
                "当前是 demo 模式：使用确定性本地规划器，不走任何模型。"
                "要接真模型，把 .env 里的 LLM_PROVIDER 改成 openai 或 anthropic"
            )
        return warnings

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "audio").mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings(root: Path | None = None, *, load_env: bool = True) -> Settings:
    repo_root = Path(root) if root else Path(__file__).resolve().parent.parent

    # Load the repository's .env explicitly instead of letting dotenv search from
    # the current working directory: `python -m backend.main` and
    # `python backend/main.py` from a different cwd must behave identically.
    # `override=False` keeps real environment variables authoritative, so CI can
    # inject a key without editing the file.
    if load_env and load_dotenv is not None:
        env_file = repo_root / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

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
        llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 60.0),
        llm_trust_env=_env_bool("LLM_TRUST_ENV", True),
        stt_provider=_env("STT_PROVIDER", "faster_whisper").lower(),
        whisper_model=_env("WHISPER_MODEL", "small"),
        stt_stream_model=_env("STT_STREAM_MODEL", "base"),
        free_daily_limit=_env_int("FREE_DAILY_LIMIT", 10),
        host=_env("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8787),
        prompt_version=_env("PROMPT_VERSION", "v3"),
        seed_demo_users=_env_bool("SEED_DEMO_USERS", True),
        approval_timeout_seconds=_env_int("APPROVAL_TIMEOUT_SECONDS", 600),
        max_agent_turns=_env_int("MAX_AGENT_TURNS", 6),
        job_max_attempts=_env_int("JOB_MAX_ATTEMPTS", 3),
        step_timeout_seconds=_env_int("STEP_TIMEOUT_SECONDS", 90),
    )
    settings.ensure_dirs()
    return settings
