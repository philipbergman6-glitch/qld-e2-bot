#!/usr/bin/env python3
"""Regenerate docs/dashboard/data.js from log/*.jsonl.

Deterministic, no LLM, no network, no credentials (design decision:
logs-only). HARD-FAILS on any parse mismatch — never publishes partial or
stale data. `--check` regenerates and exits non-zero if the result differs
from the committed docs/dashboard/data.js.

Sources
  log/signal_log.jsonl          engine signal records (live)
  log/trade_log.jsonl           execution records (live; `equity` only on
                                non-halted runs)
  log/ops_log.jsonl             routine-level events (live)
  log/close_log.jsonl           end-of-day equity + QLD buy & hold marks
  reference/e2_backtest_daily.csv  frozen backtest returns; only AGGREGATES
                                (vol, drawdown, rolling-window quantiles) are
                                emitted, never the series

Go-live anchor (dashboard Q6): the first trade_log record carrying an
`equity` field. Cross-checked against the ops_log `resume` event — if an
anchor exists without a preceding resume event, this script fails rather
than anchoring somewhere unexpected. No anchor yet => live panels render
the pre-first-run state.

Output is deterministic: "as of" derives from the newest log record, never
from the wall clock, so --check reproduces byte-identically on any day.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
SIGNAL_LOG = REPO / "log" / "signal_log.jsonl"
TRADE_LOG = REPO / "log" / "trade_log.jsonl"
OPS_LOG = REPO / "log" / "ops_log.jsonl"
CLOSE_LOG = REPO / "log" / "close_log.jsonl"
BACKTEST = REPO / "reference" / "e2_backtest_daily.csv"
OUT = REPO / "docs" / "dashboard" / "data.js"

# NYSE full-day closures. Coverage and staleness must not read a holiday as a
# missed run. Hard-fail outside the listed years: extend deliberately.
NYSE_HOLIDAYS: dict[int, set[str]] = {
    2026: {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
           "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"},
    2027: {"2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
           "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"},
}
QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)

ET = ZoneInfo("America/New_York")

class ParseError(RuntimeError):
    pass


def fail(msg: str) -> NoReturn:
    raise ParseError(msg)


def need(rec: dict[str, Any], key: str, where: str) -> Any:
    if key not in rec:
        fail(f"{where}: missing required field {key!r}: {rec}")
    return rec[key]


def et_date(iso_utc: str) -> str:
    """UTC ISO timestamp -> ET calendar date string."""
    return datetime.fromisoformat(iso_utc).astimezone(ET).date().isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        fail(f"{path} missing")
    recs = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError as e:
            fail(f"{path.name}:{n}: invalid JSON ({e})")
    if not recs:
        fail(f"{path.name} is empty")
    return recs


# --------------------------------------------------------------------------
# Live logs
# --------------------------------------------------------------------------

def parse_signals() -> list[dict[str, Any]]:
    sigs = []
    for r in read_jsonl(SIGNAL_LOG):
        s = {
            "date": need(r, "signal_date", "signal_log"),
            "run": et_date(need(r, "computed_at_utc", "signal_log")),
            "px": float(need(r, "px", "signal_log")),
            "sma200": float(need(r, "sma200", "signal_log")),
            "sma20": float(need(r, "sma20", "signal_log")),
            "vol": float(need(r, "vol", "signal_log")),
            "hi": float(need(r, "vol_hi_p90", "signal_log")),
            "volmax60": float(need(r, "volmax60", "signal_log")),
            "trend": bool(need(r, "trend", "signal_log")),
            "offpeak": bool(need(r, "vol_off_peak", "signal_log")),
            "alloc": float(need(r, "signal_alloc", "signal_log")),
            "sha": r.get("bars_sha256"),
            "src": r.get("source_query"),
            "bars": r.get("bars_total"),
        }
        if s["alloc"] not in (0.0, 0.5, 1.0):
            fail(f"signal_log: invalid signal_alloc {s['alloc']}")
        sigs.append(s)
    return sigs


def parse_trades() -> list[dict[str, Any]]:
    trs = []
    for r in read_jsonl(TRADE_LOG):
        t = {
            "run": need(r, "run_at_utc", "trade_log"),
            "run_et": et_date(r["run_at_utc"]),
            "sig_date": need(r, "signal_date", "trade_log"),
            "alloc": float(need(r, "signal_alloc", "trade_log")),
            "action": need(r, "action", "trade_log"),
            "order_id": r.get("order_id"),
            "dry": bool(r.get("dry_run", False)),
            "halt_reason": r.get("halt_reason"),
        }
        # equity/qty fields exist only on non-halted runs — keep optional,
        # but if any is present its shape must be right.
        for k in ("equity", "ref_px"):
            if k in r:
                t[k] = float(r[k])
        for k in ("current_qty", "target_qty"):
            if k in r:
                t[k] = int(r[k])
        if "last_acted_alloc" in r:
            t["last_acted"] = r["last_acted_alloc"]
        trs.append(t)
    return trs


def parse_ops() -> list[dict[str, Any]]:
    KNOWN = {"market-closed", "failure", "halt", "resume", "manual", "note"}
    ops = []
    for r in read_jsonl(OPS_LOG):
        o = {
            "at": need(r, "at_utc", "ops_log"),
            "date": need(r, "et_date", "ops_log"),
            "event": need(r, "event", "ops_log"),
            "note": need(r, "note", "ops_log"),
        }
        if o["event"] not in KNOWN:
            fail(f"ops_log: unknown event type {o['event']!r} — extend the "
                 f"parser deliberately, never skip a record")
        ops.append(o)
    return ops


def parse_closes() -> list[dict[str, Any]]:
    closes = []
    for r in read_jsonl(CLOSE_LOG):
        c = {
            "session": need(r, "session", "close_log"),
            "equity": float(need(r, "equity", "close_log")),
            "qld_tr": float(need(r, "qld_tr", "close_log")),
        }
        if closes and c["session"] <= closes[-1]["session"]:
            fail(f"close_log: session {c['session']} not after {closes[-1]['session']}")
        closes.append(c)
    return closes


def derive_anchor(trades: list[dict[str, Any]], ops: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First trade record carrying equity = go-live anchor (Q6 option A),
    cross-checked against the ops resume event."""
    live = [t for t in trades if "equity" in t and not t["dry"]]
    if not live:
        return None
    a = live[0]
    resumes = [o for o in ops if o["event"] == "resume"]
    if not any(o["date"] <= a["run_et"] for o in resumes):
        fail(f"anchor {a['run_et']} (first equity record) has no preceding "
             f"ops resume event — refusing to anchor")
    return a


