#!/usr/bin/env python3
"""Regenerate docs/dashboard/data.js from log/*.jsonl + frozen reference/ CSVs.

Deterministic, no LLM, no network, no credentials (e2bot-10 / dashboard Q3:
logs-only). HARD-FAILS on any parse mismatch — never publishes partial or
stale data. `--check` regenerates and exits non-zero if the result differs
from the committed docs/dashboard/data.js.

Sources
  log/signal_log.jsonl          engine signal records (live)
  log/trade_log.jsonl           execution records (live; `equity` only on
                                non-halted runs)
  log/ops_log.jsonl             routine-level events (live)
  reference/e2_backtest_daily.csv    frozen backtest daily series (2006-2026)
  reference/e2_backtest_trades.csv   frozen backtest trade log (200 trades)

Go-live anchor (dashboard Q6): the first trade_log record carrying an
`equity` field. Cross-checked against the ops_log `resume` event — if an
anchor exists without a preceding resume event, this script fails rather
than anchoring somewhere unexpected. No anchor yet => live panels render
the pre-first-run state.

Output is deterministic: "as of" derives from the newest log record, never
from the wall clock, so --check reproduces byte-identically on any day.
"""

import csv
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
SIGNAL_LOG = REPO / "log" / "signal_log.jsonl"
TRADE_LOG = REPO / "log" / "trade_log.jsonl"
OPS_LOG = REPO / "log" / "ops_log.jsonl"
BT_DAILY = REPO / "reference" / "e2_backtest_daily.csv"
BT_TRADES = REPO / "reference" / "e2_backtest_trades.csv"
OUT = REPO / "docs" / "dashboard" / "data.js"

ET = ZoneInfo("America/New_York")

# Engine-vs-backtest verification window (e2bot05-verification-report.md).
VERIFIED_START = "2017-01-31"
VERIFIED_END = "2026-07-28"


class ParseError(RuntimeError):
    pass


def fail(msg):
    raise ParseError(msg)


def need(rec, key, where):
    if key not in rec:
        fail(f"{where}: missing required field {key!r}: {rec}")
    return rec[key]


def et_date(iso_utc):
    """UTC ISO timestamp -> ET calendar date string."""
    return datetime.fromisoformat(iso_utc).astimezone(ET).date().isoformat()


def read_jsonl(path):
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

def parse_signals():
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


def parse_trades():
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


def parse_ops():
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


def derive_anchor(trades, ops):
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


def weekdays(d0, d1):
    d = date.fromisoformat(d0)
    end = date.fromisoformat(d1)
    while d <= end:
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def coverage(sigs, trades, ops):
    """AUDIT.md §1 rendered: every weekday from the first record to the last
    is covered by a signal/trade record (log), an ops record (ops), or is a
    GAP."""
    sig_days = {s["run"] for s in sigs}
    trd_days = {t["run_et"] for t in trades}
    ops_days = {o["date"] for o in ops}
    all_days = sig_days | trd_days | ops_days
    first, last = min(all_days), max(all_days)
    out = []
    for d in weekdays(first, last):
        if d in sig_days or d in trd_days:
            st = "log"
        elif d in ops_days:
            st = "ops"
        else:
            st = "gap"
        out.append({"d": d, "st": st})
    return out


# --------------------------------------------------------------------------
# Frozen backtest
# --------------------------------------------------------------------------

def read_ref_csv(path, expect_header):
    if not path.exists():
        fail(f"{path} missing")
    rows = []
    with path.open(encoding="utf-8") as f:
        rd = csv.reader(ln for ln in f if not ln.startswith("#"))
        hdr = next(rd, None)
        if hdr != expect_header:
            fail(f"{path.name}: header {hdr} != expected {expect_header}")
        rows.extend(rd)
    if not rows:
        fail(f"{path.name} has no data rows")
    return rows


def parse_backtest():
    rows = read_ref_csv(BT_DAILY, ["Period", "QLD_px", "QLD_ret",
                                   "Signal_alloc", "Effective_alloc", "New_ret"])
    days = []
    prev = None
    for r in rows:
        d = r[0]
        if prev is not None and d <= prev:
            fail(f"backtest daily: dates out of order at {d}")
        prev = d
        days.append({
            "d": d,
            "px": float(r[1]),
            "alloc": float(r[3]) if r[3] != "" else None,
            "ret": float(r[5]) if r[5] != "" else None,
        })
    # Curve starts at the first day the frozen model produced a return.
    start = next((i for i, x in enumerate(days) if x["ret"] is not None), None)
    if start is None:
        fail("backtest daily: no New_ret values at all")
    eq = 1.0
    qld0 = days[start - 1]["px"] if start > 0 else days[start]["px"]
    curve = []
    peak = 1.0
    for x in days[start:]:
        eq *= 1.0 + x["ret"]
        peak = max(peak, eq)
        curve.append({
            "d": x["d"],
            "eq": eq,
            "bh": x["px"] / qld0,
            "dd": eq / peak - 1.0,
            "alloc": x["alloc"],
        })
    return days, curve, start


