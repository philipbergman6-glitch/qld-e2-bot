# Incidents

What went wrong in the first month of the paper forward test, mined from
`log/ops_log.jsonl`, `log/trade_log.jsonl` and `git log`. Each entry says what
the agent did on its own and what needed a human, because that boundary is
the design: the LLM runs the routine and reports; it never edits state,
touches the engine, or changes a schedule without an operator decision.

Evidence pointers are `ops_log <et_date>` (the line's `et_date` field) and
commit SHAs on `main`.

---

## 1. Staleness guard fired on every intraday run — 2026-08-04

- **Symptom:** `signal_engine.py` hard-failed at 11:39 ET on the first
  in-hours run: `stale data: last bar 2026-08-04, last completed session
  2026-08-03`.
- **Root cause:** during market hours Alpaca's daily-bars endpoint returns
  today's *in-progress* bar. The guard `last bar == last completed session`
  was therefore false by construction from 09:30 to 16:00 ET — exactly when
  the 12:00 ET routine runs. Relaxing the guard was not an option: the rule
  may never see a partial close.
- **Fix:** `de698d2` (2026-08-04). Resolve the last completed session first
  and pass it as the fetch's `end` bound (`T23:59:59Z`, because a daily bar
  is stamped at the session open in UTC). The in-progress bar can no longer
  arrive; the equality check survives as a real staleness guard. Verified
  by re-running in hours: `bars_sha256 40716df9…`, byte-identical to the
  pre-open run.
- **Evidence:** `ops_log 2026-08-04` (`failure`); `signal_log` shows two
  2026-08-03 records that day, the second with the digest.
- **Agent vs operator:** agent reported the failure and traded nothing.
  Operator diagnosed and shipped the engine change as a separate commit.

## 2. Cron evaluated in UTC: three consecutive 05:30 ET misfires — 2026-08-05 → 08-07

- **Symptom:** the daily routine fired at 05:37 ET on 08-05, 08-06 and 08-07.
  `execute.py` refused each time (`market is closed`), so the signal was
  logged but no order was placed. The go-live order was two days late.
- **Root cause:** the routine's cron `30 9 * * *` was authored as ET but
  evaluated in UTC. The agent tried to correct it (`update_trigger`) and was
  refused — it may only edit routines it created — then **corrected its own
  earlier log line** to say the cron was *not* fixed (`ops_log 2026-08-05`,
  `note` "CORRECTION to the preceding manual entry").
- **Fix:** operator re-set the schedule by hand; the first on-time run is
  the 12:17 ET run on 2026-08-10 (`trade_log` `run_at_utc` 16:17Z). No code
  change; `routines/README.md` documents the cron in both zones.
- **Evidence:** `ops_log 2026-08-05` (`failure`, `manual`, `note`),
  `2026-08-06`, `2026-08-07` (`failure`).
- **Agent vs operator:** agent placed the 08-05 order only after an explicit
  operator-authorized re-run at 12:47 ET; agent could not change the
  schedule and said so rather than reporting success.

## 3. Submission-vs-fill state trap: partial fills were invisible — 2026-08-05 → 08-10

- **Symptom:** the 08-05 MOC buy 1081 expired filled **109/1081**; the 08-07
  top-up buy 984 expired filled **791/984**. The account sat at ~10%, then
  ~83% invested while the signal read 100% and `execute.py` returned
  `hold (signal unchanged; drift never rebalanced)` every day.
- **Root cause:** `last_acted_signal.json` recorded the allocation
  *submitted*, not *filled*, and execution-spec rule 3 only traded on a
  signal change. A partial fill could never trigger a follow-up — the
  shortfall was structurally invisible to the only path that places orders.
- **Fix:** two operator state resets (`d6a9e2f` 08-07, `d57b983` 08-10:
  `signal_alloc 1.0 -> null`, so the next run saw a "change") bought time;
  the real fix is the drift gate, `1e24f1b` (2026-08-10): rebalance when
  the *realized* allocation (shares actually held × price / equity) drifts
  more than 1% from the signal. Band chosen by simulating over the reference
  series with a 20% under-fill rate: no gate −4.64% CAGR, 1% band −0.71%.
  Full reasoning in `engine/execute.py` (`DRIFT_BAND`), tests in
  `tests/test_execute_decide.py`.
- **Evidence:** `ops_log 2026-08-06` ("POSITION DISCREPANCY"), `2026-08-07`
  ×4, `2026-08-08` (`note`), `2026-08-10` (`manual`).
- **Agent vs operator:** agent detected and reported the discrepancy on
  08-06, 08-07 and 08-08 and explicitly took no corrective action each time
  ("no order, no state edit, engine untouched; operator decision required").
  Operator authorized the resets and the engine change.

## 4. Non-marginable buying power: three consecutive 0-fills — 2026-08-10 → 08-13

- **Symptom:** top-up MOC buys of 187 (08-10), 188 (08-11) and 184 (08-12)
  QLD all expired **0 filled**. Drift stuck at ~17% for five sessions.
