#!/usr/bin/env python3
"""render-check.py — TWO-LAYER rendering audit: content-SSR AND visual no-JS presence.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

WHY THIS CHECK EXISTS (standing doctrine, owner 2026-07-16):
Google cannot be assumed to render JavaScript for every page on every crawl. Rendering is a
second, deferred, budgeted pass — it can be delayed, time out, or be skipped, and the initial
HTML-only pass is what discovery and first indexing work from. Other consumers (Bing's HTML
pass, most AI/LLM crawlers, scrapers, users with failed/blocked JS) never render at all.
So a page must be judged BOTH ways:

  LAYER 1 — CONTENT: is the text/links/schema present in the raw server HTML?
     (If not: the site is client-side rendered and EVERY raw-HTML crawl number under-reports.)
  LAYER 2 — VISUAL: is the content VISIBLE without JavaScript executing?
     Page builders (Elementor entrance animations, AOS, WOW, JS-gated sliders/forms, data-src
     lazy images) ship content IN the HTML but CSS-hide it until JS reveals it. Layer 1 passes,
     yet without rendering the visitor/crawler-snapshot sees a mostly blank page. This layer is
     measured DIRECTLY: the page is screenshotted with scripts stripped vs live with JS, and the
     painted-content coverage is compared pixel-wise — it does NOT depend on recognizing any
     particular framework's class names. Marker counts (Elementor/AOS/WOW/data-src) are reported
     afterwards only to EXPLAIN what causes the gating, never to detect it.

VERDICTS per page:
  content:  SSR (raw >=85% of rendered words+links, or 100% vocabulary = carousel-clone surplus)
            PARTIAL (raw 40-85% of words) | CSR (raw <40% AND the page has scripts AND JavaScript adds
            >= 50 words or builds an empty id=root/app/__next root) | FETCH DIFFERS (no scripts, yet the
            browser got a different body: unknown, never CSR)
  visual:   OK (no-JS paint covers >=70% of the JS paint) | GATED (<70% — content requires
            rendering to be visually present)

Exit codes: 0 = clean · 2 = any CSR page (crawl untrustworthy) · 3 = visually GATED page(s).
Screenshots are written for every page (render-<slug>-nojs.png / -js.png) — LOOK at them
before writing the finding. (--blink-settings=scriptEnabled=false crashes some headless
builds; the no-JS render is therefore done by stripping <script> tags and injecting a
<base href> so CSS/images still load — same effect.)

Usage: .venv/bin/python render-check.py <url> [<url> ...]
"""
import json, os, re, subprocess, sys, tempfile, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_env

CHROME = kit_env.chrome_bin(required=True)
# PROXY SUPPORT (2026-08-05). CRAWL_PROXY routed the curl crawl and the checks' urlopen calls, but
# render-check drives headless CHROME, which has its own network stack and ignores that env var —
# so on a host that rate-limits our crawl IP (Sucuri) Chrome fetched the WAF interstitial while the
# proxied crawl read the real site. The render then reported "raw = 0% / CSR" and the pipeline
# shipped three false findings (AUDIT-PROBLEMS §51). Chrome takes --proxy-server; curl takes -x.
_PROXY = os.environ.get("CRAWL_PROXY") or ""
CURL_PROXY_ARGS = ["-x", _PROXY] if _PROXY else []
# ⚠️ CHROME CANNOT USE AN AUTHENTICATED PROXY NON-INTERACTIVELY. --proxy-server takes host:port but
# IGNORES embedded user:pass credentials — Chrome expects to PROMPT for them, which headless cannot
# do. Verified 2026-08-05: with --proxy-server="http://user:pass@gateway", Chrome could not reach
# ipinfo.io at all and rendered the WAF page for the target. So we pass the proxy ONLY when it has
# no credentials; when it does, Chrome goes direct and the RENDER VERDICT IS DECLARED UNTRUSTWORTHY
# rather than silently poisoned (that silent failure produced 3 false client findings — §51).
_PROXY_HAS_AUTH = "@" in _PROXY
CHROME_PROXY_ARGS = [f"--proxy-server={_PROXY}"] if (_PROXY and not _PROXY_HAS_AUTH) else []
CHROME_PROXY_UNAVAILABLE = bool(_PROXY and _PROXY_HAS_AUTH)
import crawl_transport as _tx   # polite layer (owner safety rule 2026-10-14): bot UA, pacing, robots.txt
UA = _tx.UA
# Chrome sends the same bot UA (its subresource requests cannot be paced; the page URL is checked)
CHROME_PROXY_ARGS = CHROME_PROXY_ARGS + [f"--user-agent={UA}"]
SHOT_W, SHOT_H = 1300, 3000

GATES = [("elementor-invisible", "Elementor entrance animation (visibility:hidden until JS)"),
         ("data-aos=", "AOS scroll animation"),
         ('class="wow', "WOW.js animation"),
         ("data-sal=", "Sal.js animation")]