def weekdays(d0: str, d1: str) -> Iterator[str]:
    d = date.fromisoformat(d0)
    end = date.fromisoformat(d1)
    while d <= end:
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def coverage(
    sigs: list[dict[str, Any]], trades: list[dict[str, Any]], ops: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """AUDIT.md §1 rendered: every weekday from the first record to the last
    is an exchange holiday (closed), covered by a signal/trade record (log),
    an ops record (ops), or is a GAP."""
    sig_days = {s["run"] for s in sigs}
    trd_days = {t["run_et"] for t in trades}
    ops_days = {o["date"] for o in ops}
    all_days = sig_days | trd_days | ops_days
    first, last = min(all_days), max(all_days)
    out = []
    for d in weekdays(first, last):
        if int(d[:4]) not in NYSE_HOLIDAYS:
            fail(f"coverage: no NYSE holiday calendar for {d[:4]} — extend NYSE_HOLIDAYS")
        if d in NYSE_HOLIDAYS[int(d[:4])]:
            st = "closed"
        elif d in sig_days or d in trd_days:
            st = "log"
        elif d in ops_days:
            st = "ops"
        else:
            st = "gap"
        out.append({"d": d, "st": st})
    return out


# --------------------------------------------------------------------------
# Backtest reference (aggregates only)
# --------------------------------------------------------------------------

def quantile(xs: list[float], q: float) -> float:
    """Linear interpolation between order statistics (numpy default)."""
    ys = sorted(xs)
    h = (len(ys) - 1) * q
    lo = math.floor(h)
    return ys[lo] + (h - lo) * (ys[min(lo + 1, len(ys) - 1)] - ys[lo])


def ann_vol(rets: list[float]) -> float:
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)


