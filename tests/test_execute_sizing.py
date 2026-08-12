#!/usr/bin/env python3
"""Unit tests for the cash affordability cap on buys (e2bot-14 step 2).

Pure arithmetic only — no network, no logs, no state. Run:
    python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

import execute  # noqa: E402


class TestAffordableQty(unittest.TestCase):
    def test_rounds_down_to_whole_shares(self):
        # 1000 cash, px 100, eps 0.005 -> 995/100 = 9.95 -> 9 shares.
        self.assertEqual(execute.affordable_qty(1000.0, 100.0), 9)

    def test_reserves_the_epsilon_buffer(self):
        # Exactly 10 shares' worth of cash still yields 9: the buffer is the point.
        self.assertEqual(execute.affordable_qty(1000.0, 100.0, eps=0.005), 9)
        self.assertEqual(execute.affordable_qty(1000.0, 100.0, eps=0.0), 10)

    def test_the_2026_08_11_order_would_have_been_capped(self):
        # Live record: cash 17181.58, ref_px 91.18, delta 188, buffer_shares 0.44.
        # 17181.58 * 0.995 / 91.18 = 187.49 -> 187, one share below the order sent.
        self.assertEqual(execute.affordable_qty(17181.58, 91.18), 187)

    def test_zero_and_negative_cash_afford_nothing(self):
        self.assertEqual(execute.affordable_qty(0.0, 91.18), 0)
        self.assertEqual(execute.affordable_qty(-500.0, 91.18), 0)

    def test_ample_cash_is_not_the_binding_constraint(self):
        self.assertEqual(execute.affordable_qty(100000.0, 91.18), 1091)

    def test_non_positive_price_is_fatal_not_silently_zero(self):
        with self.assertRaises(ValueError):
            execute.affordable_qty(1000.0, 0.0)
        with self.assertRaises(ValueError):
            execute.affordable_qty(1000.0, -1.0)


class TestCapAppliedToDelta(unittest.TestCase):
    """The cap is a min() on buys only; sells raise cash and are never capped."""

    @staticmethod
    def capped(delta, cash, px):
        return min(delta, execute.affordable_qty(cash, px)) if delta > 0 else delta

    def test_buy_larger_than_cash_is_trimmed(self):
        self.assertEqual(self.capped(188, 17181.58, 91.18), 187)

    def test_buy_within_cash_is_untouched(self):
        self.assertEqual(self.capped(10, 17181.58, 91.18), 10)

    def test_sell_is_never_capped_by_cash(self):
        self.assertEqual(self.capped(-188, 0.0, 91.18), -188)

    def test_buy_with_no_cash_becomes_zero(self):
        self.assertEqual(self.capped(188, 0.0, 91.18), 0)


if __name__ == "__main__":
    unittest.main()
