#!/usr/bin/env python3
"""pixel-consent-check.py — LAYER 2 of check 18 (PRE-CONSENT TRACKING EXPOSURE): the live layer.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

Cold-loads a URL in headless Chrome (fresh profile — no cookies, no prior consent), captures every
network request made BEFORE any interaction (no clicks, no scroll, no banner answer), and prints
which known tracking hosts were contacted. This is the AUTHORITATIVE layer:

  - Check 18's static layer reads signatures out of raw HTML. It cannot see trackers loaded by a
    Google Tag Manager container, and it cannot tell whether a detected consent manager actually
    BLOCKS anything. A banner is not blocking — "compliance theater" (banner shows, pixels wired
    outside the consent tool fire anyway) is a common GTM misconfiguration.
  - THIS script measures the thing itself: the pre-consent network waterfall. A pre-consent
    request to facebook.com/tr, px.ads.linkedin.com etc. is the exact fact pattern behind the
    CIPA (Cal. Penal Code §§630-638) demand-letter wave — regardless of what banner is on screen.

Verdicts:  🚩 tracking host(s) contacted pre-consent  ·  ✅ none contacted.
A ✅ on a site WITH trackers in its HTML means the consent tool is genuinely blocking — the
"verify blocking" ⚠️ from check 18 can be closed.

Usage: python3 pixel-consent-check.py <url> [<url> ...]
Exit 2 if any URL contacted a tracking host pre-consent.
"""
import json, os, re, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_env
import crawl_transport as _tx   # polite layer: bot UA, pacing, robots.txt

CHROME = kit_env.chrome_bin(required=True)
SETTLE_MS = 5000          # after cold networkidle — GTM-loaded tags fire late
SETTLE2_MS = 12000        # after the interaction phase — delayed-JS stacks reload everything

# host-substring -> label. Keep in sync with check 18's _TRK signature table in audit-checks.py.
TRACKING_HOSTS = [
    ("connect.facebook.net", "Meta (Facebook) Pixel loader"),
    ("facebook.com/tr",      "Meta (Facebook) Pixel — event beacon"),
    ("px.ads.linkedin.com",  "LinkedIn Insight — event beacon"),
    ("snap.licdn.com",       "LinkedIn Insight loader"),
    ("analytics.tiktok.com", "TikTok Pixel"),
    ("bat.bing.com",         "Microsoft/Bing UET"),
    ("clarity.ms",           "Microsoft Clarity (session replay)"),
    ("hotjar.com",           "Hotjar (session replay)"),
    ("hotjar.io",            "Hotjar (session replay)"),
    ("fullstory.com",        "FullStory (session replay)"),
    ("google-analytics.com", "Google Analytics (GA4 hit)"),
    ("analytics.google.com", "Google Analytics (GA4 hit)"),
    ("googletagmanager.com/gtag/js", "GA4 gtag loader"),
]

JS = r"""
const puppeteer = %(pp)s;
(async () => {
  const url = process.argv[2];
  const browser = await puppeteer.launch({
    executablePath: %(chrome)s,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  try {
    const page = await browser.newPage();
    await page.setUserAgent(%(ua)s);   // the kit's bot UA (owner safety rule 2026-10-14)
    const reqs = [];
    let phase = 'cold';
    page.on('request', r => reqs.push({url: r.url(), type: r.resourceType(), phase}));
    // PHASE 1 — COLD LOAD: no clicks, no scroll, no banner interaction.
    // networkidle2 never settles on long-polling chat widgets (a client site 2026-08-02: timed out
    // twice on a healthy 879ms page) — fall back to domcontentloaded + fixed settle
    try {
      await page.goto(url, {waitUntil: 'networkidle2', timeout: 45000});
    } catch (e) {
      await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 45000});
      await new Promise(r => setTimeout(r, 12000));
    }
    await new Promise(r => setTimeout(r, %(settle)d));
    // PHASE 2 — SCROLL/MOUSE (still pre-consent): WP Rocket's "delay JavaScript execution" and
    // similar optimizers hold EVERY script — GTM and trackers included — until the first TRUSTED
    // user event (mousemove/wheel/keydown; a programmatic window.scrollBy does NOT count). A
    // cold-load-only capture false-negatives those sites — measured live on a roofing site
    // 2026-08-01: Clarity/Hotjar/GTM in the HTML, ZERO tracker requests cold, then Meta + Clarity
    // + Hotjar + GA4 + Bing + doubleclick all fired on the first wheel event. Moving the mouse or
    // scrolling is not consenting, so everything firing here is still pre-consent.
    phase = 'scroll';
    await page.mouse.move(200, 200);
    await page.mouse.move(400, 350);
    await page.mouse.wheel({deltaY: 700});
    await page.keyboard.press('ArrowDown');
    await new Promise(r => setTimeout(r, %(settle2)d));
    const docState = await page.evaluate(() => ({
      len: (document.body && document.body.innerText || '').length,
      title: document.title,
      challenge: /captcha|challenge|checking your browser|just a moment|security checkpoint/i
                 .test((document.body && document.body.innerText || '').slice(0, 3000))
    }));
    console.log(JSON.stringify({reqs, docState}));
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('PUPPETEER-ERROR: ' + e.message); process.exit(3); });
""" % {"ua": json.dumps(_tx.UA), "pp": kit_env.puppeteer_require_js(), "chrome": json.dumps(CHROME), "settle": SETTLE_MS, "settle2": SETTLE2_MS}