def max_dd(rets: list[float]) -> float:
    level, peak, worst = 1.0, 1.0, 0.0
    for r in rets:
        level *= 1 + r
        peak = max(peak, level)
        worst = min(worst, level / peak - 1)
    return worst


def backtest(closes: list[dict[str, Any]]) -> dict[str, Any]:
    """Full-period stats for E2 and QLD, plus the distribution of every
    overlapping N-session window, N = live close-to-close returns so far."""
    rows = []
    with BACKTEST.open(encoding="utf-8") as f:
        for r in csv.DictReader(line for line in f if not line.startswith("#")):
            if r["New_ret"] == "":
                continue
            try:
                rows.append((r["Period"], float(r["New_ret"]), float(r["QLD_ret"])))
            except (KeyError, ValueError) as e:
                fail(f"{BACKTEST.name}: bad row {r} ({e})")
    if len(rows) < 252:
        fail(f"{BACKTEST.name}: only {len(rows)} return rows")
    e2 = [x[1] for x in rows]
    qld = [x[2] for x in rows]
    live = [closes[i]["equity"] / closes[i - 1]["equity"] - 1 for i in range(1, len(closes))]
    n = len(live)
    out: dict[str, Any] = {
        "from": rows[0][0], "to": rows[-1][0], "days": len(rows),
        "vol": ann_vol(e2), "dd": max_dd(e2),
        "cagr": math.prod(1 + r for r in e2) ** (252 / len(e2)) - 1,
        "qldVol": ann_vol(qld), "qldDd": max_dd(qld),
        "qldCagr": math.prod(1 + r for r in qld) ** (252 / len(qld)) - 1,
        "n": n,
    }
    if n < 2:
        return out
    win_ret, win_dd, win_vol = [], [], []
    for i in range(len(e2) - n + 1):
        w = e2[i:i + n]
        win_ret.append(math.prod(1 + r for r in w) - 1)
        win_dd.append(max_dd(w))
        win_vol.append(ann_vol(w))
    live_ret = math.prod(1 + r for r in live) - 1
    live_dd = max_dd(live)
    live_vol = ann_vol(live)
    out.update({
        "windows": len(win_ret),
        "retQ": [quantile(win_ret, q) for q in QUANTILES],
        "ddQ": [quantile(win_dd, q) for q in QUANTILES],
        "volQ": [quantile(win_vol, q) for q in QUANTILES],
        "retPct": sum(x <= live_ret for x in win_ret) / len(win_ret),
        "ddPct": sum(x <= live_dd for x in win_dd) / len(win_dd),
        "volPct": sum(x <= live_vol for x in win_vol) / len(win_vol),
    })
    return out


# --------------------------------------------------------------------------
# Emit
# --------------------------------------------------------------------------

