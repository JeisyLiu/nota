"""Pipeline: queued job -> transcript -> agent -> summary, with retries.

Every state change is recorded, and the terminal failure path refunds exactly one
reserved request. Retries are bounded and re-runnable: before the agent runs we
clear the todos/memories previously produced for this note, so a retry never
duplicates the user's tasks.
"""
from __future__ import annotations

import logging
import threading
import time

from ..domain import jobs as jobstates
from ..infrastructure import repos
from ..infrastructure.clock import Clock
from ..infrastructure.db import Database
from .agent_harness import AgentHarness
from .memory_service import MemoryService
from .quota_service import QuotaService

log = logging.getLogger("nota.pipeline")


class Pipeline:
    def __init__(self, *, db: Database, settings, clock: Clock, quota: QuotaService,
                 memory: MemoryService, harness: AgentHarness, stt, model):
        self._db = db
        self._settings = settings
        self._clock = clock
        self._quota = quota
        self._memory = memory
        self._harness = harness
        self._stt = stt
        self._model = model
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ worker
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nota-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_next()
            except Exception:  # pragma: no cover - the loop must survive
                log.exception("worker iteration failed")
                worked = False
            if not worked:
                self._stop.wait(0.25)

    # ------------------------------------------------------------------ public
    def run_next(self) -> bool:
        """Claim and process one due job. Returns False when the queue is idle."""
        with self._db.session() as conn:
            job = repos.claim_next_job(conn, self._clock)
        if job is None:
            return False
        self.run_job(job["id"])
        return True

    def run_job(self, job_id: str) -> None:
        with self._db.session() as conn:
            job = repos.get_job(conn, job_id)
            if job is None:
                return
            if job["state"] == jobstates.QUEUED:
                self._move(conn, job_id, jobstates.TRANSCRIBING)
            note = repos.get_note(conn, job["note_id"])
            if note is None:
                self._fail(conn, job, "note missing")
                return

            try:
                transcript, lang = note["transcript"], note["lang"]
                if not transcript:
                    if not note["audio_path"]:
                        raise RuntimeError("voice note has no audio file")
                    result = self._stt.transcribe(note["audio_path"])
                    transcript, lang = result.text, result.lang
                    repos.set_transcript(conn, note["id"], transcript, lang)

                self._move(conn, job_id, jobstates.AGENT_RUNNING)

                # retry-safety: this note's derived rows are rebuilt from scratch
                repos.delete_todos_for_note(conn, note["id"])
                self._memory.delete_note(conn, note["id"])

                user = repos.get_user(conn, job["user_id"])
                plan = user["plan"] if user else "free"
                tz = user["tz"] if user else "Asia/Shanghai"

                run_id = repos.insert_agent_run(
                    conn, user_id=job["user_id"], note_id=note["id"],
                    provider=self._model.name, model=getattr(self._model, "model", "local"),
                    prompt_version=self._settings.prompt_version,
                    clock=self._clock,
                )
                started = time.monotonic()
                outcome = self._harness.run(
                    conn, user_id=job["user_id"], note_id=note["id"], transcript=transcript,
                    run_id=run_id, plan=plan, tz=tz, prompt_version=self._settings.prompt_version,
                )
                latency = int((time.monotonic() - started) * 1000)
                repos.finish_agent_run(conn, run_id, status="ok",
                                       tokens_in=outcome.usage.tokens_in,
                                       tokens_out=outcome.usage.tokens_out, latency_ms=latency)

                title = _title_from(transcript)
                repos.upsert_summary(conn, note["id"], title, outcome.summary, self._clock)
                self._memory.index_note(conn, user_id=job["user_id"], note_id=note["id"],
                                        text=transcript)

                self._move(conn, job_id, jobstates.DONE)
            except Exception as exc:  # noqa: BLE001 - any failure must release the quota
                log.warning("job %s failed: %s", job_id, exc)
                self._fail(conn, job, str(exc))

    # ------------------------------------------------------------------ helpers
    def _move(self, conn, job_id: str, dst: str) -> None:
        job = repos.get_job(conn, job_id)
        src = job["state"]
        if src == dst:
            return
        if not jobstates.can_transition(src, dst):
            raise jobstates.InvalidTransition(src, dst)
        repos.set_job_state(conn, job_id, dst, clock=self._clock)

    def _fail(self, conn, job, error: str) -> None:
        attempts = int(job["attempts"] or 0)
        backoff_table = tuple(self._settings.retry_backoff_seconds)
        if attempts < self._settings.job_max_attempts:
            index = max(0, min(attempts - 1, len(backoff_table) - 1)) if backoff_table else 0
            backoff = backoff_table[index] if backoff_table else 0
            repos.set_job_state(
                conn, job["id"], jobstates.QUEUED, error=error[:500],
                next_retry_at=self._clock.plus_seconds_iso(backoff), clock=self._clock,
            )
            return
        repos.set_job_state(conn, job["id"], jobstates.FAILED, error=error[:500], clock=self._clock)
        self._quota.refund(job["user_id"], job["id"])


def _title_from(transcript: str) -> str:
    text = (transcript or "").strip().replace("\n", " ")
    return text[:24] + ("…" if len(text) > 24 else "")
