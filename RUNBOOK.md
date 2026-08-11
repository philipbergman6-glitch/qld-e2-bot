# RUNBOOK.md — operating the E2 paper bot

For the human operator. The bot runs itself; this is how to check it, read it,
fix it, and stop it. Decided 2026-08-04 (wayfinder ticket `e2bot-07`).

Ground rules that override anything below: **the signal comes from
`engine/*.py`, never from judgment**; **a failed run trades nothing and
self-heals tomorrow**; **never rewrite `log/` history**.

---

## 1. Normal day

12:00 ET Mon–Fri the `E2BOT-daily` cloud routine runs `routines/daily.md`:
clock check → `signal_engine.py` → `execute.py` → account snapshot →
heartbeat email → commit + push. Friday 16:20 ET `E2BOT-weekly` sends a
read-only rollup.

**Expected evidence, every trading day:** one heartbeat email by ~12:15 ET,
and one `E2 daily <date>` commit on `main`.

## 2. Is it alive?

Three checks, cheapest first:

```bash
# 1. Did today's commit land? (from a clone, on main)
git pull && git log --oneline -5

# 2. Do the logs cover every trading day this week?
tail -5 log/signal_log.jsonl
tail -5 log/trade_log.jsonl
tail -5 log/ops_log.jsonl        # market-closed / failure / halt entries

# 3. Does Alpaca agree?
bash scripts/alpaca.sh account
bash scripts/alpaca.sh positions
bash scripts/alpaca.sh orders all
```

**Silence is failure.** No email and no commit on a trading day means the run
died before its alert step — check the routine's run history in the Claude
Code cloud UI first, since a dead routine writes nothing anywhere.

Liveness verdict:

| Symptom | Meaning |
|---|---|
| Email + commit | healthy |
| Email says `market closed`, ops log record | healthy (holiday) |
| `FAILURE` email, no trade | run died safely; §4 |
| Nothing at all | routine never ran or died early; §4, start at the cloud UI |
| Alpaca order with no trade-log line | **stop and investigate** — §5 halt first |

## 2b. Execution-timing check (weekly, with the rollup)

The backtest convention is **signal at close N → MOC order placed N+1 →
position effective from N+2**. This is part of the spec, not a detail:
measured on the E10 overlay work (2026-08-09, `QLD-model`
`wayfinder/assets/e10-tv-stepb-reconciliation.md`), executing one day later
silently turns a −34% worst-case into −47%. The same shift(3) haircut was
measured on E9 (Martin 1.331 → 0.983). A late bot *looks* healthy — nothing
errors — so this must be checked against fills, not logs alone:

```bash
# For the most recent trade: signal_log date must be exactly one trading
# day BEFORE the Alpaca fill date (MOC fills at the N+1 close).
tail -1 log/signal_log.jsonl | python3 -m json.tool | grep -E 'date|alloc'
bash scripts/alpaca.sh orders all   # compare filled_at vs signal date
```

Signal date == fill date, or fill two+ trading days after signal → the bot
is running a different (worse) strategy than the backtest. Halt (§5) and
reconcile before the next run.

## 3. Reading the logs

```bash
# last signal, human-readable
tail -1 log/signal_log.jsonl | python3 -m json.tool

# every allocation change ever
# NOTE: the log records RUNS, not days — a day can hold several records
# (recovery runs, determinism checks, operator sessions). Collapse each
# signal_date to its LAST record — the authoritative one, and the one
# execute.py acts on — before diffing, or a re-run that straddles a real
# signal change prints a phantom transition or hides a real one. See AUDIT.md.
python3 -c "import json
last={}
for l in open('log/signal_log.jsonl'):
    r=json.loads(l); last[r['signal_date']]=r['signal_alloc']
p=None
for d in sorted(last):
    if last[d]!=p: print(d, p, '->', last[d]); p=last[d]"

# every order actually sent
grep submitted log/trade_log.jsonl | python3 -m json.tool --json-lines 2>/dev/null || grep submitted log/trade_log.jsonl
```

