"""Jaro-Winkler and place-name keys, built in-house (matching is core to the project)."""

from __future__ import annotations

import re

_ABBREV = {"mt": "mount", "mtn": "mountain", "pk": "peak", "st": "saint", "ft": "fort"}
_GENERIC = frozenset({"mount", "mountain", "peak", "the"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    a_hit = [False] * len(a)
    b_hit = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_hit[j] and b[j] == ch:
                a_hit[i] = b_hit[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    a_seq = [ch for ch, hit in zip(a, a_hit) if hit]
    b_seq = [ch for ch, hit in zip(b, b_hit) if hit]
    transpositions = sum(x != y for x, y in zip(a_seq, b_seq)) / 2
    return (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3


def jaro_winkler(a: str, b: str, *, prefix_scale: float = 0.1) -> float:
    j = jaro(a, b)
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return j + prefix * prefix_scale * (1 - j)


def normalize_text(s: str | None) -> str:
    tokens = _NON_ALNUM.sub(" ", (s or "").lower()).split()
    return " ".join(_ABBREV.get(t, t) for t in tokens)


def place_key(s: str | None) -> str:
    tokens = normalize_text(s).split()
    kept = [t for t in tokens if t not in _GENERIC]
    return " ".join(kept) if kept else " ".join(tokens)
