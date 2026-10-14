#!/usr/bin/env bash
# audit.sh: one command, one site, finished reports. Runs the full pipeline unattended (website only).
#
#   ./audit.sh https://example.com [--name "Example"] [--csr] [--proxy URL]
#
#   --name   display name in the report (default: the domain)
#   --csr    the site is client-rendered (JavaScript builds the page): continue on a thin raw crawl
#   --proxy  route the site crawl through this proxy (sets CRAWL_PROXY)
# Polite, read-only defaults (raise them only by setting the env yourself):
#   CRAWL_MAXP=500 pages, CRAWL_CONCURRENCY=2, CRAWL_DELAY=1 (s), CRAWL_UA=GrokBot-SiteAudit, robots.txt obeyed.
# Env: AUDIT_TIMEOUT (seconds, default 1800) caps the whole run; KIT_HOME, KIT_WORK, PY, NODE,
#      CHROME_BIN (see kit-paths.sh). Run ./install.sh first.
#
# Writes $REPORTS_DIR/<domain>/ (default ./reports/<domain>/, domain = host without www.):
#   site-audit.html  report.md  report.pdf  findings.json  linkmap.html (if built)  run.log
# When anything failed or was skipped, report.md starts with "# PARTIAL REPORT: <reason>" and says
# PARTIAL in its summary block; a failed run still writes that report.md (never a fake complete one).
# Exit: 0 complete, 2 bad args, 3 preflight (install missing), 4 crawl failed, 5 report failed,
#       6 partial report written (something was blocked, skipped or timed out; see the reasons).
# No publish gate on this path: nothing here is verified by a person.
set -u
KIT_HOME="${KIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
T0=$(date +%s)

DISCLAIMER='This is an automated audit. It can miss things or flag things that aren'"'"'t real problems, so have your web person check before making changes.
It doesn'"'"'t guarantee rankings or traffic.
I didn'"'"'t change anything on your site.
Your site data stays on this bot'"'"'s computer and isn'"'"'t sent to the template creator.
Some advice may not apply to your business, site, or situation. Use your own judgment and check with your web person.
You choose which changes to make. The template creator and this bot are not responsible for any loss of traffic, rankings, leads, or revenue from following or not following this report.'
trap 'echo; echo "$DISCLAIMER"' EXIT

usage(){ sed -n 4,10p "$0" | sed 's/^# \{0,1\}//'; exit 2; }
URL=""; NAME=""; CSR_ON=0; PROXY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name) [ $# -ge 2 ] || usage; NAME="$2"; shift 2 ;;
    --name=*) NAME="${1#--name=}"; shift ;;
    --csr) CSR_ON=1; shift ;;
    --proxy) [ $# -ge 2 ] || usage; PROXY="$2"; shift 2 ;;
    --proxy=*) PROXY="${1#--proxy=}"; shift ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1"; usage ;;
    *) [ -z "$URL" ] || { echo "only one URL, got a second: $1"; usage; }; URL="$1"; shift ;;
  esac
