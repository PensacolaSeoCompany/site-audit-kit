#!/usr/bin/env bash
# publish-run.sh: the ONLY way a site-audit page reaches $REPORTS_DIR (2026-10-13). run-full-audit.sh
# Makes no network requests (read-only toward audited sites; local file copy only).
# builds into the case folder ($CASES_DIR/<slug>/); a verifier checks it; this script publishes.
# "Publish" = copy into $REPORTS_DIR/<domain>/ (default $KIT_HOME/reports/<domain>/). It never touches
# a web server; serve or upload that folder however you like. Tests: tests/test_publish_gate.py.
#
# Usage: publish-run.sh <slug> <kind> <source.html> [domain]
#   kind = report    -> <domain>/site-audit.html           (the builder page, when no email exists)
#          email     -> <domain>/site-audit.html           (the plain-language email page; the client link)
#          detailed  -> <domain>/site-audit-detailed.html  (the builder page, when an email exists)
#          linkmap   -> <domain>/linkmap.html              (interlink map; no verification needed)
#          correction -> <domain>/site-audit.html          (TEXT fix of an already-published page whose crawl
#                        data is gone; needs PUBLISH_REASON; logged to <case>/CORRECTIONS.log; 2026-10-14)
#   domain = host without "www." (example.com). Default: PUBLISH_DOMAIN, else the "site" (or first page
#            URL) of $CRAWL_DIR/<slug>-full.json, else the slug itself.
#
# Refuses (exit 3) when (2026-10-14 review round 2):
#   - the source is not inside $CASES_DIR/<slug>/  (no publishing one client's file under another slug)
#   - report/email/detailed: <case>/findings-verified.json is missing (nothing was verified)
#   - report/email/detailed: the run log ends INCOMPLETE and a skipped step is not declared in
#     $CRAWL_DIR/<slug>-findings.json "not_measured": [{"step","what","why"}] (the builder shows that list)
#   - the rules file (KIT_RULES, default report-rules.json, or the older name owner-rules.json) is
#     missing or broken, or page_forbidden matches anywhere in the page, or finding_forbidden matches
#     finding text
#   - a "goes nowhere" finding without a click-test.js "does nothing" result in <case>/evidence/
#   - the target exists and was stamped with a DIFFERENT kind (a report would overwrite an approved email)
# Every overwrite is backed up first to $KIT_WORK/publish-backups/<domain>/ (outside $REPORTS_DIR;
# an earlier in-folder _backup/ was public). Nothing is deleted.
set -u
SLUG="${1:?slug}"; KIND="${2:?kind report|email|detailed|linkmap|correction}"; SRC="${3:?source html}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"
BACKUP_ROOT="${PUBLISH_BACKUP_DIR:-$KIT_WORK/publish-backups}"
RULES="${KIT_RULES:-$KIT_HOME/report-rules.json}"
[ -f "$RULES" ] || { [ -z "${KIT_RULES:-}" ] && [ -f "$KIT_HOME/owner-rules.json" ] && RULES="$KIT_HOME/owner-rules.json"; }
refuse() { echo "REFUSED: $*"; exit 3; }

[[ "$SLUG" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || refuse "slug '$SLUG' has characters outside a-z 0-9 . _ -"
DOMAIN="${4:-${PUBLISH_DOMAIN:-}}"
if [ -z "$DOMAIN" ] && [ -s "$CRAWL_DIR/$SLUG-full.json" ]; then
  DOMAIN="$("$PY" - "$CRAWL_DIR/$SLUG-full.json" <<'EOF' 2>&1 | tail -1
import json, sys
from urllib.parse import urlparse
try:
    d = json.load(open(sys.argv[1]))
    s = d.get('site') or ''
    if '/' in s: s = urlparse(s).hostname or ''
    if not s:
        pg = d.get('pages') or []
        s = urlparse(pg[0].get('url') or '').hostname or '' if pg else ''
    print(s)
except Exception:
    print('')
EOF
)"
fi
DOMAIN="${DOMAIN:-$SLUG}"; DOMAIN="$(echo "$DOMAIN" | tr 'A-Z' 'a-z')"; DOMAIN="${DOMAIN#www.}"
[[ "$DOMAIN" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || refuse "domain '$DOMAIN' has characters outside a-z 0-9 . _ -"
DEST="$REPORTS_DIR/$DOMAIN"
case "$KIND" in
  report|email) TGT="$DEST/site-audit.html" ;;
  detailed)     TGT="$DEST/site-audit-detailed.html" ;;
  linkmap)      TGT="$DEST/linkmap.html" ;;
  correction)   TGT="$DEST/site-audit.html"
                [ -f "$TGT" ] || refuse "correction needs an already-published $TGT"
                [ -n "${PUBLISH_REASON:-}" ] || refuse "correction needs PUBLISH_REASON='what was corrected and why'" ;;
  *) refuse "kind must be report, email, detailed, linkmap or correction" ;;
esac
[ -s "$SRC" ] || refuse "source $SRC missing or empty"

