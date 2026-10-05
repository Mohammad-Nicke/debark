"""Small offline dependency-name ranker. Suggestions are never installed automatically."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MODEL_PATH = Path(__file__).with_name("data") / "dependency-ranker.json"
MAX_MODEL_BYTES = 20 * 1024 * 1024


def model_size_bytes() -> int:
    paths = [Path(__file__), *MODEL_PATH.parent.rglob("*")]
    total = 0
    for path in paths:
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            return MAX_MODEL_BYTES + 1
    return total


def _load_model() -> dict[str, Any]:
    try:
        if model_size_bytes() > MAX_MODEL_BYTES:
            return {}
        value = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("max_model_bytes", 0) > MAX_MODEL_BYTES:
            return {}
        return value
    except (OSError, ValueError, TypeError):
        return {}


_MODEL = _load_model()


def _normalize(value: str) -> str:
    value = value.lower().replace("lib", " ")
    value = re.sub(r"\d+(?:\.\d+)*", " ", value)
    return re.sub(r"[^a-z]+", " ", value).strip()


def _trigrams(value: str) -> set[str]:
    padded = f"  {value}  "
    return {padded[index:index + 3] for index in range(max(0, len(padded) - 2))}


def rank_candidates(debian_name: str, candidates: set[str], limit: int = 3) -> list[tuple[str, float]]:
    """Rank local Arch package names for display as low-confidence hints."""
    if not _MODEL or not candidates:
        return []
    weights = _MODEL.get("weights", {})
    stop = set(_MODEL.get("stop_tokens", []))
    source = _normalize(debian_name)
    source_tokens = set(source.split()) - stop
    source_grams = _trigrams(source)
    minimum = float(_MODEL.get("minimum_suggestion_score", 0.34))
    ranked: list[tuple[str, float]] = []
    for candidate in candidates:
        target = _normalize(candidate)
        target_tokens = set(target.split()) - stop
        target_grams = _trigrams(target)
        token_union = source_tokens | target_tokens
        gram_union = source_grams | target_grams
        token_overlap = (len(source_tokens & target_tokens) / len(token_union)) if token_union else 0.0
        gram_overlap = (len(source_grams & target_grams) / len(gram_union)) if gram_union else 0.0
        score = (
            float(weights.get("token_overlap", 0.0)) * token_overlap
            + float(weights.get("trigram_overlap", 0.0)) * gram_overlap
        )
        if source == target:
            score = float(weights.get("exact", 1.0))
        elif source and target and (source.startswith(target) or target.startswith(source)):
            score += float(weights.get("prefix_match", 0.0))
        if score >= minimum:
            ranked.append((candidate, min(score, 1.0)))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))[:max(1, limit)]
