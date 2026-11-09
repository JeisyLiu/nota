"""History-grounded Q&A.

Retrieval happens first (hybrid lexical + vector, fused with RRF). The model then
receives *only* those snippets and must cite them. If retrieval returns nothing,
we answer "no record" without calling the model at all — cheaper and impossible
to hallucinate.
"""
from __future__ import annotations

import re

from ..domain.agent import ChatModel
from ..infrastructure.clock import Clock
from . import prompts
from .memory_service import MemoryService

_CITE = re.compile(r"\[note:([a-zA-Z0-9_]+)\]")


def _repair_citations(answer: str, valid_ids: set[str]) -> tuple[str, list[str]]:
    """Fix citations whose id is a truncated form of a real one.

    Models reliably copy the prefix but sometimes drop part of the id — observed
    against deepseek-chat, which emitted ``[note:187206ead94e]`` for the real
    ``note_187206ead94e``. That is a formatting slip, not a hallucination, so it
    is repaired only when the bare id matches exactly one retrieved note; an id
    that matches nothing, or matches several, is left alone and stays ungrounded.
    """
    repairs: list[str] = []

    def fix(match: re.Match[str]) -> str:
        cited = match.group(1)
        if cited in valid_ids:
            return match.group(0)
        candidates = [
            v for v in valid_ids
            if v == cited or v.endswith(cited) or v.endswith("_" + cited)
        ]
        if len(candidates) == 1:
            repairs.append(f"{cited} -> {candidates[0]}")
            return f"[note:{candidates[0]}]"
        return match.group(0)

    return _CITE.sub(fix, answer), repairs


class AskService:
    def __init__(self, memory: MemoryService, model: ChatModel, clock: Clock):
        self._memory = memory
        self._model = model
        self._clock = clock

    def ask(self, user_id: str, question: str, *, limit: int = 5) -> dict:
        hits = self._memory.search(user_id, question, limit=limit)
        if not hits:
            return {
                "answer": "我在已有的记录里没有找到相关内容，所以不给出推测性答案。",
                "citations": [],
                "retrieved": 0,
                "grounded": True,
            }

        snippet_block = "\n".join(
            f"- [note:{h['note_id']}] {h['text']}" for h in hits
        )
        user_message = f"问题：{question}\n\n历史片段：\n{snippet_block}"
        response = self._model.complete(
            [{"role": "user", "content": user_message}], [], system=prompts.ANSWER_SYSTEM
        )
        raw_answer = (response.text or "").strip()

        valid_ids = {h["note_id"] for h in hits}
        answer, repairs = _repair_citations(raw_answer, valid_ids)

        cited_ids = set(_CITE.findall(answer))
        citations = [
            {"note_id": h["note_id"], "text": h["text"], "score": h["score"]}
            for h in hits if h["note_id"] in cited_ids
        ]
        # Any citation still not in the retrieved set is a fabricated reference.
        ungrounded = cited_ids - valid_ids
        return {
            "answer": answer,
            "citations": citations,
            "retrieved": len(hits),
            "grounded": not ungrounded,
            # Which slips were auto-corrected, so the behaviour is auditable
            # rather than silently forgiving.
            "repaired_citations": repairs,
            "usage": {"in": response.usage.tokens_in, "out": response.usage.tokens_out},
        }
