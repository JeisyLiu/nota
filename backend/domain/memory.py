"""Memory: chunking + hybrid ranking.

Two independent recall channels are merged with Reciprocal Rank Fusion:

1. lexical — SQLite FTS5 ``trigram`` (great for Chinese substrings such as
   张总 / 报价) plus a LIKE fallback for queries shorter than 3 characters,
   because trigram needs >=3 code points to match anything;
2. semantic — hashed character n-gram vectors (n = 2..3) with cosine
   similarity. No embedding API and no model download, deterministic, and
   swappable: implement `EmbeddingProvider` to plug a real model in.

Ranking also applies a mild recency decay so "上个月那个报价" prefers the
recent conversation when several mention the same entity.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Iterable, Protocol

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class Chunk:
    id: str
    note_id: str
    text: str
    created_at: str


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    lex_rank: int | None = None
    vec_rank: int | None = None


class EmbeddingProvider(Protocol):
    """Plug a real embedding model in here; shape must stay fixed."""

    dim: int

    def embed(self, text: str) -> list[float]:
        ...


class HashedNgramEmbedder:
    """Cheap, deterministic stand-in for a semantic encoder."""

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        norm = 0.0
        for gram in _grams(text):
            h = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
            norm += 1.0
        if norm:
            vec = [v / math.sqrt(norm) for v in vec]
        return vec


def _grams(text: str) -> Iterable[str]:
    clean = re.sub(r"\s+", "", text)
    for n in (2, 3):
        for i in range(0, max(0, len(clean) - n + 1)):
            yield clean[i : i + n]


#: Words that carry no retrieval signal in a Chinese question. Removing them is
#: what turns "我是不是说过要买牛奶" into the query "买牛奶" that actually matches.
_QUESTION_STOP = (
    "我是不是", "是不是", "有没有", "什么", "什么时候", "多少", "几点", "怎么",
    "为什么", "哪个", "哪里", "上次", "之前", "上次说", "说过", "说过的", "记得吗",
    "还记得", "关于", "那个", "这个", "请问", "帮我", "一下", "我们", "你我",
    "什么时", "还有", "是否", "能否", "你", "我", "他", "她", "的", "了", "吗",
    "呢", "吧", "啊", "是", "要", "在", "和", "与", "给", "说",
)


def query_terms(query: str) -> list[str]:
    """Strip interrogative noise, then emit 3-grams suitable for FTS5 trigram."""
    text = (query or "").strip()
    for word in sorted(_QUESTION_STOP, key=len, reverse=True):
        text = text.replace(word, " ")
    terms: list[str] = []
    for run in re.split(r"[\s，。！？、,.!?；;：:（）()\[\]\"'\-]+", text):
        run = run.strip()
        if len(run) >= 3:
            # every 3-gram is a valid trigram token; keep them distinct & ordered
            seen = set()
            for i in range(len(run) - 2):
                gram = run[i : i + 3]
                if gram not in seen:
                    seen.add(gram)
                    terms.append(gram)
        elif len(run) == 2:
            terms.append(run)  # LIKE fallback uses this
    return terms


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def split_chunks(text: str, max_len: int = 300, overlap: int = 40) -> list[str]:
    """Split long transcripts on sentence-ish boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    pieces: list[str] = []
    for para in re.split(r"(?<=[。！？!?；;\n])", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_len:
            pieces.append(para)
            continue
        start = 0
        while start < len(para):
            pieces.append(para[start : start + max_len])
            if start + max_len >= len(para):
                break
            start += max_len - overlap
    merged: list[str] = []
    for p in pieces:
        if merged and len(merged[-1]) + len(p) <= max_len:
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)
    return merged


def rrf_merge(
    lexical: list[int],
    semantic: list[int],
    chunks: dict[int, Chunk],
    *,
    k: int = 60,
    limit: int = 5,
) -> list[Hit]:
    """Fuse two ranked id lists with Reciprocal Rank Fusion."""
    scores: dict[int, float] = {}
    lex_rank: dict[int, int] = {}
    vec_rank: dict[int, int] = {}
    for rank, cid in enumerate(lexical, start=1):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        lex_rank[cid] = rank
    for rank, cid in enumerate(semantic, start=1):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        vec_rank[cid] = rank
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    hits: list[Hit] = []
    for cid, score in ordered[:limit]:
        chunk = chunks.get(cid)
        if chunk is None:
            continue
        hits.append(Hit(chunk=chunk, score=score, lex_rank=lex_rank.get(cid), vec_rank=vec_rank.get(cid)))
    return hits


def recency_decay(created_at: str, now_iso: str, half_life_days: float = 120.0) -> float:
    """0.5 ** (age / half_life); tolerant of unparsable timestamps."""
    from datetime import datetime

    try:
        a = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        b = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        age_days = max(0.0, (b - a).total_seconds() / 86400.0)
        return 0.5 ** (age_days / half_life_days)
    except Exception:
        return 1.0
