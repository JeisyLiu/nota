"""Retrieval behaviour + the permanent eval baseline.

The eval suite is not a one-off script: it runs in CI. If a prompt or retriever
change makes the assistant invent a task or cite the wrong note, this test fails
before the change lands.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from backend.domain.memory import HashedNgramEmbedder, cosine, query_terms, split_chunks


def test_query_terms_strip_the_question():
    terms = query_terms("我是不是说过要买牛奶")
    assert any("牛奶" in t for t in terms), terms
    assert not any("是不是" in t for t in terms), terms
    # a long question decomposes into overlapping 3-grams so trigram FTS can hit
    long_terms = query_terms("上次跟供应商对账的结论是什么")
    assert len(long_terms) > 1
    assert all(len(t) >= 2 for t in long_terms)


def test_unrelated_query_is_far_in_vector_space():
    e = HashedNgramEmbedder()
    q = e.embed("关于火星殖民我上次说了什么")
    near = e.embed("记得买牛奶和鸡蛋，顺便取快递")
    assert cosine(q, near) < 0.12


def test_split_chunks_keeps_sentences_together():
    text = "第一句话。第二句话。第三句话。"
    chunks = split_chunks(text, max_len=300)
    assert len(chunks) == 1 and chunks[0] == text


def test_split_chunks_respects_the_bound():
    text = "。".join(f"第{i}项内容大概十个字" for i in range(80))
    for chunk in split_chunks(text, max_len=120):
        assert len(chunk) <= 160  # bound + one merged sentence


def test_eval_suite_is_green():
    """F1 = 1.0, time accuracy = 1.0, zero hallucinated citations."""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "eval.run_eval", "--quiet"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "F1=1.000" in proc.stdout, proc.stdout
    assert "hallucinated=0" in proc.stdout, proc.stdout