done
[ -n "$URL" ] || usage
[[ "$URL" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/.*)?$ ]] || { echo "bad URL: $URL (need http:// or https:// and a host)"; exit 2; }
HOST=${URL#*://}; HOST=${HOST%%/*}; HOST=${HOST%%:*}; HOST=$(echo "$HOST" | tr 'A-Z' 'a-z')
DOMAIN=${HOST#www.}
SLUG=$(echo "$DOMAIN" | tr -c 'a-z0-9\n' '-')
[ -n "$NAME" ] || NAME="$DOMAIN"
AUDIT_TIMEOUT="${AUDIT_TIMEOUT:-1800}"
[[ "$AUDIT_TIMEOUT" =~ ^[0-9]+$ ]] || { echo "AUDIT_TIMEOUT must be whole seconds"; exit 2; }
# polite defaults; a user who wants more sets the env explicitly
export CRAWL_MAXP="${CRAWL_MAXP:-500}" CRAWL_CONCURRENCY="${CRAWL_CONCURRENCY:-2}" CRAWL_DELAY="${CRAWL_DELAY:-1}"
export CRAWL_UA="${CRAWL_UA:-GrokBot-SiteAudit}" CRAWL_ROBOTS="${CRAWL_ROBOTS:-obey}"
for _n in CRAWL_MAXP CRAWL_CONCURRENCY; do [[ "${!_n}" =~ ^[0-9]+$ ]] || { echo "$_n must be a whole number"; exit 2; }; done

# ── paths + tools ──────────────────────────────────────────────────────────────────────────
_W="${KIT_WORK:-$KIT_HOME/work}"
if [ -z "${CHROME_BIN:-}" ] && [ -s "$_W/chrome-bin" ]; then CHROME_BIN=$(head -1 "$_W/chrome-bin"); export CHROME_BIN; fi
if [ -f "$KIT_HOME/kit-paths.sh" ]; then . "$KIT_HOME/kit-paths.sh"
else
  KIT_WORK="$_W"; CRAWL_DIR="${CRAWL_DIR:-$KIT_WORK/crawl}"; CASES_DIR="${CASES_DIR:-$KIT_WORK/cases}"
  REPORTS_DIR="${REPORTS_DIR:-$KIT_HOME/reports}"
  [ -n "${PY:-}" ] || { [ -x "$KIT_HOME/.venv/bin/python" ] && PY="$KIT_HOME/.venv/bin/python" || PY=python3; }
  NODE="${NODE:-node}"; EMPTY_STDIN="$KIT_WORK/.empty-stdin"
  mkdir -p "$KIT_WORK" "$CRAWL_DIR" "$CASES_DIR" "$REPORTS_DIR"; : > "$EMPTY_STDIN"
  export KIT_HOME KIT_WORK CRAWL_DIR CASES_DIR REPORTS_DIR PY NODE EMPTY_STDIN
fi
export PATH="$KIT_HOME/node_modules/.bin:$PATH"   # the kit's own lighthouse first
[ -n "${CHROME_BIN:-}" ] && export CHROME_BIN CHROME_PATH="$CHROME_BIN"
OUT="$REPORTS_DIR/$DOMAIN"; CASE="$CASES_DIR/$SLUG"; J="$CRAWL_DIR/$SLUG-full.json"
mkdir -p "$OUT"
LOG="$OUT/run.log"; SINK="$KIT_WORK/.audit-sink"
# This tool's own outputs from an earlier run are replaced, never mixed with this run's.
rm -f "$OUT"/{site-audit.html,report.md,report.pdf,findings.json,linkmap.html}

# A failed run still leaves a report.md that says, on its first line, that it is partial and why.
stub(){ # stub <exit code> <reason>
  { echo "# PARTIAL REPORT: $2"; echo
    echo "> **Audit summary: PARTIAL.** This run did not finish. Nothing below is a complete audit."
    echo "> - Site: $URL"
    echo "> - Reason: $2"
    echo "> - Exit code: $1"
    echo "> - Run log: run.log (same folder)"
    echo; echo "## About this report"; echo; echo "$DISCLAIMER" | sed 's/^/- /'
  } > "$OUT/report.md"
  [ -s "$OUT/findings.json" ] || printf '{"partial": true, "reason": %s, "findings": []}\n' \
    "$("$PY" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$2" 2>"$SINK" || echo '"see report.md"')" > "$OUT/findings.json"
  echo
  echo "RESULT: PARTIAL. $2"
  echo "Partial report written: $OUT/report.md"
  exit "$1"
}

# ── preflight (exit 3) ─────────────────────────────────────────────────────────────────────
PRE=""
"$PY" -c 'import aiohttp, bs4, yarl' >"$SINK" 2>&1 || PRE="$PRE; Python packages missing for $PY (aiohttp, bs4, yarl)"
"$NODE" -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' >"$SINK" 2>&1 || PRE="$PRE; Node.js >= 20 not found ($NODE)"
(cd "$KIT_HOME" && "$NODE" -e "require('puppeteer-core')" >"$SINK" 2>&1) || PRE="$PRE; npm package puppeteer-core not installed"
{ [ -n "${CHROME_BIN:-}" ] && "$CHROME_BIN" --version >"$SINK" 2>&1; } || PRE="$PRE; Chrome not found (CHROME_BIN='${CHROME_BIN:-}')"
[ -f "$KIT_HOME/run-full-audit.sh" ] || PRE="$PRE; run-full-audit.sh missing in $KIT_HOME"
if [ -n "$PRE" ]; then
  echo "PREFLIGHT FAILED: the audit tools are not installed:${PRE#;}"
  echo "Fix: run ./install.sh (as root or with sudo), then ./install.sh --check."
  stub 3 "install missing:${PRE#;}"
fi
command -v lighthouse >"$SINK" 2>&1 || echo "WARN: lighthouse not found; performance will be NOT MEASURED"

# ── run the pipeline ───────────────────────────────────────────────────────────────────────
echo "Crawling up to $CRAWL_MAXP pages, $CRAWL_CONCURRENCY at a time, $CRAWL_DELAY s apart, obeying robots.txt, user agent $CRAWL_UA"
if ! grep -qiE 'robots\.txt|RobotFileParser|CRAWL_ROBOTS' "$KIT_HOME/full-extract.py" "$KIT_HOME/crawl_transport.py" 2>"$SINK"; then
  echo "WARNING: this version of full-extract.py does not read robots.txt; it will not skip disallowed pages."
fi
START="$KIT_WORK/.audit-start-$SLUG"; touch "$START"
echo "audit: $URL  domain=$DOMAIN  slug=$SLUG  timeout=${AUDIT_TIMEOUT}s  (log: $LOG)"
export CSR="$CSR_ON"
[ -n "$PROXY" ] && export CRAWL_PROXY="$PROXY"
{ echo "# audit.sh $(date -u +%FT%TZ) $URL slug=$SLUG name=$NAME csr=$CSR_ON proxy=${PROXY:+set} maxp=$CRAWL_MAXP conc=$CRAWL_CONCURRENCY delay=$CRAWL_DELAY ua=$CRAWL_UA"
  cd "$KIT_HOME" && timeout --kill-after=30 "$AUDIT_TIMEOUT" bash run-full-audit.sh "$URL" "$SLUG" "$NAME" "" "" "$CRAWL_MAXP" < "$EMPTY_STDIN"
} > "$LOG" 2>&1
RC=$?
echo "# run-full-audit.sh exit $RC" >> "$LOG"
TIMED_OUT=0; { [ "$RC" -eq 124 ] || [ "$RC" -eq 137 ]; } && TIMED_OUT=1
fresh(){ [ -s "$1" ] && [ "$1" -nt "$START" ]; }
left(){ local l=$(( AUDIT_TIMEOUT - ($(date +%s) - T0) )); [ "$l" -lt 120 ] && l=120; echo "$l"; }

# ── crawl (exit 4) ─────────────────────────────────────────────────────────────────────────
# A crawl with no page that answered 200 measured nothing (down host, refused port, blocked).
read PAGES OK200 FIRST <<< "$("$PY" -c "
import json,sys; p=json.load(open(sys.argv[1])).get('pages') or []
print(len(p), sum(1 for x in p if x.get('status')==200), (p[0].get('status') if p else None))" "$J" 2>"$SINK" || echo 0 0 none)"
if ! fresh "$J" || [ "${PAGES:-0}" -eq 0 ] || [ "${OK200:-0}" -eq 0 ]; then
  WHY="the crawl got no working page: ${PAGES:-0} address(es) tried, ${OK200:-0} answered 200, first answer ${FIRST:-none}"
  case "${FIRST:-}" in 401|403|406|429) WHY="$WHY (the site refused our crawler: bot block, captcha or login)";; 0|None|none) WHY="$WHY (no answer: site down or unreachable)";; esac
  [ "$TIMED_OUT" = 1 ] && WHY="$WHY; the run hit the time limit (AUDIT_TIMEOUT=${AUDIT_TIMEOUT}s)"
  echo "CRAWL FAILED: $WHY. Log lines:"
  { grep -E "FATAL|crawled|quality|BLOCKED|fallback|TINY" "$LOG" || tail -15 "$LOG"; } | head -15 | sed 's/^/  | /'
  stub 4 "$WHY"
