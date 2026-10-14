#!/bin/bash
# ct-subdomains.sh — enumerate a domain's subdomains from CERTIFICATE TRANSPARENCY logs,
# Read-only: GET/HEAD only (one public CT-log search + one GET per subdomain; no POST, no forms, no login).
# then probe each for the thing that actually matters: an indexable duplicate of production.
#
# Added 2026-08-19 (data source: the public crt.name CT search). This finds hosts NO CRAWL CAN REACH — they are not
# linked from the site, not in the sitemap, and not in robots.txt. Every TLS cert ever issued is
# published to public CT logs, so the subdomain list is a matter of public record.
#
# USAGE: bash ct-subdomains.sh example.com
#
# 🔴 WHY THIS IS A HIGH-VALUE CHECK (measured live on a client's online store, 2026-08-19):
#   33 subdomains in CT logs. Three staging environments were LIVE and serving the complete store:
#     mcstaging.store.example          200, 269KB, INDEX,FOLLOW, NO canonical, NO robots block
#     canada.mcstaging.store.example   200, 269KB, INDEX,FOLLOW, NO canonical, NO robots block
#     mcprod.store.example             200, 227KB, INDEX,FOLLOW, robots.txt Disallow: / (ok-ish)
#   Two complete duplicate stores, indexable, competing with production. A BFS crawl of the main
#   site can NEVER find these.
#
# 🔴 FALSE POSITIVES / LIMITS:
#   - `000` = connection failed (no DNS / no listener). That is NOT proof of absence — it means
#     not reachable FROM HERE, right now. Do not report a 000 as "safe".
#   - A cert can exist for a host that was decommissioned years ago. CT is a historical record,
#     not a live inventory. Always probe.
#   - 403 = bot wall, UNKNOWN, not a finding.
#   - Wildcard certs (*.example.com) hide individual names; absence from CT is NOT proof a
#     subdomain does not exist.
#   - robots.txt `Disallow: /` on a staging host reduces crawling but does NOT guarantee
#     de-indexing — a `noindex` meta tag is the reliable control.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"
TMPD=$(mktemp -d "$KIT_WORK/tmp.XXXXXX"); SINK="$TMPD/sink"   # scratch under work/, removed on exit
trap 'rm -rf "$TMPD"' EXIT
D="${1:?usage: ct-subdomains.sh <apex-domain>}"
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
TMP="$TMPD/names"

curl -4 -s --max-time 40 -A "$UA" "https://crt.name/v1/search?apex=$D" | tr -d '\r' | grep -E '\.' | sort -u > "$TMP"
n=$(wc -l < "$TMP")
echo "  $n names in certificate transparency logs for $D"
[ "$n" -eq 0 ] && { echo "  (none returned — try crt.sh, or the apex may use a wildcard cert)"; rm -f "$TMP"; exit 0; }

echo "  --- probing (risky names first) ---"
# Names that most often expose a duplicate or a non-production environment.
RISKY='staging|dev|test|qa|uat|demo|beta|preprod|mcstaging|mcprod|old|new|backup|tmp|internal|admin|cpanel|webmail'
{ grep -iE "$RISKY" "$TMP"; grep -ivE "$RISKY" "$TMP"; } | while read -r h; do
  [ -z "$h" ] && continue
  r=$(curl -4 -sL --max-time 10 -A "$UA" -o "$TMPD/body" -w '%{http_code}:%{size_download}' "https://$h/" 2>>"$TMPD/stderr")
  code="${r%%:*}"; size="${r##*:}"
  case "$code" in
    000) printf "  %-44s unreachable from here (NOT proof of absence)\n" "$h"; rm -f "$TMPD/body"; continue;;
    403) printf "  %-44s 403 BOT WALL — unknown, re-test in a browser\n" "$h"; rm -f "$TMPD/body"; continue;;
  esac
  rb=$(grep -oiE '<meta name="robots"[^>]*' "$TMPD/body" 2>>"$TMPD/stderr" | head -1 | grep -oiE 'content="[^"]*' | cut -c10-30)
  cn=$(grep -oicE '<link[^>]+rel="canonical"' "$TMPD/body" 2>>"$TMPD/stderr")
  ttl=$(grep -oiE '<title>[^<]*' "$TMPD/body" 2>>"$TMPD/stderr" | head -1 | cut -c8-40)
  flag=""
  # THE finding: a big page, indexable, with no canonical pointing home = a duplicate site.
  if [ "$size" -gt 50000 ] && ! echo "$rb" | grep -qi noindex && [ "${cn:-0}" -eq 0 ]; then
    if echo "$h" | grep -qiE "$RISKY"; then flag="  🔴 INDEXABLE NON-PROD DUPLICATE"; fi
  fi
  printf "  %-44s %s %sKB robots=%-12s canonical=%s%s\n" \
    "$h" "$code" "$((size/1024))" "${rb:-none}" "${cn:-0}" "$flag"
  rm -f "$TMPD/body"
done
rm -f "$TMP"
