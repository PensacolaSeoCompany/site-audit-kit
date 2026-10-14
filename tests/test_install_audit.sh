#!/usr/bin/env bash
# Smoke test for the one-command layer: install self-check, then ./audit.sh on a local synthetic site
# (tests/fixtures/smoke-site, served by tests/fixtures/smoke_server.py on loopback addresses).
#   A  127.0.0.1  plain site. About links to /history-missing.html, which does not exist (the planted 404).
#   B  127.0.0.2  robots.txt disallows /members.html: it must be listed as not checked, never as broken.
#   C  127.0.0.3  /staff.html answers 403: the report must say PARTIAL and list it as not checked.
#   D  127.0.0.4  nothing listens: exit 4, and report.md still says PARTIAL on its first line.
#
#   bash tests/test_install_audit.sh            (inside the test container, or after ./install.sh)
#   SKIP_INSTALL_CHECK=1 bash tests/test_install_audit.sh
# Env: AUDIT_TIMEOUT (default 900 here). Exit 0 = every assertion passed.
set -u
KIT_HOME="${KIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export KIT_HOME
FIX="$KIT_HOME/tests/fixtures/smoke-site"
TOUT="$KIT_HOME/tests/_out"; mkdir -p "$TOUT"
export AUDIT_TIMEOUT="${AUDIT_TIMEOUT:-900}"
RDIR="${REPORTS_DIR:-$KIT_HOME/reports}"
PY="${PY:-python3}"
FAILS=0; SRV=""
check(){ if eval "$2"; then echo "  PASS  $1"; else echo "  FAIL  $1"; FAILS=$((FAILS+1)); fi; }
stop_server(){ [ -n "$SRV" ] && { kill "$SRV" 2>"$TOUT/kill.err"; wait "$SRV" 2>"$TOUT/kill.err"; }; SRV=""; }
trap stop_server EXIT
free_port(){ "$PY" -c 'import socket,sys; s=socket.socket(); s.bind((sys.argv[1], 0)); print(s.getsockname()[1]); s.close()' "$1"; }
start_server(){ # start_server <host> <port> [server args]
  local h="$1" p="$2"; shift 2
  "$PY" "$KIT_HOME/tests/fixtures/smoke_server.py" "$FIX" "$h" "$p" "$@" > "$TOUT/server-$h.log" 2>&1 &
  SRV=$!
  for _ in $(seq 1 50); do
    "$PY" -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=1)" "http://$h:$p/" 2>"$TOUT/probe.err" && return 0
    sleep 0.2
  done
  echo "FAIL: test server on $h:$p did not start"; return 1
}
run_audit(){ # run_audit <case> <url> -> sets RC, OUTF
  OUTF="$TOUT/audit-$1.out"
  bash "$KIT_HOME/audit.sh" "$2" --name "Smoke Test Site" > "$OUTF" 2>&1; RC=$?
  echo "  ($1: audit.sh exit $RC; output in $OUTF)"
}
# md_section <md> <path>: prints where <path> is listed: "notchecked", "broken" or nothing
md_where='
import re, sys
md = open(sys.argv[1], encoding="utf-8").read(); path = sys.argv[2]; where = set(); sec = ""
for line in md.splitlines():
    if line.startswith("#"): sec = line.lower()
    if path not in line: continue
    low = line.lower()
    if "could not check" in sec or "not measured" in low or "refused" in low or "robots" in low: where.add("notchecked")
    if re.search(r"\b404\b|broken|not there", low + " " + sec): where.add("broken")
print(" ".join(sorted(where)))'

if [ "${SKIP_INSTALL_CHECK:-0}" != 1 ]; then
  echo "== install.sh --check"
  bash "$KIT_HOME/install.sh" --check || { echo "FAIL: install self-check"; exit 1; }
fi

