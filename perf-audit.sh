#!/usr/bin/env bash
# perf-audit.sh <url> [profile] — Lighthouse perf + media/lazy audit across device/connection tiers.
#   profile: mobile-4g (default) | mobile-3g | desktop | all
#     mobile-4g = Moto G phone, Slow 4G (Lighthouse default mobile)
#     mobile-3g = phone, Slow 3G (rtt 400ms / 400kbps / 4x CPU)
#     desktop   = desktop UA + WiFi/cable (no throttle)
#     all       = run all three + print a comparison table
#   out-dir: where <host>-<profile>.json is written (default $CRAWL_DIR)
# Local headless Chrome (CHROME_BIN) + the kit's own Lighthouse ($KIT_HOME/node_modules/.bin/lighthouse,
# installed by `npm install` in the kit). Reports Core Web Vitals, LCP element, LCP-lazy-loaded,
# opportunities, and a direct <video>/lazy-image check. Exit 3 when no Chrome or no Lighthouse.
# Each tier runs under a 5-minute timeout (PERF_TIER_TIMEOUT). Read-only: GET/HEAD only (Lighthouse
# loads the page as a visitor would; nothing is submitted).
set -u
URL="${1:?usage: perf-audit.sh <url> [mobile-4g|mobile-3g|desktop|all] [out-dir]}"
PROFILE="${2:-mobile-4g}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kit-paths.sh"
OUTDIR="${3:-$CRAWL_DIR}"
mkdir -p "$OUTDIR"
SLUG=$(echo "$URL" | sed -E 's#https?://##; s#/.*##')
[ -n "$CHROME_BIN" ] && [ -x "$CHROME_BIN" ] || { echo "FATAL: no Chrome (set CHROME_BIN)"; exit 3; }
export CHROME_PATH="$CHROME_BIN"
# Lighthouse: the kit's pinned copy first; a global one only as a fallback (its version may differ).
if [ -x "$KIT_HOME/node_modules/.bin/lighthouse" ]; then LH=("$KIT_HOME/node_modules/.bin/lighthouse")
elif _lh="$(command -v lighthouse 2>&1)" && [ -n "$_lh" ]; then
  LH=("$_lh"); echo "WARN: $KIT_HOME/node_modules/.bin/lighthouse missing (run npm install in the kit); using $_lh" >&2
else echo "FATAL: Lighthouse not installed. Run: cd $KIT_HOME && npm install"; exit 3; fi
CF="--headless=new --no-sandbox --disable-gpu"   # always; --no-sandbox is needed in Docker or when run as root
T_TIER="${PERF_TIER_TIMEOUT:-300}"               # 5 min per Lighthouse tier

run_profile() {  # $1=profile -> writes $OUTDIR/$SLUG-$1.json
  local prof="$1"
  local json="$OUTDIR/${SLUG}-${prof}.json"
  local args=()
  case "$prof" in
    desktop)   args=(--preset=desktop) ;;
    mobile-3g) args=(--throttling-method=simulate --throttling.rttMs=400 --throttling.throughputKbps=400
                     --throttling.cpuSlowdownMultiplier=4 --throttling.requestLatencyMs=1600
                     --throttling.downloadThroughputKbps=400 --throttling.uploadThroughputKbps=400) ;;
    mobile-4g) args=() ;;   # Lighthouse default = mobile + Slow 4G
  esac
  timeout --kill-after=20 "$T_TIER" "${LH[@]}" "$URL" --only-categories=performance --output=json --output-path="$json" --quiet \
    "${args[@]}" --chrome-flags="$CF" 2>> "$OUTDIR/.lighthouse-stderr.log"
  echo "$json"
}

