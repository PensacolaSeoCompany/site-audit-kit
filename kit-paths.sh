# kit-paths.sh: the ONE place that defines where the kit lives, where it writes and which tools it runs.
# Makes no network requests.
# Source it from every shell script:   . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"
# Every value can be overridden from the environment (or from config.env, see config.example.env).
#
#   KIT_HOME     repo root (default: the folder this file is in)
#   KIT_WORK     scratch + run state, gitignored (default: $KIT_HOME/work)
#   CRAWL_DIR    crawl JSON, Lighthouse JSON, findings sidecars (default: $KIT_WORK/crawl)
#   CASES_DIR    one folder per audit slug: run logs, evidence, site-audit.html (default: $KIT_WORK/cases)
#   REPORTS_DIR  published reports, one folder per domain (default: $KIT_HOME/reports)
#   PY           Python: $KIT_HOME/.venv/bin/python if present, else python3
#   NODE         Node.js (default: node)
#   CHROME_BIN   Chrome/Chromium: env, else the path in $KIT_WORK/chrome-bin (written by install.sh),
#                else the first of google-chrome, google-chrome-stable, chromium, chromium-browser
#                found on PATH (empty when none is installed). Also exported as CHROME_PATH.
#   CRAWL_MAXP=500 CRAWL_CONCURRENCY=2 CRAWL_DELAY=1 CRAWL_UA="GrokBot-SiteAudit (+repo URL)": polite
#                crawl defaults, exported for every stage
#   EMPTY_STDIN  an empty file used as stdin for unattended children (default: $KIT_WORK/.empty-stdin)
# Creates KIT_WORK, CRAWL_DIR, CASES_DIR and REPORTS_DIR. Writes nothing else.

KIT_HOME="${KIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# optional local settings file (gitignored); plain KEY=value lines, values already in the env win
if [ -f "$KIT_HOME/config.env" ] && [ -z "${KIT_CONFIG_LOADED:-}" ]; then
  while IFS='=' read -r _k _v; do
    case "$_k" in ''|\#*) continue ;; esac
    [[ "$_k" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    [ -n "${!_k:-}" ] && continue
    _v="${_v%%[[:space:]]#*}"                                  # drop an inline " # comment"
    _v="${_v#"${_v%%[![:space:]]*}"}"; _v="${_v%"${_v##*[![:space:]]}"}"   # trim
    _v="${_v%\"}"; _v="${_v#\"}"
    case "$_v" in ./*) _v="$KIT_HOME/${_v#./}" ;; esac         # ./x = relative to the kit, not the cwd
    export "$_k=$_v"
  done < "$KIT_HOME/config.env"
  KIT_CONFIG_LOADED=1
fi
KIT_WORK="${KIT_WORK:-$KIT_HOME/work}"
CRAWL_DIR="${CRAWL_DIR:-$KIT_WORK/crawl}"
CASES_DIR="${CASES_DIR:-$KIT_WORK/cases}"
REPORTS_DIR="${REPORTS_DIR:-$KIT_HOME/reports}"

if [ -z "${PY:-}" ]; then
  if [ -x "$KIT_HOME/.venv/bin/python" ]; then PY="$KIT_HOME/.venv/bin/python"; else PY="$(command -v python3 || echo python3)"; fi
fi
NODE="${NODE:-$(command -v node || echo node)}"
# install.sh may record the Chrome it installed in $KIT_WORK/chrome-bin
if [ -z "${CHROME_BIN:-}" ] && [ -s "$KIT_WORK/chrome-bin" ]; then CHROME_BIN="$(head -1 "$KIT_WORK/chrome-bin")"; fi
if [ -z "${CHROME_BIN:-}" ]; then
  for _c in google-chrome google-chrome-stable chromium chromium-browser; do
    _p="$(command -v "$_c" 2>&1)" && [ -n "$_p" ] && { CHROME_BIN="$_p"; break; }
  done
fi
CHROME_BIN="${CHROME_BIN:-}"
# Lighthouse and puppeteer-style tools read CHROME_PATH
[ -n "$CHROME_BIN" ] && CHROME_PATH="$CHROME_BIN"   # CHROME_BIN wins

# Polite crawl defaults (every stage inherits them; a value already in the env or config.env wins):
# at most 500 pages, 2 requests at a time, 1 s between requests, an honest user agent with a link.
CRAWL_MAXP="${CRAWL_MAXP:-500}"
CRAWL_CONCURRENCY="${CRAWL_CONCURRENCY:-2}"
CRAWL_DELAY="${CRAWL_DELAY:-1}"
CRAWL_UA="${CRAWL_UA:-GrokBot-SiteAudit (+https://github.com/PensacolaSeoCompany/site-audit-kit)}"
export CRAWL_MAXP CRAWL_CONCURRENCY CRAWL_DELAY CRAWL_UA

mkdir -p "$KIT_WORK" "$CRAWL_DIR" "$CASES_DIR" "$REPORTS_DIR"
EMPTY_STDIN="${EMPTY_STDIN:-$KIT_WORK/.empty-stdin}"
: > "$EMPTY_STDIN"   # always empty: a child that reads stdin gets EOF at once

export KIT_HOME KIT_WORK CRAWL_DIR CASES_DIR REPORTS_DIR PY NODE CHROME_BIN EMPTY_STDIN
[ -n "${CHROME_PATH:-}" ] && export CHROME_PATH
unset _c _k _v _p
