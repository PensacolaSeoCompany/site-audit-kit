#!/bin/bash
# migration-verify.sh — post-migration / post-relaunch verification.
# Read-only: GET/HEAD only (curl GET/HEAD; no POST, no forms, no login).
#
# Pattern added 2026-08-19. This is a DIFFERENT JOB from auditing a settled site:
# an audit asks "what is wrong here?"; this asks "did the move land correctly?" and it is
# EXPECTATION-DRIVEN — you assert what SHOULD be true and it reports PASS/FAIL against that.
#
# USAGE
#   bash migration-verify.sh example.com                     # infer expectations
#   bash migration-verify.sh example.com --host www          # assert www is canonical
#   bash migration-verify.sh example.com --sitemap sitemap-index.xml --retired sitemap.xml
#   bash migration-verify.sh example.com --url-map moves.tsv # old<TAB>new, one per line
#
# 🔴 WHY EXPECTATION-DRIVEN MATTERS: a settled-site audit reports "301 to www" as neutral. During a
# migration the SAME observation is a PASS or a FAIL depending on which host you moved TO. Without
# a stated expectation there is no such thing as a wrong redirect — only a redirect.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"
TMPD=$(mktemp -d "$KIT_WORK/tmp.XXXXXX"); SINK="$TMPD/sink"   # scratch under work/, removed on exit
trap 'rm -rf "$TMPD"' EXIT
D="${1:?usage: migration-verify.sh <domain> [--host www|apex] [--sitemap path] [--retired path] [--url-map file]}"
shift
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
HOST=""; SITEMAP=""; RETIRED=""; MAP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2;; --sitemap) SITEMAP="$2"; shift 2;;
    --retired) RETIRED="$2"; shift 2;; --url-map) MAP="$2"; shift 2;; *) shift;;
  esac
done
pass(){ printf "  ✅ %s\n" "$1"; }
fail(){ printf "  ❌ %s\n" "$1"; }
warn(){ printf "  ⚠  %s\n" "$1"; }

echo "  === migration verification: $D ==="

# 1. CANONICAL HOST. All four variants must converge on ONE host.
#    FALSE POSITIVE: a 403 on one variant is a bot wall, not a missing redirect — re-test in a browser.
echo "  [1] canonical host convergence"
finals=""
for u in "http://$D/" "https://$D/" "http://www.$D/" "https://www.$D/"; do
  r=$(curl -4 -sL --max-time 15 -A "$UA" -o "$SINK" -w '%{num_redirects}|%{http_code}|%{url_effective}' "$u")
  hops=$(echo "$r"|cut -d'|' -f1); code=$(echo "$r"|cut -d'|' -f2); fin=$(echo "$r"|cut -d'|' -f3)
  printf "      %-32s hops=%s %s -> %s\n" "$u" "$hops" "$code" "$fin"
  [ "$code" = "403" ] && warn "403 = bot wall, UNKNOWN — verify in a browser" && continue
  finals="$finals$fin\n"
done
uniq_finals=$(printf "$finals" | grep -v '^$' | sort -u | wc -l)
[ "$uniq_finals" -eq 1 ] && pass "all variants converge on one host" || fail "variants land on $uniq_finals DIFFERENT hosts — duplicate site"
if [ -n "$HOST" ]; then
  want=$([ "$HOST" = "www" ] && echo "www.$D" || echo "$D")
  printf "$finals" | grep -q "://$want/" && pass "canonical host is $want as expected" || fail "expected canonical host $want — not what resolved"
fi

# 2. SITEMAP DECLARED IN robots.txt. Measured live: a 4,441-URL store declared NONE.
#    FALSE POSITIVE: Google also accepts a sitemap submitted directly in Search Console, so an
#    undeclared sitemap is a weakness, not proof it is unknown to Google.
echo "  [2] sitemap declaration"
decl=$(curl -4 -s --max-time 15 -A "$UA" "https://$D/robots.txt" | grep -i '^sitemap' | awk '{print $2}' | tr -d '\r')
if [ -z "$decl" ]; then fail "robots.txt declares NO sitemap"; else
  for sm in $decl; do
    r=$(curl -4 -sL --max-time 20 -A "$UA" -o "$SINK" -w '%{num_redirects}|%{http_code}' "$sm")
    [ "$(echo "$r"|cut -d'|' -f2)" = "200" ] && pass "declared sitemap resolves 200: $sm" || fail "declared sitemap returns $(echo "$r"|cut -d'|' -f2): $sm"
    [ "$(echo "$r"|cut -d'|' -f1)" != "0" ] && warn "declared sitemap costs $(echo "$r"|cut -d'|' -f1) redirect hop(s) — declare the final URL"
    # 🔴 the host INSIDE the sitemap must match the canonical host, or you are feeding Google the old site
    ih=$(curl -4 -sL --max-time 25 -A "$UA" "$sm" | grep -oE '<loc>https?://[^/<]+' | sed 's|<loc>https\?://||' | sort -u | head -1)
    [ -n "$ih" ] && printf "      urls inside use host: %s\n" "$ih"
  done
fi

# 3. RETIRED PATHS must be gone. A retired sitemap or template still serving 200 means the old
#    structure is still being advertised.
if [ -n "$RETIRED" ]; then
  echo "  [3] retired path"
  c=$(curl -4 -sI --max-time 15 -A "$UA" -o "$SINK" -w '%{http_code}' "https://$D/$RETIRED")
  case "$c" in
    404|410) pass "retired /$RETIRED returns $c";;
    200) fail "retired /$RETIRED STILL RETURNS 200 — the old structure is live";;
    3??) warn "retired /$RETIRED returns $c (redirect) — acceptable if it points at the new one";;
    *) warn "retired /$RETIRED returns $c";;
  esac
fi

# 4. URL MAP. The real test of a migration: does every old URL land on its intended new one?
#    🔴 A 301 that lands on the HOMEPAGE instead of the mapped page is treated by Google as a
#    SOFT-404 and passes no value. This is the single most common migration failure.
if [ -n "$MAP" ] && [ -f "$MAP" ]; then
  echo "  [4] url map"
  ok=0; bad=0; home=0
  while IFS=$'\t' read -r old new; do
    [ -z "$old" ] && continue
    r=$(curl -4 -sL --max-time 15 -A "$UA" -o "$SINK" -w '%{num_redirects}|%{http_code}|%{url_effective}' "$old")
    hops=$(echo "$r"|cut -d'|' -f1); code=$(echo "$r"|cut -d'|' -f2); fin=$(echo "$r"|cut -d'|' -f3)
    if [ "${fin%/}" = "${new%/}" ]; then ok=$((ok+1))
       [ "$hops" -gt 1 ] && warn "$old reaches its target in $hops hops (should be 1)"
    elif [ "${fin%/}" = "https://$D" ] || [ "${fin%/}" = "https://www.$D" ]; then
       home=$((home+1)); fail "$old -> HOMEPAGE (Google treats this as a soft-404)"
    else bad=$((bad+1)); fail "$old -> $fin (expected $new)"; fi
  done < "$MAP"
  printf "      mapped correctly: %s | wrong target: %s | dumped to homepage: %s\n" "$ok" "$bad" "$home"
fi