def jnum(v: Any, nd: int = 4) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def jstr(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def emit(
    sigs: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    ops: list[dict[str, Any]],
    anchor: dict[str, Any] | None,
    cov: list[dict[str, str]],
    closes: list[dict[str, Any]],
    bt: dict[str, Any],
) -> str:
    lines = []
    add = lines.append
    as_of = max([s["run"] for s in sigs] + [t["run_et"] for t in trades]
                + [o["date"] for o in ops] + [c["session"] for c in closes])

    add(f"// ===== E2 dashboard data — derived from log/*.jsonl, "
        f"latest log {as_of} =====")
    add("// Regenerate: python3 scripts/build_dashboard_data.py  "
        "(--check verifies byte-identity). NEVER hand-edit.")
    add(f'const AS_OF = "{as_of}";')

    # -- live signals
    add("const SIG = [")
    for s in sigs:
        add("{" + ",".join([
            f'd:{jstr(s["date"])}', f'run:{jstr(s["run"])}',
            f'px:{jnum(s["px"], 4)}', f'sma200:{jnum(s["sma200"], 4)}',
            f'sma20:{jnum(s["sma20"], 4)}', f'vol:{jnum(s["vol"], 6)}',
            f'hi:{jnum(s["hi"], 6)}', f'volmax60:{jnum(s["volmax60"], 6)}',
            f'trend:{jnum(s["trend"])}', f'offpeak:{jnum(s["offpeak"])}',
            f'alloc:{jnum(s["alloc"], 2)}',
            f'sha:{jstr(s["sha"]) if s["sha"] else "null"}',
            f'src:{jstr(s["src"]) if s["src"] else "null"}',
            f'bars:{s["bars"] if s["bars"] is not None else "null"}',
        ]) + "},")
    add("];")

    # -- live trades
    add("const TRD = [")
    for t in trades:
        parts = [
            f'run:{jstr(t["run"])}', f'runEt:{jstr(t["run_et"])}',
            f'sig:{jstr(t["sig_date"])}', f'alloc:{jnum(t["alloc"], 2)}',
            f'action:{jstr(t["action"])}',
            f'orderId:{jstr(t["order_id"]) if t["order_id"] else "null"}',
            f'dry:{jnum(t["dry"])}',
        ]
        parts.append(f'equity:{jnum(t.get("equity"), 2)}')
        parts.append(f'refPx:{jnum(t.get("ref_px"), 4)}')
        parts.append(f'curQty:{t["current_qty"] if "current_qty" in t else "null"}')
        parts.append(f'tgtQty:{t["target_qty"] if "target_qty" in t else "null"}')
        la = t.get("last_acted")
        parts.append(f'lastActed:{jnum(la, 2) if la is not None else "null"}')
        hr = t.get("halt_reason")
        parts.append(f'haltReason:{jstr(hr) if hr else "null"}')
        add("{" + ",".join(parts) + "},")
    add("];")

    # -- ops
    add("const OPS = [")
    for o in ops:
        add("{" + ",".join([
            f'at:{jstr(o["at"])}', f'd:{jstr(o["date"])}',
            f'ev:{jstr(o["event"])}', f'note:{jstr(o["note"])}',
        ]) + "},")
    add("];")

    # -- anchor
    if anchor:
        add("const ANCHOR = {" + ",".join([
            f'd:{jstr(anchor["run_et"])}', f'equity:{jnum(anchor["equity"], 2)}',
            f'refPx:{jnum(anchor.get("ref_px"), 4)}',
        ]) + "};")
    else:
        add("const ANCHOR = null; // no live equity record yet — pre-first-run")

    # -- end-of-day marks (both lines at the official close)
    add("const CLOSE = [")
    for c in closes:
        add(f'{{d:{jstr(c["session"])},equity:{jnum(c["equity"], 2)},'
            f'qldTr:{jnum(c["qld_tr"], 6)}}},')
    add("];")

    # -- coverage strip
    add("const COVERAGE = [")
    for c in cov:
        add(f'{{d:{jstr(c["d"])},st:{jstr(c["st"])}}},')
    add("];")

    # -- exchange holidays (staleness check on the page)
    add("const HOLIDAYS = [" + ",".join(
        jstr(d) for y in sorted(NYSE_HOLIDAYS) for d in sorted(NYSE_HOLIDAYS[y])) + "];")

    # -- backtest aggregates
    def jval(v: Any) -> str:
        if isinstance(v, str):
            return jstr(v)
        if isinstance(v, list):
            return "[" + ",".join(jnum(x, 6) for x in v) + "]"
        return jnum(v, 6)
    add("const BACKTEST = {" + ",".join(f"{k}:{jval(v)}" for k, v in bt.items()) + "};")

    return "\n".join(lines) + "\n"


def main() -> None:
    sigs = parse_signals()
    trades = parse_trades()
    ops = parse_ops()
    anchor = derive_anchor(trades, ops)
    cov = coverage(sigs, trades, ops)
    closes = parse_closes()
    out = emit(sigs, trades, ops, anchor, cov, closes, backtest(closes))

    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else None
        if current != out:
            print("MISMATCH: regenerated data.js differs from committed "
                  "docs/dashboard/data.js", file=sys.stderr)
            sys.exit(1)
        print("OK: data.js reproduces committed values")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(out, encoding="utf-8")
    print(f"wrote {OUT} — SIG {len(sigs)} · TRD {len(trades)} · OPS {len(ops)} "
          f"· CLOSE {len(closes)} · anchor {'yes' if anchor else 'PRE-LIVE'} "
          f"· coverage {len(cov)}d")


if __name__ == "__main__":
    main()
