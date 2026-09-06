import numpy as np

from break_signal.core.pivots import pivot_highs, pivot_lows


def test_single_peak():
    # peak at index 5, L=2 -> confirmable
    h = np.array([1, 2, 3, 4, 5, 9, 5, 4, 3, 2, 1], dtype=float)
    assert pivot_highs(h, 2) == [5]


def test_single_trough():
    lo = np.array([9, 8, 7, 6, 5, 1, 5, 6, 7, 8, 9], dtype=float)
    assert pivot_lows(lo, 2) == [5]


def test_edges_never_pivot():
    # extremes within L of the edge cannot be confirmed
    h = np.array([9, 1, 1, 1, 1, 1, 9], dtype=float)
    assert pivot_highs(h, 2) == []


def test_multiple_pivots_ordered():
    h = np.array([1, 5, 1, 1, 1, 6, 1, 1, 1, 7, 1], dtype=float)
    assert pivot_highs(h, 1) == [1, 5, 9]


def test_flat_top_not_a_pivot():
    # strict maximum on both sides — a plateau is not a pivot
    h = np.array([1, 2, 5, 5, 2, 1], dtype=float)
    assert pivot_highs(h, 1) == []
