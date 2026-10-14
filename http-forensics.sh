#!/bin/bash
# http-forensics.sh — everything curl can tell you about a site in one pass.
# Read-only: GET/HEAD only (curl GET/HEAD + DNS TXT lookups; no POST, no forms, no login).
#
# Grew out of a redirect-hop one-liner (2026-08-19). Every check below was RUN against
# live audited sites before being included; the false-positive note on each is the important part.
#
# USAGE: bash http-forensics.sh https://example.com/
#
# 🔴 GLOBAL CAVEAT: a 403 from curl usually means BOT PROTECTION, not a broken page. Cloudflare and
# Vercel 403 curl on pages that serve 200 to a real browser. Treat 403 as UNKNOWN and re-test in a
# browser. This single mistake produced a "41 broken links" claim where only 1 was real.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"
TMPD=$(mktemp -d "$KIT_WORK/tmp.XXXXXX"); SINK="$TMPD/sink"   # scratch under work/, removed on exit
trap 'rm -rf "$TMPD"' EXIT
U="${1:?usage: http-forensics.sh <url>}"
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
echo "  === $U ==="

# 1. TIMING. TTFB is SERVER THINK-TIME and is load-independent on OUR side — unlike Lighthouse,
#    which is fiction on a contended box (benchmarkIndex far below ~400 = untrustworthy).
#    GOOD: TTFB < 0.2s.  BAD: > 0.6s = the server is slow before a single byte of HTML is parsed.
#    FALSE POSITIVE: a CDN edge hit near our datacenter flatters TTFB; a cold origin is slower for
#    a real user elsewhere. Run 3x and take the median; treat one sample as indicative only.
read -r dns tls ttfb tot ver ip code < <(curl -4 -sL --max-time 25 -A "$UA" -o "$SINK" \
  -w '%{time_namelookup} %{time_appconnect} %{time_starttransfer} %{time_total} %{http_version} %{remote_ip} %{http_code}\n' "$U")
printf "  TIMING     dns=%.3f tls=%.3f TTFB=%.3f total=%.3f | HTTP/%s | %s | %s\n" \
  "$dns" "$tls" "$ttfb" "$tot" "$ver" "$ip" "$code"
awk -v t="$ttfb" 'BEGIN{if(t>0.6)print "    🔴 SLOW SERVER: TTFB >0.6s before any rendering"}'
[ "$ver" = "1.1" ] && echo "    ⚠ HTTP/1.1 — no multiplexing; every asset queues. HTTP/2 is free on any modern host."

# 2. COMPRESSION. The gap between raw and compressed is what every non-negotiating client pays.
#    🔴 FALSE POSITIVE (hit live): WITHOUT -L you measure the REDIRECT BODY (0 bytes), not the page.
#    Always follow redirects before comparing sizes.
raw=$(curl -4 -sL --max-time 20 -A "$UA" -o "$SINK" -w '%{size_download}' "$U")
cmp=$(curl -4 -sL --max-time 20 -A "$UA" --compressed -o "$SINK" -w '%{size_download}' "$U")
enc=$(curl -4 -sIL --max-time 20 -A "$UA" --compressed "$U" | grep -i '^content-encoding' | tail -1 | tr -d '\r' | awk '{print $2}')
printf "  COMPRESS   raw=%sKB compressed=%sKB encoding=%s\n" "$((raw/1024))" "$((cmp/1024))" "${enc:-NONE}"
[ -z "$enc" ] && echo "    🔴 NO COMPRESSION NEGOTIATED — every visitor downloads the full payload."
[ "$((raw/1024))" -gt 200 ] && echo "    ⚠ page is $((raw/1024))KB uncompressed — heavy regardless of encoding."

# 3. CACHE + CDN. Repeat-visit cost, and whether a paid CDN is actually caching.
#    Seen live: cf-cache-status: DYNAMIC = paying Cloudflare and caching NOTHING.
#    FALSE POSITIVE: HTML is often deliberately no-cache; judge STATIC ASSETS separately.
H=$(curl -4 -sIL --max-time 20 -A "$UA" "$U")
cc=$(echo "$H"|grep -i '^cache-control'|tail -1|tr -d '\r'|cut -c16-60)
cdn=$(echo "$H"|grep -iE '^(cf-cache-status|x-cache|x-vercel-cache|x-served-by)'|tail -1|tr -d '\r')
srv=$(echo "$H"|grep -i '^server'|tail -1|tr -d '\r'|cut -c9-40)
printf "  CACHE      %s | %s | server: %s\n" "${cc:-NONE}" "${cdn:-no-cdn-header}" "${srv:-?}"
echo "$cdn" | grep -qi 'DYNAMIC\|MISS\|BYPASS' && echo "    ⚠ CDN present but not caching this response."