- **Root cause (partially resolved):** `target_qty` is sized off equity, but
  QLD is non-marginable, so a buy is paid from cash; at alloc 1.0 the cash
  buffer is structurally zero, and any rise between the 12:00 ET reference
  price and the close makes the order unaffordable. The 08-11 order had 0.44
  shares of headroom. This explains *partial* fills; it does not obviously
  explain *zero* fills. **Unresolved; hypothesis:** Alpaca's paper venue
  sizes a MOC fill against `non_marginable_buying_power` (observed $39.73 on
  08-11, $174.45 on 08-12, $89.74 on 08-13), not against `cash` ($17,181.58),
  and rejects the whole order when that is short. Not confirmed with Alpaca.
- **Fix:** `9d94d50` (08-11) logs `cash` and `buffer_shares` on every run;
  `7e6d9ac` (2026-08-12) caps a buy at `floor(cash × 0.995 / ref_px)`
  (sells never capped). The 08-12 order was the first capped one
  (185 → 184). The 08-13 order (181, uncapped) filled **178/181** (position
  900 → 1078 on the 08-14 run); the
  remaining 3-share drift is 0.37%, inside the band, so the position has
  been at target since 2026-08-14. Why 08-13 filled when 08-10/11/12 did not
  is not established from the record — equity had risen ~1.8% that day,
  which is consistent with the hypothesis but does not prove it.
- **Evidence:** `ops_log 2026-08-11`, `2026-08-12`, `2026-08-13`;
  `trade_log` 2026-08-12 (`capped_by_cash: true`), 2026-08-14
  (`current_qty 1078`).
- **Agent vs operator:** agent logged each 0-fill with the account fields it
  could read and escalated ("needs a human decision on whether to size
  against non_marginable_buying_power"). Operator chose the cash cap.

## 5. Audit-trail integrity: push redirected to a side branch; stale-clone merge — 2026-08-04, 2026-08-10

- **Symptom (08-04):** a routine run computed the signal, logged HALTED,
  pushed — to `claude/clever-archimedes-aoyk5t` instead of `main` — and
  emailed success. `main` never moved, so "decision committed before the
  close" had nothing behind it.
- **Symptom (08-10):** an operator session ran from a clone five commits
  behind `origin/main` (the 12:17 ET routine run had landed meanwhile) and
  produced a second set of log lines for the day.
- **Root cause:** (a) the cloud environment's branch-push permission was
  inconsistent — the first run had pushed to `main` fine; (b) an operator
  working on a stale clone of an append-only log.
- **Fix:** `73c3b40` (08-04): the routine pushes `HEAD:main` explicitly and
  a mandatory STEP 6 compares `HEAD` to `git ls-remote origin
  refs/heads/main`, sending a FAILURE email naming the branch that actually
  received the commit on mismatch — never force. RUNBOOK §4 gained the
  recovery row. `e57f9a4` (08-10): merged by chronological **union** of the
  three logs, no line dropped from either side (signal 11+1, trade 7+1,
  ops 21+1); `last_acted_signal.json` took the operator-session value because
  it reflected the order actually placed. `bars_sha256` for 2026-08-07 was
  identical across the routine run and the operator run (`21c9635e…`), which
  is the determinism evidence AUDIT.md §1 cites.
- **Evidence:** `ops_log 2026-08-10` ("MERGE RECONCILIATION"); commit
  bodies of `73c3b40` and `e57f9a4`.
- **Agent vs operator:** the redirect was caught by the operator reading
  `main`, not by the agent — which is why STEP 6 exists. The merge was done
  in an operator session; the union rule (never drop a log line) came from
  AUDIT.md §5.

## 6. Weekend misfires and one silent miss — 2026-08-08 → open

- **Symptom:** the daily routine fires on Saturdays and Sundays (8 times by
  08-30). Each time the clock check sees the market closed, writes a
  `market-closed` ops record, and trades nothing. Separately, the
  **2026-08-18** trading day has no signal or trade record at all; the 08-19
  run self-healed using the 08-18 close.
- **Root cause:** confirmed 08-30 from the trigger itself — the cron is
  `0 16 * * *` (every day) while `routines/README.md` documents
  `0 16 * * 1-5`. The 08-18 miss is **unresolved**: nothing in the logs or
  the routine's history explains it; hypothesis: a cloud-side run that never
  started (a dead routine writes nothing anywhere — RUNBOOK §2).
- **Fix:** none in code — the day-of-week field is a schedule change, which
  the agent is not permitted to make. Still open as of this writing; the
  cost is noise (a redundant heartbeat), not risk, because the market-closed
  guard holds.
- **Evidence:** `ops_log 2026-08-08`, `08-09`, `08-15`, `08-16`, `08-22`,
  `08-23`, `08-29`, `08-30` (`market-closed`); `ops_log 2026-08-19` (`note`).
- **Agent vs operator:** agent diagnosed the cron field and recorded the
  exact fix, then stopped ("schedule change needs operator approval").
  Operator has not yet applied it.
