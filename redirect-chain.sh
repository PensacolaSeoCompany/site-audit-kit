#!/bin/bash
# redirect-chain.sh — measure the FULL redirect chain for a URL or a list.
# Read-only: GET/HEAD only (curl GET, follows redirects; no POST, no forms, no login).
#
# Added 2026-08-19. Plain hop-counting is the start; the value is in what
# the hops REVEAL. Every flag below came from a real audit finding this session.
#
# USAGE
#   bash redirect-chain.sh https://example.com/old-slug/
#   bash redirect-chain.sh --file urls.txt
#   bash redirect-chain.sh --matrix example.com     # the 4-way host/protocol matrix
#
# 🔴 WHY NOT JUST curl -w num_redirects:
#   1. A CHAIN can end 200 while wasting equity — 2 hops on every internal link is a
#      sitewide tax. Report hops even when the final code is fine.
#   2. A 200 is NOT proof of health: soft-404s return 200 with "not found" in the title.
#      This checks the TITLE of the final page, which plain hop-counting never sees.
#   3. Cloudflare/Vercel 403 curl on pages that serve 200 to a browser. A 403 here is
#      UNKNOWN, not broken — re-test in a real browser before reporting it.
#   4. HTTP→HTTPS→www→slash chains stack. Measuring only the apex hides them.
#
# 🔴 WHAT THIS TOOL CANNOT SEE — do not mistake "ok" for healthy:
#   - An INDEXABLE EMPTY PAGE. A medical-supply store's /wheelchairs?p=5000 returned 200, 0 hops,
#     a real title ("Wheelchairs | <Store> / Page 5000") and ZERO products (a client site, 2026-08). This tool
#     says "ok". It is an infinite indexable crawl trap. Transport health != content health.
#   - meta robots / rel=canonical. A 200 that is noindexed, or canonicals elsewhere, looks
#     identical here. Check those separately.
#   - JS-rendered redirects. Only HTTP-level 3xx are counted.
#   Use this to find TRANSPORT waste and dead ends. Use the crawler for content verdicts.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"
TMPD=$(mktemp -d "$KIT_WORK/tmp.XXXXXX"); SINK="$TMPD/sink"   # scratch under work/, removed on exit
trap 'rm -rf "$TMPD"' EXIT
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'

probe() {
  local u="$1"
  local out
  out=$(curl -4 -sL --max-time 25 -A "$UA" \
        -w '%{num_redirects}|%{http_code}|%{url_effective}|%{time_total}' -o "$TMPD/body" "$u" 2>>"$TMPD/stderr")
  local hops="${out%%|*}"; out="${out#*|}"
  local code="${out%%|*}"; out="${out#*|}"
  local final="${out%%|*}"; local secs="${out##*|}"
  local title
  title=$(grep -oiE '<title>[^<]*' "$TMPD/body" 2>>"$TMPD/stderr" | head -1 | cut -c8-60)
  rm -f "$TMPD/body"

  # flags — the part that turns a hop count into a finding
  local flags=""
  [ "${hops:-0}" -ge 2 ] && flags="$flags CHAIN(${hops})"
  [ "$code" = "403" ] && flags="$flags UNKNOWN-bot-wall"           # NOT broken; re-test in a browser
  [ "$code" = "404" ] && flags="$flags DEAD"
  # 🔴 410 Gone is DELIBERATE removal — not an error, but NOT "ok" either. Found on a live
  # WordPress site (/category/uncategorized/) where the tool first mislabelled it ok. It tells
  # Google to drop the URL permanently, which is correct for pruned content and a MISTAKE if the
  # page was meant to survive. Always surface it for a human decision.
  [ "$code" = "410" ] && flags="$flags GONE-410"
  # 5xx is a server fault, distinct from a missing page.
  case "$code" in 5??) flags="$flags SERVER-ERROR";; esac
  echo "$title" | grep -qiE 'not found|404|page (does not|doesn.t) exist' && \
    [ "$code" = "200" ] && flags="$flags SOFT-404"                 # 200 that is really a 404
  [ "$u" != "$final" ] && [ "${u%/}" = "${final%/}" ] && flags="$flags SLASH-ONLY"
  case "$final" in http://*) flags="$flags ENDS-INSECURE";; esac

  printf "  hops=%-2s %-4s %-7s %-58s %s\n" "${hops:-?}" "$code" "${secs%%.*}s" "${final:0:58}" "${flags:-ok}"
}

case "$1" in
  --matrix)
    d="${2#http*://}"; d="${d%%/*}"
    echo "  4-way host/protocol matrix for $d"
    for u in "http://$d/" "https://$d/" "http://www.$d/" "https://www.$d/"; do
      printf "  %-30s" "$u"; probe "$u"
    done ;;
  --file) while read -r u; do [ -n "$u" ] && probe "$u"; done < "$2" ;;
  *) probe "$1" ;;
esac
