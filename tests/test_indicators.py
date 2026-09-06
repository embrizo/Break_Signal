import numpy as np

from break_signal.core import indicators


def test_sma_basic():
    x = np.array([1.0, 2, 3, 4, 5])
    out = indicators.sma(x, 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == 2.0  # (1+2+3)/3
    assert out[4] == 4.0  # (3+4+5)/3


def test_rma_seed_is_sma():
    x = np.arange(1.0, 21.0)  # 1..20
    out = indicators.rma(x, 14)
    assert np.isnan(out[12])
    # first defined value is the SMA of the first 14 samples
    assert out[13] == np.mean(x[:14])
    # Wilder recursion afterward
    alpha = 1 / 14
    expect = alpha * x[14] + (1 - alpha) * out[13]
    assert abs(out[14] - expect) < 1e-9


def test_atr_positive_and_defined():
    n = 100
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    a = indicators.atr(high, low, close, 14)
    assert np.isnan(a[12])
    assert np.all(a[14:] > 0)


def test_rsi_range():
    n = 200
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    r = indicators.rsi(close, 14)
    defined = r[~np.isnan(r)]
    assert np.all(defined >= 0) and np.all(defined <= 100)


def test_rsi_all_gains_is_100():
    close = np.arange(1.0, 50.0)  # strictly increasing
    r = indicators.rsi(close, 14)
    assert abs(r[-1] - 100.0) < 1e-6
