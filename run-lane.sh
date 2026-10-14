#!/usr/bin/env bash
# run-lane.sh <lane-file> : run site audits one after another (one "lane").
# Read-only: GET/HEAD only (the sitemap fetch for render pages; the audit itself is run-full-audit.sh).
# run-lane.sh --one "slug|url|Business Name|flags" : run one site.
# PREFER run-queue.sh (2026-10-14): one shared spool queue; idle workers take the next job, so no
# lane sits empty while another has a backlog. run-queue.sh sources THIS file and calls run_site();
# the per-site logic (flags, render-page pick, logs, lock) lives only here.
# Line format: one site per line, fields separated by "|":  slug|url|Business Name|flags
#   flags: CSR    -> client-rendered site: continue past a thin raw crawl (CSR=1); verify on rendered pages
#          PROXY  -> proxy recipe for bot-walled sites: needs CRAWL_PROXY in the env or config.env
#                    (http://user:pass@host:port, never printed); adds a Safari UA, delay >= 3, concurrency 2
# Defaults: AUDIT_MIN_FREE_GB from env or 2. Render pages are the 2nd and
# 8th URLs of the site's sitemap. Each run's stdin is an empty file (children cannot eat the lane file).
# Per run (2026-10-14 review):
#   - log: $CASES_DIR/<slug>/run-<UTC>.log, never overwritten; run1.log is a symlink to the newest run log.
#     A run1.log that is a real file (from an older layout) is renamed run-legacy-<mtime UTC>.log first.
#     publish-run.sh reads the newest run*.log by mtime, which is this run.
#   - lock: flock on $QUEUE_DIR/locks/<slug>.lock; a slug that is already running is skipped (rc 75,
#     "LANE-SKIP <slug> locked"), in lanes and in the queue alike.
#   - exit code: "LANE-DONE <slug> rc=N log=<path>".
# Overrides: AUDIT_CASE_ROOT (default $CASES_DIR), QUEUE_DIR (default $KIT_HOME/queue), QUEUE_AUDIT_CMD
# (an executable used in place of "bash run-full-audit.sh"; the tests use a fake one).
# Writes nothing outside $CASES_DIR/<slug>/, $QUEUE_DIR/locks/ and what run-full-audit.sh writes.
set -u
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"
CASE_ROOT="${AUDIT_CASE_ROOT:-$CASES_DIR}"
QDIR="${QUEUE_DIR:-$KIT_HOME/queue}"
EMPTY="$EMPTY_STDIN"
export AUDIT_MIN_FREE_GB="${AUDIT_MIN_FREE_GB:-2}"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

# Same rule as run-full-audit.sh stage 0: free GB where KIT_WORK lives (integer, rounded down) vs AUDIT_MIN_FREE_GB.
free_gb() { df -P "$KIT_WORK" | awk 'NR==2{print int($4/1048576)}'; }
disk_ok() { [ "$(free_gb)" -ge "$AUDIT_MIN_FREE_GB" ]; }

pick() {
  local u="$1" sm
  sm=$(curl -4 -sL -m 20 -A "$UA" "${u}sitemap.xml" | grep -oE '<loc>[^<]*' | sed 's/<loc>//')
  if echo "$sm" | grep -qE '\.xml$'; then
    sm=$(curl -4 -sL -m 20 -A "$UA" "$(echo "$sm" | grep -E 'page|post' | head -1)" | grep -oE '<loc>[^<]*' | sed 's/<loc>//')
  fi
  echo "$sm" | grep -vE '\.(xml|jpg|png|webp|pdf)$' | grep -vE "^${u%/}/?$" | sed -n '2p;8p' | tr '\n' ' '
}

