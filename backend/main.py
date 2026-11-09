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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = load_settings()
    app = create_app(settings)
    mode = "demo (no model key)" if settings.demo_mode else settings.llm_provider
    print(f"[nota] http://{settings.host}:{settings.port}  provider={mode}  stt={settings.stt_provider}")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