# 4. UA DIFFERENTIAL (cloaking / bot-walling). Same bytes to all three = healthy.
#    A large delta means bots see different content — either cloaking, or a wall that stops indexing.
#    FALSE POSITIVE: dynamic content (rotating phone numbers, A/B tests, personalisation) shifts
#    size legitimately. Judge on ORDER OF MAGNITUDE, not a few hundred bytes.
b=$(curl -4 -sL --max-time 20 -A "$UA" -o "$SINK" -w '%{http_code}:%{size_download}' "$U")
g=$(curl -4 -sL --max-time 20 -A 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' -o "$SINK" -w '%{http_code}:%{size_download}' "$U")
printf "  UA-DIFF    browser=%s googlebot=%s\n" "$b" "$g"
[ "${b#*:}" != "${g#*:}" ] && echo "    ⚠ DIFFERENT PAYLOAD TO GOOGLEBOT — investigate before concluding cloaking."

# 5. HEAD vs GET. Link checkers and many crawlers use HEAD; a disagreement makes healthy pages
#    look broken to third-party tools (and to our own audits).
hd=$(curl -4 -sIL --max-time 20 -A "$UA" -o "$SINK" -w '%{http_code}' "$U")
gt=$(curl -4 -sL  --max-time 20 -A "$UA" -o "$SINK" -w '%{http_code}' "$U")
printf "  HEAD/GET   HEAD=%s GET=%s\n" "$hd" "$gt"
[ "$hd" != "$gt" ] && echo "    🔴 MISMATCH — HEAD-based link checkers will report this page wrongly."

# 6. SECURITY HEADERS — cheap, and a trust signal on regulated/medical/legal sites.
miss=""
for x in strict-transport-security content-security-policy x-frame-options x-content-type-options; do
  echo "$H" | grep -qi "^$x:" || miss="$miss $x"
done
# NOTE: content-security-policy-report-only is NOT a CSP — it reports without enforcing.
# The `:` in the grep pattern is what keeps them distinct.
if [ -n "$miss" ]; then printf "  SECURITY   MISSING:%s\n" "$miss"; else echo "  SECURITY   all four present"; fi

# 7. CONDITIONAL GET (contributed by a zero-context review, 2026-08-19 — a real gap in my own list).
#    If a page emits NO ETag and NO Last-Modified, Googlebot cannot revalidate: it re-downloads the
#    FULL body on every crawl of every page. Measured live: a client's online store had neither, so all
#    4,441 pages re-transfer ~227KB each crawl. Sites WITH an ETag returned 304 with 0 bytes.
#    GOOD: 304 + 0 bytes.  BAD: 200 + full body, or no validator at all.
#    FALSE POSITIVE: a CDN may strip validators on HTML while honouring them on static assets —
#    test an asset too before calling it sitewide.
et=$(echo "$H" | grep -i '^etag:' | tail -1 | tr -d '\r' | cut -d' ' -f2-)
lm=$(echo "$H" | grep -i '^last-modified:' | tail -1 | tr -d '\r' | cut -d' ' -f2-)
if [ -n "$et" ]; then
  rv=$(curl -4 -sL --max-time 20 -A "$UA" -H "If-None-Match: $et" -o "$SINK" -w '%{http_code}:%{size_download}' "$U")
  printf "  REVALIDATE etag=yes -> %s\n" "$rv"
  case "$rv" in 304:*) ;; *) echo "    ⚠ ETag present but server still returned a full body." ;; esac
else
  printf "  REVALIDATE etag=NO last-modified=%s\n" "${lm:-NO}"
  [ -z "$lm" ] && echo "    🔴 NO VALIDATORS — Googlebot re-downloads the full page on every crawl."
fi

# 8. SOFT-404 PROBE (also contributed). A garbage URL must 404. A 200 means infinite indexable
#    duplicates; a 301-to-homepage is treated as a soft-404 by Google anyway.
#    FALSE POSITIVE: 403 here is a bot wall, not a result. And a CDN may serve a cached 200 shell
#    while the JS app 404s client-side — confirm a 200 in a browser before reporting it.
gp="${U%/}/no-such-page-zx9q17"
sf=$(curl -4 -sL --max-time 20 -A "$UA" -o "$SINK" -w '%{http_code}:%{num_redirects}' "$gp")
printf "  SOFT-404   garbage-url -> %s\n" "$sf"
case "$sf" in
  404:*) ;;
  403:*) echo "    ? 403 = bot wall, UNKNOWN — re-test in a browser." ;;
  200:0) echo "    🔴 GARBAGE URL RETURNS 200 — infinite indexable duplicate space." ;;
  200:*) echo "    ⚠ garbage URL redirects to a 200 — Google treats this as a soft-404." ;;
