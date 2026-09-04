#!/usr/bin/env python3
"""Unit tests for engine/execute.py `decide()` — the execution rule as a pure
function. No network, no logs, no state. Run:
    python3 -m unittest discover -s tests -v

Numbers are taken from live trade_log records where one exists, so each test
doubles as a regression check on a day the bot actually traded.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import execute  # noqa: E402


def decide(**kw):
    base = dict(signal_alloc=1.0, last_acted_alloc=1.0, equity=100_000.0,
                cash=0.0, position_qty=0, ref_px=100.0, halted=False)
    base.update(kw)
    return execute.decide(**base)


class TestHold(unittest.TestCase):
    def test_hold_when_signal_unchanged_and_within_band(self):
        # 2026-08-14 live record: 1078 held, target 1081, drift 0.37%.
        d = decide(equity=101597.34, cash=373.03, position_qty=1078, ref_px=93.9001)
        self.assertEqual(d.action, "hold")
        self.assertEqual(d.target_qty, 1081)
        self.assertEqual(d.requested_delta, 3)
        self.assertAlmostEqual(d.drift, 0.0037, places=3)
        self.assertIn("signal unchanged", d.reason)
        self.assertIn("within band 1%", d.reason)

    def test_halt_places_no_order_even_on_signal_change(self):
        d = decide(last_acted_alloc=0.0, cash=100_000.0, halted=True)
        self.assertEqual(d.action, "halt")
        self.assertEqual(d.qty, 0)
        self.assertIsNone(d.side)
        # Sizing diagnostics are still computed, so the halt is auditable.
        self.assertEqual(d.target_qty, 1000)

    def test_cash_cap_to_zero_is_a_hold_not_an_order(self):
        # Signal change demands 1000 shares; no cash -> 0 affordable -> hold.
        d = decide(last_acted_alloc=None, cash=50.0, position_qty=0)
        self.assertEqual(d.action, "hold")
        self.assertTrue(d.capped_by_cash)
        self.assertEqual(d.cash_cap_qty, 0)
        self.assertIn("unaffordable", d.reason)
        self.assertIn("pays for 0 shares", d.reason)

    def test_already_at_target_after_signal_change_is_hold(self):
        d = decide(last_acted_alloc=0.5, position_qty=1000, cash=0.0)
        self.assertEqual(d.action, "hold")
        self.assertEqual(d.reason, "already at target")


class TestOrder(unittest.TestCase):
    def test_signal_change_buys_to_target(self):
        # 2026-08-05 go-live: 0 held, equity 100000, ref 92.46 -> target 1081.
        # The order actually sent that day was 1081 and filled 109. Under the
        # cash cap that landed on 08-12 the same inputs send 1076 — the target
        # is unchanged, the order is what cash can pay for at the close.
        d = decide(last_acted_alloc=None, equity=100_000.0, cash=100_000.0,
                   position_qty=0, ref_px=92.46)
        self.assertEqual(d.target_qty, 1081)
        self.assertEqual((d.action, d.side, d.qty), ("order", "buy", 1076))
        self.assertTrue(d.capped_by_cash)
        self.assertTrue(d.reason.startswith("signal change; buy capped 1081->1076"))

    def test_signal_change_with_ample_cash_is_uncapped(self):
        d = decide(last_acted_alloc=None, equity=100_000.0, cash=150_000.0,
                   position_qty=0, ref_px=92.46)
        self.assertEqual((d.action, d.side, d.qty), ("order", "buy", 1081))
        self.assertEqual(d.reason, "signal change")
        self.assertFalse(d.capped_by_cash)

    def test_drift_beyond_band_rebalances_with_unchanged_signal(self):
        # 2026-08-11 live record: 900 held of target 1088, drift 17.31%.
        d = decide(equity=99243.58, cash=17181.58, position_qty=900, ref_px=91.18)
        self.assertEqual(d.action, "order")
        self.assertEqual(d.side, "buy")
        self.assertEqual(d.target_qty, 1088)
        self.assertTrue(d.reason.startswith("drift 17.31%"))
        # The 08-11 order was sent uncapped at 188 and got 0 fills; the cap
        # that landed the next day trims it to 187.
        self.assertEqual(d.cash_cap_qty, 187)
        self.assertEqual(d.qty, 187)
        self.assertTrue(d.capped_by_cash)
        self.assertIn("buy capped 188->187", d.reason)

    def test_drift_inside_band_does_not_rebalance(self):
        # 995 of 1000 = 0.5% drift: inside the 1% band -> no churn.
        d = decide(position_qty=995, cash=1000.0)
        self.assertEqual(d.action, "hold")

    def test_sell_on_signal_drop_is_never_capped(self):
        # Signal 1.0 -> 0.0 with zero cash: the whole position is sold.
        d = decide(signal_alloc=0.0, last_acted_alloc=1.0, position_qty=1000, cash=0.0)
        self.assertEqual((d.action, d.side, d.qty), ("order", "sell", 1000))
        self.assertIsNone(d.cash_cap_qty)
        self.assertFalse(d.capped_by_cash)

    def test_half_allocation_sells_half(self):
        d = decide(signal_alloc=0.5, last_acted_alloc=1.0, position_qty=1000, cash=0.0)
        self.assertEqual((d.side, d.qty), ("sell", 500))


class TestInvalidInput(unittest.TestCase):
    def test_bad_allocation_is_an_error_not_a_default(self):
        with self.assertRaises(ValueError):
            decide(signal_alloc=0.75)

    def test_non_positive_equity_or_price_is_an_error(self):
        with self.assertRaises(ValueError):
            decide(equity=0.0)
        with self.assertRaises(ValueError):
            decide(ref_px=0.0)


if __name__ == "__main__":
    unittest.main()
