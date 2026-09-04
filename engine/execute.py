#!/usr/bin/env python3
"""E2 QLD executor — turns today's signal into (at most) one MOC order.

Implements the execution spec (decided 2026-08-02) verbatim:
  1. Market-on-close order placed on day N+1, before MOC_CUTOFF (15:45 ET —
     five minutes inside Alpaca's 15:50 ET cutoff).
  2. Whole shares, round down; target dollars = signal_alloc * account equity.
  3. Trade when the signal differs from the last acted-on signal, OR when the
     realized allocation (from shares actually HELD) has drifted more than
     DRIFT_BAND from the signal. Amended from the original "never rebalance for
     drift" by decision of 2026-08-10 (see docs/INCIDENTS.md).
  4. Missed days self-heal: today's run trades toward today's fresh signal;
     no back-fill.

State: log/last_acted_signal.json holds the last signal an order was placed
for (or confirmed equal). log/trade_log.jsonl is append-only.

Kill switch: a `HALT` file at the repo root suppresses all ordering (exit 0,
logged as HALTED, state untouched). See RUNBOOK.md.

Idempotency: before any POST, open orders for the symbol are checked; if one
exists (e.g. a re-run on the same day while a MOC order rests), the run holds.

Structure: `decide()` is the pure rule (sizing, drift gate, cash cap) and is
unit-tested in tests/test_execute_decide.py; `main()` does I/O only — reads
the signal and state, calls Alpaca, writes the logs.

Usage: python3 engine/execute.py [--dry-run]
Run AFTER engine/signal_engine.py on the same day; reads the newest record
from log/signal_log.jsonl and hard-fails if it is not from the last completed
session (i.e. the signal is stale). --dry-run prints the decision, sends no
order, and does not update state.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNAL_LOG = os.path.join(ROOT, "log", "signal_log.jsonl")
TRADE_LOG = os.path.join(ROOT, "log", "trade_log.jsonl")
STATE_PATH = os.path.join(ROOT, "log", "last_acted_signal.json")
HALT_PATH = os.path.join(ROOT, "HALT")  # kill switch: present => never order

SYM = "QLD"
MOC_CUTOFF = dt.time(15, 45)  # ET; 5 min safety before Alpaca's 15:50 cutoff
ET = ZoneInfo("America/New_York")

# Rebalance when the REALIZED allocation deviates from the signal by more than
# this fraction of equity, even if the signal itself has not changed
# (decided 2026-08-10).
#
# Why this exists: state records the allocation *submitted*, not the one
# *filled*. Both live MOC orders to date expired partially filled (109/1081 on
# 2026-08-05, 791/984 on 2026-08-07), so state read "acted on 100%" while the
# account held 82.8%, and the signal-change test — the only path to an order —
# could never fire. The shortfall was structurally invisible.
#
# Why 0.01: simulated over the 2006-2026 reference series (which is a
# constant-fraction strategy, New_ret = Effective_alloc * QLD_ret, i.e. an exact
# daily rebalance — so a drift gate moves live CLOSER to the backtest, not away
# from it). With clean fills the band is nearly free (-0.00% CAGR vs backtest,
# ~13 orders/yr). With 20% of each buy failing to fill — the observed base rate
# — no gate costs -4.64% CAGR while 0.01 costs -0.71% at ~20 orders/yr. Tighter
# bands gain little (0.005: -0.63%) and 0.01 stays 10x above floor()-rounding
# noise (1 share ~ 0.09% of equity at current prices).
DRIFT_BAND = 0.01

# Fraction of cash held back when sizing a buy (decided 2026-08-11).
#
# Why this exists: target_qty is sized off EQUITY, but QLD is non-marginable
# (margin_requirement_long 100, asset_marginable false), so a buy is paid from
# CASH — and at alloc 1.0 cash is exactly equity minus the position, i.e. the
# buffer is structurally zero. The order is sized at an intraday reference price
# and fills at the close, so any rise in between makes it unaffordable and it
# fills short by roughly delta * (close/ref - 1). The 2026-08-11 top-up (buy 188
# at ref 91.18, cash 17181.58) had 0.44 shares of headroom.
#
# Why 0.005: it must cover the intraday-to-close move on the LAST share only,
# not the whole order, so it is small by construction. 0.5% of cash at alloc 1.0
# is ~0.09 shares of slack per 188 ordered — enough to absorb a rounding cent
# and a normal close-vs-reference tick, while costing at most one share of
# under-fill. Under-filling by one share is self-healing: the drift gate
# sees the realized shortfall the next day. Over-ordering is not — an
# unaffordable MOC order expires partially filled, which is exactly the failure
# traced on 2026-08-11 (docs/INCIDENTS.md).
CASH_BUFFER = 0.005


def affordable_qty(cash, px, eps=CASH_BUFFER):
    """Whole shares payable from `cash` at `px`, keeping an `eps` fraction back.

    Never negative: a cash deficit affords zero shares, it does not imply a sell.
    """
    if px <= 0:
        raise ValueError(f"non-positive price {px}")
    return max(0, math.floor(cash * (1 - eps) / px))


@dataclass(frozen=True)
class Decision:
    """The pure output of `decide()`: what to do and every number behind it.

    `action` is one of "hold", "order", "halt". `side`/`qty` are set only for
    "order" (qty is always positive; side says which way). `reason` is the
    human-readable parenthetical that ends up in the trade log's `action`
    string. The remaining fields are the sizing diagnostics AUDIT.md §1 lists
    for `log/trade_log.jsonl`, so `main()` can log them without recomputing.
    """
    action: str
    side: str | None
    qty: int
    reason: str
    target_qty: int
    requested_delta: int
    cash_cap_qty: int | None
    capped_by_cash: bool
    buffer_shares: float | None
    current_alloc: float
    drift: float


def decide(signal_alloc, last_acted_alloc, equity, cash, position_qty, ref_px, halted):
    """Execution spec rules 2–3 as a pure function. No I/O, no clock, no state.

    signal_alloc     today's signal (0.0 / 0.5 / 1.0)
    last_acted_alloc the signal the last order was placed for (None = never)
    equity, cash     account equity and settled cash, dollars
    position_qty     whole QLD shares currently HELD (not ordered)
    ref_px           intraday reference price used for sizing
    halted           True when the HALT kill switch is present

    Returns a Decision. Raises ValueError on inputs the rule cannot act on;
    callers hard-fail on that, never default.
    """
    if signal_alloc not in (0.0, 0.5, 1.0):
        raise ValueError(f"invalid signal_alloc {signal_alloc}")
    if equity <= 0:
        raise ValueError(f"non-positive equity {equity}")
    if ref_px <= 0:
        raise ValueError(f"non-positive price {ref_px}")
    if position_qty < 0:
        raise ValueError(f"negative position {position_qty}")

    target_qty = math.floor(signal_alloc * equity / ref_px)
    requested_delta = target_qty - position_qty

    # How many shares of headroom the cash leaves beyond the UNCAPPED order,
    # priced at the intraday reference. Negative means the order could not have
    # been paid for in full at this price; near zero means a rise between now and
    # the close would do the same. Diagnostic — the cap below is what acts on it.
    buffer_shares = round(cash / ref_px - requested_delta, 2) if requested_delta > 0 else None

    # Cap a buy at what cash can actually pay for. Sells are never capped: they
    # raise cash. Under-ordering is self-healing (the drift gate re-fires
    # tomorrow); over-ordering expires partially filled, which is the bug.
    cash_cap_qty = affordable_qty(cash, ref_px) if requested_delta > 0 else None
    delta = min(requested_delta, cash_cap_qty) if requested_delta > 0 else requested_delta
    capped = delta != requested_delta

    # Realized allocation, derived from shares actually HELD — never from what
    # was ordered. This is what makes a partial fill visible to the gate below.
    cur_alloc = position_qty * ref_px / equity
    drift = abs(cur_alloc - signal_alloc)

    diag = dict(target_qty=target_qty, requested_delta=requested_delta,
                cash_cap_qty=cash_cap_qty, capped_by_cash=capped,
                buffer_shares=buffer_shares, current_alloc=round(cur_alloc, 6),
                drift=round(drift, 6))

    if halted:
        return Decision("halt", None, 0, "HALT file present", **diag)
    if last_acted_alloc == signal_alloc and drift <= DRIFT_BAND:
        return Decision("hold", None, 0,
                        f"signal unchanged; drift {drift:.2%} within band {DRIFT_BAND:.0%}",
                        **diag)
    if delta == 0:
        reason = (f"buy {requested_delta} unaffordable: cash {cash:.2f} at {ref_px} "
                  f"pays for 0 shares" if capped else "already at target")
        return Decision("hold", None, 0, reason, **diag)
    why = "signal change" if last_acted_alloc != signal_alloc else f"drift {drift:.2%}"
    if capped:
        why += f"; buy capped {requested_delta}->{delta} by cash {cash:.2f}"
    return Decision("order", "buy" if delta > 0 else "sell", abs(delta), why, **diag)


def fatal(msg):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_env():
    # Missing .env allowed — cloud routines inject process env vars;
    # the key check below still hard-fails if keys arrive from neither source.
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def api(method, path, body=None):
    ep = os.environ.get("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2")
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        fatal("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec,
               "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{ep}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404 and path.startswith("/positions/"):
            return None  # no open position
        fatal(f"Alpaca API {e.code} on {path}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        fatal(f"Alpaca API unreachable: {e.reason}")


def latest_signal():
    if not os.path.exists(SIGNAL_LOG):
        fatal(f"{SIGNAL_LOG} missing — run signal_engine.py first")
    with open(SIGNAL_LOG) as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    if not lines:
        fatal("signal log is empty")
    return json.loads(lines[-1])


def open_orders(sym):
    """Open (resting) orders for `sym` on the account — the idempotency check."""
    q = urllib.parse.urlencode({"status": "open", "symbols": sym})
    return [o for o in api("GET", f"/orders?{q}") or [] if o.get("symbol") == sym]


def main():
    dry = "--dry-run" in sys.argv[1:]
    load_env()

    sig = latest_signal()
    alloc = sig["signal_alloc"]
    if alloc not in (0.0, 0.5, 1.0):
        fatal(f"invalid signal_alloc {alloc}")
    run_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    # Kill switch (RUNBOOK "Halt"): a committed HALT file at the repo root stops
    # all ordering, checked before any market/clock gate so a halted run reports
    # "HALTED" rather than a market-closed failure. State is deliberately NOT
    # updated, so removing HALT lets the next run trade toward the then-current
    # signal (self-heal rule 4). Exit 0: a halt is intentional, not a failure.
    if os.path.exists(HALT_PATH):
        with open(HALT_PATH) as f:
            reason = f.read().strip().splitlines()
        halted = {
            "run_at_utc": run_at,
            "signal_date": sig["signal_date"],
            "signal_alloc": alloc,
            "action": "HALTED by HALT file — no order",
            "halt_reason": reason[0] if reason else "(no reason recorded)",
            "order_id": None,
            "dry_run": dry,
        }
        if not dry:
            append_trade(halted)
        print(json.dumps(halted, indent=2))
        return

    # Market must be open and before the MOC cutoff (order day = N+1).
    clock = api("GET", "/clock")
    now_et = dt.datetime.now(ET)
    if not dry:
        if not clock["is_open"]:
            fatal("market is closed — MOC order day must be a trading day")
        if now_et.time() >= MOC_CUTOFF:
            fatal(f"past MOC cutoff {MOC_CUTOFF} ET — do NOT chase; next run self-heals")
        # Signal must be from the session immediately before today: it may not
        # be from today (that close hasn't happened) and a signal older than
        # ~1 week is unambiguously stale.
        sig_date = dt.date.fromisoformat(sig["signal_date"])
        age = (now_et.date() - sig_date).days
        if age < 1 or age > 7:
            fatal(f"signal dated {sig_date} is not yesterday's session (age {age}d)")

    account = api("GET", "/account")
    equity = float(account["equity"])
    if equity <= 0:
        fatal(f"non-positive equity {equity}")
    # QLD is non-marginable (margin_requirement_long 100), so cash — not equity,
    # not buying_power — is what a buy is actually paid from. The TARGET is still
    # sized off equity (exec spec rule 2, unchanged); cash only caps the ORDER
    # that walks toward it (decided 2026-08-11).
    cash = float(account["cash"])

    pos = api("GET", f"/positions/{SYM}")
    cur_qty = int(float(pos["qty"])) if pos else 0
    px = float(pos["current_price"]) if pos else float(sig["px"])

    # Trade on signal change vs the last acted-on signal, or on realized drift.
    last_acted = None
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            last_acted = json.load(f).get("signal_alloc")

    try:
        d = decide(alloc, last_acted, equity, cash, cur_qty, px, halted=False)
    except ValueError as e:
        fatal(str(e))

    decision = {
        "run_at_utc": run_at,
        "signal_date": sig["signal_date"],
        "signal_alloc": alloc,
        "last_acted_alloc": last_acted,
        "equity": equity,
        "cash": cash,
        "ref_px": px,
        "current_qty": cur_qty,
        "target_qty": d.target_qty,
        "requested_delta": d.requested_delta,
        "cash_cap_qty": d.cash_cap_qty,
        "capped_by_cash": d.capped_by_cash,
        "buffer_shares": d.buffer_shares,
        "current_alloc": d.current_alloc,
        "drift": d.drift,
        "drift_band": DRIFT_BAND,
        "action": None,
        "order_id": None,
        "dry_run": dry,
    }

    if d.action == "hold":
        decision["action"] = f"hold ({d.reason})"
    else:
        # Idempotency: a resting MOC order for the symbol means this day's
        # decision has already been sent (re-run, retry, operator session).
        # Submitting again would double the order; hold instead and say why.
        pending = open_orders(SYM)
        if pending:
            ids = ", ".join(o.get("id", "?") for o in pending)
            decision["action"] = f"hold (open MOC order {ids} pending)"
            decision["order_reason"] = d.reason
        else:
            decision["order_reason"] = d.reason
            order_body = {"symbol": SYM, "qty": str(d.qty), "side": d.side,
                          "type": "market", "time_in_force": "cls"}
            if dry:
                decision["action"] = f"would submit MOC {d.side} {d.qty} {SYM} ({d.reason})"
            else:
                order = api("POST", "/orders", order_body)
                decision["action"] = f"submitted MOC {d.side} {d.qty} {SYM} ({d.reason})"
                decision["order_id"] = order["id"]

    if not dry:
        with open(STATE_PATH, "w") as f:
            json.dump({"signal_alloc": alloc, "signal_date": sig["signal_date"],
                       "updated_utc": run_at}, f, indent=2)
        append_trade(decision)

    print(json.dumps(decision, indent=2))


def append_trade(record):
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    with open(TRADE_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
