#!/usr/bin/env python3
"""E2 QLD executor — turns today's signal into (at most) one MOC order.

Implements the execution spec (e2bot-03, 2026-08-02) verbatim:
  1. Market-on-close order placed on day N+1, before the ~15:50 ET MOC cutoff.
  2. Whole shares, round down; target dollars = signal_alloc * account equity.
  3. Trade ONLY when the signal differs from the last acted-on signal; never
     rebalance for drift.
  4. Missed days self-heal: today's run trades toward today's fresh signal;
     no back-fill.

State: log/last_acted_signal.json holds the last signal an order was placed
for (or confirmed equal). log/trade_log.jsonl is append-only.

Usage: python3 engine/execute.py [--dry-run]
Run AFTER engine/signal_engine.py on the same day; reads the newest record
from log/signal_log.jsonl and hard-fails if it is not from the last completed
session (i.e. the signal is stale). --dry-run prints the decision, sends no
order, and does not update state.
"""
import datetime as dt
import json
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

SYM = "QLD"
MOC_CUTOFF = dt.time(15, 45)  # ET; 5 min safety before Alpaca's 15:50 cutoff
ET = ZoneInfo("America/New_York")


def fatal(msg):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_env():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        fatal(".env not found at repo root")
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


def main():
    dry = "--dry-run" in sys.argv[1:]
    load_env()

    sig = latest_signal()
    alloc = sig["signal_alloc"]
    if alloc not in (0.0, 0.5, 1.0):
        fatal(f"invalid signal_alloc {alloc}")

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

    pos = api("GET", f"/positions/{SYM}")
    cur_qty = int(float(pos["qty"])) if pos else 0
    px = float(pos["current_price"]) if pos else float(sig["px"])

    # Trade only on signal change vs the last acted-on signal.
    last_acted = None
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            last_acted = json.load(f).get("signal_alloc")

    target_qty = math.floor(alloc * equity / px)
    delta = target_qty - cur_qty

    decision = {
        "run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "signal_date": sig["signal_date"],
        "signal_alloc": alloc,
        "last_acted_alloc": last_acted,
        "equity": equity,
        "ref_px": px,
        "current_qty": cur_qty,
        "target_qty": target_qty,
        "action": None,
        "order_id": None,
        "dry_run": dry,
    }

    if last_acted == alloc:
        decision["action"] = "hold (signal unchanged; drift never rebalanced)"
    elif delta == 0:
        decision["action"] = "hold (already at target)"
    else:
        side = "buy" if delta > 0 else "sell"
        order_body = {"symbol": SYM, "qty": str(abs(delta)), "side": side,
                      "type": "market", "time_in_force": "cls"}
        if dry:
            decision["action"] = f"would submit MOC {side} {abs(delta)} {SYM}"
        else:
            order = api("POST", "/orders", order_body)
            decision["action"] = f"submitted MOC {side} {abs(delta)} {SYM}"
            decision["order_id"] = order["id"]

    if not dry:
        with open(STATE_PATH, "w") as f:
            json.dump({"signal_alloc": alloc, "signal_date": sig["signal_date"],
                       "updated_utc": decision["run_at_utc"]}, f, indent=2)
        os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
        with open(TRADE_LOG, "a") as f:
            f.write(json.dumps(decision) + "\n")

    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