esac

# ─────────────────────────────────────────────────────────────────────────────
# ROUND-2 CHECKS (from a second zero-context review, 2026-08-19). All measured live.

# 9. BOT-PROTECTION TRIANGULATION. Not "is the page up" but "can GOOGLEBOT fetch it".
#    A page Googlebot cannot fetch cannot rank — invisible deindexing risk. The agent measured
#    wikipedia.org returning 403 to Googlebot / 301 to a browser, blocked at the HAProxy EDGE
#    before ever reaching the app.
#    FALSE POSITIVE: a WAF challenge returns 200 or 503 with a challenge BODY, not the real page.
#    Check body size and the `server:` header before calling a 200 "accessible".
GB='Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
gb=$(curl -4 -sI --max-time 15 -A "$GB" -o "$SINK" -w '%{http_code}' "$U")
br=$(curl -4 -sI --max-time 15 -A "$UA" -o "$SINK" -w '%{http_code}' "$U")
printf "  BOT-ACCESS browser=%s googlebot=%s\n" "$br" "$gb"
[ "$br" != "$gb" ] && echo "    🔴 GOOGLEBOT SEES SOMETHING DIFFERENT — a page Googlebot cannot fetch cannot rank."

# 10. DNS VENDOR CENSUS + MAIL POSTURE. Verification TXT records enumerate every SaaS the
#     business ever connected; DMARC p= shows whether anyone can spoof their email.
#     FALSE POSITIVE: verification TXT records are almost NEVER cleaned up. Presence proves
#     someone once verified, NOT that the vendor is live today. Corroborate against live headers.
D2=$(echo "$U" | sed -E 's|https?://||; s|/.*||; s|^www\.||')
ven=$(dig +short TXT "$D2" 2>>"$TMPD/stderr" | grep -oiE '[a-z0-9-]+-verification' | sort -u | tr '\n' ' ')
dm=$(dig +short TXT "_dmarc.$D2" 2>>"$TMPD/stderr" | grep -oE 'p=[a-z]+' | head -1)
printf "  DNS        vendors: %s| dmarc: %s\n" "${ven:-none }" "${dm:-NONE}"
[ -z "$dm" ] && echo "    ⚠ NO DMARC — anyone can spoof this brand's email."
[ "$dm" = "p=none" ] && echo "    ⚠ DMARC p=none — monitoring only, spoofing not blocked."

# 11. STACK / CMS VERSION LEAK. An exact version maps to known CVEs; the plugin list reveals the
#     publishing stack. Measured live (2026-08): a cleaning-company site leaked "WordPress 7.0.4" plus
#     elementor/elementor-pro; a remodeling site leaked "WordPress 6.9.7" plus bb-plugin.
#     🔴 NEVER PUBLISH THE EXACT VERSION IN A CLIENT-FACING DOCUMENT — internal report only.
#     FALSE POSITIVE: version strings are trivially spoofed or stripped, and reverse proxies inject
#     misleading server headers. Treat as a lead; corroborate with a second fingerprint.
body=$(curl -4 -sL --compressed --max-time 20 -A "$UA" "$U")
gen=$(echo "$body" | grep -oiE '<meta name="generator" content="[^"]*' | head -1 | cut -c33-60)
plug=$(echo "$body" | grep -oE 'wp-content/plugins/[a-z0-9_-]+' | sed 's|.*/||' | sort -u | head -5 | tr '\n' ' ')
printf "  STACK      generator=%s | plugins: %s\n" "${gen:-none}" "${plug:-none}"
[ -n "$gen" ] && echo "    ⚠ CMS VERSION DISCLOSED — internal finding only, never render in a client report."

# 12. SITEMAP FRESHNESS. A sitemap frozen in the past feeds Google dead URLs and is the clearest
#     external tell that a migration stalled or the CMS stopped regenerating.
#     FALSE POSITIVE: many sitemaps are generated at BUILD time, so lastmod reflects deploy, not
#     content change. And a sitemap with NO lastmod at all is a separate (smaller) finding.
sm=$(curl -4 -s --max-time 25 -A "$UA" "https://$D2/sitemap.xml" | grep -oE '<lastmod>[^<]+' | sed 's/<lastmod>//' | sort | tail -1)
printf "  SITEMAP    newest lastmod: %s\n" "${sm:-NONE PRESENT}"
if [ -n "$sm" ]; then
  age=$(( ( $(date +%s) - $(date -d "${sm%%T*}" +%s 2>>"$TMPD/stderr" || date +%s) ) / 86400 ))
  [ "$age" -gt 60 ] && echo "    ⚠ newest sitemap entry is ${age} days old — Google may be crawling a stale map."
fi
