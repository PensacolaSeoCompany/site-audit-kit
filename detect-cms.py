#!/usr/bin/env python3
"""detect-cms.py — CMS/platform fingerprint for the audit LOGIC TREE (added 2026-08-05).
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

    detect-cms.py <crawl.json> [homepage-url] > cms.log      (also writes <slug>-cms.json next to the crawl JSON)

WHY THIS EXISTS
---------------
Until now CMS knowledge lived as scattered one-off special cases inside audit-checks.py (a Shopify
branch here, a WordPress robots.txt aside there). That means platform-specific DEFECT CLASSES — the
ones that only exist because of the CMS — were never systematically checked. The owner's directive
(2026-08-05): detect the CMS, then fire the matching deep-dive module.

    detect-cms.py  ->  cms.json {platform, confidence, theme, plugins, seo_plugin, ...}
                        |
                        +-- wordpress -> cms/wordpress.py   (archives, SEO-plugin config, exposure, bloat)
                        +-- shopify   -> cms/shopify.py     (stub)
                        +-- other     -> skip, note the platform in the report

DETECTION IS EVIDENCE-BASED, NOT GUESSED: every signal that fires is recorded with WHAT matched, so
a wrong verdict can be traced. Reads the CRAWL JSON first (free, already fetched through whatever
transport beat the WAF) and only falls back to a live fetch when the crawl lacks the signal.
Honors CRAWL_PROXY + CRAWL_UA so it works on bot-walled hosts (AUDIT-PROBLEMS §43/§45).
"""
import json, os, re, sys
import urllib.request as _ur
from urllib.parse import urlparse
from collections import Counter

# polite layer (owner safety rule 2026-10-14): bot UA, pacing, robots.txt for every urllib request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl_transport as _tx
UA = _tx.UA
HDRS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
PROXY = os.environ.get("CRAWL_PROXY")
if PROXY:
    try:
        _ur.install_opener(_ur.build_opener(_ur.ProxyHandler({"http": PROXY, "https": PROXY})))
    except Exception:
        pass


