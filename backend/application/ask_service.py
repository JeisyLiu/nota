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
        answer = (response.text or "").strip()

        cited_ids = set(_CITE.findall(answer))
        valid_ids = {h["note_id"] for h in hits}
        citations = [
            {"note_id": h["note_id"], "text": h["text"], "score": h["score"]}
            for h in hits if h["note_id"] in cited_ids
        ]
        # Any citation that isn't in the retrieved set is a fabricated reference.
        ungrounded = cited_ids - valid_ids
        return {
            "answer": answer,
            "citations": citations,
            "retrieved": len(hits),
            "grounded": not ungrounded,
            "usage": {"in": response.usage.tokens_in, "out": response.usage.tokens_out},
        }
