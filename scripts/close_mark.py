#!/usr/bin/env python3
"""Append end-of-day marks to log/close_log.jsonl — account equity and QLD
buy & hold, both at the official close, so the dashboard compares like with like.

Read-only against Alpaca: places no orders, touches no engine state. Appends one
record per completed session not yet logged (first run backfills from BASE).
Exits 0 with no change when already up to date. HARD-FAILS on missing data.
Keys come from the process environment (cloud routine) or the repo-root .env
(operator clone), the same way the engine scripts load them.

Fields: session (ET date), recorded_at_utc, equity (Alpaca daily closing
equity), qld_close (raw close), qld_tr (total-return factor vs the BASE close:
adjusted close / adjusted BASE close — later dividend adjustments rescale both,
so a written factor never goes stale).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "engine"))
from _common import load_env  # noqa: E402  (stdlib-only helper; engine is not modified)

LOG = REPO / "log" / "close_log.jsonl"
ET = ZoneInfo("America/New_York")
SYM = "QLD"
BASE = "2026-08-04"  # last close before the first order (ops_log resume 2026-08-05)
SETTLE = timedelta(minutes=30)  # closing data is final well before this


def fatal(msg: str) -> NoReturn:
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def get(url: str, params: dict[str, str]) -> Any:
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        fatal("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        fatal(f"Alpaca API {e.code} on {url}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        fatal(f"Alpaca API unreachable: {e.reason}")


def main() -> None:
    load_env()
    api = os.environ.get("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2")
    data = os.environ.get("ALPACA_DATA_ENDPOINT", "https://data.alpaca.markets/v2")
    now = datetime.now(timezone.utc)

    logged = [json.loads(line)["session"] for line in LOG.read_text().splitlines()
              if line.strip()] if LOG.exists() else []
    start = (date.fromisoformat(logged[-1]) + timedelta(days=1)).isoformat() if logged else BASE
    today = now.astimezone(ET).date().isoformat()
    if start > today:
        print("up to date")
        return

    cal = get(f"{api}/calendar", {"start": start, "end": today})
    sessions = [c["date"] for c in cal
                if datetime.fromisoformat(f"{c['date']}T{c['close']}").replace(tzinfo=ET)
                + SETTLE <= now]
    if not sessions:
        print("up to date")
        return
    last = sessions[-1]

    hist = get(f"{api}/account/portfolio/history",
               {"start": BASE, "timeframe": "1D",  # end is exclusive: +1 day
                "end": (date.fromisoformat(last) + timedelta(days=1)).isoformat(),
                "intraday_reporting": "market_hours"})
    equity = {datetime.fromtimestamp(t, timezone.utc).astimezone(ET).date().isoformat(): e
              for t, e in zip(hist["timestamp"], hist["equity"])}

    def closes(adjustment: str) -> dict[str, float]:
        bars = get(f"{data}/stocks/{SYM}/bars",
                   {"timeframe": "1Day", "start": BASE, "end": last, "feed": "sip",
                    "adjustment": adjustment, "limit": "10000"})["bars"]
        return {b["t"][:10]: float(b["c"]) for b in bars}

    raw, adj = closes("raw"), closes("all")
    if BASE not in adj:
        fatal(f"no {SYM} bar for base session {BASE}")

    ts = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    lines = []
    for s in sessions:  # validate everything before writing anything
        if equity.get(s) is None or s not in raw or s not in adj:
            fatal(f"incomplete closing data for {s}: equity={equity.get(s)} "
                  f"bar={'yes' if s in raw else 'no'}")
        lines.append(json.dumps({"session": s, "recorded_at_utc": ts,
                                 "equity": round(equity[s], 2), "qld_close": raw[s],
                                 "qld_tr": round(adj[s] / adj[BASE], 6)}))
    with LOG.open("a") as f:
        f.write("".join(line + "\n" for line in lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