# new_run_log <case dir>: create a fresh run-<UTC>.log (noclobber), point run1.log at it. Prints the path.
new_run_log() {
  local case="$1" ts log n=1 legacy tmp
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  log="$case/run-$ts.log"
  while ! ( set -C; exec 2>&-; : > "$log" ); do n=$((n+1)); log="$case/run-$ts-$n.log"; done
  if [ -f "$case/run1.log" ] && [ ! -L "$case/run1.log" ]; then
    legacy="$case/run-legacy-$(date -u -r "$case/run1.log" +%Y%m%dT%H%M%SZ).log"; n=1
    while [ -e "$legacy" ]; do n=$((n+1)); legacy="${legacy%.log}-$n.log"; done
    mv -n "$case/run1.log" "$legacy"
  fi
  tmp="$case/.run1.log.tmp.$$"
  ln -s "$(basename "$log")" "$tmp" && mv -Tf "$tmp" "$case/run1.log"
  echo "$log"
}

# run_site "slug|url|name|flags" -> return code of the audit (75 = slug locked, nothing ran).
# Sets RUN_SITE_LOCKED (1 when skipped for the lock) and RUN_SITE_LOG for callers in the same shell.
run_site() {
  local line="$1" slug url name flags P rc lockfd
  RUN_SITE_LOCKED=0; RUN_SITE_LOG=""
  IFS='|' read -r slug url name flags <<< "$line"
  slug="${slug// }"
  [ -z "$slug" ] && return 0
  [[ "$slug" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "LANE-BAD-SLUG '$slug'"; return 64; }
  mkdir -p "$CASE_ROOT/$slug" "$QDIR/locks"
  exec {lockfd}>>"$QDIR/locks/$slug.lock"
  if ! flock -n "$lockfd"; then
    exec {lockfd}>&-
    RUN_SITE_LOCKED=1; echo "LANE-SKIP $slug locked (another run of this slug is active)"; return 75
  fi
  RUN_SITE_LOG=$(new_run_log "$CASE_ROOT/$slug")
  P=$(pick "$url" < "$EMPTY")
  (
    exec {lockfd}>&-     # children (Chrome, Lighthouse) must not inherit the lock and hold it after the run
    cd "$KIT_HOME" || exit 70
    if [[ "${flags:-}" == *PROXY* ]]; then
      [ -n "${CRAWL_PROXY:-}" ] || { echo "LANE-NO-PROXY $slug: flag PROXY needs CRAWL_PROXY (env or config.env)" > "$RUN_SITE_LOG"; exit 78; }
      export CRAWL_PROXY \
             CRAWL_UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15" \
             CRAWL_DELAY="$(awk -v d="${CRAWL_DELAY:-3}" 'BEGIN{print (d<3)?3:d}')" CRAWL_CONCURRENCY="${CRAWL_CONCURRENCY:-2}"   # proxy pool: at least 3 s
    fi
    [[ "${flags:-}" == *CSR* ]] && export CSR=1
    if [ -n "${QUEUE_AUDIT_CMD:-}" ]; then CMD=("$QUEUE_AUDIT_CMD"); else CMD=(bash "$KIT_HOME/run-full-audit.sh"); fi
    # shellcheck disable=SC2086
    "${CMD[@]}" "$url" "$slug" "$name" $P < "$EMPTY" > "$RUN_SITE_LOG" 2>&1
  )
  rc=$?
  exec {lockfd}>&-
  echo "LANE-DONE $slug rc=$rc log=$RUN_SITE_LOG"
  return $rc
}

# Main only when executed (run-queue.sh sources this file for the functions above).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [ "${1:-}" = "--one" ]; then
    run_site "${2:?job line}" < "$EMPTY"; exit $?
  fi
  LANE="${1:?lane file (or --one \"slug|url|name|flags\")}"
  FAILS=0
  while IFS= read -r line; do
    run_site "$line" < "$EMPTY"; r=$?
    [ "$r" -ne 0 ] && [ "$r" -ne 75 ] && FAILS=$((FAILS+1))
  done < "$LANE"
  echo "LANE-FINISHED $LANE failed=$FAILS"
fi