CASE="$CASES_DIR/$SLUG"
SRC_REAL="$(realpath -e "$SRC")"; CASE_REAL="$(realpath -m "$CASE")"
[[ "$SRC_REAL" == "$CASE_REAL"/* ]] || refuse "source must be inside $CASE/ (got $SRC_REAL)"

if [ "$KIND" != linkmap ] && [ "$KIND" != correction ]; then
  [ -s "$CASE/findings-verified.json" ] || refuse "$CASE/findings-verified.json is missing: verify the audit first (VERIFY-AGENT-BRIEF.md)"
  LOG="$(ls -t "$CASE"/run*.log 2>&1 | grep -v 'No such' | head -1)"
  if [ -n "$LOG" ] && [ "$(grep -E 'INCOMPLETE|ALL STEPS COMPLETE' "$LOG" | tail -1 | grep -c INCOMPLETE)" = 1 ]; then
    SKIPPED="$(grep -oE '✗ [a-z0-9_-]+ +— SKIPPED' "$LOG" | awk '{print $2}' | sort -u)"
    MISSING="$("$PY" - "$CRAWL_DIR/$SLUG-findings.json" $SKIPPED <<'EOF'
import json, sys
try: nm = {str(x.get('step')) for x in json.load(open(sys.argv[1])).get('not_measured', [])}
except Exception: nm = set()
print(' '.join(s for s in sys.argv[2:] if s not in nm))
EOF
)"
    [ -z "$MISSING" ] || refuse "run log $LOG says INCOMPLETE; skipped step(s) not declared in $CRAWL_DIR/$SLUG-findings.json not_measured: $MISSING"
  fi
fi

# Report rules. Finding text = finding titles, walk-through steps and finding bodies.
[ -f "$RULES" ] || refuse "rules file $RULES not found (set KIT_RULES, or restore report-rules.json)"
"$PY" - "$RULES" "$SRC" "$CASE" <<'EOF' || exit 3
import glob, json, os, re, sys, html as H
try:
    rules = json.load(open(sys.argv[1]))
except Exception as e:
    print(f'REFUSED: rules file {sys.argv[1]} is not valid JSON: {e}'); sys.exit(3)
page = open(sys.argv[2], encoding='utf-8', errors='replace').read()
def hits(pattern, text):
    return [m.group(0) for m in re.finditer(pattern, text, re.I)][:3]
bad = []
for r in rules.get('page_forbidden', []):
    for h in hits(r['re'], page): bad.append((r['id'], h))
parts = re.findall(r'<span class=ft>(.*?)</span>|<div class=wbody>(.*?)</div>|<div class=fxb><p>(.*?)</p>', page, re.S)
ftext = '\n'.join(H.unescape(re.sub(r'<[^>]+>', ' ', ''.join(p))) for p in parts)
for r in rules.get('finding_forbidden', []):
    for h in hits(r['re'], ftext): bad.append((r['id'], h))
# 2026-10-14 (a client site: "7 service buttons go nowhere" were working tabs): a "goes nowhere /
# does nothing" finding needs a click-test.js result in <case>/evidence/ with a "does nothing" verdict.
NOWHERE = r"\b(go(es)?|lead(s)?|point(s)?) nowhere\b(?! useful)|\bdo(es)? nothing\b|\bdead (buttons?|links?)\b"
if re.search(NOWHERE, ftext, re.I):
    dead = 0
    for f in glob.glob(os.path.join(sys.argv[3], 'evidence', 'click-test*.json')):
        try:  # click-test.js format: {"status": "measured", "results": [{"verdict": ...}]}
            ct = json.load(open(f))
            if ct.get('status') == 'measured': dead += sum(1 for e in ct.get('results', []) if e.get('verdict') == 'does nothing')
        except Exception: pass
    if not dead:
        bad.append(('click-test', 'a "goes nowhere" finding needs evidence/click-test*.json (node click-test.js <url> <case>/evidence/click-test-<page>.json) with a "does nothing" verdict'))
if bad:
    print('REFUSED: report rule(s) broken:')
    for i, h in bad: print(f'  [{i}] ...{h}...')
    sys.exit(3)
EOF

stamp_kind() { grep -oE 'published-by: publish-run.sh kind=[a-z]+' "$1" 2>&1 | head -1 | sed 's/.*kind=//'; }
mkdir -p "$DEST"
if [ -f "$TGT" ]; then
  OLD=$(stamp_kind "$TGT")
  if [ -n "$OLD" ] && [ "$OLD" != "$KIND" ] && [ "$KIND" != correction ]; then
    echo "REFUSED: $TGT is a published '$OLD' page; will not overwrite it with '$KIND'."
    [ "$OLD" = "email" ] && echo "         Publish the builder page as: publish-run.sh $SLUG detailed $SRC"
    exit 3; fi
  mkdir -p "$BACKUP_ROOT/$DOMAIN"
  cp -p "$TGT" "$BACKUP_ROOT/$DOMAIN/$(basename "$TGT").$(date -u +%Y%m%dT%H%M%SZ)"
fi

SHA=$(sha256sum "$SRC" | cut -c1-16)
TMP=$(mktemp "$DEST/.publish.XXXXXX")
{ cat "$SRC"; printf '\n<!-- published-by: publish-run.sh kind=%s slug=%s src_sha256=%s at=%s -->\n' "$KIND" "$SLUG" "$SHA" "$(date -u +%FT%TZ)"; } > "$TMP"
chmod 644 "$TMP"; mv -f "$TMP" "$TGT"
[ "$KIND" = correction ] && printf '%s\t%s\tsrc_sha256=%s\t%s\n' "$(date -u +%FT%TZ)" "$DOMAIN/$(basename "$TGT")" "$SHA" "$PUBLISH_REASON" >> "$CASE/CORRECTIONS.log"
echo "PUBLISHED $KIND -> $TGT"
