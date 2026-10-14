#!/usr/bin/env bash
# install.sh: set up everything site-audit-kit needs, inside this folder, without root.
# Safe to re-run: every step skips what is already present and verified. No prompts.
#
#   ./install.sh            no-root install (the default): never calls sudo or apt, never replaces the
#                           system Node or Chrome. Downloads what is missing into this folder:
#                             Node   system node >= 20, else Node $NODE_PIN into .node/ (SHA256 checked)
#                             Python system python3 >= 3.10 + venv, packages from requirements.txt into .venv/
#                             npm    exact versions from package-lock.json (npm ci) into node_modules/
#                             Chrome CHROME_BIN, else google-chrome/chromium on PATH, else Chrome for Testing
#                                    $CHROME_PIN (the build puppeteer-core expects) into .chrome/
#                           then runs the self-check.
#   ./install.sh --system   the same, plus apt packages (Chrome runtime libraries, fonts, Google Chrome
#                           .deb or distro chromium). Changes the system: needs root or passwordless sudo.
#   ./install.sh --check    self-check only (version table); changes nothing. Same as ./doctor.sh.
#
# Env: KIT_HOME (default: this folder), KIT_WORK (default: $KIT_HOME/work), PY (python to check),
#      CHROME_BIN (use this Chrome), KIT_LOCAL_CHROME=1 (ignore Chrome on PATH; use the pinned local one).
# Exit: 0 all OK, 1 a self-check item FAILED, 2 bad args, 3 an install step failed.
set -u
KIT_HOME="${KIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
KIT_WORK="${KIT_WORK:-$KIT_HOME/work}"
export DEBIAN_FRONTEND=noninteractive
NODE_MIN=20
# Node 22 LTS ("Jod"), released 2026-09-23. SHA256 values from https://nodejs.org/dist/v22.23.3/SHASUMS256.txt
NODE_PIN=v22.23.3
NODE_SHA_x64=df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de
NODE_SHA_arm64=a44aeb94849a299b22df10b9e622ec2f605c2183501bc40590705131de7c740f
# Chrome for Testing build that puppeteer-core 24.43.1 expects (lib/cjs/puppeteer/revisions.js)
CHROME_PIN=148.0.7778.97
LOCAL_NODE="$KIT_HOME/.node"
LOCAL_CHROME_DIR="$KIT_HOME/.chrome"
LOCAL_CHROME_BIN="$LOCAL_CHROME_DIR/chrome/linux-$CHROME_PIN/chrome-linux64/chrome"
MODE=user
for a in "$@"; do
  case "$a" in
    --check) MODE=check ;;
    --system) MODE=system ;;
    --no-apt) MODE=user ;;   # old name of the default
    -h|--help) sed -n 2,19p "$0"; exit 0 ;;
    *) echo "unknown option: $a (use --check, --system or nothing)"; exit 2 ;;
  esac
done
mkdir -p "$KIT_WORK"
SINK="$KIT_WORK/.install-sink"
say(){ echo; echo "=== $* ==="; }
die(){ echo "INSTALL FAILED: $*"; exit 3; }
sha_of(){ sha256sum "$1" 2>"$SINK" | awk '{print $1}'; }
# the kit-local Node wins over the system one once it exists (kit-paths.sh does the same)
[ -x "$LOCAL_NODE/bin/node" ] && export PATH="$LOCAL_NODE/bin:$PATH"

case "$(uname -m)" in
  x86_64|amd64) ARCH=x64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) ARCH="$(uname -m)" ;;
esac

