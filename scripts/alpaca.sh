#!/usr/bin/env bash
# Alpaca API wrapper. All trading API calls go through here.
# Usage: bash scripts/alpaca.sh <subcommand> [args...]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${ALPACA_API_KEY:?ALPACA_API_KEY not set in environment}"
: "${ALPACA_SECRET_KEY:?ALPACA_SECRET_KEY not set in environment}"

API="${ALPACA_ENDPOINT:-https://paper-api.alpaca.markets/v2}"
DATA="${ALPACA_DATA_ENDPOINT:-https://data.alpaca.markets/v2}"
H_KEY="APCA-API-KEY-ID: $ALPACA_API_KEY"
H_SEC="APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY"

# curl wrapper: retries up to 3x on transient errors (incl. HTTP 429),
# honours Retry-After, exponential backoff via curl's built-in --retry.
req() {
  curl -fsS --retry 3 --retry-delay 1 --retry-max-time 30 \
    -H "$H_KEY" -H "$H_SEC" "$@"
}

cmd="${1:-}"
shift || true

case "$cmd" in
  account)
    req "$API/account"
    ;;
  positions)
    req "$API/positions"
    ;;
  position)
    sym="${1:?usage: position SYM}"
    req "$API/positions/$sym"
    ;;
  quote)
    sym="${1:?usage: quote SYM}"
    req "$DATA/stocks/$sym/quotes/latest"
    ;;
  bar)
    sym="${1:?usage: bar SYM}"
    req "$DATA/stocks/$sym/bars/latest"
    ;;
  asset)
    sym="${1:?usage: asset SYM}"
    req "$API/assets/$sym"
    ;;
  clock)
    req "$API/clock"
    ;;
  calendar)
    req "$API/calendar"
    ;;
  orders)
    status="${1:-open}"
    req "$API/orders?status=$status&nested=true"
    ;;
  order)
    body="${1:?usage: order '<json>'}"
    # Pre-flight: Alpaca silently rejects trailing_stop + fractional qty.
    # jq is required so this safety check can never silently disable itself.
    command -v jq >/dev/null 2>&1 || {
      echo "error: jq is required for order pre-flight validation (install with: brew install jq)" >&2
      exit 2
    }
    otype="$(printf '%s' "$body" | jq -r '.type // empty')"
    if [[ "$otype" == "trailing_stop" ]]; then
      qty="$(printf '%s' "$body" | jq -r '.qty // empty')"
      if [[ ! "$qty" =~ ^[0-9]+$ ]]; then
        echo "error: trailing_stop requires integer qty (got '$qty'); Alpaca silently rejects fractional + trailing stops" >&2
        exit 2
      fi
    fi
    req -H "Content-Type: application/json" -X POST -d "$body" "$API/orders"
    ;;
  replace)
    oid="${1:?usage: replace OID '<json>'}"
    body="${2:?usage: replace OID '<json>'}"
    req -H "Content-Type: application/json" -X PATCH -d "$body" "$API/orders/$oid"
    ;;
  cancel)
    oid="${1:?usage: cancel ORDER_ID}"
    req -X DELETE "$API/orders/$oid"
    ;;
  cancel-all)
    req -X DELETE "$API/orders"
    ;;
  close)
    sym="${1:?usage: close SYM}"
    req -X DELETE "$API/positions/$sym"
    ;;
  close-all)
    req -X DELETE "$API/positions"
    ;;
  *)
    echo "Usage: bash scripts/alpaca.sh <account|positions|position|quote|bar|asset|clock|calendar|orders|order|replace|cancel|cancel-all|close|close-all> [args]" >&2
    exit 1
    ;;
esac
echo
