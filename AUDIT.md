# AUDIT.md — audit-trail convention

Decided 2026-08-04; the underlying decision lives in the decision record
(private research notes). Binding on every routine and every agent touching
this repo.

**Claim this trail is designed to support:** *every order this bot placed was
the mechanical output of the frozen E2 rule applied to the data the engine
saw that day — decided before the order, not reconstructed after it.*

Two independent records back that claim:

1. **This git repo** — what the bot saw and decided, timestamped by commits
   pushed before the close each day. Under our control.
2. **Alpaca's server-side record** — orders, timestamps, fills. Not under our
   control; the bot can only append to it, never edit or delete it.

Neither alone is convincing (we could rewrite the repo; Alpaca alone shows
trades with no reasoning). Together they are: a decision recorded in the repo
*before* the close, matched by an Alpaca order created at the timestamp the
repo claims, cannot be back-dated without forging Alpaca's record too.

---

## 1. The three logs

All logs are **append-only JSONL** under `log/`, one JSON object per line,
committed daily. Never edit or reorder a line; corrections are new lines
(see §5).

### `log/signal_log.jsonl` — what the engine saw and computed

Written by `engine/signal_engine.py`, one record per run:

| field | meaning |
|---|---|
| `signal_date` | session whose close produced the signal (day N) |
| `computed_at_utc` | when the engine ran |
| `bars_total`, `first_bar` | size and start of the fetched history |
| `bars_sha256` | SHA-256 of the exact (date, close) series consumed |
| `source_query` | the Alpaca bars URL the data came from (no keys) |
| `px`, `sma200`, `sma20`, `vol`, `vol_hi_p90`, `volmax60` | every input the rule branches on |
| `trend`, `vol_off_peak` | the two booleans the rule reads |
| `signal_alloc` | 0.0 / 0.5 / 1.0 — the decision |

`bars_sha256` is the input fingerprint: canonical form is one
`YYYY-MM-DD,<repr(close)>` line per bar, ascending, newline-terminated —
`engine/signal_engine.py: bars_digest()`. The raw bars are **not** stored
(they are ~2,700 rows/day of re-fetchable vendor data); the hash plus
`source_query` lets anyone re-fetch and prove they hold the same series.

**Contract: this is a log of runs, not of days** (e2bot-15, 2026-08-11).
Re-running `signal_engine.py` on the same day appends another record for the
same `signal_date`, and that is intended: recovery runs, determinism checks and
operator sessions are all legitimate, and the duplication is what *proves*
determinism — signal_date `2026-08-07` was computed three times on two machines
and produced `bars_sha256 21c9635e…` every time.

Two rules follow, and every reader of this log must apply them:

- **The last record for a `signal_date` is authoritative.** This is not a new
  convention; it is what the trader already does — `engine/execute.py` reads
  `lines[-1]` of this file and hard-fails if that record is not from the last
  completed session.
- **Deduplicate by `signal_date` before comparing records across days.** Any
  scan that diffs consecutive lines (allocation changes, transition counts)
  must collapse each `signal_date` to its last record first, or a re-run that
  straddles a genuine signal change will print a transition that never happened
  or hide one that did. The worked one-liner is `RUNBOOK.md` §3.

There are **three** readers of this log, and all three now apply those rules:
`engine/execute.py` (`lines[-1]`), `RUNBOOK.md` §3's allocation-change scan,
and the dashboard (`docs/dashboard/index.html`, via its `SIG_DAYS` helper).
`docs/dashboard/data.js` itself stays a faithful record of *runs* — it is
generated from this log verbatim, and its per-run table is where the
determinism evidence is meant to be visible. Deduplication happens at the
point of use, never in the data.

So the accounting rule below ("every trading day is accounted for") means
**at least one** record per trading day, not exactly one. A duplicate is never
a gap; zero records with no ops-log entry is.

### `log/trade_log.jsonl` — what was decided and sent

Written by `engine/execute.py`, one record per run (including no-trade days):
`run_at_utc`, `signal_date`, `signal_alloc`, `last_acted_alloc`, `equity`,
`cash`, `ref_px`, `current_qty`, `target_qty`, `requested_delta`,
`cash_cap_qty`, `capped_by_cash`, `buffer_shares`, `current_alloc`, `drift`,
`drift_band`, `action`, `order_id`, `dry_run`, and `order_reason` on ordering
runs. Records written before 2026-08-11 carry only the first-listed subset;
the fields arrived with e2bot-11 (drift) and e2bot-14 (cash).

The cash fields exist because QLD is non-marginable, so a buy is paid from
`cash` while `target_qty` is sized off `equity` (e2bot-14): `requested_delta`
is the order the target implies, `cash_cap_qty` what cash can pay for, and
`capped_by_cash` says which one was sent. `buffer_shares` is the headroom the
uncapped order would have had, in shares — it was 0.44 on the 2026-08-11
top-up, which is what the cap now trims.

`action` is one of: `submitted MOC <side> <qty> QLD (<reason>)` · `would
submit …` (dry-run) · `hold (signal unchanged; drift …% within band …)` ·
`hold (already at target)` · `hold (buy N unaffordable: cash … pays for 0
shares)` · `HALTED by HALT file — no order` (adds `halt_reason`).

