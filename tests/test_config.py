"""Configuration behaviour: where .env is read from, and how bad keys surface.

These exist because the failure modes here are silent: a `.env` that is not
found looks exactly like a `.env` with no key in it, and both look like "demo
mode" until a user wonders why the model never answers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import Settings, load_settings  # noqa: E402
from backend.infrastructure.llm.base import ProviderError, build_model  # noqa: E402

_LLM_VARS = ("LLM_PROVIDER", "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL",
             "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "LLM_TRUST_ENV")


@pytest.fixture()
def clean_env(monkeypatch):
    """Blank every model variable so the developer's own .env cannot leak in."""
    for name in _LLM_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(root=tmp_path, db_path=tmp_path / "t.db", data_dir=tmp_path / "d")
    base.update(overrides)
    return Settings(**base)


# --------------------------------------------------------------------- defaults
def test_defaults_to_scripted_so_a_fresh_clone_needs_no_key(clean_env, tmp_path):
    settings = load_settings(tmp_path, load_env=False)
    assert settings.llm_provider == "scripted"
    assert settings.demo_mode is True
    assert settings.llm_problems() == []


def test_relative_paths_resolve_against_the_repo_root_not_the_cwd(clean_env, tmp_path):
    # A relative DB_PATH must mean "inside the project", otherwise launching from
    # C:\ or from a parent folder silently creates a second empty database.
    settings = load_settings(tmp_path, load_env=False)
    assert settings.db_path == (tmp_path / "data" / "nota.db").resolve()
    assert settings.data_dir == (tmp_path / "data").resolve()


# ------------------------------------------------------------------- .env loading
def test_dotenv_is_read_from_the_repo_root(clean_env, tmp_path):
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=openai\nOPENAI_MODEL=probe-model\nOPENAI_API_KEY=sk-test-abc\n",
        encoding="utf-8",
    )
    settings = load_settings(tmp_path)
    assert settings.llm_provider == "openai"
    assert settings.openai_model == "probe-model"
    assert settings.openai_api_key == "sk-test-abc"


def test_real_environment_wins_over_the_dotenv_file(clean_env, tmp_path):
    # CI injects keys as real env vars; those must not be shadowed by a stale file.
    (tmp_path / ".env").write_text("OPENAI_MODEL=from-file\n", encoding="utf-8")
    clean_env.setenv("OPENAI_MODEL", "from-env")
    settings = load_settings(tmp_path)
    assert settings.openai_model == "from-env"


# ------------------------------------------------------------------ problem lists
def test_openai_without_a_key_reports_actionable_problems(clean_env, tmp_path):
    settings = _settings(tmp_path, llm_provider="openai", openai_model="m")
    problems = settings.llm_problems()
    assert any("OPENAI_API_KEY" in p for p in problems)
    assert settings.api_key_configured is False


def test_openai_without_a_model_reports_it_separately(clean_env, tmp_path):
    settings = _settings(tmp_path, llm_provider="openai", openai_api_key="sk-x")
    assert any("OPENAI_MODEL" in p for p in settings.llm_problems())


def test_every_missing_value_is_reported_at_once(clean_env, tmp_path):
    # One restart per missing variable would be a bad experience.
    settings = _settings(tmp_path, llm_provider="openai")
    assert len(settings.llm_problems()) >= 2


def test_unknown_provider_is_rejected_explicitly(clean_env, tmp_path):
    settings = _settings(tmp_path, llm_provider="gpt5")
    problems = settings.llm_problems()
    assert len(problems) == 1
    assert "不认识" in problems[0]


def test_a_complete_configuration_has_no_problems(clean_env, tmp_path):
    settings = _settings(tmp_path, llm_provider="openai",
                        openai_api_key="sk-real", openai_model="deepseek-chat")
    assert settings.llm_problems() == []
    assert settings.api_key_configured is True
    assert settings.model_label == "deepseek-chat"


# --------------------------------------------------------------------- build_model
def test_build_model_refuses_to_start_with_a_missing_key(clean_env, tmp_path):
    settings = _settings(tmp_path, llm_provider="openai", openai_model="m")
    with pytest.raises(ProviderError) as excinfo:
        build_model(settings)
    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    # The message must point at the file to edit, not just at the variable.
    assert ".env" in message


def test_build_model_still_returns_scripted_without_any_configuration(clean_env, tmp_path):
    model = build_model(_settings(tmp_path, llm_provider="scripted"))
    assert model.name == "scripted"


def test_scripted_mode_warns_that_it_is_not_a_real_model(clean_env, tmp_path):
    warnings = _settings(tmp_path, llm_provider="scripted").llm_warnings()
    assert any("demo" in w for w in warnings)


def test_openai_without_base_url_warns_about_the_default_endpoint(clean_env, tmp_path):
    settings = _settings(tmp_path, llm_provider="openai",
                         openai_api_key="sk-real", openai_model="m")
    assert any("api.openai.com" in w for w in settings.llm_warnings())


# ------------------------------------------------------------------------ secrets
def test_no_secret_is_ever_exposed_through_convenience_properties(clean_env, tmp_path):
    secret = "sk-super-secret-value"
    settings = _settings(tmp_path, llm_provider="openai",
                         openai_api_key=secret, openai_model="m")
    # These are what /healthz and the logs are built from.
    assert secret not in settings.model_label
    assert secret not in " ".join(settings.llm_problems())
    assert secret not in " ".join(settings.llm_warnings())
    assert secret not in str(int(settings.api_key_configured))