fi

# ── reports (exit 5) ───────────────────────────────────────────────────────────────────────
NOTES=()
if ! fresh "$CASE/site-audit.html"; then
  mkdir -p "$CASE"
  echo "# audit.sh: building the HTML report (pipeline did not)" >> "$LOG"
  timeout "$(left)" "$NODE" "$KIT_HOME/build-crawl-report.js" "$J" "$CASE/site-audit.html" "$NAME" >> "$LOG" 2>&1
fi
fresh "$CASE/site-audit.html" || { echo "REPORT FAILED: site-audit.html was not built (see $LOG)"; stub 5 "the HTML report could not be built"; }
cp "$CASE/site-audit.html" "$OUT/site-audit.html"

# report.md comes from the builder's Markdown output; summary.py adds the verdict and the disclaimer.
MDSRC="$CASE/report.md"
timeout "$(left)" "$NODE" "$KIT_HOME/build-crawl-report.js" "$J" "$MDSRC" "$NAME" >> "$LOG" 2>&1
fresh "$MDSRC" || { echo "REPORT FAILED: the Markdown report was not built (see $LOG)"; stub 5 "the Markdown report could not be built"; }

SIDE="$CRAWL_DIR/$SLUG-findings.json"
if fresh "$SIDE"; then cp "$SIDE" "$OUT/findings.json"
else echo '{"findings": [], "note": "the findings sidecar was not produced by this run"}' > "$OUT/findings.json"
  NOTES+=("the findings step did not produce its file; findings.json is empty"); fi