def bt_stats(curve):
    d0, d1 = curve[0]["d"], curve[-1]["d"]
    years = (date.fromisoformat(d1) - date.fromisoformat(d0)).days / 365.25
    total = curve[-1]["eq"]
    bh_total = curve[-1]["bh"]
    cagr = total ** (1 / years) - 1
    bh_cagr = bh_total ** (1 / years) - 1
    maxdd = min(c["dd"] for c in curve)
    bh_peak, bh_maxdd = 0.0, 0.0
    for c in curve:
        bh_peak = max(bh_peak, c["bh"])
        bh_maxdd = min(bh_maxdd, c["bh"] / bh_peak - 1.0)
    rets = [curve[i]["eq"] / curve[i - 1]["eq"] - 1 for i in range(1, len(curve))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sharpe = mean / math.sqrt(var) * math.sqrt(252) if var > 0 else 0.0
    return {
        "start": d0, "end": d1, "years": round(years, 1),
        "total_x": round(total, 2), "bh_total_x": round(bh_total, 2),
        "cagr": round(cagr * 100, 2), "bh_cagr": round(bh_cagr * 100, 2),
        "maxdd": round(maxdd * 100, 1), "bh_maxdd": round(bh_maxdd * 100, 1),
        "sharpe": round(sharpe, 2),
    }


def parse_bt_trades():
    rows = read_ref_csv(BT_TRADES, ["Trade#", "Signal_date", "Exec_date",
                                    "Exec_price", "Effective_from",
                                    "Prev_alloc", "New_alloc", "Note"])
    out = []
    for r in rows:
        out.append({
            "n": int(r[0]), "sig": r[1], "exec": r[2],
            "px": float(r[3]), "eff": r[4],
            "prev": float(r[5]), "new": float(r[6]), "note": r[7],
        })
    if [t["n"] for t in out] != list(range(1, len(out) + 1)):
        fail("backtest trades: Trade# not a contiguous 1..N sequence")
    return out


# --------------------------------------------------------------------------
# Emit
# --------------------------------------------------------------------------

def jnum(v, nd=4):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def jstr(s):
    return json.dumps(s, ensure_ascii=False)


def emit(sigs, trades, ops, anchor, cov, curve, stats, bt_trades):
    lines = []
    add = lines.append
    as_of = max([s["run"] for s in sigs] + [t["run_et"] for t in trades]
                + [o["date"] for o in ops])

    add(f"// ===== E2 dashboard data — derived from log/*.jsonl + frozen "
        f"reference/, latest log {as_of} =====")
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

    # -- coverage strip
    add("const COVERAGE = [")
    for c in cov:
        add(f'{{d:{jstr(c["d"])},st:{jstr(c["st"])}}},')
    add("];")

    # -- backtest (frozen)
    add(f'const BT_VERIFIED = {{start:"{VERIFIED_START}",end:"{VERIFIED_END}",'
        f'agreePct:99.53,agreeRawPct:98.45,days:2385}};')
    add("const BT_STATS = " + json.dumps(stats) + ";")
    add("// [date, strategy equity (x), QLD buy&hold (x), drawdown, alloc]")
    add("const BT = [")
    for c in curve:
        add(f'[{jstr(c["d"])},{jnum(c["eq"], 4)},{jnum(c["bh"], 4)},'
            f'{jnum(c["dd"], 4)},{jnum(c["alloc"], 2)}],')
    add("];")
    add("const BT_TRADES = [")
    for t in bt_trades:
        add("{" + ",".join([
            f'n:{t["n"]}', f'sig:{jstr(t["sig"])}', f'exec:{jstr(t["exec"])}',
            f'px:{jnum(t["px"], 4)}', f'eff:{jstr(t["eff"])}',
            f'prev:{jnum(t["prev"], 2)}', f'nw:{jnum(t["new"], 2)}',
            f'note:{jstr(t["note"])}',
        ]) + "},")
    add("];")
    return "\n".join(lines) + "\n"


def main():
    sigs = parse_signals()
    trades = parse_trades()
    ops = parse_ops()
    anchor = derive_anchor(trades, ops)
    cov = coverage(sigs, trades, ops)
    _days, curve, _start = parse_backtest()
    stats = bt_stats(curve)
    bt_trades = parse_bt_trades()
    out = emit(sigs, trades, ops, anchor, cov, curve, stats, bt_trades)

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
          f"· anchor {'yes' if anchor else 'PRE-LIVE'} · coverage {len(cov)}d "
          f"· BT {len(curve)}d/{len(bt_trades)} trades")


if __name__ == "__main__":
    main()
