"""Agent primitives: tool contract, risk levels, and the chat protocol types.

The harness is deliberately framework-free. It is a small ReAct loop over a
provider-neutral `ChatModel` interface, which is what makes it possible to run
deterministically offline (scripted planner) and against OpenAI-compatible or
Anthropic endpoints with the same code path.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class Risk(str, enum.Enum):
    READ = "read"            # side-effect free
    WRITE = "write"          # reversible mutation
    IRREVERSIBLE = "irreversible"  # needs an explicit human confirmation


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]        # JSON Schema object
    risk: Risk = Risk.READ

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0

    def plus(self, other: "Usage") -> "Usage":
        return Usage(self.tokens_in + other.tokens_in, self.tokens_out + other.tokens_out)


@dataclass(frozen=True)
class ChatResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw_finish_reason: str | None = None


class ChatModel(Protocol):
    """Provider-neutral chat interface."""

    name: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        max_tokens: int = 900,
        system: str | None = None,
    ) -> ChatResponse:
        ...


@dataclass(frozen=True)
class AgentOutcome:
    summary: str
    turns: int
    steps: list[dict[str, Any]]
    usage: Usage
    pending_action_ids: list[str] = field(default_factory=list)
