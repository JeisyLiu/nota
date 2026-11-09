"""Entry point: `python -m backend.main` (or `python backend/main.py`)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python backend/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from backend.config import load_settings
from backend.interfaces.app import create_app
from backend.infrastructure.llm.base import ProviderError


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = load_settings()

    try:
        app = create_app(settings)
    except ProviderError as exc:
        # A misconfigured .env is a user action to take, not a crash. Print the
        # checklist and exit 2 so a wrapper script can tell it apart from a bug.
        print("\n[nota] 启动失败：模型配置有问题\n", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("\n提示：填好 .env 后重启；不确定就先跑 LLM_PROVIDER=scripted 验证链路。\n",
              file=sys.stderr)
        return 2

    for warning in settings.llm_warnings():
        print(f"[nota] 注意：{warning}")

    mode = "demo (no model key)" if settings.demo_mode else settings.llm_provider
    print(
        f"[nota] http://{settings.host}:{settings.port}  "
        f"provider={mode}  model={settings.model_label}  stt={settings.stt_provider}"
    )
    if not settings.demo_mode:
        print(f"[nota] 首次调用会真实计费，请在测试时注意用量。")

    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
