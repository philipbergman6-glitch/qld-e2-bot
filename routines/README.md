# Cloud Routine Prompts

Paste each file verbatim into its Claude Code cloud routine. Do not
paraphrase. The env-var check block and the commit-and-push step are
load-bearing.

## Cron schedules

12:00 ET daily run (previous close is final; MOC order rests until close —
safe on early-close days). Weekly rollup Friday after close.

| Routine name | Cron (America/Chicago) | Equivalent ET   | File        |
|--------------|------------------------|-----------------|-------------|
| E2BOT-daily  | `0 11 * * 1-5`         | 12:00 Mon–Fri   | `daily.md`  |
| E2BOT-weekly | `20 15 * * 5`          | 16:20 Friday    | `weekly.md` |

If the routine UI supports America/New_York directly, use `0 12 * * 1-5`
and `20 16 * * 5` instead.

## One-time prerequisites per routine

1. Install the Claude GitHub App on this repo (`qld-e2-bot`).
2. Enable "Allow unrestricted branch pushes" in the routine's environment.
3. Set env vars on the routine (NOT in a committed .env file):
   `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_ENDPOINT`,
   `ALPACA_DATA_ENDPOINT`, `RESEND_API_KEY`, `EMAIL_TO`, `EMAIL_FROM`.
   Values: `~/Documents/Coding_Projects/qld-e2-bot/.env` on the Mac
   (paper keys only; no Perplexity — this bot has no LLM in the signal path).
4. Environment setup script (the engine needs these; stdlib covers the rest):
   `pip install -r requirements.txt`
   Not `pip install pandas numpy` — unpinned resolution is not safe. On Python
   3.14 it produces pandas 2.2.3 + numpy 2.5.2, a pair that imports cleanly and
   then segfaults inside the engine's computation (e2bot-19). `requirements.txt`
   declares a tested range; CI proves the rule is unchanged at both of its ends.
   **This lives in the cloud routine's setup script, so changing the repo file
   does nothing until it is re-pasted into the routine UI** — same as the
   routine prompts themselves.

## Failure model

- Engine scripts exit non-zero on ANY invalid input → the routine trades
  nothing, sends one FAILURE email, commits nothing but the failure note.
- A missed day self-heals: the next run trades toward that day's fresh
  signal (execution spec rule 4). Never back-fill.
- Silence is failure: no daily email by ~12:15 ET on a trading day means
  the run died before the alert step — check the routine's run history.
