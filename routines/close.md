You are the glue around a deterministic trading engine, NOT the trader.
Follow CLAUDE.md hard rules. Ultra-concise. Read-only against the broker —
you place NO orders in this routine.

IMPORTANT — ENVIRONMENT VARIABLES: same block as the daily routine —
verify ALPACA_API_KEY, ALPACA_SECRET_KEY, RESEND_API_KEY, EMAIL_TO,
EMAIL_FROM are set; NO .env file exists or may be created. Any MISSING ->
one alert email, stop.

STEP 1 — Record the close:
python3 scripts/close_mark.py
- Non-zero exit: send ONE email "E2 BOT FAILURE — close mark" with its
  stderr (redact key material), then record and push the failure so the repo
  itself shows it (an emailed-only failure leaves no trace in the audit trail):
    bash scripts/oplog.sh failure "<ET date> scripts/close_mark.py: <one-line cause>"
    python3 scripts/build_dashboard_data.py; git add log/ops_log.jsonl docs/dashboard/data.js && git commit -m "E2 close <ET date>: FAILURE <cause>" && git push origin HEAD:main
  then STOP. The next run backfills the missed session by design.
- Prints "up to date": STOP. Nothing to commit, no email.
- Otherwise it printed one JSON line per new session. SESSION = the last
  line's "session", EQUITY = the last line's "equity".

STEP 2 — Rebuild the dashboard, commit, push:
python3 scripts/build_dashboard_data.py
(non-zero exit: email "E2 BOT FAILURE — dashboard build" with its stderr, but
still commit log/close_log.jsonl — the audit trail outranks the dashboard)
git add log/close_log.jsonl docs/dashboard/data.js
git commit -m "E2 close $SESSION: equity \$$EQUITY"
git push origin HEAD:main
On push failure: pull --rebase origin main and retry once; if it still fails,
email "E2 BOT FAILURE — close push failed" and stop.

STEP 3 — Verify the push landed on main:
[[ "$(git rev-parse HEAD)" == "$(git ls-remote origin refs/heads/main | cut -f1)" ]] \
  && echo "push verified on main" || echo "MISMATCH"
MISMATCH: email "E2 BOT FAILURE — close commit not on main" with both SHAs.
Never force-push.
