"""Memory service — index transcripts, retrieve with hybrid ranking."""
from __future__ import annotations

from ..domain.memory import (
    Chunk, HashedNgramEmbedder, cosine, query_terms, recency_decay, rrf_merge, split_chunks,
)
from ..infrastructure import repos
from ..infrastructure.clock import Clock


class MemoryService:
    #: character-n-gram cosine below this is treated as "unrelated".
    #: Measured on the eval set: unrelated pairs sit under 0.06, real matches
    #: above 0.18, so 0.12 separates them without dropping short queries.
    _min_similarity = 0.12

    def __init__(self, db, clock: Clock, embedder=None):
        self._db = db
        self._clock = clock
        self._embedder = embedder or HashedNgramEmbedder()

    # ------------------------------------------------------------------ write
    def index_note(self, conn, *, user_id: str, note_id: str, text: str) -> int:
        chunks = split_chunks(text)
        for chunk in chunks:
            repos.insert_memory(conn, user_id=user_id, note_id=note_id, text=chunk, clock=self._clock)
        return len(chunks)

    def delete_note(self, conn, note_id: str) -> None:
        repos.delete_memories_for_note(conn, note_id)

    # ------------------------------------------------------------------- read
    def search(self, user_id: str, query: str, *, limit: int = 5) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []
        with self._db.session() as conn:
            rows_by_id: dict[str, object] = {}
            lexical: list[str] = []

            # Query terms with the interrogative noise removed. "我是不是说过要
            # 买牛奶" must look up 买牛奶, otherwise trigram matches the question
            # words instead of the answer.
            terms = query_terms(query) or []
            probes = terms if terms else [query]
            for term in probes[:8]:
                for row in repos.memories_fts_search(conn, user_id, term, limit=30):
                    rows_by_id.setdefault(row["id"], row)
                    if row["id"] not in lexical:
                        lexical.append(row["id"])
                for row in repos.memories_like_search(conn, user_id, term, limit=30):
                    rows_by_id.setdefault(row["id"], row)
                    if row["id"] not in lexical:
                        lexical.append(row["id"])

            # raw query as a whole, in case it is already a good literal phrase
            for row in repos.memories_like_search(conn, user_id, query, limit=30):
                rows_by_id.setdefault(row["id"], row)
                if row["id"] not in lexical:
                    lexical.append(row["id"])

            all_rows = repos.all_memories(conn, user_id, limit=2000)
            for row in all_rows:
                rows_by_id.setdefault(row["id"], row)

            qvec = self._embedder.embed(query)
            scored: list[tuple[str, float]] = []
            for row in all_rows:
                score = cosine(qvec, self._embedder.embed(row["text"]))
                if score > 0:
                    scored.append((row["id"], score))
            scored.sort(key=lambda kv: kv[1], reverse=True)
            semantic = [cid for cid, _ in scored[:30]]
            vector_scores = {cid: s for cid, s in scored}

            chunks = {
                cid: Chunk(id=cid, note_id=row["note_id"] or "", text=row["text"],
                           created_at=row["created_at"])
                for cid, row in rows_by_id.items()
            }
            hits = rrf_merge(lexical, semantic, chunks, limit=limit * 2)

        now_iso = self._clock.now_iso()
        rescored = sorted(
            ((h, h.score * (0.85 + 0.15 * recency_decay(h.chunk.created_at, now_iso))) for h in hits),
            key=lambda pair: pair[1],
            reverse=True,
        )
        # Relevance floor. Reciprocal-rank fusion always produces a ranking, even
        # for a query that has nothing to do with the corpus, so "we found
        # something" must be decided on the underlying similarity, not on rank.
        # Without this the assistant happily cites an unrelated note.
        out = []
        for hit, score in rescored:
            lexical_hit = hit.lex_rank is not None
            semantic_score = vector_scores.get(hit.chunk.id, 0.0)
            if not lexical_hit and semantic_score < self._min_similarity:
                continue
            out.append({
                "id": hit.chunk.id,
                "note_id": hit.chunk.note_id,
                "text": hit.chunk.text,
                "created_at": hit.chunk.created_at,
                "score": round(score, 6),
                "similarity": round(semantic_score, 6),
                "lex_rank": hit.lex_rank,
                "vec_rank": hit.vec_rank,
            })
        return out[:limit]