def fetch(url, timeout=20):
    """Fetch a URL; returns (status, body, headers-dict). Never raises."""
    try:
        r = _ur.urlopen(_ur.Request(url, headers=HDRS), timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except Exception as e:
        code = getattr(e, "code", None)
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")     # HTTPError still carries a body
        except Exception:
            pass
        return code, body, dict(getattr(e, "headers", {}) or {})


# ---- platform signatures ------------------------------------------------------------------------
# Each: (platform, weight, regex). Weight reflects how EXCLUSIVE the signal is — /wp-content/ is
# near-conclusive for WordPress; the word "wordpress" in prose is not.
SIGS = [
    ("wordpress", 5,  r"/wp-content/"),
    ("wordpress", 5,  r"/wp-includes/"),
    ("wordpress", 4,  r"<meta name=[\"']generator[\"'] content=[\"']WordPress"),
    ("wordpress", 3,  r"/wp-json/"),
    ("wordpress", 2,  r"wp-emoji|wpemoji|wp-block-library"),
    ("shopify",   5,  r"cdn\.shopify\.com|/cdn/shop/"),
    ("shopify",   4,  r"Shopify\.theme|shopify-section|window\.Shopify"),
    ("shopify",   3,  r"myshopify\.com"),
    ("squarespace", 5, r"squarespace\.com|static1\.squarespace|Static\.SQUARESPACE_CONTEXT"),
    ("wix",       5,  r"wix\.com|wixstatic\.com|X-Wix-"),
    ("webflow",   5,  r"webflow\.com|data-wf-page|data-wf-site"),
    ("duda",      4,  r"dudaone|/_dm/s/|irp-cdn\.multiscreensite"),
    ("drupal",    5,  r"/sites/default/files/|Drupal\.settings|drupal-settings-json"),
    ("joomla",    4,  r"/media/jui/|Joomla!|/templates/[a-z0-9_-]+/css/"),
    ("ghost",     4,  r"content=[\"']Ghost [0-9]|/ghost/api/"),
    ("hubspot",   4,  r"hs-scripts\.com|hubspot\.net/hub/"),
    ("bigcommerce", 4, r"bigcommerce\.com|/stencil/"),
    ("nextjs",    3,  r"/_next/static/|__NEXT_DATA__"),
    ("nuxt",      3,  r"/_nuxt/|__NUXT__"),
    ("react-spa", 2,  r'<div id=["\']root["\']></div>'),
]

# CMSes that have a dedicated deep-dive module. Everything else is detected + reported only.
HAS_MODULE = {"wordpress"}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: detect-cms.py <crawl.json> [homepage-url]")
    crawl_path = sys.argv[1]
    D = json.load(open(crawl_path))
    pages = D.get("pages") or []
    site = D.get("site") or ""
    home = sys.argv[2] if len(sys.argv) > 2 else (f"https://{site}/" if site else "")

    # ---- gather source text: prefer the crawl's own captured signals, fall back to a live fetch ----
    # The crawl JSON stores DERIVED fields (no raw html), so URL-shaped signals come from the link
    # graph + asset paths it recorded; the definitive markup signals need one homepage fetch.
    # FIRST-PARTY ONLY (a client site's false positive 2026-08-05): an ASP.NET site was scored "wordpress"
    # because it LINKS OUT to news.microsoft.com/wp-content/... and "drupal" because it links to
    # cisa.gov/sites/default/files/... . Another site's CMS says nothing about the audited platform.
    # Score only URLs on the audited registrable domain; external links never enter the corpus.
    reg = (site or "").lower().replace("www.", "")

    def first_party(u):
        if not u:
            return False
        h = (urlparse(u).hostname or "").lower()
        if not h:                       # relative URL = same site
            return True
        h = h[4:] if h.startswith("www.") else h
        return bool(reg) and (h == reg or h.endswith("." + reg))

    corpus = []
    for p in pages[:400]:
        if not first_party(p.get("url") or ""):
            continue
        corpus.append(p.get("url") or "")
        for l in (p.get("internal_links") or [])[:60]:
            href = (l.get("href") if isinstance(l, dict) else l) or ""
            if first_party(href):
                corpus.append(href)
        corpus.append(" ".join(p.get("jsonld_types") or []))
    crawl_text = "\n".join(corpus)

    status, html, headers = (None, "", {})
    if home:
        status, html, headers = fetch(home)
    # did we actually get the SITE's markup, or a challenge page / error? A challenge screen is a
    # few hundred bytes and carries WAF markers — treat it as "no markup", not as evidence.
    html_ok = bool(status == 200 and len(html) > 2000 and not re.search(
        r"Robot Challenge Screen|sgcaptcha|Just a moment|Checking your browser|Attention Required",
        html, re.I))
    if not html_ok:
        html = ""                      # never let a challenge page contribute platform signals
    hdr_text = " ".join(f"{k}: {v}" for k, v in (headers or {}).items())
    blob = crawl_text + "\n" + html + "\n" + hdr_text

    # ---- score platforms ----
    scores, evidence = Counter(), {}
    for plat, weight, pat in SIGS:
        m = re.search(pat, blob, re.I)
        if m:
            scores[plat] += weight
            evidence.setdefault(plat, []).append(m.group(0)[:60])

    # CONFIDENCE FLOOR + TIE GUARD: one weak signal must not declare a platform, and a near-tie means
    # we genuinely don't know (report both rather than coin-flip a deep-dive onto the wrong module).
    platform, confidence = "unknown", 0
    if scores:
        ranked = scores.most_common()
        top_plat, top_score = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0
        if top_score >= 4 and top_score > runner:
            platform, confidence = top_plat, top_score
        elif top_score >= 4 and top_score == runner:
            platform, confidence = "ambiguous", top_score      # two platforms tied — do NOT dispatch
        else:
            platform, confidence = "unknown", top_score        # only weak/incidental signals

    # ---- WordPress detail: theme + plugins straight off the asset paths ----
    theme = ""
    m = re.search(r"/wp-content/themes/([a-z0-9_-]+)", blob, re.I)
    if m:
        theme = m.group(1)
    plugins = sorted(set(re.findall(r"/wp-content/plugins/([a-z0-9_-]+)", blob, re.I)))

    # SEO plugin: the single most useful fact for a WP audit — it OWNS archive indexability.
    seo_plugin = ""
    if re.search(r"yoast|wpseo", blob, re.I):
        seo_plugin = "yoast"
    elif re.search(r"rank ?math|rankmath", blob, re.I):
        seo_plugin = "rankmath"
    elif re.search(r"aioseo|all in one seo", blob, re.I):
        seo_plugin = "aioseo"
    elif re.search(r"the seo framework|autodescription", blob, re.I):
        seo_plugin = "seoframework"

    out = {
        "platform": platform,
        "confidence": confidence,
        "has_module": platform in HAS_MODULE,
        "evidence": {k: v[:4] for k, v in evidence.items()},
        "all_scores": dict(scores),
        "theme": theme,
        "plugins": plugins,
        "seo_plugin": seo_plugin,
        "server": (headers or {}).get("server", ""),
        "homepage_status": status,
        "html_available": html_ok,      # False => theme/plugins/seo_plugin are UNKNOWN, not absent
        "site": site,
    }

    # ---- write the machine-readable verdict next to the crawl json ----
    slug = re.sub(r"-full\.json$", "", os.path.basename(crawl_path))
    outp = os.path.join(os.path.dirname(crawl_path) or ".", f"{slug}-cms.json")
    with open(outp, "w") as f:
        json.dump(out, f, indent=2)

    # ---- human/gate-readable log ----
    print("=== CMS DETECTION ===")
    print(f"PLATFORM: {platform}  (confidence {confidence})")
    if evidence.get(platform):
        print(f"   evidence: {', '.join(evidence[platform][:3])}")
    if len(scores) > 1:
        others = ", ".join(f"{k}={v}" for k, v in scores.most_common()[1:4])
        print(f"   other signals: {others}")
    if platform == "wordpress":
        # HTML-DERIVED detail (theme/plugins/SEO plugin) needs the homepage MARKUP. The crawl JSON
        # stores only derived fields, so if the live fetch failed (bot-walled host, §43/§45) these
        # come back empty — that is UNKNOWN, never "the site has no SEO plugin". Saying "no SEO
        # plugin" on a pest-control site (which runs Yoast) would invert the whole archive diagnosis.
        if not html_ok:
            print("   ⚠️ homepage markup UNAVAILABLE from this host "
                  f"(status {status}) — theme/plugins/SEO-plugin NOT determined this run.")
            print("      Re-run with CRAWL_PROXY + CRAWL_UA set (see AUDIT-PROBLEMS §43/§45).")
        else:
            print(f"   theme: {theme or '(not detected)'}")
            print(f"   SEO plugin: {seo_plugin or '(none detected — archives are UNMANAGED)'}")
            print(f"   plugins seen ({len(plugins)}): {', '.join(plugins[:14])}")
    if out["server"]:
        print(f"   server: {out['server']}")
    print(f"DEEP-DIVE MODULE: {'yes -> cms/' + platform + '.py' if out['has_module'] else 'none for this platform'}")
    print(f"wrote {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
