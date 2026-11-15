"""Semantic hippocampus: episodes stored with embedding vectors, recalled by similarity.

Plain ledger rows are chronological; this layer answers "episodes like this one".
Vectors live in state/embeddings.jsonl beside the ledger. Cosine similarity, stdlib only.
"""
from __future__ import annotations

import json
import math
from typing import Any

from . import ledger
from .llm import embed
from .paths import state_dir
from .registry import Registry


def _path():
    return state_dir() / "embeddings.jsonl"


def _rows() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def remember(reg: Registry, text: str, tier_spec: str = "openai-embed") -> dict[str, Any]:
    vec = embed(reg, text, tier_spec)
    row = {"text": text, "vec": vec}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    ledger.append({"type": "remember", "status": "ok"})
    return {"stored": text, "dims": len(vec)}


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def recall(reg: Registry, query: str, k: int = 5, tier_spec: str = "openai-embed") -> list[dict[str, Any]]:
    rows = _rows()
    if not rows:
        return []
    qv = embed(reg, query, tier_spec)
    scored = [{"text": r["text"], "score": round(_cos(qv, r["vec"]), 4)} for r in rows]
    return sorted(scored, key=lambda r: -r["score"])[:k]
