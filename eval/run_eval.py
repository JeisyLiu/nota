"""Offline evaluation harness.

Why it exists (this is the "自己做一套评估方法" bonus):

Fifteen Chinese utterances with hand-labelled expectations plus five history
questions. The suite measures the things that actually break in production and
that a demo hides:

* task extraction F1 — did we create the right artefacts, and only those?
* time resolution accuracy — is ``明天下午三点`` the right absolute instant?
* hallucination rate — on statements with nothing to do, how many phantom todos?
* groundedness — does a history answer cite a note, and does it refuse when the
  corpus is empty?

Runs against the *same* code path as production (real pipeline, real SQLite,
real tool layer), not a mock. ``--provider openai`` switches to a live model so
prompt changes can be A/B'd with identical scoring.

    python -m eval.run_eval                    # scripted baseline
    python -m eval.run_eval --json out.json    # machine readable
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.container import AppContainer  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.domain import jobs as jobstates  # noqa: E402
from backend.application.ingest_note import ingest_note  # noqa: E402
from backend.infrastructure.clock import FixedClock  # noqa: E402

DATASET = Path(__file__).with_name("dataset.json")

# Phrases that mark an honest "the record does not say". Used to accept a refusal
# on a question whose snippet came back but does not actually contain the answer.
_NO_RECORD_MARKERS = (
    "没有相关记录", "没有记录", "未提及", "未记录", "没有提到",
    "未给出", "没有给出", "没有说明", "未说明", "无法确定",
)


def _says_no_record(answer: str) -> bool:
    return any(marker in (answer or "") for marker in _NO_RECORD_MARKERS)


def _build(dataset: dict, provider: str, tmp: Path) -> AppContainer:
    # Start from the real configuration (which loads .env) so `--provider openai`
    # picks up the same credentials the server uses. Then override the bits that
    # must not touch production data: a throwaway DB, a fixed clock, no retries
    # and an effectively unlimited quota.
    from backend.config import load_settings

    base = load_settings(ROOT)
    settings = dataclasses.replace(
        base,
        db_path=tmp / "eval.db",
        data_dir=tmp / "data",
        llm_provider=provider,
        stt_provider="noop",
        retry_backoff_seconds=(0,),
        free_daily_limit=10_000,
        seed_demo_users=False,
    )
    if provider != "scripted":
        problems = settings.llm_problems()
        if problems:
            raise SystemExit(
                f"provider={provider} 缺少配置：\n"
                + "\n".join(f"  - {p}" for p in problems)
                + f"\n\n请编辑 {ROOT / '.env'}，或先跑 python -m scripts.check_llm"
            )
    settings.ensure_dirs()
    container = AppContainer.build(settings, start_worker=False)
    moment = datetime.fromisoformat(dataset["now"])
    container.clock = FixedClock(moment)
    # re-bind the clock-dependent collaborators
    from backend.infrastructure.clock import Clock

    container.quota._clock = container.clock
    container.memory._clock = container.clock
    container.registry._clock = container.clock
    container.harness._clock = container.clock
    container.pipeline._clock = container.clock
    assert isinstance(container.clock, Clock)
    return container


def _drain(container) -> None:
    for _ in range(50):
        if not container.pipeline.run_next():
            return


def run(provider: str = "scripted", verbose: bool = True) -> dict:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="nota-eval-"))
    container = _build(dataset, provider, tmp)

    tp = fp = fn = 0
    time_ok = time_total = 0
    task_rows = []

    for case in dataset["notes"]:
        with container.db.session() as conn:
            from backend.infrastructure.db import write_tx
            from backend.infrastructure import repos

            with write_tx(conn):
                repos.ensure_user(conn, "eval", clock=container.clock)
                note_id = repos.insert_note(conn, user_id="eval", source="text",
                                            transcript=case["text"], audio_path=None,
                                            lang="zh", clock=container.clock)
                job_id = repos.insert_job(conn, note_id=note_id, user_id="eval",
                                          idempotency_key=f"eval-{case['id']}",
                                          clock=container.clock)
        _drain(container)

        with container.db.session() as conn:
            todos = conn.execute("SELECT * FROM todos WHERE note_id = ?", (note_id,)).fetchall()
            reminders = conn.execute("SELECT * FROM reminders WHERE note_id = ?", (note_id,)).fetchall()
            job = repos.get_job(conn, job_id)

        expect = case.get("expect", {})
        want_todo = expect.get("todos", [])
        want_rem = expect.get("reminders", [])
        got_todo = [dict(r) for r in todos]
        got_rem = [dict(r) for r in reminders]

        case_tp = case_fn = case_fp = case_time_bad = 0
        case_time_notes: list[str] = []
        for want in want_todo:
            match = [t for t in got_todo if want["title_contains"] in t["title"]]
            if match:
                case_tp += 1
                if want.get("due_at"):
                    time_total += 1
                    due = match[0]["due_at"] or ""
                    if due.startswith(want["due_at"]):
                        time_ok += 1
                    else:
                        case_time_bad += 1
                        case_time_notes.append(f"todo {want['title_contains']}: want {want['due_at']} got {due}")
            else:
                case_fn += 1
        case_fp += max(0, len(got_todo) - len(want_todo))

        for want in want_rem:
            match = [r for r in got_rem if want["title_contains"] in r["title"]]
            if match:
                case_tp += 1
                if want.get("fire_at"):
                    time_total += 1
                    fire = match[0]["fire_at"] or ""
                    if fire.startswith(want["fire_at"]):
                        time_ok += 1
                    else:
                        case_time_bad += 1
                        case_time_notes.append(f"reminder {want['title_contains']}: want {want['fire_at']} got {fire}")
            else:
                case_fn += 1
        case_fp += max(0, len(got_rem) - len(want_rem))

        tp += case_tp
        fn += case_fn
        fp += case_fp
        task_rows.append({
            "id": case["id"],
            "state": job["state"],
            # Keep due_at visible: without it a wrong time is invisible in the
            # report even though it is scored, which made a 10/11 look like a
            # mystery during debugging.
            "todos": [f"{t['title']}@{t['due_at']}" if t["due_at"] else t["title"]
                      for t in got_todo],
            "reminders": [f"{r['title']}@{r['fire_at']}" for r in got_rem],
            "tp": case_tp, "fp": case_fp, "fn": case_fn,
            "time_bad": case_time_bad,
            "ok": case_fp == 0 and case_fn == 0 and case_time_bad == 0,
        })
        if verbose:
            mark = "PASS" if case_fp == 0 and case_fn == 0 and case_time_bad == 0 else "FAIL"
            print(f"  [{mark}] {case['id']}  {case['text']}")
            if case_time_bad:
                print(f"         wrong time: {case_time_notes}")
            if case_fn:
                print(f"         missing: {[w['title_contains'] for w in want_todo + want_rem]}"
                      f" got todos={[t['title'] for t in got_todo]} "
                      f"reminders={[r['title'] for r in got_rem]}")

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # ------------------------------------------------------------- history Q&A
    ask_rows = []
    citations_ok = refusals_ok = 0
    ask_total = 0
    for case in dataset["ask"]:
        ask_total += 1
        result = container.ask.ask("eval", case["question"], limit=5) if hasattr(container, "ask") else None
        if result is None:
            from backend.application.ask_service import AskService

            result = AskService(container.memory, container.model, container.clock).ask(
                "eval", case["question"], limit=5)
        cited = {c["note_id"] for c in result["citations"]}
        if case.get("expect_note"):
            # Three acceptable outcomes for a question whose answer IS in the
            # corpus:
            #   a) it cites the note, or
            #   b) the retrieved snippet genuinely does not contain the detail
            #      asked for, and the model says so honestly. q05 asks "what time
            #      is the dentist appointment" while the note only says "remind
            #      me to book the dentist" — demanding a citation there rewards
            #      inventing one. An honest "not recorded" is the correct answer.
            # Anything else (fabricated content, silent guess) fails.
            answered = result["retrieved"] > 0 and len(cited) >= 1
            honest_gap = (result["retrieved"] > 0
                          and result["grounded"]
                          and _says_no_record(result["answer"]))
            expected_ok = answered or honest_gap
            if answered:
                citations_ok += 1
            if honest_gap and not answered:
                refusals_ok += 1
        else:
            # Nothing was retrieved: it must refuse, and must cite nothing.
            expected_ok = result["retrieved"] == 0 and len(cited) == 0
            if expected_ok:
                refusals_ok += 1
        ask_rows.append({
            "id": case["id"], "question": case["question"],
            "retrieved": result["retrieved"], "grounded": result["grounded"],
            "citations": sorted(cited), "ok": expected_ok,
            "answer": result["answer"][:80],
        })
        if verbose:
            mark = "PASS" if expected_ok else "FAIL"
            print(f"  [{mark}] {case['id']}  {case['question']} -> "
                  f"retrieved={result['retrieved']} cites={len(cited)}")

    report = {
        "provider": provider,
        "model": container.model.name,
        "prompt_version": container.settings.prompt_version,
        "task_extraction": {
            "cases": len(dataset["notes"]),
            "cases_ok": sum(1 for r in task_rows if r["ok"]),
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "no_false_positive_cases": sum(1 for r in task_rows if r["fp"] == 0),
        },
        "time_resolution": {
            "checked": time_total,
            "correct": time_ok,
            "accuracy": round(time_ok / time_total, 4) if time_total else 1.0,
        },
        "groundedness": {
            "questions": ask_total,
            "citation_ok": citations_ok,
            "refusal_ok": refusals_ok,
            "hallucinated_citation": sum(1 for r in ask_rows if not r["grounded"]),
        },
        "task_rows": task_rows,
        "ask_rows": ask_rows,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Nota evaluation suite")
    parser.add_argument("--provider", default="scripted", choices=["scripted", "openai", "anthropic"])
    parser.add_argument("--json", default="", help="write the full report to this path")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    print(f"Nota eval — provider={args.provider}")
    started = time.monotonic()
    report = run(args.provider, verbose=not args.quiet)
    elapsed = time.monotonic() - started

    tx = report["task_extraction"]
    tr = report["time_resolution"]
    g = report["groundedness"]
    print()
    print(f"  task extraction   F1={tx['f1']:.3f}  P={tx['precision']:.3f}  "
          f"R={tx['recall']:.3f}  ({tx['cases_ok']}/{tx['cases']} cases clean)")
    print(f"  time resolution   {tr['correct']}/{tr['checked']} = {tr['accuracy']:.3f}")
    print(f"  groundedness      citations ok={g['citation_ok']}, refusals ok={g['refusal_ok']}, "
          f"hallucinated={g['hallucinated_citation']}")
    print(f"  elapsed           {elapsed:.2f}s")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  report written to {args.json}")

    # a run is "green" when nothing was invented and every time was right
    green = tx["fp"] == 0 and tr["accuracy"] == 1.0 and g["hallucinated_citation"] == 0
    return 0 if green else 1


if __name__ == "__main__":
    raise SystemExit(main())
