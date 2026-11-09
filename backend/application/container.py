"""Composition root: wires concrete adapters to use cases once per process."""
from __future__ import annotations

import logging
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
        log.info("nota ready: provider=%s stt=%s db=%s", model.name, stt.name, settings.db_path)
        return container