How to read a signal record: `trend` (px > sma200) and `vol` vs `vol_hi_p90`
give the in-trend branch; when `trend` is false, `px > sma20` plus
`vol_off_peak` give the re-entry branch. `signal_alloc` must follow from
those — if it does not, the engine is wrong, which is a stop-everything event
(§5), not a thing to correct by hand.

Field-by-field definitions and the reconciliation procedure: `AUDIT.md`.

## 4. A run failed — what to do

The failure email names the script and its stderr. **Do not re-run
`execute.py` with modified inputs, ever.** Choose by cause:

| Cause (from stderr) | Action |
|---|---|
| `stale data: last bar …` | Vendor lag. Do nothing; tomorrow's run self-heals. |
| `Alpaca API unreachable` / 5xx | Transient. Do nothing. |
| `past MOC cutoff` | Deliberate: the bot refuses to chase. Do nothing. |
| `only N bars; need >= 273` | Feed/entitlement change — investigate before the next run; consider halting (§5). |
| `ALPACA_* not set` | Fix the routine's env vars in the cloud UI. |
| push failed | Re-run only the commit+push step from a clone; the decision is what matters. |
| `commit not on main` | The environment redirected the push to a `claude/*` branch (seen 2026-08-04). Recover it: `git fetch origin 'refs/heads/claude/*:refs/remotes/origin/claude/*'`, then `git merge --ff-only origin/claude/<branch>` on `main` and push. Then fix the routine's branch-push permission — a heartbeat that says "success" while `main` never moved is the trail's worst failure mode. |
| `invalid allocations` / signal NaN | **Engine-level. Halt (§5) and open a ticket on the map.** |

Then `bash scripts/oplog.sh failure "<one line: date, script, cause>"` and
commit `log/`, so the day is accounted for (AUDIT.md §1).

**Never back-fill a missed day.** Execution spec rule 4: the next run trades
toward that day's fresh signal. A missed change means the bot was flat/held
through it — that is real forward-test history and it stays in the record.

## 5. Halt

Two levels. Use both if you mean it.

**Kill switch (repo, takes effect on the next run):**

```bash
echo "halted 2026-08-04: <reason>" > HALT
bash scripts/oplog.sh halt "<reason>"
git add HALT log/ && git commit -m "HALT: <reason>" && git push
```

`execute.py` sees `HALT`, logs `HALTED`, places no order, exits 0, and leaves
`last_acted_signal.json` untouched. The signal keeps being computed and
logged, so the trail continues through the halt.

**Hard stop (immediate):** disable the `E2BOT-daily` routine in the Claude
Code cloud UI. Do this too if the failure might be in the glue itself.

**Flatten the position** (only if the halt is because something is wrong with
the bot's *positions*, not its reasoning):

```bash
bash scripts/alpaca.sh cancel-all
bash scripts/alpaca.sh close QLD
bash scripts/oplog.sh manual "flattened QLD by hand: <reason>"
```

This breaks the "every position change came from the rule" claim for the
forward test — record it, and treat the forward test as segmented at that
date.

**Resume:**

```bash
git rm HALT && bash scripts/oplog.sh resume "<reason resolved>"
git commit -m "resume: <reason>" && git push
```

Re-enable the routine. The next run trades toward the then-current signal —
no back-fill of what was missed during the halt.

## 6. Changing anything

- **The E2 rule is frozen.** Changing it requires a new decision on the
  wayfinder map (`MAP-e2-bot.md` in the QLD-model repo), not an edit here.
- Engine changes: separate commit, never inside a daily commit, and re-run the
  reference check (`reference/e2_reference_signals.csv`, method in the
  QLD-model repo's `assets/e2bot05-verification-report.md`) before the next
  trading day.
- Routine prompt changes: edit `routines/*.md` **and** re-paste into the cloud
  routine — the cloud copy is what actually runs; the repo copy is
  documentation.

## 7. Escalation

Anything that cannot be explained by the tables above — an unexplained Alpaca
order, a signal that does not follow from its own logged inputs, a rewritten
log — is a stop-everything event: halt (§5), do not "fix" the logs, and take
it to the map as a ticket.