fresh "$CASE/linkmap.html" && cp "$CASE/linkmap.html" "$OUT/linkmap.html"
[ "$TIMED_OUT" = 1 ] && NOTES+=("the run hit the time limit (AUDIT_TIMEOUT=${AUDIT_TIMEOUT}s); later steps did not run")
[ "$RC" -ne 0 ] && [ "$TIMED_OUT" = 0 ] && NOTES+=("the pipeline (run-full-audit.sh) exited with code $RC")
printf '%s\n' "${NOTES[@]:-}" > "$KIT_WORK/.audit-notes-$SLUG"

# ── verdict, summary, final report.md / PDF ────────────────────────────────────────────────
RULES="${KIT_RULES:-$KIT_HOME/report-rules.json}"; [ -f "$RULES" ] || RULES="$KIT_HOME/owner-rules.json"
DISCLAIMER="$DISCLAIMER" "$PY" "$KIT_HOME/audit-lib/summary.py" "$J" "$OUT" "$LOG" "$CRAWL_DIR/$SLUG-operator-findings.json" \
  "$KIT_WORK/.audit-notes-$SLUG" "$RULES" "$MDSRC" "$KIT_WORK/.audit-verdict-$SLUG"
VERDICT=$(head -1 "$KIT_WORK/.audit-verdict-$SLUG" 2>"$SINK" || echo PARTIAL)
fresh "$OUT/report.md" || { echo "REPORT FAILED: report.md was not written"; stub 5 "the Markdown report could not be written"; }

timeout "$(left)" "$NODE" "$KIT_HOME/report-pdf.js" "$OUT/site-audit.html" "$OUT/report.pdf" >> "$LOG" 2>&1
if ! fresh "$OUT/report.pdf"; then
  echo "REPORT FAILED: report.pdf was not printed (see $LOG). report.md and site-audit.html are written."
  exit 5
fi
[ "$VERDICT" = COMPLETE ] && exit 0
exit 6