`equity` on every line is also the daily equity series the weekly rollup and
all forward-test comparisons read.

### `log/ops_log.jsonl` — what happened around the run

Written by the routine via `bash scripts/oplog.sh <event> "<note>"`, because
the engine cannot record days it never ran: `market-closed`, `failure`,
`halt`, `resume`, `manual`, `note`. Fields: `at_utc`, `et_date`, `event`,
`note`.

Rule: **every trading day is accounted for** — either by at least one
signal+trade record, or by an ops record saying why not. A trading day with
neither is a gap, and the runbook's liveness check exists to catch it.
*More* than one signal record for a day is normal, not a gap (see the
run-log contract above).

`log/last_acted_signal.json` is *state*, not audit: it is the last signal
acted on, overwritten each run. It is committed so the cloud runtime (fresh
clone every day) can read it, but the trade log is the record of truth.

## 2. Commit convention

One commit per run, pushed the same run — a cloud routine's disk vanishes, so
an uncommitted decision never existed.

```
E2 daily YYYY-MM-DD: alloc=100% <order id abc-123 | no trade | HALTED | FAILURE: reason>
```

- Only `log/` (and `HALT`, when halting) changes in a daily commit. A daily
  commit that touches `engine/` is a red flag by construction.
- Engine, rule, or convention changes are **separate commits**, never mixed
  into a daily one, and require a decision recorded in the decision record
  first (`CLAUDE.md` hard rules).
- Never rewrite history in this repo — no amend, no rebase, no force-push on
  `main`. The trail's value is that it is append-only.

## 3. Cross-reference: how a third party reconciles the two records

For any day D, without trusting us:

1. **Read the repo at that point in time.** `git log --since=D --until=D+1`
   gives the day's commit; `git show <sha>:log/signal_log.jsonl` gives the
   signal record as it stood. The commit timestamp must precede the 16:00 ET
   close.
2. **Check the decision was mechanical.** Re-run
   `python3 engine/signal_engine.py` (or the rule by hand — it is 8 lines,
   README "The E2 rule") on the same series. Same `bars_sha256` ⇒ same
   inputs ⇒ the rule must produce the logged `signal_alloc`. The logged
   `px`/`sma200`/`sma20`/`vol`/`vol_hi_p90`/`volmax60` make the branch taken
   checkable on paper without re-running anything.
3. **Check the order followed the decision.** `target_qty` must equal
   `floor(signal_alloc × equity / ref_px)` and `requested_delta` must equal
   `target_qty − current_qty` (execution spec rule 2). An order must exist only
   when `signal_alloc ≠ last_acted_alloc` **or** `drift > drift_band` (rule 3,
   as amended by e2bot-11). Its qty is `requested_delta` capped on buys at
   `cash_cap_qty = floor(cash × 0.995 / ref_px)` (e2bot-14) — a sell is never
   capped, and a cap that bites must show `capped_by_cash: true` and, on an
   ordering run, say so in `order_reason`. (`capped_by_cash` describes the
   sizing, not the action, so it can also read true on a hold — a
   rounding-sized buy the drift band suppressed anyway.)
4. **Match against Alpaca.** `bash scripts/alpaca.sh orders all` (or the
   dashboard) — the `order_id` in the trade log must exist server-side, with
   the same symbol, side, qty, `time_in_force=cls`, and a `submitted_at`
   consistent with the commit's timestamp. Its fill is Alpaca's, not ours.
5. **Check for orders with no decision behind them.** Every Alpaca order in
   the period must appear in the trade log. An Alpaca order with no matching
   log line is the failure this whole convention is built to expose — it means
   something traded outside the rule.

Direction 5 matters more than 4: forging *extra* repo entries proves nothing,
but an Alpaca order our log cannot explain breaks the claim outright.

## 4. What this trail does **not** prove

Stated so the argument is not oversold:

- **Not third-party timestamped.** Commit times are self-asserted (Alpaca's
  order times are not). A determined operator could rewrite the repo and
  force-push; the defence is convention (§2) plus the fact that Alpaca's
  independent record would have to be forged in step too, which it cannot be.
- **Not a proof the vendor data was right** — only that the engine acted on
  the series it recorded, from a named query.
- **Paper only.** No real capital, so fills are Alpaca's simulation, not
  market reality.
- **Bars are not archived**, only hashed. If Alpaca restates history
  (adjustment changes), the old hash stops reproducing — that is a *feature*
  (it flags the restatement) but it means step 2 can require a same-vintage
  feed.

## 5. Corrections

Mistakes are appended, never erased. Write a `note` ops record naming the
affected `signal_date` and what was wrong, and (if it changed the position) a
`manual` record describing the intervention. The wrong line stays.

## 6. Retention

The repo is the archive: never prune `log/`. Snapshot Alpaca's order history
to `log/alpaca-orders-<date>.json` before any account reset — a reset wipes
the server-side witness (this happened to an earlier bot on this account;
that snapshot is kept in private research notes).
