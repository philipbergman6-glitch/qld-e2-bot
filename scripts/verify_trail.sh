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
# Exit 0: every signature present verified. Exit 1: one did not, or a key
# fetch failed. Commits carrying no signature at all are reported, not failed —
# the operator's pre-2026-08-12 history is unsigned and stays that way, because
# history is never rewritten (§2). See §4a.

set -euo pipefail

# GitHub accounts whose published SSH signing keys are trusted, as
# "<github-account> <committer-email>". Both halves of the trail sign: the
# cloud routine as `claude` (its key is Anthropic's, not ours), the operator
# as themselves. Commits predating 2026-08-12 from the operator are unsigned
# and stay that way — history is never rewritten (AUDIT.md §2).
SIGNERS=(
  "claude                 noreply@anthropic.com"
  "philipbergman6-glitch  philip.bergman6@gmail.com"
)

SINCE="${1:-}"
RANGE="HEAD"
[[ -n "$SINCE" ]] && RANGE="${SINCE}..HEAD"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SIGNERS_FILE="$WORK/allowed_signers"

echo "Fetching published signing keys from GitHub..."
: > "$SIGNERS_FILE"
for entry in "${SIGNERS[@]}"; do
  read -r account email <<< "$entry"
  url="https://api.github.com/users/${account}/ssh_signing_keys"
  if ! keys=$(curl -fsS "$url" | grep -o 'ssh-[a-z0-9-]* [A-Za-z0-9+/=]*'); then
    echo "FAIL: could not fetch signing keys for '$account' ($url)." >&2
    exit 1
  fi
  [[ -n "$keys" ]] || { echo "FAIL: no signing keys published for '$account'." >&2; exit 1; }
  echo "$keys" | sed "s|^|${email} |" >> "$SIGNERS_FILE"
  echo "  $(echo "$keys" | wc -l | tr -d ' ') key(s) for $account <$email> — $url"
done
echo

ok=0; bad=0; gh_merge=0; unsigned=0

while IFS='|' read -r sha email date subject; do
  header=$(git cat-file commit "$sha")
  case "$header" in
    *"gpgsig -----BEGIN SSH SIGNATURE"*) kind=ssh ;;
    *"gpgsig -----BEGIN PGP SIGNATURE"*) kind=pgp ;;
    *)                                   kind=none ;;
  esac

  case "$email" in
    noreply@anthropic.com) who="bot     " ;;
    noreply@github.com)    who="github  " ;;
    *)                     who="operator" ;;
  esac

  if [[ "$kind" == ssh ]]; then
    if git -c gpg.ssh.allowedSignersFile="$SIGNERS_FILE" verify-commit "$sha" 2>/dev/null; then
      verdict="$who  signed  OK"
      ok=$((ok + 1))
    else
      verdict="$who  signed  *** DID NOT VERIFY ***"
      bad=$((bad + 1))
    fi
  elif [[ "$kind" == pgp ]]; then
    # GitHub's web-flow key signs PR merges made in the browser. Not a
    # decision record, and not an SSH signature; reported, not verified here.
    verdict="$who  pgp     (github web-flow, not checked)"
    gh_merge=$((gh_merge + 1))
  else
    verdict="$who  UNSIGNED"
    unsigned=$((unsigned + 1))
  fi

  printf '%s  %s  %-46s  %s\n' "${sha:0:7}" "${date:0:10}" "$verdict" "${subject:0:50}"
done < <(git log --reverse --format='%H|%ce|%cI|%s' "$RANGE")

echo
echo "verified: $ok   github merges: $gh_merge   unsigned: $unsigned   FAILED: $bad"

if [[ "$bad" -gt 0 ]]; then
  echo
  echo "A signature did not verify. Either history was rewritten or a signing key" >&2
  echo "was rotated and the old key withdrawn. Do not trade on this; halt" >&2
  echo "(RUNBOOK.md §5) and escalate (§7)." >&2
  exit 1
fi
