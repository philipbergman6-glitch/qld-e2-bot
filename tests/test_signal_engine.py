#!/usr/bin/env python3
"""Unit tests for the frozen E2 rule in engine/signal_engine.py.

Pure arithmetic on synthetic series — no network, no logs, no state. Run:
    python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

import signal_engine as se  # noqa: E402


def series(closes):
    idx = pd.bdate_range("2020-01-01", periods=len(closes))
    px = pd.Series(np.asarray(closes, dtype=float), index=idx)
    return px, px.pct_change()


def steady_uptrend(n=600, daily=0.0005, noise=0.001, seed=0):
    rng = np.random.default_rng(seed)
    ret = daily + noise * rng.standard_normal(n)
    return 100.0 * np.cumprod(1.0 + ret)


class TestWarmup(unittest.TestCase):
    def test_signal_is_nan_until_expanding_percentile_has_252_vols(self):
        px, ret = series(steady_uptrend(n=400))
        state = se.compute_signals(px, ret)
        # vol needs 21 bars (first at 0-based 20), hi needs 252 vol observations
        # -> first valid signal at 0-based 271. MIN_BARS (273) is a strict superset.
        first = int(np.flatnonzero(state.signal_alloc.notna().to_numpy())[0])
        self.assertEqual(first, 271)
        self.assertTrue(state.signal_alloc.iloc[:first].isna().all())
        self.assertGreaterEqual(se.MIN_BARS, first + 1)

    def test_min_bars_constant_matches_rule_windows(self):
        self.assertEqual(se.MIN_BARS, 252 + se.VOL_WIN + 1)


class TestAllocationValues(unittest.TestCase):
    def test_only_three_allocation_levels_ever_appear(self):
        rng = np.random.default_rng(1)
        ret = 0.0002 + 0.02 * rng.standard_normal(1500)
        px, r = series(100.0 * np.cumprod(1.0 + ret))
        state = se.compute_signals(px, r)
        self.assertTrue(set(state.signal_alloc.dropna().unique()) <= {0.0, 0.5, 1.0})

    def test_calm_uptrend_is_fully_allocated(self):
        px, ret = series(steady_uptrend(n=600, noise=0.001))
        state = se.compute_signals(px, ret)
        self.assertEqual(state.signal_alloc.iloc[-1], 1.0)
        self.assertTrue(bool(state.trend.iloc[-1]))

    def test_vol_spike_in_trend_halves_allocation(self):
        base = steady_uptrend(n=600, noise=0.001)
        # Violent alternating moves over the last 20 sessions: vol >> its 90th pct,
        # price still above SMA200 -> trend holds, alloc must drop to 0.5.
        tail = base[-21:].copy()
        for i in range(1, 21):
            tail[i] = tail[i - 1] * (1.06 if i % 2 else 0.95)
        closes = np.concatenate([base[:-21], tail])
        px, ret = series(closes)
        state = se.compute_signals(px, ret)
        self.assertTrue(bool(state.trend.iloc[-1]))
        self.assertGreater(state.vol.iloc[-1], state.hi.iloc[-1])
        self.assertEqual(state.signal_alloc.iloc[-1], 0.5)

    def test_below_trend_without_reentry_is_flat(self):
        up = steady_uptrend(n=500, noise=0.001)
        # Long steady decline: px < SMA200 and px < SMA20 -> no re-entry, alloc 0.
        down = up[-1] * np.cumprod(np.full(200, 1.0 - 0.003))
        px, ret = series(np.concatenate([up, down]))
        state = se.compute_signals(px, ret)
        self.assertFalse(bool(state.trend.iloc[-1]))
        self.assertEqual(state.signal_alloc.iloc[-1], 0.0)


class TestBarsDigest(unittest.TestCase):
    def test_digest_binds_full_float_and_order(self):
        idx = pd.bdate_range("2024-01-01", periods=3)
        a = pd.DataFrame({"px": [100.0, 101.25, 99.5]}, index=[d.date() for d in idx])
        b = pd.DataFrame({"px": [100.0, 101.25, 99.500001]}, index=[d.date() for d in idx])
        c = a.iloc[::-1]
        self.assertNotEqual(se.bars_digest(a), se.bars_digest(b))
        self.assertNotEqual(se.bars_digest(a), se.bars_digest(c))
        self.assertEqual(se.bars_digest(a), se.bars_digest(a.copy()))


if __name__ == "__main__":
    unittest.main()