def visible_stats(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "template"]): t.decompose()
    text = soup.get_text(" ", strip=True)
    return {"words": len(re.findall(r"\w+", text)),
            "links": len(soup.find_all("a", href=True)),
            "headings": sum(len(soup.find_all(h)) for h in ["h1","h2","h3","h4","h5","h6"]),
            "title": (soup.title.get_text(strip=True) if soup.title else ""),
            "jsonld": len(re.findall(r"application/ld\+json", html)),
            "vocab": set(re.findall(r"\w{4,}", text.lower()))}

def fetch_raw(url):
    """urllib first; on 4xx/exception fall back to curl — some hosts (Shopify/Cloudflare)
    TLS-fingerprint Python clients and 429 them while serving curl/browsers normally."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    except Exception:
        _tx.before_request(url)
        r = subprocess.run(["curl", "-sL", "--compressed", "-m", "25", *CURL_PROXY_ARGS, "-A", UA, url], capture_output=True, timeout=35)
        if not r.stdout: raise
        return r.stdout.decode("utf-8", "replace")

def fetch_rendered(url):
    _tx.before_request(url)
    out = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                          *CHROME_PROXY_ARGS,
                          "--virtual-time-budget=10000", "--dump-dom", url],
                         capture_output=True, text=True, timeout=90)
    return out.stdout

def screenshot(target, path, _tries=2):
    """Screenshot with retry. A single transient Chrome hang used to abort the ENTIRE render stage
    (all 3 pages) and the orchestrator gate then reported the audit INCOMPLETE — while a manual
    rerun minutes later passed cleanly (a chiropractic site 2026-07-31). Never let one hang cascade."""
    for _a in range(_tries):
        try:
            subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                            *CHROME_PROXY_ARGS,
                            f"--window-size={SHOT_W},{SHOT_H}", "--virtual-time-budget=8000",
                            f"--screenshot={path}", target],
                           capture_output=True, timeout=90)
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return True
        except subprocess.TimeoutExpired:
            print(f"    (screenshot timed out{' — retrying' if _a + 1 < _tries else '; giving up on this shot'})")
        except Exception as _e:
            print(f"    (screenshot error: {str(_e)[:70]})")
    return False

def shoot_pair(url, raw_html, slug):
    """No-JS vs JS screenshots. No-JS = scripts stripped + <base href> injected."""
    stripped = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.I)
    stripped = re.sub(r"(<head[^>]*>)", rf'\1<base href="{url}">', stripped, count=1, flags=re.I)
    (kit_env.KIT_WORK / "tmp").mkdir(parents=True, exist_ok=True)
    tf = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, dir=kit_env.KIT_WORK / "tmp"); tf.write(stripped); tf.close()
    nojs, js = os.path.abspath(f"render-{slug}-nojs.png"), os.path.abspath(f"render-{slug}-js.png")
    screenshot("file://" + tf.name, nojs); screenshot(url, js)
    os.unlink(tf.name)
    return nojs, js

def paint_coverage(nojs_png, js_png):
    """Fraction of the JS render's painted rows that also paint without JS.
    Direct measurement — framework-agnostic; a row 'paints' if it has pixel variation."""
    try:
        from PIL import Image
    except ImportError:
        return None
    def content_rows(path):
        im = Image.open(path).convert("L").resize((160, 600))
        px = im.load()
        return {y for y in range(600)
                if max(px[x, y] for x in range(160)) - min(px[x, y] for x in range(160)) > 16}
    try:
        a, b = content_rows(nojs_png), content_rows(js_png)
    except Exception:
        return None
    return (len(a & b) / len(b)) if b else 1.0

def main(urls):
    urls = [u for u in urls if not u.startswith("--")]
    worst = 0
    print("=== render check — layer 1: content in raw HTML · layer 2: visible without JS ===")
    if CHROME_PROXY_UNAVAILABLE:
        print("  ⚠️ RENDER TRANSPORT MISMATCH — CRAWL_PROXY is set WITH credentials, but Chrome "
              "cannot authenticate to a proxy headlessly. The RAW side used the proxy; the RENDERED "
              "side came from this host's own IP. On a host that blocks/throttles this IP the "
              "rendered capture is a WAF page, so treat the CONTENT/VISUAL verdicts below as "
              "UNTRUSTWORTHY (AUDIT-PROBLEMS §52).")
    for url in urls:
        if not _tx.allowed(url):
            print(f"\n{url}\n  SKIPPED — robots.txt disallows this URL; not loaded (not measured)")
            continue
        try:
            raw_html = fetch_raw(url)
            raw, dom = visible_stats(raw_html), visible_stats(fetch_rendered(url))
        except Exception as e:
            # per-PAGE failure must never abort the remaining pages or the stage
            print(f"\n{url}\n  ERROR (this page only, continuing): {str(e)[:120]}"); continue
        # RENDER-BLOCKED GUARD (2026-07-31): if the RENDERED side came back near-empty while raw has
        # real content, headless Chrome was BLOCKED (WAF/rate-limit), not "server-rendered". Live
        # failure on a client site: printed "SSR ... clean" on a blank `local_rate_limited` page with
        # raw = 225500% of rendered and 0% vocabulary overlap. Impossible ratios = blocked, not clean.
        if dom["words"] < 25 and raw["words"] > 100:
            print(f"\n{url}")
            print(f"  ⚠️ RENDER BLOCKED — headless Chrome returned {dom['words']} words vs {raw['words']}")
            print(f"     in raw HTML (likely a WAF/rate-limit interstitial, not the real page).")
            print(f"     CANNOT judge rendering for this URL. Re-run from a Chrome on a residential connection, or treat")
            print(f"     as UNKNOWN — do NOT report it as clean/SSR.")
            worst = max(worst, 2)
            continue
        wr = raw["words"] / dom["words"] if dom["words"] else 1.0
        lr = raw["links"] / dom["links"] if dom["links"] else 1.0
        vc = (len(raw["vocab"] & dom["vocab"]) / len(dom["vocab"])) if dom["vocab"] else 1.0
        # CSR NEEDS EVIDENCE THAT JAVASCRIPT ADDS CONTENT (2026-10-14: a static one-page site with
        # ~30 words and no JavaScript shipped "empty shell until JavaScript runs"). A page with no
        # <script> cannot be client-rendered; if its two fetches still differ, the SERVER sent the
        # browser a different body: that is UNKNOWN, not CSR. With scripts, CSR also needs the
        # rendered page to add >= 50 words, or a script-built root (id=root/app/__next/__nuxt)
        # with an almost empty raw body.
        n_scripts = len(re.findall(r"<script\b", raw_html, re.I))
        script_root = bool(re.search(r"""<div[^>]+id=["'](root|app|__next|__nuxt)["'][^>]*>\s*</div>""", raw_html, re.I))
        added = dom["words"] - raw["words"]
        dup = ""
        if n_scripts == 0:
            content = "SSR" if wr >= .85 else "FETCH DIFFERS"
            if content != "SSR":
                dup = " (no scripts on the page: the browser was sent a different body; UNKNOWN, not CSR)"
        elif wr < .85 and vc >= .97:
            content = "SSR"; dup = " (rendered surplus = carousel/slider CLONES, not new text)"
        else:
            js_adds = added >= 50 or (script_root and raw["words"] < 50)
            content = "SSR" if (wr >= .85 and lr >= .80) else ("CSR" if (wr < .40 and js_adds) else "PARTIAL")
        slug = re.sub(r"[^a-z0-9]+", "-", url.split("://",1)[-1].lower()).strip("-")[:60]
        nojs, js = shoot_pair(url, raw_html, slug)
        cov = paint_coverage(nojs, js)
        visual = "?" if cov is None else ("OK" if cov >= .70 else "GATED")
        if n_scripts == 0 and visual == "GATED":
            visual = "OK"          # nothing to run: a paint difference is a fetch difference, not gating
        print(f"\n{url}")
        print(f"  CONTENT: {content}   (raw = {wr:.0%} of rendered words, {lr:.0%} links, {vc:.0%} vocabulary){dup}")
        print(f"  VISUAL:  {visual}" + (f"    (no-JS paint covers {cov:.0%} of the JS paint)" if cov is not None
              else "    (Pillow missing — could not measure; LOOK at the screenshots)"))
        print(f"  words raw/rendered {raw['words']}/{dom['words']} · links {raw['links']}/{dom['links']}"
              f" · headings {raw['headings']}/{dom['headings']} · jsonld {raw['jsonld']}/{dom['jsonld']}"
              f" · scripts {n_scripts}" + (" · script root" if script_root else ""))
        if raw["title"] != dom["title"]:
            print(f"  TITLE DIFFERS  raw {raw['title'][:60]!r}  rendered {dom['title'][:60]!r}")
        hits = [(m, l, raw_html.count(m)) for m, l in GATES if raw_html.count(m)]
        lazy = len(re.findall(r"<img[^>]+data-src", raw_html))
        if hits or lazy:
            print("  cause of gating (explanatory only — detection is the pixel measurement above):")
            for m, l, n in hits: print(f"    {n:>3} × {l}  [{m}]")
            if lazy: print(f"    {lazy:>3} × lazy image (data-src — never swaps in without JS)")
        print(f"  screenshots: {nojs} · {js}  <- LOOK at these")
        if content == "CSR": worst = max(worst, 2)
        elif visual == "GATED": worst = max(worst, 3)
    print("\n" + {0: "clean — content server-rendered AND visible without JS",
                  2: "CSR page(s) — the raw-HTML crawl UNDER-REPORTS this site; audit the rendered DOM",
                  3: "VISUALLY GATED page(s) — content requires rendering to be visually present; "
                     "report it as a finding (Google's render pass is not guaranteed)"}[worst])
    sys.exit(worst)

if __name__ == "__main__":
    if len(sys.argv) < 2: print(__doc__); sys.exit(1)
    # strip #fragments: an anchor URL makes headless Chrome SCROLL to the anchor, so the JS
    # screenshot's top is blank and the visual diff measures SCROLL OFFSET as "hidden content"
    # (a client site 2026-08-02: 58% "gated" on a fully-SSR one-pager given #shop/#visit URLs)
    main([u.split('#')[0] for u in sys.argv[1:]])