echo "== case A: plain site (planted 404)"
P=$(free_port 127.0.0.1); start_server 127.0.0.1 "$P" || exit 1
run_audit A "http://127.0.0.1:$P/"; stop_server
R="$RDIR/127.0.0.1"
cat "$OUTF" | sed -n '/AUDIT SUMMARY/,$p'
check "A: exit 0 or 6 (got $RC)"                 '[ "$RC" -eq 0 ] || [ "$RC" -eq 6 ]'
check "A: site-audit.html exists"                '[ -s "$R/site-audit.html" ]'
check "A: report.md exists"                      '[ -s "$R/report.md" ]'
check "A: report.pdf exists and is a PDF"        '[ -s "$R/report.pdf" ] && head -c 5 "$R/report.pdf" | grep -q "%PDF-"'
check "A: findings.json is valid JSON"           '"$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$R/findings.json"'
check "A: run.log exists"                        '[ -s "$R/run.log" ]'
check "A: report.md names the planted 404"       'grep -q "history-missing" "$R/report.md"'
check "A: PARTIAL first line iff exit 6"         'if [ "$RC" -eq 6 ]; then head -1 "$R/report.md" | grep -q "^# PARTIAL REPORT: "; else ! grep -q "PARTIAL" "$R/report.md"; fi'
check "A: polite crawl settings line printed"    'grep -q "^Crawling up to 500 pages, 2 at a time, 1 s apart, obeying robots.txt, user agent GrokBot-SiteAudit$" "$OUTF"'
check "A: summary with pages and NOT MEASURED"   'grep -qE "pages: +[1-9][0-9]* crawled" "$OUTF" && grep -q "NOT MEASURED" "$OUTF"'
check "A: disclaimer printed"                    'grep -q "^I didn.t change anything on your site.$" "$OUTF" && grep -q "^It doesn.t guarantee rankings or traffic.$" "$OUTF"'
check "A: disclaimer in report.md"               'grep -q "I didn.t change anything on your site." "$R/report.md"'

echo "== case B: robots.txt disallows /members.html"
P=$(free_port 127.0.0.2); start_server 127.0.0.2 "$P" --disallow /members.html || exit 1
SKIP_PERF=1 run_audit B "http://127.0.0.2:$P/"; stop_server
R="$RDIR/127.0.0.2"; W=$("$PY" -c "$md_where" "$R/report.md" members.html 2>"$TOUT/where.err")
echo "  (members.html listed as: ${W:-not listed})"
check "B: report.md written (exit $RC)"          '[ -s "$R/report.md" ]'
check "B: members.html listed as not checked"    '[[ " $W " == *" notchecked "* ]]'
check "B: members.html not listed as broken"     '[[ " $W " != *" broken "* ]]'

echo "== case C: /staff.html answers 403"
P=$(free_port 127.0.0.3); start_server 127.0.0.3 "$P" --forbid /staff.html || exit 1
SKIP_PERF=1 run_audit C "http://127.0.0.3:$P/"; stop_server
R="$RDIR/127.0.0.3"; W=$("$PY" -c "$md_where" "$R/report.md" staff.html 2>"$TOUT/where.err")
echo "  (staff.html listed as: ${W:-not listed}; first line: $(head -1 "$R/report.md"))"
check "C: exit 6 (partial) (got $RC)"            '[ "$RC" -eq 6 ]'
check "C: report.md first line is PARTIAL"       'head -1 "$R/report.md" | grep -q "^# PARTIAL REPORT: "'
check "C: summary block says PARTIAL"            'grep -q "Audit summary: PARTIAL" "$R/report.md"'
check "C: staff.html listed as not checked"      '[[ " $W " == *" notchecked "* ]]'
check "C: staff.html not listed as broken"       '[[ " $W " != *" broken "* ]]'
check "C: stdout says PARTIAL"                   'grep -q "^RESULT: *PARTIAL" "$OUTF"'

echo "== case D: nothing listens (crawl fails)"
P=$(free_port 127.0.0.4)
AUDIT_TIMEOUT=300 run_audit D "http://127.0.0.4:$P/"
R="$RDIR/127.0.0.4"
check "D: exit 4 (got $RC)"                      '[ "$RC" -eq 4 ]'
check "D: report.md first line is PARTIAL"       'head -1 "$R/report.md" | grep -q "^# PARTIAL REPORT: "'
check "D: stdout says CRAWL FAILED and PARTIAL"  'grep -q "CRAWL FAILED" "$OUTF" && grep -q "^RESULT: PARTIAL" "$OUTF"'
check "D: disclaimer printed on failure"         'grep -q "^I didn.t change anything on your site.$" "$OUTF"'

echo "== bad arguments"
bash "$KIT_HOME/audit.sh" not-a-url > "$TOUT/audit-badargs.out" 2>&1; RC=$?
check "bad args exit 2 (got $RC)"                '[ "$RC" -eq 2 ]'

echo
if [ "$FAILS" -eq 0 ]; then echo "SMOKE TEST OK"; exit 0; fi
echo "SMOKE TEST FAILED: $FAILS assertion(s)"; exit 1
