#!/usr/bin/env bash
# verify_trail.sh — check the repo half of the audit trail cryptographically.
#
# AUDIT.md §4a claims every daily bot commit is SSH-signed by a key published
# on GitHub, so a reader who does not trust the operator can still tell which
# commits the operator could have written. This script is how that claim is
# checked, by anyone, from a clone.
#
# It fetches the signing keys from GitHub's public API (the anchor — a key list
# committed to this repo would be as rewritable as the history it vouches for)
# and verifies signatures locally with git. Nothing here talks to Alpaca and
# nothing here is on the daily critical path: this must never be able to stop
# an order.
#
#   bash scripts/verify_trail.sh              # whole history on main
#   bash scripts/verify_trail.sh <since-sha>  # since a commit, exclusive
#
# Exit 0: every bot commit verified. Exit 1: one did not, or a key fetch
# failed. Unsigned operator commits are reported, not failed — see §4a.

set -euo pipefail

# GitHub accounts whose published SSH signing keys are trusted, and the
# committer email each signs as. The bot's daily commits are the load-bearing
# ones; add the operator here once an operator signing key is registered.
BOT_ACCOUNT="claude"
BOT_EMAIL="noreply@anthropic.com"

SINCE="${1:-}"
RANGE="HEAD"
[[ -n "$SINCE" ]] && RANGE="${SINCE}..HEAD"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SIGNERS="$WORK/allowed_signers"

echo "Fetching published signing keys for GitHub user '$BOT_ACCOUNT'..."
if ! curl -fsS "https://api.github.com/users/${BOT_ACCOUNT}/ssh_signing_keys" \
     | grep -o 'ssh-[a-z0-9-]* [A-Za-z0-9+/=]*' \
     | sed "s|^|${BOT_EMAIL} |" > "$SIGNERS"; then
  echo "FAIL: could not fetch signing keys for '$BOT_ACCOUNT'." >&2
  exit 1
fi
KEYCOUNT=$(wc -l < "$SIGNERS" | tr -d ' ')
[[ "$KEYCOUNT" -gt 0 ]] || { echo "FAIL: no signing keys published for '$BOT_ACCOUNT'." >&2; exit 1; }
echo "  $KEYCOUNT key(s) — https://api.github.com/users/${BOT_ACCOUNT}/ssh_signing_keys"
echo

bot=0; bot_bad=0; gh_merge=0; unsigned=0

while IFS='|' read -r sha email date subject; do
  header=$(git cat-file commit "$sha")
  case "$header" in
    *"gpgsig -----BEGIN SSH SIGNATURE"*) kind=ssh ;;
    *"gpgsig -----BEGIN PGP SIGNATURE"*) kind=pgp ;;
    *)                                   kind=none ;;
  esac

  if [[ "$kind" == ssh ]]; then
    if git -c gpg.ssh.allowedSignersFile="$SIGNERS" verify-commit "$sha" 2>/dev/null; then
      verdict="signed-bot   OK"
      bot=$((bot + 1))
    else
      verdict="signed-bot   *** SIGNATURE DID NOT VERIFY ***"
      bot_bad=$((bot_bad + 1))
    fi
  elif [[ "$kind" == pgp ]]; then
    # GitHub's web-flow key signs PR merges made in the browser. Not a bot
    # decision record; reported for completeness, not verified here.
    verdict="signed-gpg   (github web-flow, not checked)"
    gh_merge=$((gh_merge + 1))
  else
    verdict="UNSIGNED     (operator machine)"
    unsigned=$((unsigned + 1))
  fi

  printf '%s  %s  %-45s  %s\n' "${sha:0:7}" "${date:0:10}" "$verdict" "${subject:0:50}"
done < <(git log --reverse --format='%H|%ce|%cI|%s' "$RANGE")

echo
echo "bot commits verified: $bot   github merges: $gh_merge   unsigned: $unsigned   FAILED: $bot_bad"

if [[ "$bot_bad" -gt 0 ]]; then
  echo
  echo "A bot commit's signature did not verify. Either history was rewritten or"
  echo "the signing key was rotated and the old key withdrawn. Do not trade on"
  echo "this; escalate per RUNBOOK.md §7." >&2
  exit 1
fi