node_major(){ { command -v node >"$SINK" 2>&1 && node -p 'process.versions.node.split(".")[0]'; } 2>"$SINK" || echo 0; }
node_kind(){ case "$(command -v node 2>"$SINK")" in "$LOCAL_NODE"/*) echo kit-local ;; '') echo none ;; *) echo system ;; esac; }

# find_chrome: CHROME_BIN, else (unless KIT_LOCAL_CHROME=1) a working Chrome on PATH, else the pinned local one
find_chrome(){
  if [ -n "${CHROME_BIN:-}" ] && [ -x "$CHROME_BIN" ]; then echo "$CHROME_BIN"; return 0; fi
  local c p
  if [ "${KIT_LOCAL_CHROME:-0}" != 1 ]; then
    for c in google-chrome google-chrome-stable chromium chromium-browser; do
      p=$(command -v "$c" 2>"$SINK") || continue
      # Ubuntu's chromium-browser .deb is a shim that asks for snap; it does not run in Docker.
      if "$p" --version >"$SINK" 2>&1; then echo "$p"; return 0; fi
    done
  fi
  if [ -x "$LOCAL_CHROME_BIN" ]; then echo "$LOCAL_CHROME_BIN"; return 0; fi
  return 1
}
chrome_kind(){ case "$1" in "$LOCAL_CHROME_DIR"/*) echo kit-local ;; *) echo system ;; esac; }
# chrome_launch <bin>: one headless page load; output = Chrome's stderr when it fails
chrome_launch(){
  local out
  out=$(timeout 60 "$1" --headless=new --no-sandbox --disable-gpu --dump-dom about:blank 2>&1) \
    && echo "$out" | grep -q "<html" && { echo "launched (headless page load OK)"; return 0; }
  echo "$out" | grep -v '^\s*$' | tail -3
  return 1
}
missing_libs(){ ldd "$1" 2>"$SINK" | awk '/not found/{print $1}' | sort -u | tr '\n' ' '; }

# ── system mode: apt packages (explicit flag only) ─────────────────────────────────────────────
SUDO=""
apt_install(){ $SUDO apt-get install -y -q --no-install-recommends "$@"; }
apt_has(){ apt-cache show "$1" >"$SINK" 2>&1; }
if [ "$MODE" = system ]; then
  echo "NOTICE: --system installs apt packages and may install Google Chrome system-wide (changes this machine)."
  if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >"$SINK" 2>&1 && sudo -n true 2>"$SINK" && SUDO="sudo -n" \
      || die "--system needs root or passwordless sudo; run ./install.sh without --system for the no-root install"
  fi
  say "apt packages"
  $SUDO apt-get update -q || die "apt-get update"
  apt_install ca-certificates curl python3 python3-venv xz-utils procps fontconfig fonts-liberation fonts-dejavu-core \
    || die "apt packages"
  # Chrome runtime shared libraries (also what the kit-local Chrome for Testing needs)
  ASOUND=libasound2t64; apt_has libasound2t64 || ASOUND=libasound2
  apt_install libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 "$ASOUND" libpango-1.0-0 libcairo2 libx11-xcb1 libxshmfence1 libgtk-3-0 \
    || die "apt Chrome runtime libraries"
fi

if [ "$MODE" != check ]; then
  # ── Python ────────────────────────────────────────────────────────────────────────────────
  say "Python venv ($KIT_HOME/.venv) from requirements.txt"
  command -v python3 >"$SINK" 2>&1 || { echo "python3 not found. Ask your admin to run: sudo apt-get install -y python3 python3-venv"; exit 3; }
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "python3 >= 3.10 needed (found $(python3 --version 2>&1))"
  if [ ! -x "$KIT_HOME/.venv/bin/python" ]; then
    if ! python3 -c 'import venv, ensurepip' >"$SINK" 2>&1; then
      echo "python3 has no venv/ensurepip module (the python3-venv package is missing)."
      echo "Fix (needs an admin): sudo apt-get install -y python3-venv   (or: ./install.sh --system)"
      exit 3
    fi
    python3 -m venv "$KIT_HOME/.venv" || die "python3 -m venv"
  fi
  REQ_SHA=$(sha_of "$KIT_HOME/requirements.txt")
  if [ "$(cat "$KIT_HOME/.venv/.kit-requirements.sha256" 2>"$SINK")" = "$REQ_SHA" ]; then
    echo "Python packages already present (requirements.txt unchanged): skip"
  else
    "$KIT_HOME/.venv/bin/python" -m pip install -q --disable-pip-version-check -r "$KIT_HOME/requirements.txt" \
      || die "pip install -r requirements.txt"
    echo "$REQ_SHA" > "$KIT_HOME/.venv/.kit-requirements.sha256"
  fi

  # ── Node ──────────────────────────────────────────────────────────────────────────────────
  say "Node.js (>= $NODE_MIN)"
  if [ "$(node_major)" -ge "$NODE_MIN" ] 2>"$SINK"; then
    if [ "$(node_kind)" = kit-local ]; then echo "Node $NODE_PIN already present in $LOCAL_NODE: skip"
    else echo "using system node $(node --version) at $(command -v node)"; fi
  else
    [ "$(node_major)" = 0 ] && echo "no node on PATH" || echo "system node $(node --version 2>&1) is older than $NODE_MIN"
    case "$ARCH" in x64) NSHA=$NODE_SHA_x64 ;; arm64) NSHA=$NODE_SHA_arm64 ;; *) die "no Node build pinned for CPU $ARCH" ;; esac
    command -v xz >"$SINK" 2>&1 || { echo "xz is missing (needed to unpack Node). Fix (needs an admin): sudo apt-get install -y xz-utils"; exit 3; }
    NTAR="node-$NODE_PIN-linux-$ARCH.tar.xz"
    mkdir -p "$KIT_WORK/downloads"
    if [ "$(sha_of "$KIT_WORK/downloads/$NTAR")" != "$NSHA" ]; then
      echo "downloading https://nodejs.org/dist/$NODE_PIN/$NTAR"
      curl -fsSL "https://nodejs.org/dist/$NODE_PIN/$NTAR" -o "$KIT_WORK/downloads/$NTAR" || die "download $NTAR"
    fi
    [ "$(sha_of "$KIT_WORK/downloads/$NTAR")" = "$NSHA" ] || die "SHA256 mismatch for $NTAR (expected $NSHA)"
    echo "SHA256 OK for $NTAR"
    rm -r -f "$LOCAL_NODE.tmp" "$LOCAL_NODE"
    mkdir -p "$LOCAL_NODE.tmp" && tar -xJf "$KIT_WORK/downloads/$NTAR" -C "$LOCAL_NODE.tmp" --strip-components=1 \
      || die "unpack $NTAR"
    mv "$LOCAL_NODE.tmp" "$LOCAL_NODE"
    export PATH="$LOCAL_NODE/bin:$PATH"
    hash -r
    [ "$("$LOCAL_NODE/bin/node" --version 2>&1)" = "$NODE_PIN" ] || die "kit-local node does not run"
    echo "installed kit-local Node $NODE_PIN in $LOCAL_NODE"
  fi
  echo "node $(node --version) ($(node_kind)), npm $(npm --version)"

  # ── npm packages ──────────────────────────────────────────────────────────────────────────
  say "npm packages (npm ci from package-lock.json)"
  export PUPPETEER_SKIP_DOWNLOAD=1   # install.sh fetches the pinned Chrome itself
  [ -f "$KIT_HOME/package-lock.json" ] || die "package-lock.json missing: the npm versions are pinned there"
  LOCK_SHA=$(sha_of "$KIT_HOME/package-lock.json")
  if [ "$(cat "$KIT_HOME/node_modules/.kit-lock.sha256" 2>"$SINK")" = "$LOCK_SHA" ] && [ -d "$KIT_HOME/node_modules/puppeteer-core" ]; then
    echo "npm packages already present (package-lock.json unchanged): skip"
  else
    (cd "$KIT_HOME" && npm ci --no-audit --no-fund) || die "npm ci"
    echo "$LOCK_SHA" > "$KIT_HOME/node_modules/.kit-lock.sha256"
  fi

  # ── Chrome ────────────────────────────────────────────────────────────────────────────────
  say "Chrome"
  CB=$(find_chrome) || CB=""
  if [ -z "$CB" ] && [ "$MODE" = system ]; then
    if [ "$(dpkg --print-architecture 2>"$SINK")" = amd64 ]; then
      DEB="$KIT_WORK/downloads/google-chrome-stable_current_amd64.deb"; mkdir -p "$KIT_WORK/downloads"
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
  if [ -z "$CB" ]; then
    [ "$ARCH" = x64 ] || die "no Chrome found, and Chrome for Testing has no Linux $ARCH build. Install chromium (./install.sh --system) or set CHROME_BIN"
    echo "no Chrome on PATH: downloading Chrome for Testing $CHROME_PIN into $LOCAL_CHROME_DIR (no root)"
    (cd "$KIT_HOME" && npx --no-install @puppeteer/browsers install "chrome@$CHROME_PIN" --path "$LOCAL_CHROME_DIR") \
      || die "Chrome for Testing download (npx @puppeteer/browsers install chrome@$CHROME_PIN)"
    [ -x "$LOCAL_CHROME_BIN" ] || die "Chrome for Testing not found at $LOCAL_CHROME_BIN after download"
    CB="$LOCAL_CHROME_BIN"
  elif [ "$CB" = "$LOCAL_CHROME_BIN" ]; then
    echo "Chrome for Testing $CHROME_PIN already present in $LOCAL_CHROME_DIR: skip"
  fi
  echo "$CB" > "$KIT_WORK/chrome-bin"
  echo "Chrome: $CB ($(chrome_kind "$CB")), $("$CB" --version 2>&1 | head -1)"
  echo "  saved to $KIT_WORK/chrome-bin"
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
  printf '  %-6s %-16s %-26s %s\n' "$st" "$name" "$want" "$(echo "$out" | tail -1 | cut -c1-110)"
}
if [ -n "${PY:-}" ]; then :; elif [ -x "$KIT_HOME/.venv/bin/python" ]; then PY="$KIT_HOME/.venv/bin/python"; else PY=python3; fi
req(){ grep -E "^$1[=<>~ ]" "$KIT_HOME/requirements.txt" | sed -E 's/#.*//; s/ +$//; s/^[A-Za-z0-9_.-]+//' | head -1; }
pyver(){ "$PY" -c "import importlib.metadata as m; print(m.version('$1'))"; }
os_name(){ . /etc/os-release 2>"$SINK" && echo "${PRETTY_NAME:-$ID} ($(uname -m))" || uname -sm; }
row os       "Linux"                    os_name
row python   ">= 3.10"                  "$PY" -c 'import sys; assert sys.version_info >= (3, 10), sys.version; print(sys.version.split()[0] + " at " + sys.executable)'
row aiohttp  "$(req aiohttp)"           pyver aiohttp
row yarl     "$(req yarl)"              pyver yarl
row bs4      "$(req beautifulsoup4)"    pyver beautifulsoup4
row lxml     "$(req lxml)"              pyver lxml
node_row(){ local m; m=$(node_major); [ "$m" -ge "$NODE_MIN" ] 2>"$SINK" || { echo "found ${m/#0/none}; run ./install.sh"; return 1; }
  echo "$(node --version) ($(node_kind)) at $(command -v node)"; }
row node     ">= $NODE_MIN"             node_row
row npm      "(comes with node)"        npm --version
lockver(){ "$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['packages']['node_modules/'+sys.argv[2]]['version'])" "$KIT_HOME/package-lock.json" "$1" 2>"$SINK" || echo "?"; }
row puppeteer-core "$(lockver puppeteer-core) (lock)" bash -c "cd '$KIT_HOME' && node -e \"require('puppeteer-core'); const p=require('path'), f=require('fs'); let d=p.dirname(require.resolve('puppeteer-core')); while(!f.existsSync(p.join(d,'package.json'))||JSON.parse(f.readFileSync(p.join(d,'package.json'))).name!=='puppeteer-core') d=p.dirname(d); console.log(JSON.parse(f.readFileSync(p.join(d,'package.json'))).version+' at '+d)\""
LH="$KIT_HOME/node_modules/.bin/lighthouse"; [ -x "$LH" ] || LH=$(command -v lighthouse 2>"$SINK" || echo lighthouse)
row lighthouse "$(lockver lighthouse) (lock)" "$LH" --version
if [ -z "${CHROME_BIN:-}" ] && [ -s "$KIT_WORK/chrome-bin" ]; then CHROME_BIN=$(head -1 "$KIT_WORK/chrome-bin"); fi
CB="${CHROME_BIN:-$(find_chrome || echo none)}"
chrome_row(){ [ -x "$CB" ] || { echo "none found; run ./install.sh"; return 1; }
  local v; v=$("$CB" --version 2>&1 | head -1) || { echo "$v"; return 1; }
  echo "$v ($(chrome_kind "$CB")) at $CB"; }
row chrome   "any; pinned $CHROME_PIN" chrome_row
LAUNCH_OK=1
if [ -x "$CB" ]; then
  row chrome-launch "headless page load" chrome_launch "$CB" || true
  chrome_launch "$CB" >"$SINK" 2>&1 || LAUNCH_OK=0
fi
disk_row(){ local k; k=$(df -Pk "$KIT_WORK" | awk 'NR==2{print $4}'); echo "$((k/1024)) MB free in $KIT_WORK"; [ "$k" -ge 1048576 ]; }
row disk     ">= 1024 MB free"          disk_row
echo
if [ -x "$CB" ] && [ "$LAUNCH_OK" = 0 ]; then
  LIBS=$(missing_libs "$CB")
  echo "Chrome at $CB does not start."
  if [ -n "$LIBS" ]; then
    echo "Missing shared libraries: $LIBS"
    echo "Fix: ./install.sh --system (uses sudo apt) or ask your admin to install: $LIBS"
  else
    echo "Fix: ./install.sh --system (uses sudo apt; installs Chrome's runtime libraries) or set CHROME_BIN to a Chrome that runs"
  fi
fi
if [ "$FAIL" -eq 0 ]; then
  echo "SELF-CHECK OK. Python: $PY  Node: $(command -v node)  Chrome: $CB"
  echo "Next: ./audit.sh https://example.com"
  exit 0
fi
echo "SELF-CHECK FAILED: $FAIL item(s). Fix the FAIL rows above, then re-run ./install.sh (or ./doctor.sh to check again)."
exit 1
