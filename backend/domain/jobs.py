"""Job state machine.

Keeping the legal transitions in one place means the worker cannot invent a
state the UI does not understand, and a failed job can only be retried from
``failed`` (never silently from ``done``).
"""
from __future__ import annotations

QUEUED = "queued"
TRANSCRIBING = "transcribing"
AGENT_RUNNING = "agent_running"
DONE = "done"
FAILED = "failed"

ALL_STATES = (QUEUED, TRANSCRIBING, AGENT_RUNNING, DONE, FAILED)

# src -> allowed destinations
_TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({TRANSCRIBING, AGENT_RUNNING, FAILED}),
    TRANSCRIBING: frozenset({AGENT_RUNNING, FAILED, QUEUED}),  # QUEUED = retry
    AGENT_RUNNING: frozenset({DONE, FAILED, QUEUED}),
    DONE: frozenset(),
    FAILED: frozenset({QUEUED}),  # explicit manual retry only
}

TERMINAL = frozenset({DONE, FAILED})


class InvalidTransition(Exception):
    def __init__(self, src: str, dst: str):
        super().__init__(f"illegal job transition {src} -> {dst}")
        self.src = src
        self.dst = dst


def assert_transition(src: str, dst: str) -> None:
    if src not in _TRANSITIONS:
        raise InvalidTransition(src, dst)
    if dst not in _TRANSITIONS[src]:
        raise InvalidTransition(src, dst)


def can_transition(src: str, dst: str) -> bool:
    return dst in _TRANSITIONS.get(src, frozenset())


def is_terminal(state: str) -> bool:
    return state in TERMINAL
