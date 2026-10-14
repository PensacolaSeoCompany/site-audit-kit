#!/usr/bin/env bash
# install.sh: set up everything site-audit-kit needs on a fresh Ubuntu 22.04/24.04 or Debian 12 machine.
# Safe to re-run (each step skips what is already there). No prompts.
#
#   ./install.sh            full install (apt packages need root or sudo), then the self-check
#   ./install.sh --no-apt   skip apt (no root): venv, npm and the self-check only
#   ./install.sh --check    self-check only (version table); changes nothing
#
# Versions: Node 22.x from NodeSource; npm packages exactly as in package-lock.json (npm ci);
# Python packages from the pinned requirements.txt; Chrome is Google's (or the distro's) current
# stable and cannot be pinned by this kit: the self-check prints the version you got.
# Env: KIT_HOME (default: this folder), KIT_WORK (default: $KIT_HOME/work), PY (python to check),
#      CHROME_BIN (use this Chrome; skip the Chrome install).
# Exit: 0 all OK, 1 a self-check item FAILED, 2 bad args, 3 an install step failed.
set -u
KIT_HOME="${KIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
KIT_WORK="${KIT_WORK:-$KIT_HOME/work}"
export DEBIAN_FRONTEND=noninteractive
NODE_MAJOR=22
MODE=full
for a in "$@"; do
  case "$a" in
    --check) MODE=check ;;
    --no-apt) MODE=noapt ;;
    -h|--help) sed -n 2,15p "$0"; exit 0 ;;
    *) echo "unknown option: $a (use --check, --no-apt or nothing)"; exit 2 ;;
  esac
done
mkdir -p "$KIT_WORK"
SINK="$KIT_WORK/.install-sink"
say(){ echo; echo "=== $* ==="; }
die(){ echo "INSTALL FAILED: $*"; exit 3; }

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >"$SINK" 2>&1; then SUDO="sudo -n"; fi
apt_install(){ $SUDO apt-get install -y -q --no-install-recommends "$@"; }
node_major(){ { command -v node >"$SINK" 2>&1 && node -p 'process.versions.node.split(".")[0]'; } 2>"$SINK" || echo 0; }

find_chrome(){
  if [ -n "${CHROME_BIN:-}" ] && [ -x "$CHROME_BIN" ]; then echo "$CHROME_BIN"; return 0; fi
  local c p
  for c in google-chrome google-chrome-stable chromium chromium-browser; do
    p=$(command -v "$c" 2>"$SINK") || continue
    # Ubuntu's chromium-browser .deb is a shim that asks for snap; it does not run in Docker.
    if "$p" --version >"$SINK" 2>&1; then echo "$p"; return 0; fi
  done
  return 1
}

if [ "$MODE" = full ]; then
  say "apt packages"
  if [ "$(id -u)" -ne 0 ] && [ -z "$SUDO" ]; then die "run as root, with passwordless sudo, or use --no-apt"; fi
  $SUDO apt-get update -q || die "apt-get update"
  apt_install ca-certificates curl gnupg python3 python3-venv python3-pip python3-dev build-essential \
    git bind9-dnsutils openssl procps fonts-liberation fonts-dejavu-core fontconfig xz-utils \
    || die "apt packages"
fi

if [ "$MODE" != check ]; then
  say "Python venv ($KIT_HOME/.venv) from requirements.txt"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "python3 >= 3.10 needed (found $(python3 --version 2>&1))"
  if [ ! -x "$KIT_HOME/.venv/bin/python" ]; then
    python3 -m venv "$KIT_HOME/.venv" || die "python3 -m venv (install python3-venv)"
  fi
  "$KIT_HOME/.venv/bin/python" -m pip install -q --upgrade pip || die "pip upgrade"
  "$KIT_HOME/.venv/bin/python" -m pip install -q -r "$KIT_HOME/requirements.txt" || die "pip install -r requirements.txt"

  say "Node.js $NODE_MAJOR.x (NodeSource)"
  if [ "$(node_major)" != "$NODE_MAJOR" ]; then
    [ "$MODE" = noapt ] && die "Node $NODE_MAJOR.x not found and --no-apt was given; install Node $NODE_MAJOR first"
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" -o "$KIT_WORK/nodesource_setup.sh" || die "download NodeSource setup"
    $SUDO bash "$KIT_WORK/nodesource_setup.sh" || die "NodeSource setup"
    apt_install nodejs || die "apt install nodejs"
  fi
  [ "$(node_major)" = "$NODE_MAJOR" ] || die "node $NODE_MAJOR.x expected, found $(node --version 2>&1)"
  echo "installed node $(node --version), npm $(npm --version)"

  say "npm packages (npm ci from package-lock.json)"
  export PUPPETEER_SKIP_DOWNLOAD=1   # we use the system Chrome, never puppeteer's bundled one
  [ -f "$KIT_HOME/package-lock.json" ] || die "package-lock.json missing: the npm versions are pinned there"
  (cd "$KIT_HOME" && npm ci --no-audit --no-fund) || die "npm ci"

  say "Chrome"
  CB=$(find_chrome) || CB=""
  if [ -z "$CB" ]; then
    [ "$MODE" = noapt ] && die "no Chrome found and --no-apt was given; install Chrome or set CHROME_BIN"
    if [ "$(dpkg --print-architecture)" = amd64 ]; then
      DEB="$KIT_WORK/google-chrome-stable_current_amd64.deb"
      if curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o "$DEB"; then
        apt_install "$DEB" || echo "google-chrome-stable .deb install failed; trying apt chromium"
      fi
    fi
    CB=$(find_chrome) || CB=""
    if [ -z "$CB" ]; then
      # Debian: chromium is a real package. Ubuntu: chromium-browser is a snap shim (no Docker).
      apt_install chromium || apt_install chromium-browser || true
      CB=$(find_chrome) || CB=""
    fi
  fi
  [ -n "$CB" ] || die "no working Chrome/Chromium (tried google-chrome, google-chrome-stable, chromium, chromium-browser)"
  echo "$CB" > "$KIT_WORK/chrome-bin"
  echo "Chrome: $CB, $("$CB" --version 2>&1)"
  echo "  (this is Google's or the distro's current stable; this kit cannot pin the Chrome version)"
  echo "  saved to $KIT_WORK/chrome-bin"
  echo "export CHROME_BIN=$CB"
