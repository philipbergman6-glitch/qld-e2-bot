#!/usr/bin/env python3
"""E2 QLD signal engine — deterministic daily allocation signal.

Port of the frozen E2 rule ("graded vol-confirmed re-entry"), verified against
the backtest reference. No LLM in the loop. Hard-fails on any invalid input.

Rule (signal computed from day-N close, QLD price/returns only):
  vol   = 20d realized vol of daily returns, annualized (sqrt 252, ddof=1)
  hi    = expanding 90th percentile of vol (min 252 vol observations)
  trend = px > SMA(200)
  In-trend:    alloc = 1.0, but 0.5 while vol > hi
  Below trend: alloc = 0.0, EXCEPT re-entry:
               px > SMA(20) AND vol < 0.9 * max(vol, last 60d)
               -> alloc = 1.0 (or 0.5 if vol still > hi)

Execution contract: signal from close N -> MOC order day N+1 -> position live
from N+2 (matches backtest eff = sig.shift(2)).

Data: Alpaca Market Data v2, daily bars, adjustment=all (splits + dividends)
so closes are total-return-adjusted, matching the rule's assumptions.
Endpoint: GET {ALPACA_DATA_ENDPOINT}/stocks/{SYM}/bars
          ?timeframe=1Day&adjustment=all&feed=sip&sort=asc (paginated)

Usage: python3 engine/signal_engine.py [--no-log]
Prints the full signal state as JSON on stdout; appends the same record to
log/signal_log.jsonl unless --no-log. Exit code 0 only on a valid signal.
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT, "log", "signal_log.jsonl")

SYM = "QLD"
START = "2006-06-01"  # QLD inception; feed may start later — warmup check governs
L_TREND, L_FAST, VOL_WIN, VOLMAX_WIN = 200, 20, 20, 60
FRAC, XQ = 0.9, 0.90
MIN_BARS = 273  # 20d vol needs 21 bars; expanding-252 percentile of vol -> 252+21

ET = ZoneInfo("America/New_York")


def fatal(msg):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_env():
    """Load repo-root .env into os.environ (no override of existing vars)."""
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


def api_get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        fatal(f"Alpaca API {e.code} on {url.split('?')[0]}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        fatal(f"Alpaca API unreachable: {e.reason}")


def alpaca_headers():
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        fatal("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def fetch_bars():
    """All daily bars for SYM, adjustment=all, ascending. Returns DataFrame."""
    data_ep = os.environ.get("ALPACA_DATA_ENDPOINT", "https://data.alpaca.markets/v2")
    headers = alpaca_headers()
    bars, token = [], None
    while True:
        params = {"timeframe": "1Day", "adjustment": "all", "feed": "sip",
                  "start": START, "limit": "10000", "sort": "asc"}
        if token:
            params["page_token"] = token
        url = f"{data_ep}/stocks/{SYM}/bars?{urllib.parse.urlencode(params)}"
        payload = api_get(url, headers)
        bars.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    if not bars:
        fatal("no bars returned")
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"]).dt.tz_convert(ET).dt.date
    df = df.set_index("date")[["c", "v"]].rename(columns={"c": "px", "v": "volume"})
    if df.index.has_duplicates:
        fatal("duplicate bar dates in feed")
    if not df.index.is_monotonic_increasing:
        fatal("bars not in ascending date order")
    if df.px.isna().any() or (df.px <= 0).any():
        fatal("missing or non-positive close prices")
    return df


def last_completed_session():
    """Most recent trading session whose close has passed (ET), via calendar."""
    api = os.environ.get("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2")
    now = dt.datetime.now(ET)
    start = (now - dt.timedelta(days=14)).date()
    url = (f"{api}/calendar?start={start}&end={now.date()}")
    cal = api_get(url, alpaca_headers())
    if not cal:
        fatal("empty trading calendar")
    completed = []
    for day in cal:
        close_t = dt.datetime.strptime(f"{day['date']} {day['close']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        if close_t <= now:
            completed.append(dt.date.fromisoformat(day["date"]))
    if not completed:
        fatal("no completed trading session in the last 14 days")
    return max(completed)


def compute_signals(px, ret):
    """The E2 rule, verbatim from qld_final_model.py. px/ret: pd.Series."""
    sma = px.rolling(L_TREND).mean()
    fsma = px.rolling(L_FAST).mean()
    vol = ret.rolling(VOL_WIN).std() * np.sqrt(252)
    volmax = vol.rolling(VOLMAX_WIN).max()
    hi = vol.expanding(252).quantile(XQ)
    trend = px > sma
    sig = pd.Series(0.0, index=px.index)
    sig[trend] = 1.0
    sig[trend & (vol > hi)] = 0.5
    reentry = (~trend) & (px > fsma) & (vol < FRAC * volmax)
    sig[reentry & (vol <= hi)] = 1.0
    sig[reentry & (vol > hi)] = 0.5
    sig[sma.isna() | hi.isna() | volmax.isna()] = np.nan
    bad = set(sig.dropna().unique()) - {0.0, 0.5, 1.0}
    if bad:
        fatal(f"invalid allocations {bad}")
    state = pd.DataFrame({"px": px, "sma200": sma, "sma20": fsma, "vol": vol,
                          "volmax60": volmax, "hi": hi, "trend": trend,
                          "signal_alloc": sig})
    return state


def main():
    no_log = "--no-log" in sys.argv[1:]
    load_env()
    df = fetch_bars()
    if len(df) < MIN_BARS:
        fatal(f"only {len(df)} bars; need >= {MIN_BARS} for warmup")
    session = last_completed_session()
    last_bar = df.index[-1]
    if last_bar != session:
        fatal(f"stale data: last bar {last_bar}, last completed session {session}")
    ret = df.px.pct_change()
    state = compute_signals(df.px, ret)
    row = state.iloc[-1]
    if pd.isna(row.signal_alloc):
        fatal("signal is NaN on latest bar despite warmup check")
    record = {
        "signal_date": str(last_bar),
        "computed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "bars_total": int(len(df)),
        "first_bar": str(df.index[0]),
        "px": round(float(row.px), 4),
        "sma200": round(float(row.sma200), 4),
        "sma20": round(float(row.sma20), 4),
        "vol": round(float(row.vol), 6),
        "vol_hi_p90": round(float(row.hi), 6),
        "volmax60": round(float(row.volmax60), 6),
        "trend": bool(row.trend),
        "vol_off_peak": bool(row.vol < FRAC * row.volmax60),
        "signal_alloc": float(row.signal_alloc),
    }
    if not no_log:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
