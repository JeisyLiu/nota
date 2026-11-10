"""Composition root: wires concrete adapters to use cases once per process."""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.clock import Clock
from ..infrastructure.db import Database
from ..infrastructure.llm.base import build_model
from ..infrastructure.stt.base import build_stt
from .agent_harness import AgentHarness
from .memory_service import MemoryService
from .pipeline import Pipeline
from .quota_service import QuotaService
from .tools import ToolRegistry

log = logging.getLogger("nota.container")


@dataclass
class AppContainer:
    settings: object
    db: Database
    clock: Clock
    quota: QuotaService
    memory: MemoryService
    registry: ToolRegistry
    harness: AgentHarness
    stt: object
    model: object
    pipeline: Pipeline

    @classmethod
    def build(cls, settings, *, start_worker: bool = True) -> "AppContainer":
        from ..domain.quota import QuotaPolicy

        clock = Clock()
        db = Database(settings.db_path)
        db.init_schema()

        quota = QuotaService(db, QuotaPolicy(free_daily_limit=settings.free_daily_limit), clock)
        memory = MemoryService(db, clock)
        model = build_model(settings)
        registry = ToolRegistry(clock, Path(settings.data_dir), memory, settings)
        harness = AgentHarness(model, registry, clock, settings)
        stt = build_stt(settings)
        pipeline = Pipeline(db=db, settings=settings, clock=clock, quota=quota, memory=memory,
                            harness=harness, stt=stt, model=model)

        container = cls(settings=settings, db=db, clock=clock, quota=quota, memory=memory,
                        registry=registry, harness=harness, stt=stt, model=model,
                        pipeline=pipeline)
        if start_worker:
            pipeline.start()
        _preload_stream_model(settings, stt)
        log.info("nota ready: provider=%s stt=%s db=%s", model.name, stt.name, settings.db_path)
        return container

    def use_clock(self, clock: Clock) -> None:
        """Replace the clock on every collaborator.

        Relative dates ("tomorrow at three") make any test that runs against the
        real clock start failing once the wall clock rolls over midnight. Tests
        and the eval suite pin a fixed instant through this instead of
        monkey-patching each service.
        """
        self.clock = clock
        for collaborator in (self.quota, self.memory, self.registry, self.harness,
                             self.pipeline):
            if hasattr(collaborator, "_clock"):
                collaborator._clock = clock


def _preload_stream_model(settings, stt) -> None:
    """Warm the live-preview model in the background.

    Cold loading it takes ~20 s here; paying that on the first press of the record
    button looks like the feature is broken. Failures are ignored on purpose: the
    preview is optional, and a machine without faster-whisper should still start.
    """
    if getattr(stt, "name", "") != "faster_whisper":
        return

    def run() -> None:
        try:
            from ..infrastructure.stt.streaming import preload

            preload(
                model_size=settings.stt_stream_model,
                local_pylibs=os.environ.get("NOTA_PYLIB_DIR", ""),
                download_root=str(Path(settings.data_dir) / "models"),
            )
            log.info("stream model ready: %s", settings.stt_stream_model)
        except Exception as exc:  # noqa: BLE001 - preview is optional
            log.warning("stream model unavailable: %s", exc)

    threading.Thread(target=run, name="nota-stream-preload", daemon=True).start()
