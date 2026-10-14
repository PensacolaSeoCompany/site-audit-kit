#!/usr/bin/env bash
# ONE entry point for a complete site audit: runs EVERY stage in order and HARD-FAILS (or loudly
# Read-only: GET/HEAD only. No stage logs in, submits a form or POSTs to the audited site.
# warns) if any stage is skipped. Exists because a hand-rolled per-run script silently omitted
# perf/trace-errors/social/3-page-render on a client site (2026-07).
#
# Usage: run-full-audit.sh <url> <slug> "<Business Name>" [service-page-url] [deep-page-url] [max-pages]
#
# Unattended: no prompts, stdin is never read. Exit 0 = every stage complete and nothing partial;
# non-zero = a fatal preflight/crawl error (1, 2, 4), else the number of skipped stages, else 3 for a
# PARTIAL run (page cap hit, bot wall, robots.txt refusal, crawl timeout). A partial run prints
# "PARTIAL: <reason>" as its last line and records the reason in the findings sidecar. The last lines list every absolute
# path the run wrote. Paths and tools come from kit-paths.sh (KIT_WORK, CRAWL_DIR, CASES_DIR, PY,
# NODE, CHROME_BIN). Writes: $CRAWL_DIR/<slug>-*.json, $CRAWL_DIR/<host>-<tier>.json (Lighthouse),
# $CASES_DIR/<slug>/ (stage logs, site-audit.html, linkmap.html), $REPORTS_DIR/<domain>/linkmap.html.
# Env switches: BIG_CRAWL=1 (long crawl timeouts: 4 h first pass, 3 h fallback, for sites of many
# thousands of pages), CHECK_EXTERNAL=1, SKIP_PERF=1, CSR=1, CRAWL_PROXY/CRAWL_UA/CRAWL_DELAY/
# CRAWL_CONCURRENCY, AUDIT_MIN_FREE_GB (default 2). Website only: no Google Business Profile checks.
set -u  # NOTE: no pipefail — head-terminated pipes must not abort the run; the completion gate is the source of truth
URL="${1:?url}"; SLUG="${2:?slug}"; NAME="${3:?name}"
SVC="${4:-}"; DEEP="${5:-}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"   # also sets the polite CRAWL_* defaults
MAXP="${6:-$CRAWL_MAXP}"
export CRAWL_MAXP="$MAXP"   # hack-scan reads it to detect a CAPPED (sample) crawl (2026-09-22)
[[ "$SLUG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "FATAL: slug '$SLUG' must be letters, digits, . _ -"; exit 2; }
exec < "$EMPTY_STDIN"       # nothing in this run may wait on a terminal
OUT="$CASES_DIR/$SLUG"
mkdir -p "$OUT"; cd "$KIT_HOME" || { echo "FATAL: cannot cd to $KIT_HOME"; exit 2; }
J="$CRAWL_DIR/$SLUG-full.json"
HOST=$(echo "$URL" | sed -E 's#https?://##; s#/.*$##')
if [ "${BIG_CRAWL:-0}" = 1 ]; then T_CRAWL=14400; T_FALLBACK=10800; else T_CRAWL=1200; T_FALLBACK=900; fi
declare -A DONE TIMEDOUT
CRAWL_BLOCKED=0; _HITS=""
PARTIAL_WHY=()   # plain-English reasons this run is not a full measurement (printed + written to the sidecar)
# tmo <stage> <seconds> cmd...: every stage runs under a timeout; a timeout is remembered for the report.
tmo(){ local st="$1" secs="$2" rc; shift 2
  timeout --kill-after=30 "$secs" "$@"; rc=$?
  { [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; } && TIMEDOUT[$st]=$secs
  return "$rc"; }
# ── STAGE 0 PREFLIGHT (2026-10-13) ─────────────────────────────────────────────────────────────
# Website only: this kit has no Google Business Profile steps.
# Disk floor: stop, never delete. 2 GB keeps crawl + Lighthouse + reports from failing mid-run.
_FREE_GB=$(df -P "$KIT_WORK" | awk 'NR==2{print int($4/1048576)}')
_MIN_GB="${AUDIT_MIN_FREE_GB:-2}"   # one-run override, e.g. AUDIT_MIN_FREE_GB=1
[ "$_FREE_GB" -lt "$_MIN_GB" ] && { echo "FATAL preflight: only ${_FREE_GB} GB free for $KIT_WORK (need ${_MIN_GB}, AUDIT_MIN_FREE_GB). Free disk space; the kit never deletes files."; exit 2; }
[ "$_MIN_GB" -lt 2 ] && echo "  ⚠️ preflight: disk floor lowered to ${_MIN_GB} GB for this run (${_FREE_GB} GB free)"
# Search-engine lookups must not go through a generic env proxy. CRAWL_PROXY (site crawl only) is allowed.
# A machine-wide proxy is common on bot hosts, so it is unset for this run (with a note), not refused.
for _v in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; do
  [ -n "${!_v:-}" ] && { echo "  NOTE preflight: $_v was set; unset for this run so live checks see the site directly (use CRAWL_PROXY to proxy the crawl)."; unset "$_v"; }; done
[ -n "$CHROME_BIN" ] && [ -x "$CHROME_BIN" ] || { echo "FATAL preflight: no Chrome found. Install google-chrome or chromium, or set CHROME_BIN=/path/to/chrome"; exit 2; }
"$PY" -c "import aiohttp, bs4" 2>&1 | grep -q . && { echo "FATAL preflight: $PY lacks the Python deps. Run: python3 -m venv $KIT_HOME/.venv && $KIT_HOME/.venv/bin/pip install -r $KIT_HOME/requirements.txt"; exit 2; }
command -v "$NODE" 2>&1 | grep -q '^/' || { echo "FATAL preflight: Node.js not found (NODE=$NODE). Install Node 18+ and run: cd $KIT_HOME && npm install"; exit 2; }
touch "$OUT/.run-start"
# ── PARTIAL, never a fake complete run (2026-10-14) ──────────────────────────────────────────────
# Any skipped stage, timeout, bot wall, robots.txt refusal or page-cap hit makes the run PARTIAL:
#   - each skipped stage goes into $CRAWL_DIR/<slug>-findings.json "not_measured" [{step, what, why}]
#     (the builder shows them as "What we could not check"); entries already there are kept
#   - the sidecar gets "partial": {"reason": "..."} (the builder prints a PARTIAL banner)
#   - the report page is rebuilt so it carries both; the run prints "PARTIAL: <reason>" and exits non-zero
partial_report(){
  local s
  [ "${CRAWL_BLOCKED:-0}" = 1 ] && PARTIAL_WHY+=("the site blocked or slowed our crawler (403/429 or a bot-check page), so some pages may not have been read")
  [ -n "${_HITS:-}" ] && PARTIAL_WHY+=("a bot-check or captcha page showed up during the live checks, so some results may describe that page instead of the site")
  [ -n "${TIMEDOUT[crawl]:-}" ] && PARTIAL_WHY+=("the crawl ran out of time ($(( ${TIMEDOUT[crawl]} / 60 )) min) before it reached every page")
  if [ -s "$J" ]; then
    while IFS= read -r s; do [ -n "$s" ] && PARTIAL_WHY+=("$s"); done < <("$PY" - "$J" "$MAXP" <<'PYEOF' 2>&1
import json, sys
d = json.load(open(sys.argv[1])); cap = int(sys.argv[2]); pages = d.get('pages') or []
if d.get('partial'): print('the crawl was cut short, so it holds only part of the site')
if cap and len(pages) >= cap: print(f'the crawl stopped at the page limit of {cap} pages; the site may have more (raise CRAWL_MAXP to read them)')
rb = d.get('robots_disallowed') or d.get('robots_blocked') or d.get('robots_skipped') or []
n = len(rb) if isinstance(rb, (list, dict)) else int(rb or 0)
if d.get('robots_refused') or (pages == [] and n):
    print("the site's robots.txt asks crawlers to stay out, so we did not crawl it")
elif n:
    print(f"the site's robots.txt asks crawlers to skip {n} address(es), so those pages were not checked")
PYEOF
)
  fi
  grep -qsiE "robots\.txt (disallows|forbids|refus|blocks)|disallowed by robots" "$OUT/crawl.log" "$OUT/crawl-curl.log" \
    && ! printf '%s\n' "${PARTIAL_WHY[@]:-}" | grep -q robots \
    && PARTIAL_WHY+=("the site's robots.txt asks crawlers to skip some pages, so those pages were not checked")
  local skipped=() st
  for st in crawl checks cms cmsdeep trace findings render perf social report linkmap attribution hackscan discovery filterscan; do
    [ -z "${DONE[$st]:-}" ] && skipped+=("$st")
  done
  [ "${#skipped[@]}" -gt 0 ] && PARTIAL_WHY+=("${#skipped[@]} check(s) did not finish: ${skipped[*]}")
  [ "${#PARTIAL_WHY[@]}" -eq 0 ] && return 0
  PARTIAL_REASON="$(printf '%s; ' "${PARTIAL_WHY[@]}")"; PARTIAL_REASON="${PARTIAL_REASON%; }"
  local tos=""; for st in "${!TIMEDOUT[@]}"; do tos="$tos $st=${TIMEDOUT[$st]}"; done
  "$PY" - "$CRAWL_DIR/$SLUG-findings.json" "$PARTIAL_REASON" "$OUT" "$tos" "${skipped[@]}" <<'PYEOF' 2>&1 | sed 's/^/  /'
import json, os, sys
path, reason, out, tos = sys.argv[1:5]; skipped = sys.argv[5:]
timeouts = dict(t.split('=') for t in tos.split())
WHAT = {
  'crawl': 'Reading the pages of the site', 'checks': 'Standard technical checks',
  'cms': 'Which website platform the site runs on', 'cmsdeep': 'Checks specific to the website platform',
  'trace': 'Where broken links are linked from', 'findings': 'Turning check results into findings',
  'render': 'The page with and without JavaScript', 'perf': 'Page speed (Lighthouse)',
  'social': 'Links to social media profiles', 'report': 'The report page itself',
  'linkmap': 'The map of internal links', 'attribution': 'Which content appears only with JavaScript',
  'hackscan': 'Hacked content and spam pages', 'discovery': 'Pages that no link points to (sitemaps, certificate logs, archives)',
  'filterscan': 'Filter and sort addresses on listing pages',
}
try: d = json.load(open(path))
except Exception: d = {}
if isinstance(d, list): d = {'findings': d}
nm = d.get('not_measured') or []
have = {str(x.get('step')) for x in nm if isinstance(x, dict)}
for st in skipped:
    if st in have: continue
    if st in timeouts: why = f'This check ran out of time after {int(timeouts[st]) // 60 or 1} minute(s).'
    else: why = f'This check did not finish (details in the run folder: {st}.log).'
    nm.append({'step': st, 'what': WHAT.get(st, st), 'why': why})
d['not_measured'] = nm
d['partial'] = {'reason': reason}
d.setdefault('findings', [])
tmp = path + '.tmp'
json.dump(d, open(tmp, 'w'), indent=1); os.replace(tmp, path)
print(f'sidecar: {path} partial + {len(nm)} not_measured')
PYEOF
  # rebuild the case report so it shows the PARTIAL banner and "What we could not check"
  if [ -s "$J" ] && [ -s "$OUT/site-audit.html" ]; then
    timeout 300 "$NODE" build-crawl-report.js "$J" "$OUT/site-audit.html" "$NAME" >> "$OUT/report.log" 2>&1 \
      && echo "  report rebuilt with the PARTIAL banner: $OUT/site-audit.html"
  fi
  PARTIAL_LINE="PARTIAL: $PARTIAL_REASON"
}

gate(){
echo; echo "===== AUDIT COMPLETION GATE ====="
MISS=0
for s in crawl checks cms cmsdeep trace findings render perf social report linkmap attribution hackscan discovery filterscan; do
  if [ -n "${DONE[$s]:-}" ]; then echo "  ✓ $s"; else echo "  ✗ $s  — SKIPPED"; MISS=$((MISS+1)); fi
done
if [ "$MISS" -eq 0 ]; then echo "ALL STEPS COMPLETE"; else
  echo "INCOMPLETE — $MISS step(s) skipped, DO NOT report as done"; fi
# criticals present != incomplete: report them separately so they are never conflated
_C=$(cat "$OUT"/checks.log "$OUT"/hack.log 2>&1 | grep -c "🚩\|🚨" || true)
[ "${_C:-0}" -gt 0 ] && echo "NOTE: ${_C} CRITICAL finding(s) in the logs — verify each reached the report"
GATE_MISS=$MISS
partial_report
echo; echo "===== FILES WRITTEN BY THIS RUN ====="
_BARE="${HOST#www.}"
{ find "$OUT" -newer "$OUT/.run-start" \( -type f -o -type l \)
  find "$CRAWL_DIR" -maxdepth 1 -newer "$OUT/.run-start" \( -type f -o -type l \) \( -name "$SLUG-*" -o -name "$_BARE-*" -o -name "www.$_BARE-*" \)
  find "$REPORTS_DIR/$_BARE" -newer "$OUT/.run-start" \( -type f -o -type l \); } 2>&1 | grep '^/' | sort
}
# Exit code: a fatal stage keeps its own code; otherwise the number of skipped stages (0 = complete).
# A PARTIAL run with every stage done (page cap, bot wall, robots) exits 3. The PARTIAL line is the last line.
trap '_rc=$?; gate; [ -n "${PARTIAL_LINE:-}" ] && { echo; echo "$PARTIAL_LINE"; }
      [ "$_rc" -ne 0 ] && exit "$_rc"; [ "${GATE_MISS:-0}" -gt 0 ] && exit "$GATE_MISS"; [ -n "${PARTIAL_LINE:-}" ] && exit 3; exit 0' EXIT

step(){ echo; echo "=== [$1] $2 ==="; }

step 1 "Full crawl (full-extract.py)"
# PROXY transport: when CRAWL_PROXY is set (an IP-reputation WAF that datacenter IPs cannot pass,
# e.g. a SiteGround captcha on a client site, 2026-08), route the crawl through it directly. The
# proxy implies --curl inside full-extract, so skip the aiohttp pass (it would only capture the 202
# challenge page). Pair with CRAWL_UA=<Safari UA> and a delay to pace the proxy pool.
PROXY_ARGS=""
# NOTE `${VAR:-}` — this script runs under `set -u`, so a bare "$CRAWL_PROXY" is an UNBOUND VARIABLE
# error that kills the run at stage 1 on every site where the proxy isn't exported (caught
# 2026-08-05 by the first end-to-end run on a non-walled site).
[ -n "${CRAWL_PROXY:-}" ] && PROXY_ARGS="--proxy $CRAWL_PROXY --delay ${CRAWL_DELAY:-3} --concurrency ${CRAWL_CONCURRENCY:-2}"
# --check-external is OPT-IN (CHECK_EXTERNAL=1, 2026-10-14): it HEAD-checks every external link, and
# no stage reads the result (external_links[].status is unused by audit-checks.py,
# checks-to-findings.py, build-crawl-report.js and verify-audit.py). It cost crawl time for nothing.
EXT_ARGS=""; [ "${CHECK_EXTERNAL:-0}" = 1 ] && EXT_ARGS="--check-external"
tmo crawl "$T_CRAWL" "$PY" full-extract.py "$URL" --max "$MAXP" $EXT_ARGS $PROXY_ARGS --out "$J" > "$OUT/crawl.log" 2>&1 || true
N=$("$PY" -c "import json,sys;print(len(json.load(open(sys.argv[1])).get('pages') or []))" "$J" 2>&1 | grep -E '^[0-9]+$' || echo 0)
[ "$N" -gt 0 ] && DONE[crawl]=$N || { echo "FATAL: crawl produced 0 pages (see $OUT/crawl.log)"; exit 1; }
echo "crawled $N pages"
# CRAWL-QUALITY GUARD: detect a blocked/throttled crawl (429s, or pages with ~0 words = bot shells).
# Shopify/Cloudflare velocity-throttle our aiohttp client. If blocked, AUTO-RETRY via curl fallback.
crawl_quality() {   # prints: real-content blocked total challenge-titles
"$PY" - "$J" <<'PYEOF'
import json, re, sys
d=json.load(open(sys.argv[1])); pg=d.get('pages') or []
real=[p for p in pg if p.get('status')==200 and (p.get('word_count') or 0)>50 and '.xml' not in (p.get('url') or '')]
blocked=[p for p in pg if p.get('status') in (429,403,503)]
chal=[p for p in pg if re.search(r'captcha|challenge|checking your browser|just a moment|security checkpoint|access denied', str(p.get('title') or ''), re.I)]
print(f'{len(real)} {len(blocked)} {len(pg)} {len(chal)}')
PYEOF
}
read RC BC PC CH <<< "$(crawl_quality)"
echo "  quality: $RC real-content / $PC crawled ($BC blocked-status)"
# A SINGLE blocked response is normal (an intentionally-403'd admin path). Only treat the crawl as
# blocked when real content is missing OR blocked responses are a MEANINGFUL SHARE of the crawl.
# On a client site (2026-08) one deliberate 403 triggered a full redundant re-crawl with identical results.
if [ "$RC" -lt 3 ] || { [ "$BC" -gt 2 ] && [ "$((BC * 100 / (PC > 0 ? PC : 1)))" -ge 10 ]; }; then
  echo "  🚩 CRAWL LIKELY BLOCKED (429/403 or near-zero real content) — auto-retrying via curl fallback (paced)"
  CRAWL_BLOCKED=1
  _J_MT0=$(stat -c %Y "$J" 2>&1 | grep -E '^[0-9]+$' || echo 0)
  sleep 1   # mtime has 1 s resolution: make sure a rewrite is visible as a change
  tmo crawl "$T_FALLBACK" "$PY" full-extract.py "$URL" --max "$MAXP" --curl --concurrency 1 --delay 2 ${CRAWL_PROXY:+--proxy $CRAWL_PROXY} --out "$J" > "$OUT/crawl-curl.log" 2>&1 || true
  # FALLBACK-WROTE-NOTHING GUARD (a client site, 2026-09): the fallback was SIGTERM'd at its cap
  # after 364 pages and never reached json.dump, so every later stage consumed PASS 1 (934 × 429)
  # while this very line reported "after curl fallback: 58 real-content pages" FROM THAT OLD FILE.
  # full-extract now dumps a partial:true JSON on SIGTERM; here we refuse an unchanged file and
  # shout when the crawl is a partial sample. Exit 0 ≠ crawled.
  _J_MT1=$(stat -c %Y "$J" 2>&1 | grep -E '^[0-9]+$' || echo 0)
  if [ "$_J_MT1" = "$_J_MT0" ]; then
    echo "FATAL: curl fallback wrote NOTHING ($J unchanged) — stages would run on the blocked pass-1 crawl. Re-run with CRAWL_CONCURRENCY=1 CRAWL_DELAY=1.2 and a max-pages the timeout can finish (or BIG_CRAWL=1)."; exit 1
  fi
  "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get('partial') else 1)" "$J" 2>&1 && echo "  ⚠️ PARTIAL CRAWL: the fallback hit its timeout — $J is a partial:true sample (no inlinks/external status). Say so in the report notes."
  read N2 BC2 PC2 CH2 <<< "$(crawl_quality)"
  echo "  after curl fallback: $N2 real-content pages ($PC2 crawled, $BC2 blocked-status)"
  # TINY SITE: a site with only a few short pages is not a blocked site. Both passes complete, no
  # blocked status, no challenge title, a handful of pages: continue, but say so.
  _TINY=0; [ "$N2" -lt 3 ] && [ "$BC2" -eq 0 ] && [ "$CH2" -eq 0 ] && [ "$PC2" -le 5 ] && _TINY=1
  if [ "$N2" -lt 3 ] && [ "${CSR:-0}" != "1" ] && [ "$_TINY" = 0 ]; then
    echo "FATAL: STILL THIN after fallback ($N2 real pages). Blocked or client-rendered. Fix access (CRAWL_PROXY/CRAWL_DELAY), or re-run with CSR=1 if the site is client-rendered (then render every route by hand)."; exit 4
  fi
  [ "$_TINY" = 1 ] && CRAWL_BLOCKED=0
  [ "$_TINY" = 1 ] && echo "  ⚠️ TINY SITE: $PC2 page(s), none blocked, no challenge page — continuing; say in the report that the site is very small"
  [ "$N2" -lt 3 ] && [ "${CSR:-0}" = 1 ] && echo "  ⚠️ CSR=1: continuing on a thin raw crawl; every crawl-based finding must be checked on rendered pages"
fi

step 2 "Standing checks (audit-checks.py)"
tmo checks 600 "$PY" audit-checks.py "$J" > "$OUT/checks.log" 2>&1 || true
# NOTE: audit-checks exits 2 BY DESIGN when criticals are found. Gate on OUTPUT, never exit code —
# otherwise the dirtier the site, the more likely the gate falsely reports "checks SKIPPED".
grep -q "CANONICAL TARGET DOMAIN" "$OUT/checks.log" && DONE[checks]=1
CRIT=$(grep -c "🚩\|🚨" "$OUT/checks.log" || true)
[ "${CRIT:-0}" -gt 0 ] && echo "  ⚠️ $CRIT CRITICAL check finding(s) — these MUST reach the client report"

step 2b "CMS detection (detect-cms.py) — drives the platform LOGIC TREE"
# Fingerprint the platform ONCE, early, so the deep-dive dispatch (stage 6e) and the report can both
# branch on it. Writes $CRAWL_DIR/<slug>-cms.json. Honors CRAWL_PROXY/CRAWL_UA for bot-walled hosts.
tmo cms 300 "$PY" detect-cms.py "$J" "$URL" > "$OUT/cms.log" 2>&1 || true
grep -q "PLATFORM:" "$OUT/cms.log" && DONE[cms]=1
grep -E "PLATFORM:|DEEP-DIVE MODULE:" "$OUT/cms.log" | sed 's/^/  /' || true

step 3 "Broken-link trace (trace-errors.py)"
tmo trace 300 "$PY" trace-errors.py "$J" > "$OUT/trace.log" 2>&1 && DONE[trace]=1 || true
grep -cE "^\[40|^\[50" "$OUT/trace.log" | xargs echo "  non-200s found:"

step 4 "Two-layer render check — 3 pages (render-check.py)"
ARGS="$URL"; [ -n "$SVC" ] && ARGS="$ARGS $SVC"; [ -n "$DEEP" ] && ARGS="$ARGS $DEEP"
# AUTO-SAMPLE EMPTY ROWS (a lawn-care site, 2026-09): the operator picked three STATIC pages, the
# render check said "SSR" three times, and 165 client-rendered town/blog pages (0 words, no title
# in the raw HTML) shipped as "169 pages missing title/H1/meta" instead of ONE finding: "empty until
# JavaScript". A 0-word 200 HTML row is exactly the page the render check exists for — always add
# up to two of them (different path families) to the render list, whatever the operator passed.
_EMPTY=$("$PY" - "$J" <<'PYEOF'
import json, sys, re
d = json.load(open(sys.argv[1])); seen = set(); out = []
for p in d.get("pages") or []:
    if p.get("status") == 200 and not p.get("redirected") and not p.get("non_html") and (p.get("word_count") or 0) == 0:
        fam = re.sub(r"\?.*", "", p["url"]).split("/")[3] if len(p["url"].split("/")) > 3 else ""
        if fam in seen: continue
        seen.add(fam); out.append(p["url"])
        if len(out) == 2: break
print(" ".join(out))
PYEOF
)
[ -n "$_EMPTY" ] && { echo "  + auto-added 0-word rows to the render check: $_EMPTY"; ARGS="$ARGS $_EMPTY"; }
tmo render 600 "$PY" render-check.py $ARGS > "$OUT/render.log" 2>&1 || true
grep -q "CONTENT:" "$OUT/render.log" && DONE[render]=1
[ -z "$SVC" ] && echo "  WARN: only 1 page checked — pass service+deep URLs for the full 3-page gate"

step 5 "Lighthouse performance — all tiers (perf-audit.sh)"
# SLUG-NAMESPACE GUARD (2026-07-31): perf-audit names its JSONs by HOST, so a second audit of the
# same site under a different slug silently clobbers the first. Snapshot any pre-existing host-keyed
# perf JSONs before this stage so an earlier run's evidence survives.
# SLUG RECURSION (fixed 2026-08-07): this glob also matched the SNAPSHOTS this loop wrote on
# earlier runs (`<slug>-<host>-<tier>.json`), so each run re-prefixed an already-prefixed name and
# produced `<slug>-<slug>-<host>-<tier>.json`, then `<slug>-<slug>-<slug>-...` (~3MB of byte-identical
# duplicates on one client). Snapshot ONLY files whose basename STARTS with the host — a real
# perf-audit.sh output — never one that already carries a slug prefix.
for _old in "$CRAWL_DIR/${HOST:-__none__}"-{desktop,mobile-4g,mobile-3g}.json \
            "$CRAWL_DIR/${HOST#www.}"-{desktop,mobile-4g,mobile-3g}.json; do
  [ -e "$_old" ] || continue
  _keep="$CRAWL_DIR/$SLUG-$(basename "$_old")"
  [ -e "$_keep" ] || cp -p "$_old" "$_keep"
done
# SKIP_PERF=1 skips the Lighthouse run (slowest stage) and reuses existing perf JSONs — the gate
# below still requires them to EXIST, so a first-ever audit cannot silently skip measurement.
if [ "${SKIP_PERF:-0}" = "1" ]; then
  echo "  SKIP_PERF=1 — reusing existing perf JSONs (no new Lighthouse run)" | tee -a "$OUT/perf.log"
else
  # 3 tiers x 5 min each (perf-audit.sh times every Lighthouse tier) + slack
  tmo perf 1080 bash "$KIT_HOME/perf-audit.sh" "$URL" all "$CRAWL_DIR" > "$OUT/perf.log" 2>&1 || true
fi
# perf-audit names files by the FETCHED host (may include www) while build-crawl-report looks up the
# crawl's `site` field (may not). Symlink both spellings so the report never silently loses perf.
# Same recursion hazard as the snapshot loop above — bound to the exact host-keyed and
# slug-keyed spellings, never a wildcard that re-matches this loop's own output.
for _p in "$CRAWL_DIR/$HOST"-{desktop,mobile-4g,mobile-3g}.json \
          "$CRAWL_DIR/$SLUG-$HOST"-{desktop,mobile-4g,mobile-3g}.json; do
  [ -e "$_p" ] || continue
  [[ "$HOST" == www.* ]] || continue     # nothing to alias when the host has no www.
  _b="$(basename "$_p")"; _alt="$(dirname "$_p")/${_b/"$HOST"/"${HOST#www.}"}"   # literal match (a sed regex let "." match "-")
  [ "$_alt" != "$_p" ] && [ ! -e "$_alt" ] && ln -sf "$(basename "$_p")" "$_alt"
done
# a JSON that EXISTS but carries runtimeError/score:null measured nothing (a client site, 2026-08:
# ERRORED_DOCUMENT_REQUEST x3 passed the gate) — require at least one tier with a real score
_PSCORE=$(AUDIT_HOST="$HOST" AUDIT_RUNSTART="$OUT/.run-start" "$PY" - "$CRAWL_DIR" <<'PYEOF'
import glob, json, os, sys
ok = 0
d = sys.argv[1]
_h = os.environ.get('AUDIT_HOST','').lower(); _bare = _h[4:] if _h.startswith('www.') else _h
_start = os.path.getmtime(os.environ['AUDIT_RUNSTART'])
_files = [f for pat in ('%s-desktop.json','%s-mobile-4g.json','www.%s-desktop.json','www.%s-mobile-4g.json') for f in glob.glob(os.path.join(d, pat % _bare))]
for f in [f for f in _files if os.path.getmtime(f) >= _start]:
    try:
        j = json.load(open(f))
        if (j.get('categories', {}).get('performance', {}) or {}).get('score') is not None:
            ok = 1
    except Exception:
        pass
print(ok)
PYEOF
)
if [ "$_PSCORE" = "1" ] || grep -q "Performance score" "$OUT/perf.log"; then
  DONE[perf]=1; { grep -iE "Performance score|LCP|FCP|CLS|TBT|lazy-loaded" "$OUT/perf.log" | grep -viE "deprecat|warn" | head; } || true
else
  echo "  WARN: perf JSON not produced (see $OUT/perf.log) — retry single tier: bash $KIT_HOME/perf-audit.sh $URL desktop"
fi

# CHALLENGE-SCREEN GUARD (added 2026-08-01): the gate proves stages RAN — it cannot see that
# render/perf/checks measured a WAF challenge instead of the site. On a SiteGround client site a 202
# "Robot Challenge Screen" produced plausible-looking CLS/CSR/no-sitemap results for a WHOLE RUN.
# Grep every stage log for challenge markers and compare the crawl title against a live fetch.
_MARKERS='sg-captcha|Robot Challenge|Checking your browser|cf-chl|challenge-platform|Just a moment|x-vercel-mitigated|Vercel Security Checkpoint'
_HITS=$(grep -lE "$_MARKERS" "$OUT"/*.log 2>&1 | grep '^/' | tr '\n' ' ')
if [ -n "$_HITS" ]; then
  echo "  🚩 CHALLENGE MARKERS in stage logs: $_HITS"
  echo "     Stages that fetched live may have measured a bot-challenge screen, NOT the site."
  echo "     Re-run poisoned stages through CRAWL_PROXY / the curl transport and"
  echo "     distrust any CLS / render / 'no sitemap' / 'no schema' result from this pass."
fi

step 5b "URL discovery — channels a link-crawl cannot reach (url-discovery.py)"
# A BFS crawl only reaches LINKED pages. Injected/orphaned/staging URLs are structurally invisible
# to it. This unions 5 independent channels (CT logs, Wayback, CMS API, sitemaps, hack-path
# probe) and diffs against the crawl. MEASURED on a chiropractic site: the path probe found
# /autodiscover/ed/ (200, 184KB, 207 pharma terms) plus undocumented /test/ and /new/ staging dirs —
# none reachable by crawling, none in any sitemap, and the CMS API did not know they existed.
tmo discovery 600 "$PY" url-discovery.py "$URL" "$J" > "$OUT/discovery.log" 2>&1 || true
grep -E "🚨|🚩|NOT REACHED BY THE CRAWL" "$OUT/discovery.log" | head -6
grep -q "DISCOVERY" "$OUT/discovery.log" && DONE[discovery]=1

step 6 "Social scan (social-scan.py)"
# gate on LOG CONTENT, never exit code — social-scan exits 2 BY DESIGN when it finds a problem
# (a client site, 2026-08: complete output, exit 2, gate said SKIPPED — the dirtier the site, the
# more likely the false skip; identical bug class fixed for audit-checks 2026-07-31)
tmo social 120 "$PY" "$KIT_HOME/social-scan.py" "${URL#https://}" > "$OUT/social.log" 2>&1 || true
grep -qE "REAL profiles|NO functional social|Framework/namespace|UNKNOWN" "$OUT/social.log" && DONE[social]=1

step 6d "faceted/filter URL scan (filter-url-scan.py) — parameter URL spaces a raw crawl can't see"
# LISTING pages expose category/tag/sort filters as JS buttons that build ?param= URLs client-side
# (a client site, 2026-08). Pick blog/shop/products/collections index pages from the crawl.
_LISTINGS=$("$PY" - "$J" <<'PYEOF'
import json, re, sys
p = json.load(open(sys.argv[1])).get('pages') or []
seen = []
for x in p:
    u = x.get('url') or ''
    if x.get('status') != 200: continue
    if re.search(r'/(blog|shop|products|collections|articles|news|store)/?$', u) and u not in seen:
        seen.append(u)
print(' '.join(seen[:3]))
PYEOF
)
[ -z "$_LISTINGS" ] && _LISTINGS="$URL"
tmo filterscan 300 "$PY" filter-url-scan.py $_LISTINGS > "$OUT/filterscan.log" 2>&1 || true
grep -E "FILTER-VERDICT|FILTER-COMBOS" "$OUT/filterscan.log" | head -2
DONE[filterscan]=1

step 6c "pre-consent pixel live check (pixel-consent-check.py) — feeds the check-18 finding"
# LIVE layer is authoritative (a client site: Clarity loaded via GTM — invisible to the static check).
# Runs on the homepage only; ~60-90s. Non-gated: a bot-walled site fails this gracefully.
tmo pixel 180 "$PY" pixel-consent-check.py "$URL" > "$OUT/pixel.log" 2>&1 || true
grep -E "🚩|✅" "$OUT/pixel.log" | head -3

step 6a "hacked-content / index-bloat scan (hack-scan.py) — MUST precede the sidecar"
# ORDER IS LOAD-BEARING (fixed 2026-07-31). This ran at stage 10 — AFTER the sidecar (6b) and the
# report build (7) — so on a fresh slug a DETECTED hack could never reach the client deliverable:
# hack.log had 2 alarms while the built HTML mentioned it 0 times. A hack is the headline above
# every SEO finding; the old order made that structurally impossible.
# NEVER move this after 6b.
tmo hackscan 600 "$PY" hack-scan.py "$J" "$URL" > "$OUT/hack.log" 2>&1 && DONE[hackscan]=1
grep -E "🚨|🚩|index \(site:" "$OUT/hack.log" | head -8 || true

step 6e "CMS deep-dive dispatch — the platform LOGIC TREE (added 2026-08-05)"
# if <CMS> then <module>. Only platforms with a module are dispatched; everything else is a no-op
# note. MUST precede 6b so the deep-dive's flags reach the sidecar and therefore the client report.
CMS_PLATFORM=$("$PY" -c "
import json,sys
try: print(json.load(open(sys.argv[1])).get('platform',''))
except Exception: print('')
" "$CRAWL_DIR/$SLUG-cms.json" 2>&1 | tail -1)
echo "  platform: ${CMS_PLATFORM:-unknown}"
case "$CMS_PLATFORM" in
  wordpress)
    tmo cmsdeep 600 "$PY" cms/wordpress.py "$J" "$URL" > "$OUT/cms-deepdive.log" 2>&1 || true
    grep -q "WORDPRESS DEEP-DIVE" "$OUT/cms-deepdive.log" && DONE[cmsdeep]=1
    grep -E "🚩" "$OUT/cms-deepdive.log" | head -8 || true
    ;;
  ""|unknown|ambiguous)
    echo "  no CMS confidently identified — deep-dive skipped (generic checks still applied)"
    DONE[cmsdeep]=1     # correctly skipped, not failed
    ;;
  *)
    echo "  no deep-dive module for '$CMS_PLATFORM' yet — generic checks only"
    DONE[cmsdeep]=1     # correctly skipped, not failed
    ;;
esac

step 6b "Convert check/trace/hack findings -> report sidecar (checks-to-findings.py)"
# MUST run BEFORE the report build. Critical check findings previously terminated in checks.log and
# never reached the client deliverable (independent critique 2026-07-30). The builder auto-loads
# $CRAWL_DIR/<slug>-findings.json and merges it at the TOP of the findings list.
tmo findings 300 "$PY" checks-to-findings.py "$SLUG" "$OUT" "$CRAWL_DIR" > "$OUT/findings.log" 2>&1 || true
grep -E "finding\(s\)|\[critical" "$OUT/findings.log" | head -12
grep -q "finding(s)" "$OUT/findings.log" && DONE[findings]=1

step 7 "Build report (build-crawl-report.js)"
# Builds into the CASE FOLDER only. Nothing reaches $REPORTS_DIR until the findings are verified
# and publish-run.sh runs (2026-10-13: unverified reports used to go public here).
tmo report 300 "$NODE" build-crawl-report.js "$J" "$OUT/site-audit.html" "$NAME" > "$OUT/report.log" 2>&1 \
  && DONE[report]=1
echo "  report built at $OUT/site-audit.html (NOT published; verify, then: bash $KIT_HOME/publish-run.sh $SLUG report $OUT/site-audit.html)"

# a Google Business Profile POST can link to a 404 for years (one client, 2026-08: a 2022 post
# carrying utm_source=GMB). NO SITE CRAWL CAN SEE THIS — the link lives on Google's property.

step 8 "PHASE 2 — link graph + anchor inventory (interlink-map.py)"
# Builds into the case folder; publish-run.sh is the only writer to $REPORTS_DIR (2026-10-14).
# The link map needs no verification, so it is published here.
tmo linkmap 300 "$PY" interlink-map.py "$J" "$OUT/linkmap.html" "$NAME" > "$OUT/linkmap.log" 2>&1 \
  && { DONE[linkmap]=1; bash "$KIT_HOME/publish-run.sh" "$SLUG" linkmap "$OUT/linkmap.html" 2>&1 | tee -a "$OUT/linkmap.log"; }
echo "  link map built at $OUT/linkmap.html (publish: bash $KIT_HOME/publish-run.sh $SLUG linkmap $OUT/linkmap.html)"

step 9 "PHASE 2 — content attribution raw-vs-rendered (content-attribution.py)"
tmo attribution 300 "$PY" content-attribution.py $ARGS > "$OUT/attribution.log" 2>&1 && DONE[attribution]=1
grep -E "JS-ONLY DELTA|JS-only container|IFRAME" "$OUT/attribution.log" | head -8 || true