def check(url):
    try:
        _tx.before_request(url)      # robots.txt + pacing for the page itself
    except _tx.RobotsDisallowed:
        print("   SKIPPED — robots.txt disallows this URL; not loaded (not measured)")
        return None
    tmpdir = kit_env.KIT_WORK / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, dir=tmpdir) as f:
        f.write(JS)
        js_path = f.name
    try:
        out = subprocess.run(["node", js_path, url], capture_output=True, text=True, timeout=120)
    finally:
        os.unlink(js_path)
    if out.returncode != 0 or not out.stdout.strip():
        print(f"   ⚠️ load failed: {(out.stderr or 'no output').strip()[:300]}")
        return None
    _payload = json.loads(out.stdout.strip().splitlines()[-1])
    if isinstance(_payload, dict):
        reqs = _payload.get('reqs') or []
        _doc = _payload.get('docState') or {}
    else:
        reqs, _doc = _payload, {}
    doc_status = 200 if (_doc.get('len', 0) > 0 or _doc.get('title')) else 0
    body_len = _doc.get('len', 0)
    challenge_hit = bool(_doc.get('challenge'))
    hits = {}                                     # label -> (phase, [urls])
    for r in reqs:
        u = r["url"]
        # match on scheme-stripped URL so "facebook.com/tr" style host+path signatures work
        bare = re.sub(r"^https?://(www\.)?", "", u)
        for sig, label in TRACKING_HOSTS:
            if sig in bare:
                ph, urls = hits.get(label, (r["phase"], []))
                urls.append(u)
                hits[label] = (min(ph, r["phase"]), urls)   # 'cold' < 'scroll' alphabetically
                break
    n_cold = sum(1 for r in reqs if r["phase"] == "cold")
    print(f"   {len(reqs)} network requests captured ({n_cold} on the cold load, "
          f"{len(reqs) - n_cold} after one scroll — both before any consent)")
    if not hits:
        # a tiny static site's COMPLETE load can be <10 requests (one client site: 6, fully painted) —
        # blocked means the DOCUMENT failed or a challenge served, not merely "few requests"
        if len(reqs) < 10 and (doc_status != 200 or challenge_hit or body_len < 2000):
            print(f"   ⚠️ UNKNOWN — the page load itself appears BLOCKED ({len(reqs)} requests "
                  "captured; a real page makes dozens). A WAF served a challenge to this browser; "
                  "nothing can be concluded about trackers. Re-run from a browser on a residential "
                  "connection — never report this as clean.")
            return {}
        print("   ✅ no known tracking host contacted pre-consent (cold load + scroll)")
        print("      (If check 18 found tracker code in the HTML, the consent tool is genuinely")
        print("      blocking it — the ⚠️ 'verify blocking' can be closed.)")
        return {}
    print(f"   🚩 {len(hits)} TRACKING SERVICE(S) CONTACTED BEFORE ANY CONSENT:")
    for label, (ph, urls) in sorted(hits.items()):
        when = "on first paint" if ph == "cold" else "on first scroll (delayed-JS)"
        print(f"     - {label} — {len(urls)} request(s), {when}")
        for u in urls[:2]:
            print(f"         {u[:160]}")
    print("      These fired before the visitor answered (or was even shown) any consent prompt —")
    print("      the exact CIPA demand-letter fact pattern. A consent banner on this page is not")
    print("      blocking them.")
    return hits


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: pixel-consent-check.py <url> [<url> ...]")
    any_fired = False
    for url in sys.argv[1:]:
        print(f"\n=== pre-consent network audit: {url} ===")
        hits = check(url)
        if hits:
            any_fired = True
    sys.exit(2 if any_fired else 0)


if __name__ == "__main__":
    main()
