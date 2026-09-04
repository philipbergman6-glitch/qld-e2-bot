# qld-e2-bot

[![ci](https://github.com/philipbergman6-glitch/qld-e2-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/philipbergman6-glitch/qld-e2-bot/actions/workflows/ci.yml)

**Live dashboard:** <https://philipbergman6-glitch.github.io/qld-e2-bot/dashboard/>
— every number on it is derived from the committed `log/*.jsonl` by a
deterministic parser (`scripts/build_dashboard_data.py`), no API calls.

**Status (as of 2026-09-03):** paper forward test running since 2026-08-05 —
22 trading days elapsed, 20 with a logged run (2 missed by scheduler
misfires, both accounted for in `log/ops_log.jsonl`), 6 MOC orders
submitted. Incident history: [`docs/INCIDENTS.md`](docs/INCIDENTS.md).

Autonomous paper-trading bot for the frozen **E2 QLD rule** ("graded
vol-confirmed re-entry"). Deterministic Python computes the signal; Claude
glue only schedules, executes, and reports. Alpaca paper account only.

E2 is my own backtested rule. This repo trades a **paper** account only, is a
systems-engineering exercise (deterministic engine, LLM as scheduler, git as
audit trail), and is not investment advice.

## Architecture

Git is the home and the truth. Alpaca is execution only, plus its market-data
API as the bars source and its immutable order/fill records as the
independent witness.

```
engine/signal_engine.py   fetch QLD daily bars (Alpaca, adjustment=all),
                          compute E2 signal, append to log/signal_log.jsonl
engine/execute.py         map signal -> at most one MOC order (spec below),
                          append to log/trade_log.jsonl
scripts/alpaca.sh         bash wrapper for ad-hoc Alpaca calls (Claude glue)
scripts/email.sh          Resend email wrapper (reports)
scripts/oplog.sh          append a routine-level event to log/ops_log.jsonl
reference/                backtest reference signals (derived aggregates only)
log/                      append-only signal + trade + ops logs, committed daily
HALT                      kill switch (absent normally): present => no orders
```

Audit-trail format and the git↔Alpaca reconciliation procedure: **`AUDIT.md`**.
Operator procedures (liveness, failures, halt/resume): **`RUNBOOK.md`**.

## The E2 rule (frozen — never modify without a new decision)

Signal from day-N close, QLD price/returns only:

- `vol` = 20d realized vol of daily returns, annualized
- `hi` = expanding 90th percentile of vol (min 252 vol observations)
- `trend` = px > SMA(200)
- In-trend: alloc = 1.0, but 0.5 while vol > hi
- Below trend: alloc = 0.0, except re-entry (px > SMA(20) and
  vol < 0.9 × 60d max vol) → 1.0, or 0.5 if vol still > hi

## Execution spec (decided 2026-08-02)

1. Signal from close N → **market-on-close** order day N+1 (submitted before
   15:45 ET) → position live from N+2. Matches backtest `sig.shift(2)`.
2. Whole shares, round down; target dollars = alloc × account equity. A buy is
   additionally capped at what **cash** can pay for, `floor(cash × 0.995 /
   ref_px)` — QLD is non-marginable, so equity is the target but cash is the
   funding (amended 2026-08-11 after three consecutive 0-fills; sells are
   never capped).
3. Trade on signal change **or** when realized allocation has drifted more than
   1% from the signal (amended 2026-08-10; the original spec never rebalanced
   for drift, which made partial fills structurally invisible).
4. Missed days self-heal: next run trades toward that day's fresh signal;
   never back-fill.
5. Paper account reset to $100,000 before go-live.

## Data feed decision

`GET https://data.alpaca.markets/v2/stocks/QLD/bars` with
`timeframe=1Day&adjustment=all&feed=sip&sort=asc`, paginated.
`adjustment=all` (splits **and** dividends) so closes are total-return
adjusted, matching the backtest's return series. Engine hard-fails if
history < 273 bars (SMA200 + expanding-252 vol percentile warmup) or if the
latest bar ≠ the last completed session (Alpaca calendar).

Note: Alpaca v2 historical coverage may start later than QLD inception
(2006). The reference test measures the actual overlap; warmup only
needs ~273 bars, so signals are still valid from ~13 months after the feed's
first bar.

## Daily run (trading days, before 15:45 ET)

```
python3 engine/signal_engine.py     # yesterday's-close signal, logged
python3 engine/execute.py           # at most one MOC order, logged
git add log/ && git commit          # audit trail (convention: AUDIT.md)
```

Both scripts hard-fail (non-zero exit) on any invalid/stale input — a failed
run trades nothing.

## Schedule (decided 2026-08-02)

Runtime: **Claude Code cloud routines** on this repo, prompts in
`routines/` — paste verbatim, env vars set on the routine, never in a
committed file.

| Routine      | Time (ET)     | Prompt               | Does                        |
|--------------|---------------|----------------------|-----------------------------|
| E2BOT-daily  | 12:00 Mon–Fri | `routines/daily.md`  | signal → order → commit → heartbeat email |
| E2BOT-weekly | 16:20 Fri     | `routines/weekly.md` | read-only rollup email      |

Why 12:00 ET: the previous close is final, and a MOC order submitted at
noon rests until the close — automatically safe on early-close days
(~12:45 ET MOC cutoff) with a single schedule. Holidays: the daily routine
checks the Alpaca clock and sends a "market closed" heartbeat instead.

Failure behavior: any non-zero script exit → no trade, one FAILURE email,
stop; the missed day self-heals at the next run (execution spec rule 4).
Silence is failure — no daily email by ~12:15 ET on a trading day means
the run died before its alert step.

## Built with

Built with Claude Code as pair-programmer. The LLM has two roles here, both
deliberately narrow: at build time it wrote code against decisions that were
made and recorded first; at run time it executes `routines/daily.md` as a
constrained scheduler — run the scripts, commit the logs, send the email,
never touch the signal. The engine itself has no LLM in the loop. Every
design decision (drift gate, cash cap, run-log contract, audit convention)
is recorded in the commit body that introduced it and in `AUDIT.md`, so
`git log` reads as the decision record.

## Setup

- Python ≥ 3.9 (the engine uses stdlib `zoneinfo`); tested on 3.11–3.14.
- `pip install -r requirements.txt` (pandas + numpy, a tested range —
  see the file header; `requirements.lock` is the operator's exact set).
  HTTP is stdlib `urllib` only.
- `cp env.template .env` and fill Alpaca **paper** keys (never committed).
- `python3 -m unittest discover -s tests` — offline, no keys; includes the
  frozen-rule check against `reference/e2_reference_signals.csv`.
