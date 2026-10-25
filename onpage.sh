#!/usr/bin/env bash
# onpage.sh — compare your page with your top competitors on 50 on-page factors (read-only).
# Usage: ./onpage.sh --keyword "stump removal" --mine https://you.example/ \
#          --competitor https://a.example/ --competitor https://b.example/ --competitor https://c.example/ \
#          [--variations "a|b"] [--raw] [--out DIR] [--name "Your Co"]
# Exit: 0 complete, 6 partial, 4 nothing to compare, 2 bad arguments, 3 did not start, 5 report failed.
set -u
KIT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$KIT_HOME/.venv/bin/python"
[ -x "$PY" ] || { echo "DID NOT START: $PY missing. Run ./install.sh first." >&2; exit 3; }
if [ -x "$KIT_HOME/.node/bin/node" ]; then export PATH="$KIT_HOME/.node/bin:$PATH"; fi
[ -n "$(command -v node)" ] || { echo "DID NOT START: node not found. Run ./install.sh first." >&2; exit 3; }
exec "$PY" "$KIT_HOME/onpage/cli.py" "$@"