fi

# ── self-check: version table ────────────────────────────────────────────────────────────────
say "self-check"
FAIL=0
printf '  %-6s %-16s %-26s %s\n' STATUS COMPONENT WANTED FOUND
row(){ # row <component> <wanted> <command...>: OK when the command succeeds; FOUND = its last output line
  local name="$1" want="$2"; shift 2
  local out st=OK
  out=$("$@" 2>&1) || st=FAIL
  # a pinned version (==X in requirements.txt, X (lock) from package-lock.json) must match exactly
  local exp; exp=$(echo "$want" | sed -nE 's/^==([^ ,]+).*/\1/p; s/^([0-9][0-9.]*) \(lock\)$/\1/p')
  [ "$st" = OK ] && [ -n "$exp" ] && [ "$(echo "$out" | tail -1 | awk '{print $1}')" != "$exp" ] && st=FAIL
  [ "$st" = FAIL ] && FAIL=$((FAIL+1))
  printf '  %-6s %-16s %-26s %s\n' "$st" "$name" "$want" "$(echo "$out" | tail -1 | cut -c1-90)"
}
if [ -n "${PY:-}" ]; then :; elif [ -x "$KIT_HOME/.venv/bin/python" ]; then PY="$KIT_HOME/.venv/bin/python"; else PY=python3; fi
req(){ grep -E "^$1[=<>~ ]" "$KIT_HOME/requirements.txt" | sed -E 's/#.*//; s/ +$//; s/^[A-Za-z0-9_.-]+//' | head -1; }
pyver(){ "$PY" -c "import importlib.metadata as m; print(m.version('$1'))"; }
row python   ">= 3.10"                  "$PY" -c 'import sys; assert sys.version_info >= (3, 10), sys.version; print(sys.version.split()[0])'
row aiohttp  "$(req aiohttp)"           pyver aiohttp
row yarl     "$(req yarl)"              pyver yarl
row bs4      "$(req beautifulsoup4)"    pyver beautifulsoup4
row lxml     "$(req lxml)"              pyver lxml
row node     "$NODE_MAJOR.x"            bash -c '[ "$(node -p "process.versions.node.split(\".\")[0]")" = '"$NODE_MAJOR"' ] && node --version'
row npm      "(comes with node)"        npm --version
lockver(){ "$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['packages']['node_modules/'+sys.argv[2]]['version'])" "$KIT_HOME/package-lock.json" "$1" 2>"$SINK" || echo "?"; }
row puppeteer-core "$(lockver puppeteer-core) (lock)" bash -c "cd '$KIT_HOME' && node -e \"require('puppeteer-core'); const p=require('path'), f=require('fs'); let d=p.dirname(require.resolve('puppeteer-core')); while(!f.existsSync(p.join(d,'package.json'))||JSON.parse(f.readFileSync(p.join(d,'package.json'))).name!=='puppeteer-core') d=p.dirname(d); console.log(JSON.parse(f.readFileSync(p.join(d,'package.json'))).version+' at '+d)\""
LH="$KIT_HOME/node_modules/.bin/lighthouse"; [ -x "$LH" ] || LH=$(command -v lighthouse 2>"$SINK" || echo lighthouse)
row lighthouse "$(lockver lighthouse) (lock)" "$LH" --version
if [ -z "${CHROME_BIN:-}" ] && [ -s "$KIT_WORK/chrome-bin" ]; then CHROME_BIN=$(head -1 "$KIT_WORK/chrome-bin"); fi
CB="${CHROME_BIN:-$(find_chrome || echo none)}"
row chrome   "current stable (unpinned)" "$CB" --version
echo
if [ "$FAIL" -eq 0 ]; then
  echo "SELF-CHECK OK. Python: $PY  Chrome: $CB"
  echo "export CHROME_BIN=$CB"
  echo "Next: ./audit.sh https://example.com"
  exit 0
fi
echo "SELF-CHECK FAILED: $FAIL item(s). Re-run ./install.sh (as root or with sudo)."
exit 1
