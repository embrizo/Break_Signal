"""Fractal pivot detection, matching Pine's ``ta.pivothigh`` / ``ta.pivotlow``.

A bar ``p`` is a pivot high if ``high[p]`` is strictly greater than the ``L``
bars on each side. In Pine the pivot is only *confirmed* ``L`` bars later; here
we only return pivots whose right window fully exists in the data, i.e.
``p <= n-1-L``. This reproduces the same confirmation lag when the algorithm is
evaluated at the last available bar.

Tie handling: exact float ties on real market highs/lows are vanishingly rare,
so a strict maximum on both sides is used. If two adjacent bars share an exact
extreme, neither is reported as a pivot (same practical outcome as Pine).
"""
from __future__ import annotations

import numpy as np


def _pivots(values: np.ndarray, L: int, want_high: bool) -> list[int]:
    values = np.asarray(values, dtype=float)
    n = values.shape[0]
    out: list[int] = []
    if n < 2 * L + 1:
        return out
    for p in range(L, n - L):
        c = values[p]
        left = values[p - L : p]
        right = values[p + 1 : p + L + 1]
        if want_high:
            if c > left.max() and c > right.max():
                out.append(p)
        else:
            if c < left.min() and c < right.min():
                out.append(p)
    return out


def pivot_highs(high: np.ndarray, L: int) -> list[int]:
    return _pivots(high, L, want_high=True)


def pivot_lows(low: np.ndarray, L: int) -> list[int]:
    return _pivots(low, L, want_high=False)


def merge_pivots(coarse: list[int], fine: list[int], max_pivots: int) -> list[int]:
    """Union two pivot-bar lists, deduped and sorted, keeping the newest ``max_pivots``.

    Multi-scale detection: a coarse lookback gives stable strong swings, a finer
    one adds the minor highs/lows a human would still connect. Capping at
    ``max_pivots`` keeps the O(P^2) candidate-pair loop bounded exactly as before.
    """
    merged = sorted(set(coarse) | set(fine))
    if len(merged) > max_pivots:
        merged = merged[-max_pivots:]
    return merged