detail() {  # full single-profile report from a json
  "$PY" - "$1" "$2" <<'PY'
import json,sys
prof=sys.argv[2]; d=json.load(open(sys.argv[1])); a=d['audits']; c=d['configSettings']
print(f"=== {prof}  (formFactor={c.get('formFactor')}, rtt={c.get('throttling',{}).get('rttMs')}ms, down={c.get('throttling',{}).get('throughputKbps')}kbps) ===")
print("Performance score:", round(d['categories']['performance']['score']*100), "/100")
for k,lbl in [('first-contentful-paint','FCP'),('largest-contentful-paint','LCP'),('total-blocking-time','TBT'),
              ('cumulative-layout-shift','CLS'),('speed-index','Speed Index'),('interactive','TTI')]:
    if k in a: print(f"  {lbl:13} {a[k].get('displayValue','-')}")
lcp=a.get('largest-contentful-paint-element',{}).get('details',{}).get('items',[])
if lcp:
    n=lcp[0].get('items',[{}]); node=(n[0].get('node',{}) if n else {})
    print("  LCP element :", (node.get('snippet') or node.get('selector') or '')[:110])
_lazy = a.get('lcp-lazy-loaded',{}).get('score')==0
print("  LCP lazy-loaded?", "YES (BAD — never lazy-load the LCP)" if _lazy else "no")
if _lazy:
    print("  -> #1 FIX: make the LCP image eager + fetchpriority=high (never loading=lazy).")
else:
    # LCP is NOT a lazy image — do not print the stock advice; name the real levers from the data.
    _lev=[]
    for _k,_lbl in [('render-blocking-resources','render-blocking resources'),
                    ('unused-javascript','unused JavaScript'),
                    ('unminified-javascript','unminified JS'),
                    ('font-display','web fonts blocking text'),
                    ('server-response-time','slow server response')]:
        _o=a.get(_k,{})
        _ms=_o.get('numericValue') or 0
        if _o.get('score') is not None and _o.get('score')<0.9 and _ms>100:
            _lev.append(f"{_lbl} (~{int(_ms)}ms)")
    print("  -> LCP is not a lazy image. Real levers:", "; ".join(_lev) if _lev else "see opportunities below")
print("  Opportunities:")
for k,v in a.items():
    if v.get('details',{}).get('type')=='opportunity' and v.get('numericValue',0)>100:
        print(f"    {k}: ~{int(v['numericValue'])}ms / {v.get('displayValue','')}")
PY
}

if [ "$PROFILE" = all ]; then
  for p in mobile-3g mobile-4g desktop; do echo "[$p] running..." >&2; run_profile "$p" > "$OUTDIR/.perf-last-path"; done
  echo "=== COMPARISON — $URL ==="
  "$PY" - "$OUTDIR" "$SLUG" <<'PY'
import json,sys
outdir,slug=sys.argv[1],sys.argv[2]
rows=[('Mobile Slow-3G','mobile-3g'),('Mobile Slow-4G','mobile-4g'),('Desktop / WiFi','desktop')]
_max_tbt=0
for lbl,p in rows:
    try:
        d=json.load(open(f"{outdir}/{slug}-{p}.json"));a=d['audits']
        _cls=a.get('cumulative-layout-shift',{}).get('displayValue','?')
        _flag=' <-- CLS FAILS (>0.1)' if (a.get('cumulative-layout-shift',{}).get('numericValue') or 0)>0.1 else ''
        _sc=(d.get('categories',{}).get('performance',{}) or {}).get('score')
        if _sc is None:
            print(f"{lbl:20} BLOCKED — Lighthouse could not load the page (WAF/bot protection). "
                  f"NOT a score of 0; this tier is UNMEASURED."); continue
        print(f"{lbl:20} score={round(d['categories']['performance']['score']*100):>3}  LCP={a['largest-contentful-paint']['displayValue']:>8}  FCP={a['first-contentful-paint']['displayValue']:>7}  TBT={a['total-blocking-time']['displayValue']:>9}  CLS={_cls:>6}{_flag}")
        _max_tbt=max(_max_tbt, a.get('total-blocking-time',{}).get('numericValue') or 0)
    except Exception as e: print(lbl,'ERR',e)
print("\nTargets: LCP<2.5s  FCP<1.8s  TBT<200ms  CLS<0.1")
# TBT ATTRIBUTION (a client site, 2026-08-01): TBT 1,210ms shipped next to "2 lazy images" — the cause
# of high TBT is JAVASCRIPT EXECUTION (framework hydration, unused bundles), never images. Say so
# whenever TBT blows the target so the media summary cannot misattribute it.
try:
    _tbt_ms = _max_tbt
    if _tbt_ms > 600:
        print(f"NOTE: TBT {_tbt_ms:.0f}ms is the dominant mobile defect — caused by JavaScript")
        print("      execution (hydration / unused bundles: read 'unused-javascript' and")
        print("      'mainthread-work-breakdown' in the JSON), NOT by images or lazy-loading.")
except Exception:
    pass
PY
else
  J=$(run_profile "$PROFILE"); detail "$J" "$PROFILE"
fi

echo; echo "=== Media / lazy-load (static HTML) ==="
H=$(curl -s -m 20 -A "Mozilla/5.0" "$URL" 2>&1)
echo "  <video> tags: $(echo "$H" | grep -oic '<video')   autoplay refs: $(echo "$H" | grep -oic 'autoplay')"
echo "  <img> total:  $(echo "$H" | grep -oic '<img')   loading=lazy: $(echo "$H" | grep -oic 'loading=\"lazy\"')"
echo "  (LCP guidance is printed per-profile from the audit data — see "LCP lazy-loaded?" above.)"
