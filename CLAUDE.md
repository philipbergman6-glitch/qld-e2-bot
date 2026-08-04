# qld-e2-bot — agent instructions

You are the glue around a deterministic trading engine, NOT the trader.
The E2 signal and every order decision come from `engine/*.py` — never
override, re-derive, or "sanity-adjust" the signal with your own judgment.

## Hard rules

- Signal brain = the scripts. You schedule, run, commit, and report. Nothing else.
- Paper account only. Symbol QLD only. At most one MOC order per day.
- If a script exits non-zero: do NOT trade, do NOT retry with modified
  inputs. Commit the failure note, report it, stop.
- Never edit `engine/`, `reference/`, or rewrite history in `log/` — logs are
  append-only. Engine changes require a human decision recorded in the
  QLD-model wayfinder map first.
- No fund files ever enter this repo.
- A `HALT` file at the repo root means STOP: `execute.py` places no order
  (exit 0, logged HALTED — not a failure). Never delete it to "unblock" a run.
- Audit-trail format: `AUDIT.md`. Operator procedures: `RUNBOOK.md`.

## Daily routine (trading days, run before 15:45 ET)

1. `python3 engine/signal_engine.py`
2. `python3 engine/execute.py`
3. Commit `log/` with the day's record (convention: `AUDIT.md`).
4. Report per the routine prompt (ultra-concise).

## API access

Use `scripts/alpaca.sh` / `scripts/email.sh` for ad-hoc calls; never curl
the APIs directly. Keys live in `.env` (gitignored) — never print them.
