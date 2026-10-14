#!/usr/bin/env python3
"""audit-checks.py — standing site-audit checks that are EASY TO FORGET and expensive to miss.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

Run on a full-extract crawl JSON. These are the checks that a human auditor skips under time
pressure but that change the entire verdict of an audit. Encoded so they run every time.

Checks:
  1. CANONICAL TARGET DOMAIN (critical) — every page's rel=canonical host must equal the site host.
     Cloned/template sites (Webflow especially) keep the ORIGINAL project's global canonical, so
     every page canonicals OFF-DOMAIN and Google suppresses indexing of the real site. A crawl that
     only reports "redirects clean" (http/www->https 301s) will NOT catch this — the rel=canonical
     TAG is a separate signal. (You had to be reminded of this once. Never again.)
  2. SELF-CANONICAL mismatch (within-domain) — canonical points to a different URL on the same host.
  3. STRUCTURED DATA presence — pages with empty JSON-LD (no LocalBusiness/Service/etc).
  4. THIN SITE — unique canonical page count; a 1-2 page "site" is a headline finding, not a footnote.
  5. PLACEHOLDER / DEMO CONTENT — CMS empty-state text rendered to visitors. Shopify's featured-
     collection sections fall back to "Example product title" ($19.99) tiles when their assigned
     collection was deleted; page builders leave "Lorem ipsum" / "Talk about your brand". Owner
     spotted this on exeterpaintstores 2026-07-17 (12 fake tiles under "Top-Selling Products" on
     the homepage) — the crawler stores card titles in headings, so it is machine-checkable.

Usage: python3 audit-checks.py $KIT_WORK/crawl/<slug>-full.json
Exit 2 if any CRITICAL flag fires.
"""
import json, sys, urllib.parse, re

# Module-level aliases: several checks fetch live URLs and are ordered independently. Importing
# these inside one check made EARLIER checks fail with UnboundLocalError once they started using
# them (check 7 broke this way 2026-07-31). Bind once, at module scope.
import urllib.request as _ur2   # `import urllib` alone does NOT bind .request
import os as _os3
# POLITE LAYER (owner safety rule 2026-10-14): importing crawl_transport wraps urllib's
# build_opener, so every opener in this file (the gzip opener below and every private HEAD /
# no-redirect opener) sends the GrokBot-SiteAudit user agent, is paced per host and skips
# robots.txt-disallowed URLs. It must be imported BEFORE the openers below are built.
_sys_path_kit = __import__('os').path.dirname(__import__('os').path.abspath(__file__))
sys.path.insert(0, _sys_path_kit)
import crawl_transport as _tx
import time as _time13          # module-scope (§14): a LOCAL `import time as X` inside any function
                                # makes that name function-local EVERYWHERE, so bind retry helpers here

# ── CANONICAL LocalBusiness SUBTYPE SET (a cleaning-company site, 2026-08-07) ──────────────────
# ONE list, three consumers. Previously check 3 (_LB_SUB), the business-model gate (_LB_GATE) and
# check 16 (_BIZ16) each carried their OWN hand-maintained copy, and they had DIVERGED: only the
# gate knew 'cleaningservice'. Result on a cleaning-company site — a homepage carrying a full
# CleaningService node (address + geo + telephone + openingHoursSpecification + 18 areaServed)
# fired check 3's 🚩 "has Organization schema but NO LocalBusiness node ... carries no address,
# hours, geo or service area" — a claim the site's own markup, and check 3's OWN printed type
# list ('CleaningService'), disprove. That is the §49 Rule-1 self-contradiction class, caused by
# the §66 inverted-denylist trap: an allowlist that forgets a member invents a defect.
# RULE (§13): this set is defined ONCE. Never re-inline a copy — add the subtype here.
# Source: schema.org LocalBusiness subtype tree (all descendants are LocalBusiness).
_LOCAL_SUBTYPES = {
    'localbusiness',
    # professional / trades
    'professionalservice','homeandconstructionbusiness','generalcontractor','plumber','electrician',
    'roofingcontractor','hvacbusiness','locksmith','movingcompany','housepainter','cleaningservice',
    'pestcontrolservice','landscapingbusiness','securityservice','selfstorage','employmentagency',
    # legal / financial / medical
    'attorney','legalservice','notary','accountingservice','financialservice','insuranceagency',
    'realestateagent','travelagency','medicalorganization','medicalclinic','medicalbusiness',
    'dentist','physician','hospital','pharmacy','optician','veterinarycare','healthandbeautybusiness',
    # automotive
    'automotivebusiness','autorepair','autobodyshop','autodealer','autowash','autopartsstore',
    'autorental','gasstation','motorcyclerepair','motorcycledealer','towingservice','taxiservice',
    # food / hospitality / retail
    'foodestablishment','restaurant','cafeorcoffeeshop','bakery','barorpub','brewery','winery',
    'icecreamshop','fastfoodrestaurant','distillery','store','shoppingcenter','departmentstore',
    'grocerystore','hardwarestore','furniturestore','clothingstore','florist','petstore',
    'lodgingbusiness','hotel','motel','bedandbreakfast','resort','campground',
    # personal care / fitness / leisure
    'dayspa','beautysalon','hairsalon','nailsalon','tattooparlor','healthclub','sportsactivitylocation',
    'golfcourse','gym','entertainmentbusiness','childcare','daycare','drycleaningorlaundry',
    'emergencyservice','governmentoffice','library','recyclingcenter','internetcafe',
    'radiostation','televisionstation','touristinformationcenter',
}
# honor CRAWL_UA (a SiteGround-hosted client site, 2026-08-04): SiteGround's sgcaptcha is UA-SENSITIVE even through a residential
# proxy — a Chrome UA gets 403'd while the Safari UA the crawler used passes. The checks' live fetches
# must use the SAME UA the crawl used, or check 13 (sitemap/robots) sees a 403 and falsely reports
# "no sitemap". Default is the kit's bot UA (crawl_transport.UA).
_UA = {'User-Agent': _tx.UA,
       'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9'}
# RESIDENTIAL-PROXY passthrough (same site, 2026-08-04): the crawl goes through CRAWL_PROXY to beat an
# IP-reputation WAF (SiteGround sgcaptcha), but the CHECKS also do live fetches (sitemap/robots in
# check 13, homepage locality, host-dup) — from the crawl host's own IP they hit the same wall and see the
# captcha ("no sitemap", empty homepage). Install a GLOBAL proxy opener so every urlopen here routes
# through the same residential proxy. When CRAWL_PROXY is unset this is a no-op (direct fetch).
# 🔴 GZIP DECODING — DEFENSIVE, and read this before trusting the story behind it.
#
# urlopen does NOT decompress. If a response body ever arrives gzipped, decoding it as UTF-8
# yields mojibake, every regex misses, and checks report "no schema" / "no title" about a page
# that has both. This handler decompresses on the header OR the gzip magic bytes, so all 26 live
# fetches in this file are covered at once (Rule 13: fix the transport, not the call site).
#
# ⚠️ CORRECTION 2026-08-07. The §68 note claimed a client origin (a restoration-company site) "sends content-encoding:
# gzip UNCONDITIONALLY, even when explicitly asked for identity". That is FALSE — re-verified
# directly: Accept-Encoding: identity -> no content-encoding; no Accept-Encoding -> no
# content-encoding, body starts b'<!D'; only Accept-Encoding: gzip -> gzipped. Same result
# THROUGH the residential proxy. `_UA` below sends no Accept-Encoding, so the ORIGINAL §54
# reasoning ("urlopen receives plain bytes") was correct and §68 "corrected" it wrongly.
# The real cause of that run's "no business-entity schema" line was NOT gzip corruption.
# KEEP this handler — it is cheap, correct, and real CDNs do misbehave this way — but do not
# cite §68 as evidence that a specific origin gzips unconditionally. Verify per origin.
class _GzipDecode(_ur2.BaseHandler):
    def http_response(self, req, resp):
        try:
            _enc = (resp.headers.get('Content-Encoding') or '').lower()
            _raw = resp.read()
            if _enc not in ('gzip', 'x-gzip', 'deflate'):
                # Header may LIE or be absent — trust the gzip magic bytes as well.
                if _raw[:3] != b'\x1f\x8b\x08':
                    return _rewrap(resp, _raw)     # genuinely uncompressed
                _enc = 'gzip'
            import gzip as _gz0, zlib as _zl0
            try:
                if _enc == 'deflate':
                    try:
                        _out = _zl0.decompress(_raw)
                    except Exception:
                        _out = _zl0.decompress(_raw, -_zl0.MAX_WBITS)
                else:
                    _out = _gz0.decompress(_raw)
            except Exception:
                _out = _raw          # not really compressed; pass through
            return _rewrap(resp, _out)
        except Exception:
            return resp
    https_response = http_response


def _rewrap(resp, body):
    """Return a response-like object whose .read() yields `body`, preserving status/headers."""
    import io as _io1
    _code = getattr(resp, 'status', None) or resp.getcode()
    _fp = _io1.BytesIO(body)
    # urllib's redirect/error handlers read .msg off the fp, so mirror the reason phrase onto it.
    try:
        _fp.msg = getattr(resp, 'reason', '') or getattr(getattr(resp, 'fp', None), 'msg', '') or ''
    except Exception:
        _fp.msg = ''
    _new = _ur2.addinfourl(_fp, resp.headers, resp.url, _code)
    _new.msg = _fp.msg
    try:
        _new.status = _code
    except Exception:
        pass
    return _new


_CRAWL_PROXY = _os3.environ.get("CRAWL_PROXY")

# SITEMAP CHILD URLS WITH ESCAPED QUERY STRINGS (a Shopify store, 2026-09-22): Shopify's index
# lists children as sitemap_products_1.xml?from=…&amp;to=… — XML-escaped, and with a query after
# .xml. `loc.endswith('.xml')` was False so the children were treated as PAGES (5 "orphans"), and the
# un-unescaped URL fetched with a literal &amp; returned 0 <loc>s (sitemap count 26 instead of 2,285).
import html as _html_sm
from urllib.parse import urlparse as _up_sm
def _sm_unescape(u): return _html_sm.unescape((u or "").strip())
def _sm_is_index(u): return _up_sm(_sm_unescape(u)).path.lower().endswith(".xml")
try:
    _handlers = [_GzipDecode()]
    if _CRAWL_PROXY:
        _handlers.append(_ur2.ProxyHandler({'http': _CRAWL_PROXY, 'https': _CRAWL_PROXY}))
    _ur2.install_opener(_ur2.build_opener(*_handlers))
except Exception:
    pass
# Every PRIVATE opener below must carry this too — build_opener() ignores the installed one
# (a garage-door site, 2026-09-22: check 12 read 202/202 from the crawl host while the crawl was proxied).
_PXH = [_ur2.ProxyHandler({'http': _CRAWL_PROXY, 'https': _CRAWL_PROXY})] if _CRAWL_PROXY else []


def _strip_www(h):
    """Drop ONE leading "www." (2026-10-14 engineering review: lstrip('w.') strips the characters
    w and ., so wexample.com matched example.com, and .replace('www.', '') also hit mid-host)."""
    h = (h or '').lower()
    return h[4:] if h.startswith('www.') else h

def host(u):
    if not u: return ''
    if '://' not in u: u = 'http://' + u   # urlparse needs a scheme to fill netloc for bare domains
    try: return _strip_www(urllib.parse.urlparse(u).netloc)
    except: return ''

def norm(u):
    p = urllib.parse.urlparse(u); return p._replace(query='', fragment='').geturl().rstrip('/')


# ONE noindex definition (DE-04, 2026-10-14). BYTE-IDENTICAL copy of crawl_transport.NOINDEX_RE
# (kept as a copy from when this file could not import crawl_transport over its gzip
# opener). tests/test_extract_and_findings.py fails if the two patterns drift. `none` = noindex,
# case-insensitive, user-agent prefixes allowed; `max-image-preview: none` is not noindex.
NOINDEX_RE = re.compile(r"(?:^|[\s,:])(?:noindex|none)(?=[\s,;]|$)", re.I)
_MAX_IMG_RE = re.compile(r"max-image-preview\s*:\s*[a-z]+", re.I)

def _robots_says_noindex(v):
    return bool(NOINDEX_RE.search(_MAX_IMG_RE.sub(" ", v or "")))

def _is_noindex(p):
    """Row is noindexed by its meta tag OR any X-Robots-Tag header. New crawls store the verdict
    (`noindex`, computed once in full-extract); older crawls are judged from meta_robots."""
    if isinstance(p.get('noindex'), bool):
        return p['noindex'] or _robots_says_noindex(p.get('meta_robots'))
    return _robots_says_noindex(p.get('meta_robots'))

def _is_noindex_tag(p):
    """Noindex said by the page's OWN robots meta tag (what Ctrl+F 'noindex' in the source finds).
    A header-only noindex is reported separately by checks-to-findings (hidden server setting)."""
    if 'meta_robots_tag' in p:
        return _robots_says_noindex(p.get('meta_robots_tag'))
    return _robots_says_noindex(p.get('meta_robots'))


# LOCAL_LEAK host test (JP-05, two client sites 2026-10-14 + 2026-10-10). The old pattern
# `[^/]*(staging|\.local|\.test)` matched INSIDE real hosts: www.tests.example.com, www.localprofile.example.com.
# Parse the host and flag only loopback/unspecified addresses, the reserved test TLDs (RFC 2606/6761:
# .test .localhost .invalid, plus mDNS .local), and staging/dev subdomains of the AUDITED domain.
_LEAK_LABELS = re.compile(r'^(staging|stage|dev|development|uat|qa|preview|test)(-[a-z0-9]+)?$|^staging\d*$|^dev\d+$', re.I)

def _local_leak(href, site_host):
    try:
        u = urllib.parse.urlsplit((href or '').strip())
    except Exception:
        return False
    if u.scheme.lower() not in ('http', 'https'):
        return False
    h = (u.hostname or '').lower().rstrip('.')
    if not h:
        return False
    if h in ('localhost', '0.0.0.0', '::1') or re.match(r'^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$', h):
        return True
    if h.endswith(('.local', '.test', '.localhost', '.invalid')):
        return True
    reg = (site_host or '').lower().split(':')[0]
    reg = reg[4:] if reg.startswith('www.') else reg
    if reg and h.endswith('.' + reg):
        sub = h[: -(len(reg) + 1)]
        return any(_LEAK_LABELS.match(lab) for lab in sub.split('.'))
    return False

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: audit-checks.py <crawl-dir>/<slug>-full.json")
    D = json.load(open(sys.argv[1]))
    P = D.get('pages', [])
    # ENCODING-ONLY "REDIRECTS" IN OLDER CRAWLS (DE-03, 2026-10-14). Before the fix full-extract set
    # redirected = str(r.url) != url, and yarl had re-quoted r.url, so 74 of 451 redirected rows in
    # the 30 newest crawls (73 of them 200s) differed only in percent-encoding and fell out of every
    # content check. A row with no `redirect_hops` key comes from such a crawl: when its URL and
    # final URL decode to the same string there was no hop. New crawls store the hop list.
    _enc_only = 0
    for _p0 in P:
        if _p0.get('redirected') and 'redirect_hops' not in _p0:
            _a0 = (_p0.get('url') or '').split('#')[0]
            _b0 = (_p0.get('final_url') or '').split('#')[0]
            if _a0 and _b0 and urllib.parse.unquote(_a0) == urllib.parse.unquote(_b0):
                _p0['redirected'] = False
                _enc_only += 1
    if _enc_only:
        print(f"(note: {_enc_only} row(s) marked redirected differ from their URL only in percent-encoding — "
              f"not redirects; judged as pages)")
    # --- REDIRECT-DESTINATION FILTER (2026-07-31) -------------------------------------------------
    # Rows where the crawler followed a redirect hold the DESTINATION's title/canonical/H1/meta, not
    # this site's. Judging them produces spectacular false positives (an off-domain canonical that is
    # really a tracking endpoint's target). Keep them for link/status analysis; exclude them from any
    # check that judges PAGE CONTENT.
    _REDIR = [p for p in P if p.get('redirected')]
    # NON-HTML ASSETS are not pages (§66, a painting-company site, 2026-08-06): the crawl held 136 rows,
    # 112 of them /wp-content/uploads/*.jpg. Those rows were counted in the schema denominator, so
    # the report said "17 of 136 pages have schema (13%)" and raised a coverage CRITICAL — when
    # nearly every REAL page carried it. An image file cannot have JSON-LD, a title or an H1;
    # including it in a content denominator manufactures a defect out of arithmetic. Same family
    # as §37 (count LINKED pages, not synthetic ones).
    # NOTE: use module-level `re`, NOT `_r2` — `_r2` is bound by a local `import re as _r2`
    # further down main(), which makes it function-local everywhere, so touching it up here is
    # an UnboundLocalError. (Caught 2026-08-06 by running the real command; py_compile passes.)
    _ASSET_RE = re.compile(r'\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp4|webm|mov|avi|mp3|'
                           r'wav|ogg|pdf|docx?|xlsx?|pptx?|zip|gz|rar|7z|css|js|mjs|json|xml|txt|'
                           r'woff2?|ttf|eot|otf)(\?|#|$)', re.I)
    def _is_asset(p):
        return bool(_ASSET_RE.search((p.get('url') or '').split('#')[0]))
    _ASSETS = [p for p in P if _is_asset(p)]
    P_content = [p for p in P if not p.get('redirected') and not _is_asset(p)]
    if _REDIR:
        print(f"(note: {len(_REDIR)} crawled row(s) are REDIRECT DESTINATIONS — their title/canonical/"
              f"H1/meta belong to the target, not this site; excluded from content checks)")
    if _ASSETS:
        print(f"(note: {len(_ASSETS)} crawled row(s) are FILES, not pages (images/PDF/CSS/JS) — "
              f"excluded from page counts and content checks; {len(P_content)} real page(s) judged)")
    site_host = host(D.get('site') or (P[0]['url'] if P else ''))
    # REGISTRABLE-DOMAIN scope (2026-08-04): the crawler now crawls SUBDOMAINS of the audited site
    # (studio./uat./blog. …), so page URLs span multiple hosts. A check that compares host(url) to the
    # single seed host would flag every subdomain page's own canonical as "off-domain". `same_reg(h)`
    # treats the audited host AND any subdomain of it as in-scope, so subdomain pages are judged against
    # their own host, not falsely reported as off-site. site_host has www. stripped by host().
    def same_reg(h):
        h = (h or '').lower()
        return h == site_host or h.endswith('.' + site_host)
    critical = 0
    print(f"=== standing audit checks — site host: {site_host} ===\n")

    # LIVE-REACHABILITY GATE (2026-08-04) -----------------------------------------------------------
    # Several checks (2b canonical-status, 12 host-dup, and the schema-locality proxy) do LIVE probes.
    # On a host that black-holes datacenter IPs (a pest-control site, 2026-08-04: 0 bytes from both crawl hosts, yet the
    # crawl JSON is complete because the crawler used curl-fallback), those probes each hang to their
    # per-request timeout — hundreds of them serially = the whole check hangs for many minutes. Probe
    # the homepage ONCE up front; if it doesn't resolve from here, LIVE_OK=False and every live-probe
    # block skips its network calls and relies on the crawl-JSON data the crawler already captured.
    # keyed on RESPONSIVENESS, not just reachability: a host that answers but takes many seconds per
    # request turns the serial live-probe loops into an hour-long hang (same pest-control site, 2026-08-04: reachable
    # but ~8s/request × 403 canonicals ≈ 54 min). If the single homepage probe is slow (or fails),
    # disable live re-probing and fall back to crawl-JSON data.
    import time as _time3
    LIVE_OK = False
    try:
        _t3 = _time3.monotonic()
        _rg = _ur2.urlopen(_ur2.Request(f'https://{site_host}/', headers=_UA), timeout=6)
        _got = bool(_rg.read(1))
        LIVE_OK = _got and (_time3.monotonic() - _t3) < 4.0
    except Exception:
        LIVE_OK = False
    if not LIVE_OK:
        print("(note: homepage is unreachable or too slow to re-probe from this host — live re-probes "
              "are disabled; checks that would re-fetch fall back to the crawl data, which the crawler "
              "captured via its own transport. Live-only signals — canonical-target HTTP status, "
              "apex/www duplication — are reported as UNKNOWN this run rather than probed.)\n")

    # 1 + 2. canonical
    off_domain, self_mismatch, missing = [], [], []
    for p in P_content:
        can = p.get('canonical')
        if not can:
            missing.append(p['url']); continue
        ch = host(can)
        if ch and not same_reg(ch):
            off_domain.append((p['url'], can))
        elif norm(can) != norm(p['url']):
            # a queued URL that 301'd to its canonical target is CORRECT hygiene, not a mismatch —
            # the crawler stores the post-redirect page under the original URL (a title-company site
            # false-alarm 2026-07-18: legacy root URLs 301 -> /services/ versions)
            if p.get('redirected') and norm(p.get('final_url') or '') == norm(can):
                continue
            self_mismatch.append((p['url'], can))
    print("1. CANONICAL TARGET DOMAIN")
    if off_domain:
        critical += 1
        tgt = host(off_domain[0][1])
        print(f"   🚩 CRITICAL: {len(off_domain)} page(s) canonical to a DIFFERENT domain ({tgt}).")
        print(f"      This suppresses indexing of the whole site. Likely a cloned-template global canonical.")
        for u, c in off_domain[:4]:
            print(f"        {u}  ->  {c}")
    else:
        print("   ok — all canonicals stay on-domain")
    if missing:
        print(f"   note: {len(missing)} page(s) have NO canonical tag")
    if self_mismatch:
        print(f"\n2. SELF-CANONICAL — {len(self_mismatch)} page(s) canonical to a different on-domain URL:")
        for u, c in self_mismatch[:5]:
            print(f"     {u}  ->  {c}")

    # 2b. CANONICAL POINTS TO A REDIRECT (GSC "Page with redirect" root cause).
    #     A rel=canonical MUST resolve to a 200 — never a 3xx. The classic miss: directory
    #     "silos" declare a no-slash canonical (/pressure-washing) while serving at the slash
    #     URL (/pressure-washing/), so the canonical 301s away. Google files these under
    #     "Page with redirect" and won't cleanly index the declared canonical.
    #     SLASH-SENSITIVE on purpose: the trailing slash IS the defect, so do NOT rstrip here.
    #     (Confirmed 2026-07-24 on a client site — 15 silo pages; the crawler's own
    #     redirect-follow + rstrip normalization HID this. See /gsc skill diagnostics PATTERN 1.)
    import urllib.request as _ur, ssl as _ssl
    _ctx = _ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = _ssl.CERT_NONE
    class _NR(_ur.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    def _canon_status(u):
        try:
            o = _ur.build_opener(*_PXH, _NR, _ur.HTTPSHandler(context=_ctx))
            r = o.open(_ur.Request(u, method="HEAD", headers=_UA), timeout=12)
            return r.status, ""
        except _ur.HTTPError as e:
            return e.code, e.headers.get("Location", "")
        except Exception:
            return None, ""
    canon_redirects = []
    seen_can = {}
    _canon_capped = False
    if LIVE_OK:
        # cap live probes so a moderately-slow-but-reachable host can't turn this into an hour-long
        # serial loop; 60 unique canonicals is plenty to detect a template-wide canonical-redirect
        # pattern (the defect this finds is systematic, not one-off).
        _CANON_CAP = 60
        for p in P:
            can = p.get('canonical')
            if not can or host(can) != site_host:   # off-domain handled above
                continue
            if can in seen_can:
                st, loc = seen_can[can]
            elif len(seen_can) >= _CANON_CAP:
                _canon_capped = True
                continue
            else:
                st, loc = _canon_status(can); seen_can[can] = (st, loc)
            if isinstance(st, int) and 300 <= st < 400:
                canon_redirects.append((p['url'], can, st, loc))
    print("\n2b. CANONICAL → REDIRECT (canonical URL is not a 200)")
    if not LIVE_OK:
        print("   UNKNOWN — host unreachable from here; canonical HTTP status not probed this run")
    elif canon_redirects:
        critical += 1
        print(f"   🚩 {len(canon_redirects)} page(s) declare a canonical that itself REDIRECTS ({st}).")
        print(f"      Google reports these as 'Page with redirect' and won't index the canonical.")
        print(f"      Common cause: directory silo with a no-slash canonical serving at the slash URL.")
        for u, c, s, loc in canon_redirects[:6]:
            print(f"        {u}\n           canonical {c}  ->  {s} {loc}")
    else:
        print("   ok — every on-domain canonical resolves to a 200")

    # 3. schema
    with_schema = [p for p in P_content if p.get('jsonld_types')]
    print("\n3. STRUCTURED DATA")
    if not with_schema:
        print(f"   🚩 {len(P)} page(s) crawled, ZERO with JSON-LD. No LocalBusiness/Service schema anywhere.")
    else:
        types = set(t for p in with_schema for t in (p.get('jsonld_types') or []))
        _cov = len(with_schema) * 100 // max(1, len(P_content))
        print(f"   {len(with_schema)}/{len(P_content)} pages have schema ({_cov}%). Types: {sorted(types)}")
        # COVERAGE THRESHOLD (added 2026-07-31): this only ever flagged ZERO schema, so a site with
        # 6 of 453 pages (1%) printed an informational line, raised no flag, and therefore never
        # reached the sidecar or the client report (a cleaning-company site). Near-zero coverage is effectively
        # the same defect as none at all.
        # DENOMINATOR MUST MATCH THE PERCENTAGE (§66): this printed len(P) — the raw crawl row
        # count including images — beside a percentage computed over len(P_content). Two different
        # denominators in one sentence, the larger one exaggerating the problem.
        if len(P_content) >= 20 and _cov < 25:
            critical = True
            print(f"   🚩 SCHEMA COVERAGE {_cov}% — only {len(with_schema)} of {len(P_content)} real")
            print("      page(s) carry any JSON-LD. Service and location pages are the ones that need")
            print("      it most; without it Google has no structured signal about what each page")
            print("      offers or where.")
            print("      FIX at the TEMPLATE so every service/city page emits the right schema for")
            print("      the business type (Service + a LocalBusiness subtype for local businesses;")
            print("      Organization/Service for national ones).")
        # schema.org LocalBusiness SUBTYPES are valid local-entity markup — InsuranceAgency,
        # Dentist, Attorney etc. ARE LocalBusiness. Reporting "no LocalBusiness node" on a site with
        # a full InsuranceAgency node + NAP was a false negative (an insurance-agency site, 2026-07-31).
        # §13: single source of truth — see _LOCAL_SUBTYPES at module scope. 'organization' is
        # added here because check 3 treats a bare Organization as PRESENT-but-not-local (it
        # splits three ways below); it is NOT a NAP-carrying subtype (_NAP_TYPES removes it).
        _LB_SUB = _LOCAL_SUBTYPES | {'organization'}
        # array @type serializes as a JOINED string ('LocalBusiness,Store') — split before
        # intersecting or every array-@type site false-fires the local-entity gap (a client site, 2026-08-02)
        _have = {x.strip().lower() for t in types for x in str(t).split(',')}
        # nested publisher/author Organization (Article schema on blogs) IS an Organization node,
        # just not standalone (a Ghost blog, 2026-08-02) — don't cry "no local-entity" on a blog.
        # Only reach for a live fetch when it could change the outcome: if a NAP subtype is already
        # present, the publisher-nested-Organization question is moot, so skip the fetch entirely
        # (it also hangs 15s on bot-walled hosts — a pest-control site, 2026-08-04).
        _hp3 = ''
        if LIVE_OK and not (_have & (_LB_SUB - {'organization'})) and 'organization' not in _have:
            try:
                _hp3 = _ur2.urlopen(_ur2.Request(f'https://{site_host}/', headers=_UA), timeout=8
                                    ).read().decode('utf-8', 'replace')
            except Exception:
                _hp3 = ''
            if re.search(r'"publisher"[^}]{0,200}"Organization"', _hp3):
                _have.add('organization')
        # a NAP-carrying node = LocalBusiness or a specific subtype (NOT bare Organization, which
        # carries no address/hours/geo). Split three ways.
        _NAP_TYPES = _LB_SUB - {'organization'}
        # LOCALITY PROXY — prefer crawl-CAPTURED fields (title/meta) over a live re-fetch. The live
        # fetch fails (0 bytes) on exactly the bot-walled sites the crawl already got via curl-fallback,
        # silently degrading this check where it matters most (a pest-control site, 2026-08-04: homepage 0-bytes
        # from both crawl hosts, yet title = "...Pest Control in <City> & <City>, TX..."). Signal:
        # a "City, ST" string in the homepage title or meta description = local business.
        _CITY_ST = re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s*[A-Z]{2}\b')
        _home3 = next((p for p in P_content
                       if host(p.get('final_url') or p.get('url') or '') == _strip_www(site_host)
                       and (p.get('url','').rstrip('/').count('/') <= 2)), None) or (P_content[0] if P_content else None)
        _looks_local3 = False
        if _home3:
            _blob3 = f"{_home3.get('title','')} {_home3.get('meta_description','')}"
            if _CITY_ST.search(_blob3):
                _looks_local3 = True
        if not _looks_local3 and LIVE_OK:
            # crawl fields inconclusive — try the live homepage (works on non-walled sites; the
            # tel:+City,ST pair is a stronger signal when the page IS reachable)
            try:
                _hp3l = _ur2.urlopen(_ur2.Request(f'https://{site_host}/', headers=_UA), timeout=15
                                     ).read().decode('utf-8', 'replace')
                _looks_local3 = bool(re.search(r'tel:\+?[0-9]', _hp3l)
                                     and _CITY_ST.search(_hp3l))
            except Exception:
                pass
        if _have & _NAP_TYPES:
            _sub = sorted(_have & _NAP_TYPES)
            print(f"   ok — local entity present via LocalBusiness subtype: {', '.join(_sub)}")
        elif 'organization' in _have and _looks_local3:
            print("   🚩 has Organization schema but NO LocalBusiness node — for a local business "
                  "Organization alone carries no address, hours, geo or service area. Add a "
                  "LocalBusiness (or the right subtype) node with full NAP.")
        elif 'organization' in _have:
            print("   ok — Organization present (non-local business); no LocalBusiness expected")
        elif _looks_local3:
            print("   🚩 NO LocalBusiness/Organization schema node anywhere — for a local business "
                  "this is a real local-SEO gap: search engines have no machine-readable statement "
                  "of your name, address, phone, hours or service area. Add a LocalBusiness (or the "
                  "right subtype) node with full NAP.")
        else:
            print("   note: no LocalBusiness/Organization node (or subtype) found — local-entity gap")

    # 4. thin site
    uniq = {norm(p['url']) for p in P if p.get('status') == 200}
    print("\n4. SITE SIZE")
    print(f"   {len(uniq)} unique 200-status pages crawled")
    if len(uniq) <= 3:
        print("   🚩 THIN SITE — a 1-3 page site has almost no organic surface. Headline finding: build real pages — services or product categories, plus the areas or topics served.")

    # 5. placeholder / demo content (CMS empty states shown to visitors)
    MARKERS = ["example product title", "lorem ipsum", "talk about your brand",
               "your collection's name", "example blog post", "sample product",
               "insert your", "placeholder text"]
    hits = []
    for p in P:
        texts = [p.get('title') or '']
        for lvl in (p.get('headings') or {}).values():
            texts.extend(lvl or [])
        blob = ' | '.join(texts).lower()
        found = sorted({m for m in MARKERS if m in blob})
        if found:
            n = sum(blob.count(m) for m in found)
            hits.append((p['url'], found, n))
    print("\n5. PLACEHOLDER / DEMO CONTENT")
    if hits:
        print(f"   🚩 {len(hits)} page(s) render CMS placeholder/demo text to visitors:")
        for u, found, n in hits[:6]:
            print(f"     {u}  ->  {n}× {found}")
        print("   Shopify: a featured-collection section whose collection was deleted falls back to")
        print("   'Example product title' tiles — reassign the collection in the theme editor.")
    else:
        print("   ok — no placeholder markers in titles/headings")

    # 6. RESOURCE HOST MISMATCH — asset/link refs on the wrong host variant (www vs non-www,
    # http vs https). Each one 301s; Googlebot's renderer often DROPS redirecting resources
    # (GSC "redirection error"). Found live: a chiropractor's site — review icons (Star.png,
    # google.png) referenced non-www on a www-canonical site — the review section's own assets
    # failed in Google's render. Scan every page's internal_links + raw image/asset hosts.
    PGS = D.get('pages') or []
    import urllib.parse as _up
    def _rawhost(u):
        try: return _up.urlparse(u if '://' in (u or '') else 'http://'+(u or '')).netloc.lower()
        except Exception: return ''
    site_host = _rawhost(PGS[0]['url']) if PGS else ''
    mism = {}
    for p in PGS:
        for l in (p.get('internal_links') or []):
            h2 = _rawhost(l.get('href') or '')
            if h2 and h2 != site_host and _strip_www(h2) == _strip_www(site_host):
                mism.setdefault(l.get('href'), set()).add(p['url'])
        for l in (p.get('external_links') or []):
            h2 = _rawhost(l.get('href') or '')
            if h2 and _strip_www(h2) == _strip_www(site_host) and h2 != site_host:
                mism.setdefault(l.get('href'), set()).add(p['url'])
    # images/scripts/css are NOT in the link lists — scan RAW html of a sample of pages
    import urllib.request as _ur, re as _re
    sample = [p['url'] for p in PGS[:1]] + [p['url'] for p in PGS if p.get('status')==200][:10]
    for su in dict.fromkeys(sample):
        try:
            _rq=_ur.Request(su, headers=_UA)
            _h=_ur.urlopen(_rq, timeout=20).read().decode('utf-8','replace')
        except Exception: continue
        for m in _re.findall(r'(?:src|href)=["\'](https?://[^"\']+)', _h):
            h2 = _rawhost(m)
            if h2 and h2 != site_host and _strip_www(h2) == _strip_www(site_host):
                mism.setdefault(m.split('?')[0], set()).add(su)
    print("\n6. RESOURCE/LINK HOST MISMATCH (www vs non-www → forced 301s)")
    if mism:
        print(f"   🚩 {len(mism)} URL(s) referenced on the WRONG host variant (each 301s; Googlebot may drop them):")
        for u, srcs in list(sorted(mism.items()))[:8]:
            print(f"     {u}")
            for s2 in list(srcs)[:2]: print(f"       referenced from: {s2}")
        print("   FIX at the template: reference the canonical host (or root-relative paths).")
    else:
        print("   ok — every same-site reference uses the canonical host")

    # 7. CRAWLABLE AUTH / ACCOUNT ROUTES (added 2026-07-30 — a cleaning-company site had
    # /user-account and /log-in INDEXED BY GOOGLE). Login/account/password routes must never be
    # indexable: they're thin, they're a bad search result (searcher lands on a login wall), and a
    # 200-to-crawler / 302-to-user split feeds Google a page real visitors can't see.
    # robots.txt CANNOT deindex a URL Google already knows — the fix is noindex + GSC removal.
    # `re` is module-level (top of file). A local `import re` here would make `re` function-local
    # for ALL of main(), unbinding every earlier use (check 3 crashed this way, a client site 2026-08-02).
    AUTH = re.compile(r'/(log-?in|sign-?in|sign-?up|register|user-account|my-account|account|'
                      r'reset-password|update-password|forgot-password|access-denied|logout|'
                      r'checkout|cart|wp-login)(/|$|\?)', re.I)
    # LIVE NO-FOLLOW RE-VERIFY (2026-07-31): the crawl FOLLOWS redirects, so a route that really 302s
    # (e.g. Shopify /checkout -> shop.app) is recorded as 200 and shipped as a false CRITICAL. Re-fetch
    # each candidate WITHOUT following redirects; only a true 200 is indexable.
    _cand = [p for p in PGS if p.get('status') == 200 and AUTH.search(p.get('url') or '')]
    authpages, _redirected = [], []
    for _p in _cand:
        try:
            _o2 = _ur2.build_opener(*_PXH, _NR, _ur2.HTTPSHandler(context=_ctx))
            _rr = _o2.open(_ur2.Request(_p['url'], method='HEAD', headers=_UA), timeout=12)
            _st = _rr.status
        except _ur2.HTTPError as e:
            _st = e.code
        except Exception:
            _st = _p.get('status')
        (authpages if _st == 200 else _redirected).append(dict(_p, _live=_st))
    # a 200 auth route carrying noindex is CORRECT, not a defect (a SaaS site, 2026-08-04: /login is
    # 200 + "noindex, follow" and was escalated to CRITICAL "can be indexed" — false positive, and
    # it also appeared in the noindex-inventory finding, contradicting itself). Split them.
    _noindexed_auth = [p for p in authpages if _is_noindex(p)]
    authpages = [p for p in authpages if not _is_noindex(p)]
    if _redirected:
        print(f"   (re-verified: {len(_redirected)} route(s) actually redirect/401 live and were "
              f"dropped: {', '.join(str(x['_live']) + ' ' + x['url'].split('/')[-1] for x in _redirected[:4])})")
    print("\n7. CRAWLABLE AUTH / ACCOUNT ROUTES")
    if authpages:
        critical = True
        print(f"   🚩 {len(authpages)} auth/account route(s) return 200 to a crawler:")
        for p in authpages[:10]:
            print(f"     {p.get('word_count') or 0:>5}w  {p.get('url')}")
        print("   FIX: add noindex to each (robots.txt will NOT deindex known URLs), request removal")
        print("   in Search Console for any already indexed, and confirm robots.txt matches the REAL")
        print("   platform (a stale WordPress robots.txt on a non-WP app blocks nothing that exists).")
    else:
        if _noindexed_auth:
            print(f"   ok — {len(_noindexed_auth)} auth route(s) return 200 but carry noindex "
                  "(correctly kept out of the index)")
        else:
            print("   ok — no auth/account routes crawlable")

    # 8. PLACEHOLDER / TEMPLATE TITLES (added 2026-07-30 — 58 pages on a cleaning-company site shared the title
    # "<Brand> V2 Website", incl. all 52 /service-locations/ pages). A build-time placeholder
    # title means Google sees a generic label on otherwise substantial pages.
    from collections import Counter as _C
    # SOURCE FIX (2026-07-31): this counted PGS (all crawl rows). On a law-firm site the "8 pages
    # sharing one title" was 1 REAL post + 7 REDIRECT rows carrying the destination's title, and the
    # other two "duplicate" groups were parameter variants that all canonical to one URL. Neither is
    # a duplicate-title defect, and "FIX at the template — unique titles" was a non-fix.
    # Count only pages that are their OWN index entry: not redirected, self-canonical (or none).
    def _own_entry(p):
        if p.get('status') != 200 or p.get('redirected'):
            return False
        _u = (p.get('url') or '').split('#')[0].rstrip('/')
        _c = (p.get('canonical') or '').split('#')[0].rstrip('/')
        if not _c or _c == _u:
            return True
        # SAME PATH on a host variant (apex vs www) is CANONICALISATION, not a different page — the
        # page is real and its title is real. Only a canonical to a DIFFERENT PATH means this row is
        # a duplicate/parameter variant. Getting this wrong collapsed that cleaning site's 58 placeholder
        # titles to 1 (56 were apex-host copies of the same www pages). Same rule as the
        # url-discovery probe: compare the PATH, not merely whether a canonical exists.
        _pu = re.sub(r'^https?://(www\.)?', '', _u)   # module-level re: _r2 is bound later in main()
        _pc = re.sub(r'^https?://(www\.)?', '', _c)
        return _pu == _pc
    _own = [p for p in PGS if _own_entry(p)]
    # dedupe slash/host variants — the homepage counted twice ("/" + no-slash) fabricated a
    # 2-member duplicate-title "group" of one page (a cleaning-company site, 2026-08-02)
    _seen8 = set()
    _own = [p for p in _own
            if not (re.sub(r'^https?://(www\.)?', '', (p.get('url') or '')).rstrip('/') in _seen8
                    or _seen8.add(re.sub(r'^https?://(www\.)?', '', (p.get('url') or '')).rstrip('/')))]
    # a title equal to just the brand name (short, 1-2 words) repeated across many pages is often
    # an SSR-streaming/edge-cache FALLBACK captured by the crawler, not the real per-page title
    # (a Next.js store, 2026-08-04: 26 pages recorded title="<Brand>" from a Vercel cache MISS; live titles unique).
    # Skip a duplicate GROUP whose title is <=2 words AND equals the site's brand token.
    _brand8 = re.sub(r'[^a-z0-9]', '', re.sub(r'^https?://(www\.)?', '', site_host).split('.')[0].lower())
    def _looks_fallback8(t):
        _tl = t.strip()
        return len(_tl.split()) <= 2 and re.sub(r'[^a-z0-9]', '', _tl.lower()) == _brand8
    _t = _C((p.get('title') or '').strip() for p in _own
            if (p.get('title') or '').strip() and not _looks_fallback8((p.get('title') or '')))
    _dropped8 = len([p for p in PGS if p.get('status') == 200]) - len(_own)
    PLACE = re.compile(r'\b(v\d+ website|untitled|home ?page|new page|test page|placeholder|'
                       r'lorem|default title|my site|website template)\b', re.I)
    # HEADER FIRST (a lawn-care site, 2026-09-22): this blank-title block printed BEFORE the "8." header,
    # so it sat inside check 7's section and the sidecar shipped 169 CSR shells as "Login and account
    # pages can be found and indexed" while check 7 itself said ok.
    print("\n8. PLACEHOLDER / MASS-DUPLICATE TITLES")
    # blank <title> is the worst case — the single most important tag, empty (a WordPress blog, 2026)
    def _is_html_page(p):
        _ct = (p.get('content_type') or '').lower()
        _u = (p.get('url') or '').lower()
        if _ct and 'html' not in _ct:
            return False
        if re.search(r'\.(xlsx?|pptx?|docx?|pdf|csv|zip|json|xml|rss)(\?|$)', _u):
            return False
        return True
    _blank_titled = [p for p in _own if _is_html_page(p) and not (p.get('title') or '').strip()]
    if _blank_titled:
        critical = True
        print(f"   🚩 {len(_blank_titled)} page(s) have a BLANK <title> — no search-result headline:")
        for p in _blank_titled[:6]:
            print(f"     {p['url']}")
    # duplicate threshold scales with site size: >=5 on big sites, but >=40% of a SMALL site
    # (a small client site: 4 of 6 identical titles slipped under a fixed >=5 bar)
    _own_n = max(1, len(_own))
    _dup_bar = 5 if _own_n >= 12 else max(2, int(_own_n * 0.4))
    dup = [(t, c) for t, c in _t.items() if c >= _dup_bar]
    if _dropped8:
        print(f"   (excluded {_dropped8} redirect/canonicalised row(s) — their titles belong to the "
              f"destination, not to a distinct page)")
    # 2-4 pages sharing a title that clearly BELONGS to another page (its slug matches the title
    # tokens) = copy-pasted template titles (a cleaning-company site, 2026-08-02: /terms-of-service and
    # /privacy-policy both titled "Standard House Cleaning | ..." — invisible below the >=5 bar)
    _LANGPREF8 = ('/de/', '/es/', '/fr/', '/nl/', '/hi/', '/pt-br/', '/af/', '/it/', '/ja/',
                  '/zh/', '/pl/', '/ru/', '/pt/', '/ko/')
    def _langroot8(u):
        # module-level re, NOT _r2 — _r2 is bound later in main() and is unbound in functions
        # defined before it (the documented scoping trap, third occurrence)
        _pl = re.sub(r'https?://[^/]+', '', (u or '').lower())
        for _lp in _LANGPREF8:
            if _pl.startswith(_lp):
                return _pl[len(_lp) - 1:]
        return _pl
    for _t8, _c8 in _t.items():
        if not (2 <= _c8 < 5):
            continue
        # same title across LANGUAGE variants of one page = hreflang alternates, not duplicates
        _mem8 = [p for p in _own if (p.get('title') or '').strip() == _t8]
        if len({_langroot8(p.get('url')) for p in _mem8}) == 1:
            continue
        _tok8 = set(re.findall(r'[a-z]{4,}', _t8.lower()))
        # DISTINCTIVE tokens only: words in ≥30% of all titles (the site's trade vocabulary —
        # "house","cleaning") match every owner candidate and mis-attribute (same cleaning site)
        _all_titles8 = [(_p.get('title') or '').lower() for _p in _own]
        _common8 = {w for w in _tok8
                    if sum(1 for _tt in _all_titles8 if w in _tt) >= max(2, len(_all_titles8) * 0.3)}
        _tok8 -= _common8
        if len(_tok8) < 2:
            continue
        _members8 = [p for p in _own if (p.get('title') or '').strip() == _t8]
        def _slugmatch8(p):
            return len(_tok8 & set(re.findall(r'[a-z]{4,}', (p.get('url') or '').lower()))) >= 2
        # the OWNER may not be in the duplicate group at all — its own title can differ slightly
        # (that cleaning site: the real standard-house-cleaning page). Search the whole site for a slug match.
        _owners8 = [p for p in _own if _slugmatch8(p)]
        _borrowers8 = [p for p in _members8 if not _slugmatch8(p)]
        # if this title ALSO hits the mass-duplicate bar it's a N-way dupe, not a 1-page "borrow"
        # (a client site, 2026-08-04: 5 pages share the title; the copied-title sub-message conflated it)
        if _c8 >= _dup_bar:
            continue
        # "COPIED from" only when there's a CLEAR owner (its slug matches the title) AND clear
        # borrowers. Otherwise it's just N pages sharing one title — say that, don't imply theft.
        _clear_owner = len(_owners8) == 1 and _owners8[0] in _members8
        if _owners8 and _borrowers8 and _clear_owner:
            print(f"   ⚠️ {len(_borrowers8)} page(s) carry a title COPIED from another page "
                  f"(\"{_t8[:48]}\" belongs to {_owners8[0]['url']}):")
            for p in _borrowers8[:4]:
                print(f"        {p['url']}")
            print("      These pages describe themselves to Google as a different page entirely.")
        elif _c8 >= 2:
            print(f"   ⚠️ {_c8} pages share the identical title \"{_t8[:56]}\":")
            for p in _members8[:4]:
                print(f"        {p['url']}")
            print("      Give each a distinct title describing its own page.")
    ph  = [(t, c) for t, c in _t.items() if PLACE.search(t)]
    if ph or dup:
        for t, c in sorted(ph, key=lambda x: -x[1])[:5]:
            critical = True
            print(f"   🚩 PLACEHOLDER title on {c} page(s): \"{t[:64]}\"")
        for t, c in sorted(dup, key=lambda x: -x[1])[:5]:
            if (t, c) in ph:
                continue
            _pages8 = [p for p in _own if (p.get('title') or '').strip() == t]
            if len({_langroot8(p.get('url')) for p in _pages8}) == 1:
                print(f"   (\"{t[:48]}\" x{c} = language variants of ONE page — hreflang "
                      "alternates, not duplicates; not flagged)")
                continue
            # PAGINATION RECOGNITION (an insurance site on Webflow, 2026-08-01): 19 "duplicate titles" were Webflow
            # ?page=2..18 views of ONE glossary listing, each self-canonical. "Write unique titles"
            # is the wrong fix for paginated views — the real question is the pagination canonical
            # strategy (§20). Detect: same base path, differing only by a page-ish query param.
            _bases8 = {re.sub(r'https?://[^/]+', '', (p.get('url') or '')).split('?')[0]
                       for p in _pages8}
            _qs8 = sum(1 for p in _pages8 if '?' in (p.get('url') or ''))
            if len(_bases8) == 1 and _qs8 >= c - 1:
                print(f"   ⚠️ {c} PAGINATED VIEWS of one listing share the title "
                      f"\"{t[:56]}\" ({next(iter(_bases8))}?<page-param>) — this is a pagination")
                print("      setup question, NOT a write-unique-titles task: decide whether the")
                print("      numbered views should self-canonicalize (status quo) or point at the")
                print("      base listing, and consider adding ' — page N' to their titles.")
            else:
                print(f"   ⚠️ {c} pages share the title: \"{t[:64]}\"")
                for p in _pages8[:3]:
                    print(f"        {p.get('url')}")
                if c > 3:
                    print(f"        … and {c - 3} more")
        print("   FIX for true duplicates: unique, descriptive <title> per page (at the template).")
    else:
        print("   ok — no placeholder or mass-duplicate titles")

    # ===== BUSINESS-INTEGRITY CHECKS (added 2026-07-30) ==========================================
    # WHY THIS BLOCK EXISTS: every check above asks "did the server respond correctly?". None asked
    # "does the page do what it claims for the business?". An independent no-context audit of
    # a cleaning-company site found the single worst defect on the site that way — a click-to-call
    # CTA wired to an ARIZONA number on a Tampa company's 12 money pages — which returns a perfectly
    # valid HTTP 200 and passes every status/HTML check ever written. These three checks encode that
    # class: contact integrity, malformed hrefs, placeholder content in body/slug.
    import re as _r2
    from collections import Counter as _C2

    # 9 + 10 MUST read LIVE HTML: the crawl JSON stores neither raw_html (no tel: to extract) nor
    # #-prefixed hrefs (the crawler resolves them as same-page fragments and DISCARDS them). That
    # discard is exactly why 39 dead nav links went unnoticed until the owner clicked them. So both
    # checks fetch a representative SAMPLE of live pages instead of trusting the crawl.
    # (_ur2/_UA are module-level — see top of file)
    def _live(u):
        try: return _ur2.urlopen(_ur2.Request(u, headers=_UA), timeout=20).read().decode('utf-8','replace')
        except Exception: return ''
    # sample: home + up to 7 deepest-content pages across distinct URL sections
    _byd = sorted([p for p in PGS if p.get('status') == 200],
                  key=lambda p: -(p.get('word_count') or 0))
    _sec_seen, _sample = set(), []
    for p in _byd:
        sec = _r2.sub(r'https?://[^/]+', '', p['url']).strip('/').split('/')[0]
        if sec in _sec_seen: continue
        _sec_seen.add(sec); _sample.append(p['url'])
        if len(_sample) >= 7: break
    _sample = ([PGS[0]['url']] if PGS else []) + _sample
    _htmls = {u: _live(u) for u in dict.fromkeys(_sample)}
    _htmls = {u: h for u, h in _htmls.items() if h}

    # BUSINESS-MODEL GATE (an iOS-app site, 2026-08-02: an iOS app publisher got "no phone",
    # "missing NAP", "no contact but calling" and "store has nothing in it" — four MEDIUMs from
    # checks that assume local-service or ecommerce). Model detection: any LocalBusiness-subtype
    # node anywhere in the crawl = local; store platform markers = commerce; else = organization.
    # §13: single source of truth — see _LOCAL_SUBTYPES at module scope. The gate deliberately
    # EXCLUDES bare 'organization' (an Organization node alone does not make a business local).
    _LB_GATE = _LOCAL_SUBTYPES
    _all_types_gate = set()
    for p in PGS:
        for t in (p.get('jsonld_types') or []):
            for x in str(t).split(','):
                _all_types_gate.add(x.strip().lower())
    IS_LOCAL_BIZ = bool(_all_types_gate & _LB_GATE)
    # SCHEMA is not the only local signal (two client sites, 2026-08-04: local painters with NO
    # LocalBusiness schema were classed as "app publisher" and had the phone check SUPPRESSED — the
    # missing schema is itself a finding, not grounds to skip local checks). If the homepage shows
    # a tel: link OR a "City, ST" address string, treat as local regardless of schema.
    if not IS_LOCAL_BIZ:
        try:
            _hpg = _ur2.urlopen(_ur2.Request(f'https://{site_host}/', headers=_UA), timeout=15
                                ).read().decode('utf-8', 'replace')
            if re.search(r'tel:\+?[0-9]', _hpg) or re.search(r'\b[A-Z][a-z]+,\s*[A-Z]{2}\b', _hpg):
                IS_LOCAL_BIZ = True
        except Exception:
            pass

    # 9. CONTACT INTEGRITY — phone/email consistency (NAP is a local ranking signal)
    TEL = _r2.compile(r'href=["\']tel:([+0-9().\-\s]{7,})["\']', _r2.I)
    MAIL = _r2.compile(r'href=["\']mailto:([^"\'?]+)', _r2.I)
    all_tels, all_mails, multi = _C2(), _C2(), {}
    shown_by_page, all_shown = {}, _C2()
    # DISPLAYED numbers matter as much as tel: hrefs — a wrong number printed in an H2 with no link
    # is still the number a customer dials. LIVE CATCH: /florida/st-petersburg showed THREE different
    # numbers, one of them a 727 with no tel: link, which a tel:-only check cannot see.
    # EXCLUSIONS (critique 2026-07-30): form placeholder="(111) 111-1111" and repeated-digit dummies
    # would false-positive a naive version. Strip form attrs and obvious dummies before matching.
    SHOWN = _r2.compile(r'\(?\b([2-9]\d{2})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b')
    DUMMY = _r2.compile(r'^(\d)\1{6,}$|^1234567|^5551\d{3}$')

    def _merge_trunk_forms(keys):
        """Collapse `0<national>` onto `+CC<national>` — the SAME subscriber written two ways.

        a Turkish clinic's site (2026-08-07) wired `tel:+905001234567` AND `tel:05001234567`
        on every page — one line, two notations (E.164 vs the national trunk-prefix form, where
        the leading 0 replaces the country code). They normalised to two DIFFERENT keys, so
        check 9 shipped a CRITICAL "2 DIFFERENT phone numbers wired sitewide" about ONE number,
        and listed the same line twice as proof. §49 fixed the MASKING of trunk-form numbers but
        never their EQUIVALENCE to the +CC form.

        Order-independent by construction (a set is unordered — resolving this inside _norm_tel
        would depend on which spelling was parsed first). check 16 already merged these correctly
        via last-10-digit comparison: two phone normalisers in one file with different rules is
        the §62/Rule-13 divergence again, so this helper is the single place that owns the rule.
        """
        _intl = [k for k in keys if k.startswith('+')]
        _out = set()
        for k in keys:
            if k.startswith('0'):
                _nat = k[1:]
                _hit = next((i for i in _intl
                             if len(_nat) >= 9 and i[1:].endswith(_nat) and i[1:] != _nat), None)
                _out.add(_hit or k)
            else:
                _out.add(k)
        return _out

    for u, h in _htmls.items():
        def _norm_tel(x):
            _x = x.strip()
            _d = _r2.sub(r'[^\d]', '', _x)
            # international (+CC where CC != 1) keeps full digits with a + marker; NANP -> last 10
            if _x.startswith('+') and not _d.startswith('1'):
                return '+' + _d
            # NATIONAL-FORMAT INTERNATIONAL (a UK salon site, 2026-08-05): a UK mobile written WITHOUT
            # +44 — "07700900123" — has no + to signal its country, so it fell through to the NANP
            # branch, got truncated to the last 10 digits and was masked "(770) 090-0123" with a
            # fabricated area code, then flagged as an inconsistent number. A leading 0 + 10-11
            # digits is the national trunk-prefix form (UK/DE/FR/…) and is NEVER valid NANP, whose
            # area codes cannot start with 0. Keep it whole and mark it non-NANP.
            #
            # a Turkish clinic's site (2026-08-07): the §49 fix stopped the MASKING but not the EQUIVALENCE.
            # A Turkish clinic wired the SAME line two ways on every page — `tel:+905001234567`
            # (E.164) and `tel:05001234567` (national trunk form). These normalised to two
            # DIFFERENT keys, so check 9 shipped a CRITICAL "2 DIFFERENT phone numbers wired
            # sitewide" about ONE number. E.164 `+CC<national>` and `0<national>` are the same
            # subscriber: drop the trunk `0`, and if the remaining national digits are a SUFFIX
            # of an already-seen international number, collapse onto that number's key.
            # (check 16 already merged these correctly by comparing last-10 digits — two phone
            # normalisers in one file with different rules, the §62/Rule-13 divergence again.)
            if _d.startswith('0') and len(_d) >= 10:
                return '0' + _d.lstrip('0')
            return _d[-10:] if len(_d) >= 10 else _d
        t = _merge_trunk_forms({_norm_tel(x) for x in TEL.findall(h)})
        if len(t) > 1: multi[u] = sorted(t)
        for x in t: all_tels[x] += 1
        for m in MAIL.findall(h): all_mails[m.strip().lower()] += 1
        # visible text only: drop <script>/<style>, and drop placeholder=/value=/aria-label= attrs
        _vis = _r2.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', h)
        _vis = _r2.sub(r'(?i)\s(placeholder|value|aria-label|title|alt)=["\'][^"\']*["\']', ' ', _vis)
        _vis = _r2.sub(r'(?is)<[^>]+>', ' ', _vis)
        _s = {''.join(g) for g in SHOWN.findall(_vis)}
        _s = {x for x in _s if not DUMMY.search(x)}
        if _s:
            shown_by_page[u] = sorted(_s)
            for x in _s: all_shown[x] += 1

    # SITEWIDE trunk-form reconciliation (the Turkish clinic's site, 2026-08-07). The per-page merge above only
    # sees ONE page's spellings: this site writes `tel:+905001234567` on some pages and
    # `tel:05001234567` on others, so no single page held both and the aggregate still counted
    # TWO numbers — shipping "2 DIFFERENT phone numbers wired sitewide" about one line. Reconcile
    # across the whole corpus once every page has been read, folding each trunk-form key's count
    # into its international twin. Scope is deliberately the same rule as _merge_trunk_forms.
    _fold9 = {}
    _intl9 = [k for k in all_tels if k.startswith('+')]
    for _k9 in list(all_tels):
        if _k9.startswith('0'):
            _nat9 = _k9[1:]
            _tw9 = next((i for i in _intl9
                         if len(_nat9) >= 9 and i[1:].endswith(_nat9) and i[1:] != _nat9), None)
            if _tw9:
                _fold9[_k9] = _tw9
    if _fold9:
        for _from9, _to9 in _fold9.items():
            all_tels[_to9] += all_tels.pop(_from9)
        for _u9 in list(multi):
            _m9 = sorted({_fold9.get(x, x) for x in multi[_u9]})
            if len(_m9) > 1:
                multi[_u9] = _m9
            else:
                del multi[_u9]          # the "two numbers" on this page were one number, twice

    print(f"\n9. CONTACT INTEGRITY (click-to-call / email) — live sample of {len(_htmls)} page(s)")
    def _fmt9(n):
        # only the US/Canada (NANP) 10-digit form gets the (NNN) NNN-NNNN mask; anything else —
        # e.g. Singapore +65 9123 4567 — keeps its digits (a client blog, 2026-08-02: a +65 number
        # was rendered "(659) 123-4567", a phantom Alabama area code)
        _dig = _r2.sub(r'\D', '', n)
        if n.strip().startswith('+') and not _dig.startswith('1'):
            return '+' + _dig
        # national-format international (leading 0) is not NANP — never apply the (NNN) NNN-NNNN mask
        if _dig.startswith('0'):
            return n.strip()
        _d = _dig[-10:] if len(_dig) >= 10 else _dig
        return f"({_d[:3]}) {_d[3:6]}-{_d[6:]}" if len(_d) == 10 else n
    def _bad_areacode(n):
        # structurally impossible / known-unassigned NANP area codes (a Next.js store, 2026-08-04: 943 is not
        # an assigned code — a fabricated phone on a payment-taking store). Only for NANP (no +CC).
        _x = n.strip()
        if _x.startswith('+') and not _r2.sub(r'\D', '', _x).startswith('1'):
            return False                          # international — don't judge as NANP
        _a = _r2.sub(r'\D', '', _x)[-10:][:3]
        if len(_a) != 3:
            return False
        if _a[0] in '01' or _a[1:] == '11':       # first digit 0/1, or N11 service codes
            return True
        return _a in {'943', '000', '555', '311', '411', '511', '611', '711', '811', '911'}
    if not all_tels and not all_shown and not IS_LOCAL_BIZ:
        print("   (no phone anywhere — but no LocalBusiness schema either: an app/online business")
        print("    model. Not flagged; phone-based checks assume a local business.)")
    elif not all_tels and not all_shown:
        print("   ⚠️ NO PHONE NUMBER anywhere in the sample — neither click-to-call links nor a")
        print("      displayed number. For a local or physical business that is a missed way for")
        print("      customers to reach you, and search engines have no phone number to associate")
        print("      with your business: publish your main number (as a tap-to-call link) sitewide.")
    elif not all_tels:
        print("   (no tel: links found in the sample)")
    else:
        # area codes are a NANP concept — slicing [-10:-7] off an international number invents one
        # ("770" from a UK mobile 07700900123). Only derive them from NANP-shaped numbers.
        areas = sorted({x[-10:-7] for x in all_tels
                        if len(x) >= 10 and not x.startswith('+') and not x.startswith('0')})
        print("   distinct tel: numbers: " + ", ".join(
            f"{_fmt9(k)} on {v} page(s)" for k, v in all_tels.items()))
        if areas:
            print(f"   area codes seen: {areas}")
        else:
            print("   (numbers are not US/Canada format — no area-code analysis)")
        _fake_ac = [k for k in list(all_tels) + list(all_shown) if _bad_areacode(k)]
        if _fake_ac:
            critical = True
            print("   🚩 phone number uses an INVALID / unassigned area code — likely a placeholder "
                  f"or fabricated number, not a real line: {', '.join(_fmt9(x) for x in dict.fromkeys(_fake_ac))}. "
                  "A non-working number on a live/paying site loses every call and damages trust.")
        _same_everywhere = len(multi) == 0 or len(multi) == len(_htmls)
        if len(all_tels) > 1 and len(areas) == 1 and _same_everywhere:
            # same metro + the same set on every page = a main line + an alternate, not a defect
            print(f"   ok — {len(all_tels)} numbers but ALL share area code {areas[0]} and appear")
            print("        identically sitewide (main + alternate line). Not flagged.")
        elif len(all_tels) > 1:
            # multi-LOCATION architecture: a shared toll-free on every page + local numbers that
            # are consistent WITHIN each location subtree = deliberate, not inconsistent (a multi-location garage-door site,
            # 2026-08-01: 503-Portland / 503-Salem / 208-Boise + one 888, uniform per metro,
            # shipped as a cover-page CRITICAL). Test: for pages under /locations/-style subtrees,
            # numbers must be uniform per subtree; directory pages listing every branch are exempt.
            # shared line = on a MAJORITY of sampled pages (strict intersection failed on that site:
            # one page lacked the toll-free and the whole multi-location pattern collapsed to 🚩);
            # directory pages (3+ numbers = branch listings like /pricing, /sitemap) are exempt
            # from the one-local-number rule; remaining pages may carry at most ONE local number,
            # consistent within their URL subtree.
            _page_tels = {}
            for _u9, _h9 in _htmls.items():
                _page_tels[_u9] = {_norm_tel(x) for x in TEL.findall(_h9)}
            _shared9 = {n for n, c in all_tels.items() if c >= max(2, int(0.6 * len(_htmls)))}
            _viol9 = []
            _sub_local = {}
            for _u9, _t9 in _page_tels.items():
                _loc9 = _t9 - _shared9
                if len(_t9) >= 3:
                    continue                       # directory page listing every branch
                if len(_loc9) > 1:
                    _viol9.append((_u9, _loc9))
                    continue
                _seg9 = '/'.join(_r2.sub(r'https?://[^/]+', '', _u9).strip('/').split('/')[:3])
                if _loc9:
                    _prev9 = _sub_local.setdefault(_seg9, _loc9)
                    if _prev9 != _loc9:
                        _viol9.append((_u9, _loc9))
            # GUARD for the founding catch (a cleaning-company site, 480-ARIZONA): a widely-shared number is
            # only benign if it is TOLL-FREE or matches the area code of the local numbers. A
            # shared GEOGRAPHIC number foreign to every local area code IS the wrong-state bug —
            # it must never ride the multi-location whitelist.
            _TOLLFREE = {'800', '833', '844', '855', '866', '877', '888'}
            _loc_areas = {n[:3] for _u9, _t9 in _page_tels.items() for n in (_t9 - _shared9)}
            _foreign_shared = [n for n in _shared9
                               if n[:3] not in _TOLLFREE
                               and _loc_areas and n[:3] not in _loc_areas]
            if _foreign_shared:
                _viol9.append(("SITEWIDE", set(_foreign_shared)))
            _multiloc9 = bool(_shared9 and not _viol9)
            if _multiloc9:
                print(f"   ⚠️ {len(all_tels)} numbers, but the pattern is a MULTI-LOCATION setup: one")
                print(f"      shared line ({', '.join(_fmt9(x) for x in sorted(_shared9))}) on every page,")
                print("      plus local numbers uniform within each location section. Deliberate")
                print("      architecture — VERIFY each local number belongs to that branch, then dismiss.")
            else:
                critical = True
                print(f"   🚩 {len(all_tels)} DIFFERENT phone numbers wired sitewide; {len(multi)} sampled")
                print(f"      page(s) expose 2+ numbers in one page:")
                for u, t in list(multi.items())[:6]:
                    print("        " + u + " -> " + ", ".join(_fmt9(x) for x in t))
                print("   VERIFY every number belongs to THIS business in THIS metro — the classic")
                print("   failure is a call CTA wired to a number in the WRONG STATE on money pages:")
                print("   HTTP 200, valid HTML, invisible to every status check, and every call lost.")
        if len(all_mails) > 1:
            print(f"   ⚠️ {len(all_mails)} distinct mailto addresses: {list(all_mails)[:5]}")
    if all_shown:
        _multi_shown = {u: v for u, v in shown_by_page.items() if len(v) > 1}
        _areas_shown = sorted({x[:3] for x in all_shown})
        print(f"   DISPLAYED numbers (visible text, form placeholders excluded): "
              f"{len(all_shown)} distinct, area codes {_areas_shown}")
        _shown_uniform = len(_multi_shown) == len(shown_by_page) and len(shown_by_page) > 1
        if len(all_shown) > 1 and len(_areas_shown) == 1 and _shown_uniform:
            print(f"   ok — {len(all_shown)} displayed numbers but all share area code "
                  f"{_areas_shown[0]} and appear identically sitewide (main + alternate). Not flagged.")
        elif len(all_shown) > 1 and _shown_uniform and len(_areas_shown) > 1:
            # multiple metros shown identically everywhere: legitimate multi-location businesses do
            # this, but so does a wrong-number bug. Warn with the numbers rather than cry critical.
            print(f"   ⚠️ {len(all_shown)} numbers across area codes {_areas_shown} appear on every")
            print("      sampled page. Legitimate for a multi-location business — VERIFY each number")
            print("      belongs to this company before dismissing.")
            for u, v in list(_multi_shown.items())[:3]:
                print(f"        {u} shows: {v}")
        elif len(all_shown) > 1 and locals().get('_multiloc9') and \
                set(all_shown) <= {x[-10:] for x in all_tels}:
            print(f"   ⚠️ displayed numbers match the multi-location line set above — same verdict,")
            print("      verify each local number belongs to its branch.")
        elif len(all_shown) > 1:
            _tel_uniform = len(all_tels) == 1
            _extra_counts = {n: c for n, c in all_shown.items()
                             if n not in {x.lstrip('+1')[-10:] for x in all_tels}}
            _all_extras_single_page = _extra_counts and all(c <= 1 for c in _extra_counts.values())
            if _tel_uniform and _all_extras_single_page:
                print(f"   ⚠️ every click-to-call is the same number ({_fmt9(next(iter(all_tels)))}),")
                print(f"      but {len(_extra_counts)} OTHER number(s) appear in the TEXT of one page")
                print("      each — usually editorial content quoting a third party's number (a")
                print("      carrier review, a courthouse listing). VERIFY the surrounding sentence")
                print("      attributes each to a named third party; only then dismiss:")
                for _n9, _c9 in list(_extra_counts.items())[:4]:
                    _pgs9 = [u for u, v in shown_by_page.items() if _n9 in v]
                    print(f"        {_fmt9(_n9)} on {_pgs9[0] if _pgs9 else '?'}")
            else:
                critical = True
                print(f"   🚩 {len(_multi_shown)} sampled page(s) SHOW more than one phone number:")
                for u, v in list(_multi_shown.items())[:5]:
                    print(f"     {u}")
                    print("        shows: " + ", ".join(_fmt9(x) for x in v))
                print("   A visible number with no tel: link is still the number a customer dials —")
                print("   and inconsistent NAP across pages weakens local ranking signals.")
        _orphan = sorted(set(all_shown) - {x.lstrip('+1') for x in all_tels})
        if _orphan and all_tels:
            print("   ⚠️ shown in text but never click-to-call linked: "
                  + ", ".join(_fmt9(x) for x in _orphan[:4]))

    # 10. MALFORMED HREFS — valid HTML that navigates nowhere (no bad status code exists to catch it)
    # EXEMPTION (critical): bare "#" and "#anchor-id" are LEGITIMATE — Webflow/JS interaction buttons.
    # Both this audit and an independent no-context audit nearly false-positived on 9 WORKING modal
    # CTAs. Only flag "#" followed by a hostname, a scheme, or "/path" = a mangled absolute URL.
    BAD = {
      # #/route = SPA client route (Ghost portal, hash routers) — NOT a mangled absolute URL.
      # Only #scheme:, #host.tld, or #/path-that-looks-like-a-real-path with a dot are malformed.
      # #scheme:, #host.tld(/...), or #/deep/path — but NOT a bare SPA route like #/portal or
      # #/signup (Ghost portal / hash routers). A real path fragment has a dot or 2+ segments.
      'HASH_URL':   _r2.compile(r'^#(?:https?:|[a-z0-9-]+\.[a-z]{2,})', _r2.I),
      'NO_SCHEME':  _r2.compile(r'^www\.', _r2.I),
      # LOCAL_LEAK is a parsed-host test now (_local_leak, JP-05), not a pattern; see the loop below
    }
    hits, examples, per_page = _C2(), {}, _C2()
    # PLACEHOLDER "#" LINKS (a bathroom-remodel site, 2026-08-02): the bare-# exemption is for INTERACTION
    # buttons (Book Now, menu toggles). A bare-# link whose TEXT is a page noun — "Privacy
    # Policy", "Terms of Service", a service name — is a nav link never wired up: the destination
    # does not exist. Flag those; keep short/action text exempt.
    # Ghost Members portal buttons are href="#/portal/signup" intercepted by portal.min.js —
    # working CTAs, not broken links (a Ghost blog, 2026-08-02; same class as the Webflow modal
    # exemption). Only exempt when the Ghost portal script is actually present.
    _ghost_portal = any('tryghost/portal' in _h or '/portal.min.js' in _h for _h in _htmls.values())
    _PLACEHOLDER_TXT = _r2.compile(r'privacy|terms|sitemap|policy', _r2.I)
    _ph_links = {}
    for u, h in _htmls.items():
        # <noscript> fallback widgets (Shopify country/currency selector: every country as
        # href="#") are not navigation — 6,641 FPs on a Shopify store 2026-08-02. Scan visible markup only.
        h = _r2.sub(r'(?is)<noscript[^>]*>.*?</noscript>', ' ', h)
        # ONLY <a href> — a <link>/<base> href is not navigation. And protocol-relative //host is
        # legitimate on stylesheets/assets, so PROTO_REL was removed from BAD entirely (2026-07-31).
        for _mh in _r2.finditer(r'(?is)<a\b[^>]*?\bhref=["\']([^"\']*)["\'][^>]*>(.*?)</a>', h):
            href, _atext = _mh.group(1).strip(), _r2.sub(r'<[^>]+>|\s+', ' ', _mh.group(2)).strip()
            for k, pat in BAD.items():
                if pat.search(href):
                    hits[k] += 1; per_page[u] += 1
                    examples.setdefault(k, href[:76])
            if _local_leak(href, site_host):
                hits['LOCAL_LEAK'] += 1; per_page[u] += 1
                examples.setdefault('LOCAL_LEAK', href[:76])
            if href == '#' and _atext and (
                    _PLACEHOLDER_TXT.search(_atext) or len(_atext.split()) >= 3):
                _ph_links.setdefault(_atext[:40], 0)
                _ph_links[_atext[:40]] += 1
    print(f"\n10. MALFORMED HREFS (valid HTML, navigates nowhere) — live sample of {len(_htmls)} page(s)")
    if len(_ph_links) > 30:
        print(f"   (note: {len(_ph_links)} distinct href=\"#\" texts — a selector/list widget, "
              "not placeholder navigation; not flagged)")
        _ph_links = {}
    if _ph_links:
        print(f"   ⚠️ {sum(_ph_links.values())} PLACEHOLDER link(s): href=\"#\" on nav-style link text —")
        print("      these look like links to real pages but go nowhere (the pages were never built):")
        for _txt10, _n10 in sorted(_ph_links.items(), key=lambda x: -x[1])[:8]:
            print(f"        \"{_txt10}\" ({_n10}x)")
        print("      Build the pages (privacy/terms at minimum) or remove the links.")
    if hits:
        critical = True
        for k, n in hits.most_common():
            print(f"   🚩 {k}: {n} link(s)   e.g. {examples[k]}")
        _pp = per_page.most_common()
        if len(_pp) > 1 and min(c for _, c in _pp) >= max(c for _, c in _pp) * 0.8:
            print(f"   ⚠️ SAME count on every sampled page ({[c for _,c in _pp]}) = GLOBAL TEMPLATE bug;")
            print("      one template fix repairs the whole site.")
        print("   HASH_URL: browser treats it as a same-page fragment -> click appends garbage to the")
        print("   URL and nothing loads; crawlers follow NO link -> targets become ORPHANS.")
        print("   (bare '#'/'#anchor' EXEMPT — legitimate JS/Webflow interaction buttons)")
    else:
        print("   ok — no malformed hrefs in the sample")

    # 11. PLACEHOLDER CONTENT IN BODY / SLUG (check 8 covers titles only)
    PH = _r2.compile(r'voluptas|dolorum|distinctio|lorem ipsum|\bipsum\b|asdfasdf|'
                     r'your-text-here|placeholder-text|test-?page\b|dummy-content', _r2.I)
    slug_hits = [p['url'] for p in PGS if p.get('status') == 200 and PH.search(p['url'])]
    print("\n11. PLACEHOLDER CONTENT IN URL SLUGS / BODY")
    if slug_hits:
        critical = True
        print(f"   🚩 {len(slug_hits)} live URL(s) contain placeholder/lorem slugs:")
        for u in slug_hits[:6]: print(f"     {u}")
        print("   These are demo pages shipped to production — remove or 410 them, and remove from")
        print("   the sitemap (a lorem page in a submitted sitemap is a quality signal to Google).")
    else:
        print("   ok — no placeholder slugs")
    # FABRICATED SOCIAL PROOF (a bathroom-remodel site, 2026-08-02): testimonial photos served from stock/AI
    # face APIs (randomuser.me "Maria S. / 2 weeks ago") = fake reviews on the live site — an
    # FTC 16 CFR Part 465 exposure, and instantly recognisable to any competitor who checks.
    _FAKE_FACES = _r2.compile(r'randomuser\.me|thispersondoesnotexist|uifaces\.co|pravatar\.cc|'
                              r'ui-avatars\.com|fakeface', _r2.I)
    _fake_hits = {}
    for _u11, _h11 in _htmls.items():
        _n11 = len(_FAKE_FACES.findall(_h11))
        if _n11:
            _fake_hits[_u11] = _n11
    if _fake_hits:
        critical = True
        print(f"   🚩 FABRICATED TESTIMONIAL FACES: stock/AI face-API images "
              f"({sum(_fake_hits.values())} across {len(_fake_hits)} sampled page(s)):")
        for _u11, _n11 in list(_fake_hits.items())[:4]:
            print(f"     {_u11} ({_n11} images)")
        print("      Photos of 'customers' served from a random-face API = fake reviews on the")
        print("      live site. Legal exposure (FTC fake-testimonial rule) AND trivially")
        print("      detectable by anyone who inspects the page. Remove, or replace with real,")
        print("      permissioned customer content.")
    # BROKEN LOCALIZATION WIDGET (a Shopify store, 2026-08-02, owner: "why is a majority of the content
    # currencies?"): Shopify's country/currency form should collapse to a dropdown; with broken
    # theme JS/CSS it renders ~240 countries as VISIBLE page text — 952 "(USD $)" strings, half
    # the homepage's words. Count currency tokens OUTSIDE noscript in the visible layer.
    _CURR = _r2.compile(r'\((?:USD|EUR|GBP|CAD|AUD|JPY)\s*[$€£¥]\)')
    for _u11b, _h11b in _htmls.items():
        _vis11 = _r2.sub(r'(?is)<noscript[^>]*>.*?</noscript>', ' ', _h11b)
        _vis11 = _r2.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', _vis11)
        _nc11 = len(_CURR.findall(_vis11))
        if _nc11 > 100:
            critical = True
            print(f"   🚩 BROKEN LOCALIZATION WIDGET on {_u11b}: {_nc11} currency strings render "
                  "as VISIBLE page text — the country/currency picker is not collapsing (theme "
                  "JS/CSS failure), so a giant country list has become the page's main content "
                  "in both visitors' and Google's eyes. Fix the theme (or remove the selector).")
            break

    # DEV-ONLY CDN IN PRODUCTION (same run): cdn.tailwindcss.com is vendor-documented as
    # development-only — a render-blocking runtime compiler shipped to visitors.
    _dev_cdn = [u for u, h in _htmls.items() if 'cdn.tailwindcss.com' in h]
    if _dev_cdn:
        print(f"   ⚠️ DEVELOPMENT-ONLY CDN in production: cdn.tailwindcss.com on "
              f"{len(_dev_cdn)} sampled page(s) — the vendor's docs say never to ship this; it "
              "compiles styles in the visitor's browser on every load. Build the CSS instead.")

    # 12. HOST DUPLICATION — apex AND www both serving 200 with no cross-redirect.
    # An independent critique (2026-07-30) called this "the site's #1 fault, never stated":
    # a cleaning-company site served the ENTIRE site on both hosts (239 non-www + 214 www crawled, ~180 pages
    # counted twice), canonical said www, sitemap+internal links said apex. Every headline stat in
    # the report was inflated ~1.66x and Google got conflicting signals on every URL.
    print("\n12. HOST DUPLICATION (apex vs www both live)")
    _h = site_host if 'site_host' in dir() else ''
    _apex = _r2.sub(r'^www\.', '', _h) if _h else ''
    if _apex and not LIVE_OK:
        print("   UNKNOWN — host unreachable from here; apex/www duplication not probed this run")
    elif _apex:
        _res = {}
        for _v in (_apex, 'www.' + _apex):
            try:
                # This private opener BYPASSED the CRAWL_PROXY opener installed at module scope, so on a
                # walled host it egressed from the crawl host and read 202 for BOTH variants while the proxied
                # crawl read 200 (a garage-door site, 2026-09-22, run 1 AND run 2). Route it too.
                _ph = [_ur2.ProxyHandler({'http': _CRAWL_PROXY, 'https': _CRAWL_PROXY})] if _CRAWL_PROXY else []
                _o = _ur2.build_opener(*_ph, _NR, _ur2.HTTPSHandler(context=_ctx))
                _r3 = _o.open(_ur2.Request(f'https://{_v}/', method='HEAD', headers=_UA), timeout=15)
                _res[_v] = (_r3.status, '')
            except _ur2.HTTPError as e:
                _res[_v] = (e.code, e.headers.get('Location', ''))
            except Exception as e:
                _res[_v] = (None, str(e)[:40])
        for _v, (_c3, _loc) in _res.items():
            print(f"   https://{_v}/  ->  {_c3}{('  ->  ' + _loc) if _loc else ''}")
        _both200 = all(v[0] == 200 for v in _res.values())
        # non-200/301 statuses (202 challenge screens, 403 bot-walls, None) mean we measured a
        # WAF, not the site — say UNKNOWN, never "both live" (a law-firm site, 2026-08-01: SiteGround
        # 202 sg-captcha produced a self-contradicting "served twice ... NOT duplication" finding)
        # NXDOMAIN is its own verdict (a bathroom-remodel site, 2026-08-02): a www host that does not RESOLVE
        # means only ONE host exists = clean, not "unknown/blocked" — and the canned duplication
        # title shipped over an UNKNOWN body as a fabricated client finding.
        # NOTE: _res stores str(e)[:40] — the error is TRUNCATED ("Name or servic"), so match
        # the prefix, not the full phrase
        _nx12 = [k for k, v in _res.items()
                 if v[0] is None and ('Name or servic' in (v[1] or '')
                                      or 'getaddrinfo' in (v[1] or '')
                                      or 'Errno -2' in (v[1] or ''))]
        # 307/303 are ordinary redirects too (a pest-control site, 2026-08-06: apex 307s to www —
        # a clean single-host setup — and this allowlist omitted 307, so the check declared
        # "a bot challenge or block interfered" about a perfectly normal redirect and went
        # UNKNOWN. An allowlist that forgets a member is the denylist bug inverted; enumerate
        # every non-error status here, not just the common ones.
        _blocked12 = any(v[0] not in (200, 301, 302, 303, 307, 308) for k, v in _res.items()
                         if k not in _nx12)
        if _nx12 and not _blocked12:
            print(f"   ok — only one host exists ({[k for k in _res if k not in _nx12][0]}); "
                  f"the other ({_nx12[0]}) does not resolve at all. No duplication possible. "
                  "(Optional tidy-up: add DNS for the unused variant and 301 it, so typed-in "
                  "www/apex addresses still land.)")
        elif _blocked12:
            _sts12 = "; ".join(f"{k} answered {v[0] if v[0] else 'nothing'}" for k, v in _res.items())
            # §67 a UK Shopify store, 2026-08-06: UNKNOWN used to be a TERMINAL state — everything
            # non-200 was called "a bot challenge or block" and the whole line was suppressed
            # downstream. That buried a REAL, shippable client problem: www.<store>.co.uk was a
            # dangling CNAME to shops.myshopify.com serving a CERT THAT EXPIRED 2025-11-15, so
            # every visitor who types "www." gets a browser security warning. Suppression must
            # discard the unproven CLAIM, not the INVESTIGATION. Classify the failure: a TLS
            # error or a dead host variant is its own finding, independent of duplication.
            # ACTIVELY RE-PROBE with browser-strict TLS verification. The crawl path uses an
            # unverified SSL context (right for crawling — a bad cert must not abort an audit),
            # but that HIDES the exact failure a real visitor gets: that store's www variant
            # reported "403" here while a browser gets CERTIFICATE_VERIFY_FAILED on an expired
            # cert. Escalation means MEASURE the anomaly, not just advise someone to.
            import ssl as _ssl12
            _tls12 = {}
            for _k12, _v12 in _res.items():
                if _v12[0] in (200, 301, 302, 303, 307, 308):
                    continue      # this variant is healthy; nothing to diagnose
                _errtxt = str(_v12[1] or '')
                if not _r2.search(r'certificate|CERTIFICATE_VERIFY|SSL|TLS|hostname mismatch|'
                                  r'self.signed', _errtxt, _r2.I):
                    try:
                        _ur2.urlopen(_ur2.Request(f'https://{_k12}/', headers=_UA), timeout=15,
                                     context=_ssl12.create_default_context())
                        continue          # strict TLS is fine — a genuine block/error, not a cert
                    except Exception as _te12:
                        _errtxt = str(_te12)
                        if not _r2.search(r'certificate|CERTIFICATE_VERIFY|SSL|TLS|'
                                          r'hostname mismatch|self.signed', _errtxt, _r2.I):
                            continue      # still not TLS — leave it as UNKNOWN/blocked
                _tls12[_k12] = (_v12[0], _errtxt)
            if _tls12:
                critical = True
                for _k12, _v12 in _tls12.items():
                    print(f"   🚩 {_k12} has a BROKEN HTTPS CERTIFICATE — the padlock fails: "
                          f"{str(_v12[1])[:120]}")
                print("      Anyone who types this address gets a full-page browser security")
                print("      warning (\"Your connection is not private\") before they see anything.")
                print("      Most people back out at that screen. This commonly happens when a")
                print("      hostname still points at an old provider after a site move.")
                print("      FIX: either point this hostname at the current site (and 301 it to the")
                print("      main address), or remove its DNS record so it stops resolving at all.")
                print(f"   ⚠️ (duplication itself remains UNKNOWN this run: {_sts12})")
            else:
                print(f"   ⚠️ UNKNOWN — a bot challenge or block interfered ({_sts12}); whether the "
                      "site is served twice cannot be judged from here. (Operator: re-probe via the "
                      "curl/residential-proxy transport.)")
                # ESCALATION QUEUE: record the anomaly so it is re-examined rather than dropped.
                print("   ⚠️ ESCALATE — a host variant did not answer normally. Before dismissing "
                      "this as a WAF, confirm the hostname resolves, its certificate is valid, and "
                      "it belongs to this site (dangling CNAMEs to an old host look identical to a "
                      "block from here).")
        # a crawl containing BOTH hosts is corroborating evidence + inflates every count.
        # SUBDOMAIN-AWARE (2026-08-04): the crawler now crawls legit subdomains (studio./uat./blog.),
        # so restrict the apex-vs-www duplication signal to ONLY the apex and www variants of the seed —
        # a separate subdomain is NOT www/apex duplication and must not be framed as a "301 hop" defect.
        # HTTP VARIANTS TOO (clean-agent catch 2026-08-05): this check only ever probed the two
        # HTTPS hosts, so a site reachable at http://apex, http://www, https://apex AND https://www
        # was reported as "two addresses" when it is actually FOUR. An http:// that serves 200
        # instead of redirecting to https is its own defect (insecure copy of every page).
        _http_live = []
        for _hv in (_apex, 'www.' + _apex):
            try:
                _o4 = _ur2.build_opener(*_PXH, _NR, _ur2.HTTPSHandler(context=_ctx))
                _r4 = _o4.open(_ur2.Request(f'http://{_hv}/', method='HEAD', headers=_UA), timeout=12)
                if _r4.status == 200:
                    _http_live.append(f'http://{_hv}/')
            except Exception:
                pass
        # HSTS CHECK (owner catch 2026-08-05). A site can serve 200 on port 80 to a raw client while
        # NO REAL BROWSER ever reaches it: Strict-Transport-Security tells browsers to rewrite
        # http:// to https:// THEMSELVES before the request leaves the machine. A moving-company site sends
        # `max-age=31536000; includeSubDomains; preload` — the owner typed the http URL and watched
        # it go straight to https, while our curl (which ignores HSTS) saw a bare 200. Reporting
        # "type this and watch it fail" for something that demonstrably does NOT fail is the exact
        # failure mode §47 exists to prevent — the client disproves the finding in five seconds.
        _hsts = ""
        try:
            _rh = _ur2.urlopen(_ur2.Request(f'https://{_apex}/', headers=_UA), timeout=12)
            _hsts = (_rh.headers.get('Strict-Transport-Security') or '')
        except Exception:
            pass
        if _http_live and _hsts:
            print(f"   note: http:// answers on the origin ({', '.join(_http_live)}) but the site sends "
                  f"HSTS ({_hsts.strip()[:60]}), so BROWSERS upgrade http->https themselves and no real "
                  "visitor lands on the insecure copy. Worth a server-side 301 for non-browser clients "
                  "and first-ever visits, but NOT a live exposure — do not tell the client to 'watch it "
                  "fail', because it will not.")
        elif _http_live:
            critical = True
            print(f"   🚩 the insecure http:// address also serves the site with NO redirect to https "
                  f"({', '.join(_http_live)}) and NO HSTS header, so browsers do not upgrade it either. "
                  f"Every page is reachable at {2 + len(_http_live)} addresses, and the http:// copies "
                  "are served without encryption.")
            print("      FIX: redirect ALL http:// traffic to the chosen https:// host server-side.")
        _apexset12 = {_apex, 'www.' + _apex}
        _hosts = _C2(h for p in PGS if p.get('status') == 200
                     for h in [_rawhost(p['url'])] if h in _apexset12)
        if _blocked12 or (_nx12 and not _blocked12):
            pass                          # verdict already printed above
        elif _both200:
            critical = True
            # SCOPE PROOF (owner catch 2026-08-05): probing only the two HOMEPAGES and then
            # asserting "the whole site is served twice" is an unfounded generalisation — a site can
            # answer on both hosts at / while inner pages 404 or redirect on the www variant. SAMPLE
            # real inner pages before making a sitewide claim, and state the sample size in the
            # finding. NOTE the probe MUST send Accept-Encoding-aware requests: comparing a gzipped
            # body against an uncompressed one manufactures fake "differ" results (same catch).
            _dup_paths = [_r2.sub(r'^https?://[^/]+', '', p['url']) for p in PGS
                          if p.get('status') == 200 and not p.get('redirected')
                          and _r2.sub(r'^https?://[^/]+', '', p['url']) not in ('', '/')]
            _dup_paths = sorted(set(_dup_paths))[:8]
            _same, _notsame, _unreach = 0, 0, 0
            for _pt in _dup_paths:
                try:
                    # Compare DECODED bodies. Asking for identity encoding is not always honoured
                    # (Sucuri/CDNs gzip anyway), and comparing a gzipped body to a plain one
                    # manufactures fake "differ" results — the exact error that nearly made me
                    # retract a TRUE finding (2026-08-05). Decompress whatever comes back.
                    import gzip as _gz, zlib as _zl, io as _io
                    _b = []
                    for _hv in (_apex, 'www.' + _apex):
                        _rq = _ur2.Request(f'https://{_hv}{_pt}',
                                           headers=dict(_UA, **{'Accept-Encoding': 'gzip, deflate'}))
                        _rs = _ur2.urlopen(_rq, timeout=12)
                        _raw = _rs.read()
                        _enc = (_rs.headers.get('Content-Encoding') or '').lower()
                        try:
                            if 'gzip' in _enc:
                                _raw = _gz.decompress(_raw)
                            elif 'deflate' in _enc:
                                _raw = _zl.decompress(_raw, -_zl.MAX_WBITS)
                        except Exception:
                            pass
                        _b.append(_raw)
                    if _b[0] == _b[1]:
                        _same += 1
                    else:
                        _notsame += 1
                except Exception:
                    _unreach += 1
            _tested = _same + _notsame
            if _tested and _same == _tested:
                print(f"   🚩 BOTH host variants return 200 with NO redirect between them, and {_same} of "
                      f"{_tested} inner pages tested serve BYTE-IDENTICAL content on both — the site is")
                print("      served twice. Google must guess which is canonical, and link signals split.")
            elif _tested:
                print(f"   ⚠️ Both host variants answer at '/', but of {_tested} inner pages tested only "
                      f"{_same} are identical on both hosts ({_notsame} differ). This is NOT a clean")
                print("      sitewide duplicate — check which sections exist on both before acting.")
            else:
                print("   ⚠️ Both host variants answer at '/', but no inner page could be tested from "
                      "here, so whether the DUPLICATION IS SITEWIDE is UNKNOWN — do not claim it is.")
            if len(_hosts) > 1:
                _uniq = len({_r2.sub(r'^https?://(www\.)?', '', p['url']).rstrip('/')
                             for p in PGS if p.get('status') == 200})
                print(f"      Crawl contains BOTH hosts {dict(_hosts)} -> {sum(_hosts.values())} rows but")
                print(f"      only ~{_uniq} UNIQUE pages. Every count in this report is inflated.")
            print("   FIX: pick ONE host, 301 the other to it server-side, and make canonical tags,")
            print("   internal links and the sitemap all agree with that choice.")
        elif len(_hosts) > 1:
            print(f"   ⚠️ NOT duplication — one variant correctly redirects. Crawl mixes hosts")
            print(f"      {dict(_hosts)} only because links/sitemap reference the non-canonical host,")
            print("      costing a 301 hop per request. Tidy-up, not a critical fault.")
        else:
            print("   ok — one canonical host")
    else:
        print("   (site host unavailable)")

    # 13. SITEMAP INTEGRITY — the sitemap is what you SUBMIT to Google; junk in it is a direct
    # quality signal. Independent critique (2026-07-30): a cleaning-company site's submitted sitemap contained all
    # 6 auth routes AND the live lorem page, sat on the NON-canonical host, and omitted 35 live pages.
    print("\n13. SITEMAP INTEGRITY")

    def _fetch13(_u13, _timeout=20):
        """-> (status, body). Never raises; None status = transport failure (§65 degrade-to-UNKNOWN).
        Follows redirects like a visitor's browser so a 301->200 page reads as reachable."""
        try:
            _r13 = _ur2.urlopen(_ur2.Request(_u13, headers=_UA), timeout=_timeout)
            return getattr(_r13, 'status', _r13.getcode()), _r13.read(400_000).decode('utf-8', 'replace')
        except Exception as _e13:
            _b13 = ''
            try:
                _b13 = _e13.read(400_000).decode('utf-8', 'replace')
            except Exception:
                pass
            return getattr(_e13, 'code', None), _b13

    _sm_urls, _seen_sm, _q = set(), set(), []
    _rb = ''
    try:
        _rb = _ur2.urlopen(_ur2.Request(f'https://{site_host}/robots.txt', headers=_UA), timeout=15
                           ).read().decode('utf-8', 'replace')
    except Exception:
        pass
    _q = _r2.findall(r'(?i)^\s*Sitemap:\s*(\S+)', _rb, _r2.M) or \
         [f'https://{site_host}/sitemap.xml', f'https://{site_host}/sitemap_index.xml']
    # TRANSPORT-AWARE sitemap walk (a cleaning-company site, 2026-08-07). The old loop did `except: continue`,
    # so a sitemap that could not be FETCHED was indistinguishable from one that does not EXIST —
    # and the check then printed the confident negative "no sitemap URLs found". On
    # that site (shared hosting) our own crawl concurrency pushed the host into HTTP 508
    # "Resource Limit Is Reached"; every sitemap fetch 508'd and the report shipped "Your site has
    # no sitemap at all" while Yoast was publishing 4 sitemaps / 59 URLs the whole time. Verified
    # by hand seconds later: sitemap_index.xml = 200, and /sitemap.xml 301s to it.
    # §55/§65: a blocked or throttled fetch degrades to UNKNOWN, never to "absent".
    _sm_transient = {}   # url -> last transient status (5xx/429/timeout) that blocked measurement
    _sm_any_ok = False
    while _q:
        _u = _q.pop()
        if _u in _seen_sm or len(_seen_sm) > 25: continue
        _seen_sm.add(_u)
        _x, _st13 = None, None
        # transient statuses deserve a retry — 508/503/429 are load signals, not answers
        for _try13 in range(3):
            _st13, _body13 = _fetch13(_u, 20)
            if _st13 == 200 and _body13:
                _x = _body13
                break
            if _st13 in (408, 429, 500, 502, 503, 504, 508, None):
                _time13.sleep(4 * (_try13 + 1))
                continue
            break   # a real answer (404/403/410): stop retrying
        if _x is None:
            if _st13 in (408, 429, 500, 502, 503, 504, 508, None):
                _sm_transient[_u] = _st13
            continue
        _sm_any_ok = True
        for _loc in _r2.findall(r'<loc>\s*([^<\s]+)\s*</loc>', _x):
            _loc = _sm_unescape(_loc)
            (_q if _sm_is_index(_loc) else _sm_urls).append(_loc) if _sm_is_index(_loc) \
                else _sm_urls.add(_loc.split('#')[0].rstrip('/'))
    # robots.txt EXISTENCE (a cleaning-company site, 2026-08-02): /robots.txt 404'd and nothing tested it — the
    # sitemap existed but was undeclared to crawlers
    if not _rb.strip():
        try:
            _rb_st = _ur2.urlopen(_ur2.Request(f'https://{site_host}/robots.txt',
                                  headers=_UA), timeout=12).status
        except Exception:
            _rb_st = None
        if _rb_st != 200:
            print("   ⚠️ NO robots.txt at all (returns an error) — crawlers get no sitemap")
            print("      declaration and no crawl rules. Publish one with a Sitemap: line.")
    if not _sm_urls and _sm_transient:
        # measured NOTHING — say so, and never let the sidecar mint "no sitemap at all" from this
        _codes13 = sorted({str(v) for v in _sm_transient.values()})
        print(f"   ⚠️ UNKNOWN — the sitemap could not be fetched from here "
              f"({', '.join(_codes13)} on {len(_sm_transient)} probe(s), after retries). "
              f"This is a TRANSPORT result, not a finding: whether a sitemap exists cannot be "
              f"judged from this run. (Operator: re-probe when the host is idle, or via the "
              f"a second crawl host / residential proxy. HTTP 508 = the host's own resource cap.)")
    elif not _sm_urls:
        print("   ⚠️ no sitemap URLs found (robots.txt Sitemap: line missing and /sitemap.xml empty)")
    else:
        _AUTHRE = AUTH if 'AUTH' in dir() else _r2.compile(r'/(log-?in|sign-?up|user-account)', _r2.I)
        _bad_auth = sorted(u for u in _sm_urls if _AUTHRE.search(u))
        _bad_ph   = sorted(u for u in _sm_urls if PH.search(u)) if 'PH' in dir() else []
        _sm_hosts = _C2(_rawhost(u) for u in _sm_urls)
        _crawled  = {p['url'].split('#')[0].rstrip('/') for p in PGS if p.get('status') == 200}
        # count redirect DESTINATIONS as reached, so stale entries report once (as redirects, below)
        _crawled |= {p['final_url'].split('#')[0].rstrip('/') for p in PGS
                     if p.get('status') == 200 and p.get('final_url')}
        _norm     = lambda s2: _r2.sub(r'^https?://(www\.)?', '', s2).rstrip('/')
        _cn       = {_norm(u) for u in _crawled}
        _missing  = sorted({_norm(u) for u in _sm_urls} - _cn)
        # SITEMAP URLS THAT REDIRECT (added 2026-07-31, a roofing site): the sitemap submitted
        # ~21 /commercial-roofing/<city>/ URLs that all 301 to /roofing/… — some to a DIFFERENT
        # city (CMS fuzzy-slug guess). A sitemap should list only final, 200-loading addresses.
        # The crawl seeds from the sitemap as-listed, so these have crawl rows we can read directly.
        _smn = {_norm(u) for u in _sm_urls}
        _sm_redirs = []
        for p in PGS:
            if not p.get('redirected') or _norm(p.get('url', '')) not in _smn:
                continue
            _pa = _r2.sub(r'https?://[^/]+', '', p['url']).rstrip('/')
            _pb = _r2.sub(r'https?://[^/]+', '', p.get('final_url') or '').rstrip('/')
            if _pa != _pb:                      # path actually changed — not a www/slash variant
                _sm_redirs.append((p['url'], p.get('final_url', '')))
        print(f"   sitemap URLs: {len(_sm_urls)}  ·  hosts in sitemap: {dict(_sm_hosts)}")
        if _bad_auth:
            critical = True
            print(f"   🚩 {len(_bad_auth)} AUTH route(s) SUBMITTED in the sitemap:")
            for u in _bad_auth[:6]: print(f"     {u}")
            print("      You are actively asking Google to index your login pages. Remove + noindex.")
        if _bad_ph:
            critical = True
            print(f"   🚩 {len(_bad_ph)} PLACEHOLDER/lorem URL(s) submitted:")
            for u in _bad_ph[:4]: print(f"     {u}")
        if site_host not in _sm_hosts and _sm_hosts:
            print(f"   🚩 sitemap lists host {list(_sm_hosts)} but the site/canonical host is "
                  f"{site_host} — the sitemap contradicts your canonical choice.")
            critical = True
        # TEMPLATE PLACEHOLDERS submitted (a CRM-vendor site, 2026-08-02): /crm-case-studies/[company]/
        # (bracket or %5B forms) return 200 while rendering "page doesn't exist" — soft-404s IN
        # the sitemap
        _placeholders13 = sorted(u for u in _sm_urls if '%5b' in u.lower() or '[' in u)
        if _placeholders13:
            critical = True
            print(f"   🚩 {len(_placeholders13)} TEMPLATE-PLACEHOLDER URL(s) submitted in the "
                  "sitemap (unreplaced [brackets] from the site generator) — they load a 'page "
                  "doesn't exist' screen while telling Google 200 OK:")
            for u in _placeholders13[:4]:
                print(f"     {u}")
            print("      Remove them from the sitemap and fix the generator template.")
        if _sm_redirs:
            critical = True
            print(f"   🚩 {len(_sm_redirs)} sitemap URL(s) REDIRECT instead of loading — the sitemap")
            print("      is out of date: it submits old addresses to Google. Replace each with the")
            print("      address it now forwards to (and verify the destination is the RIGHT page —")
            print("      CMS fuzzy-matching can forward a city page to a different city):")
            for _u6, _f6 in _sm_redirs:     # ALL pairs — completeness rule, no truncation
                print(f"     {_u6}")
                print(f"        -> now forwards to: {_f6}")
        if _missing:
            # §62 — DETECTING ≠ EVALUATING. "Not reached by the crawl" is a fact about OUR crawl,
            # not about the page. A URL can be absent because the crawl hit a transient 403/timeout
            # (a restoration-company site: /insurance/supplements/ was a live 200 page the crawl 403'd),
            # or because it is genuinely orphaned/stale. Those need OPPOSITE advice, so GO LOOK
            # before saying which. Only URLs that actually fail for a visitor are a client finding;
            # a live page merely missed by the crawl is OUR measurement gap and must not ship.
            _orphan_live, _orphan_dead, _orphan_unk = [], [], []
            # SAMPLE + PACE + TRANSIENT-AWARE (a Shopify store, 2026-09-22): this loop probed
            # all 1,885 sitemap URLs a 300-page sample crawl had not reached, tripped Shopify's
            # per-IP velocity limit, and shipped "1,536 sitemap URLs do NOT load (HTTP 429)" as a
            # CRITICAL. A 429/403/503 is the WAF answering, not the page. Probe ≤40 sampled URLs,
            # 0.5 s apart, and file WAF/transport answers under UNKNOWN, never under dead.
            import random as _rnd13, time as _t13
            _probe13 = list(_missing)
            _sampled13 = len(_probe13) > 40
            if _sampled13:
                _rnd13.seed(13); _probe13 = _rnd13.sample(_probe13, 40)
                print(f"   (live-verifying a random sample of 40 of the {len(_missing)} sitemap URLs the crawl did not reach)")
            for u in _probe13:
                _pu = u if u.startswith("http") else "https://" + u
                try:
                    _st, _bd = _fetch13(_pu)
                except Exception:
                    _st, _bd = None, ""
                _t13.sleep(0.5)
                if _st is None:
                    _orphan_unk.append((u, "could not be fetched"))
                elif 200 <= _st < 300:
                    _orphan_live.append((u, _st))
                elif _st in (403, 405, 406, 408, 429, 500, 502, 503, 504, 508):
                    _orphan_unk.append((u, f"HTTP {_st} — a block/throttle or server hiccup, not verified"))
                else:
                    _orphan_dead.append((u, _st))
            if _orphan_dead:
                critical = True
                print(f"   🚩 {len(_orphan_dead)} sitemap URL(s) do NOT load for a visitor "
                      "(verified live just now) — the sitemap submits broken addresses to Google:")
                for u, st in _orphan_dead[:6]:
                    print(f"     {u}  -> HTTP {st}")
                print("      Remove them from the sitemap, or restore the pages.")
            if _orphan_live:
                # NOT a client finding — our crawl simply didn't reach a working page.
                print(f"   ok — {len(_orphan_live)} sitemap URL(s) were not reached by our crawl but "
                      "LOAD FINE when fetched directly (transient block/timeout during the crawl, "
                      "not a site defect):")
                for u, st in _orphan_live[:4]:
                    print(f"     {u}  -> HTTP {st} (live, no action needed)")
            if _orphan_unk:
                print(f"   ⚠️ UNKNOWN — {len(_orphan_unk)} sitemap URL(s) could not be verified from "
                      "here (transport failure); not judged:")
                for u, _r in _orphan_unk[:4]:
                    print(f"     {u}  -> {_r}")

    # 14. THIN CUSTOM-POST-TYPE / INDEX BLOAT — mass near-identical URLs under one path prefix.
    # LIVE CATCH (a fire-safety training site, 2026-07-31, found by an independent operator, NOT by this tool):
    # 8,340 indexable /<cpt>_student/<person-name>/ pages — each a past student's full name, HTTP 200,
    # robots index,follow, listed across 9 sitemaps, ~630-char bodies that are pure navigation. The
    # skill surfaced this only as raw log text, never as a titled finding a client would ever read.
    # Mass thin pages dilute crawl budget and site quality; person-name URLs also expose identities.
    print("\n14. THIN / MASS-DUPLICATE URL PREFIXES (index bloat)")
    # DATA SOURCE MATTERS: mass-thin pages are usually ORPHANED (nothing links to them), so a BFS
    # crawl never reaches them and counting crawl rows finds NOTHING. That site: crawl saw 370 pages
    # and zero /<cpt>_student/, while the SITEMAP declared 8,340 of them. Count the SITEMAP (the set
    # the owner submits to Google) and fall back to the crawl only if no sitemap is reachable.
    _pref = _C2()
    _words = {}
    _sm_all = set()
    try:
        _rb2 = _ur2.urlopen(_ur2.Request(f'https://{site_host}/robots.txt', headers=_UA), timeout=15
                            ).read().decode('utf-8', 'replace')
    except Exception:
        _rb2 = ''
    _q2 = _r2.findall(r'(?im)^\s*Sitemap:\s*(\S+)', _rb2) or [f'https://{site_host}/sitemap.xml',
                                                                f'https://{site_host}/sitemap_index.xml']
    _seen2 = set()
    while _q2 and len(_seen2) < 30:
        _u2 = _q2.pop()
        if _u2 in _seen2:
            continue
        _seen2.add(_u2)
        try:
            _x2 = _ur2.urlopen(_ur2.Request(_u2, headers=_UA), timeout=20).read().decode('utf-8', 'replace')
        except Exception:
            continue
        for _loc2 in _r2.findall(r'<loc>\s*([^<\s]+)\s*</loc>', _x2):
            _loc2 = _sm_unescape(_loc2)
            if _sm_is_index(_loc2):
                _q2.append(_loc2)
            else:
                _sm_all.add(_loc2)
    _src = 'sitemap' if len(_sm_all) > len(PGS) else 'crawl'
    if _src == 'sitemap':
        # NEVER fabricate word counts (fixed 2026-07-31, a roofing site): this branch used to
        # append 0 for every sitemap URL and declare _thin unconditionally — the report then told
        # an owner his ~270 real blog posts (886-1,600 words each, verified) were "avg 0 words
        # each … noindex the prefix". Word counts must come from MEASUREMENT: crawl rows where the
        # URL was fetched, else a small live sample per candidate prefix.
        _crawl_wc = {}
        for p in PGS:
            if p.get('status') == 200:
                _crawl_wc[p['url'].split('#')[0].rstrip('/')] = p.get('word_count') or 0
        _sm_by_seg = {}
        for _u3 in _sm_all:
            _pt = _r2.sub(r'https?://[^/]+', '', _u3).strip('/')
            _seg = _pt.split('/')[0] if _pt else ''
            if _seg and '.' not in _seg:
                _pref[_seg] += 1
                _sm_by_seg.setdefault(_seg, []).append(_u3)
                _k3 = _u3.split('#')[0].rstrip('/')
                if _k3 in _crawl_wc:
                    _words.setdefault(_seg, []).append(_crawl_wc[_k3])
    else:
        for p in PGS:
            if p.get('status') != 200:
                continue
            # exclude non-HTML assets (epp 2026-08-04: 112 /wp-content/*.jpg counted as "near-
            # identical pages, 83% of the site" — images are not thin HTML pages)
            _ct14 = (p.get('content_type') or '').lower()
            _u14x = (p.get('url') or '').lower()
            if (_ct14 and 'html' not in _ct14) or re.search(r'\.(jpe?g|png|gif|svg|webp|avif|css|js|ico|woff2?|pdf|xml|json)(\?|$)', _u14x):
                continue
            _raw = _r2.sub(r'https?://[^/]+', '', p['url'])
            for _lp14 in ('/de/', '/es/', '/fr/', '/nl/', '/hi/', '/pt-br/', '/af/', '/pt/', '/it/'):
                if _raw.lower().startswith(_lp14):
                    _raw = _raw[len(_lp14) - 1:]
                    break
            _path, _sep, _qs = _raw.partition('?')
            _pt = _path.strip('/')
            _seg = _pt.split('/')[0] if _pt else ''
            if '.' in _seg:
                continue
            # a query string makes it a FACET/parameter variant of its path — bucket those
            # separately (e.g. "/blog?<query>") so 511 `?category=` URLs group instead of
            # fragmenting into 511 unique "prefixes" and defeating every threshold.
            _seg = (f"{_seg or '(root)'}?<query>" if _qs else _seg)
            if not _seg:
                continue
            _pref[_seg] += 1
            _words.setdefault(_seg, []).append(p.get('word_count') or 0)
    print(f"   (counting {len(_sm_all) if _src=='sitemap' else len(PGS)} URLs from the {_src})")
    _tot = max(1, sum(_pref.values()))
    _flagged = []
    for _seg, _n in _pref.most_common(12):
        _w = _words.get(_seg) or []
        # sitemap-source prefix with too few measured pages: fetch up to 3 live and COUNT
        if _src == 'sitemap' and len(_w) < 3 and _n >= 25:
            for _u4 in (_sm_by_seg.get(_seg) or [])[:3]:
                try:
                    _h4 = _ur2.urlopen(_ur2.Request(_u4, headers=_UA), timeout=20
                                       ).read().decode('utf-8', 'replace')
                    _b4 = _r2.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', '', _h4)
                    _w.append(len(_r2.sub(r'<[^>]+>', ' ', _b4).split()))
                except Exception:
                    pass
        _avg = sum(_w) // max(1, len(_w)) if _w else None
        # a prefix is bloat when it dominates the site AND its pages are MEASURED thin;
        # if we could not measure any page, we cannot call it thin (two-conditions rule)
        _thin = (_avg is not None and _avg < 350)
        # MASS-IDENTICAL trigger (added 2026-07-31): thin-ness is only ONE way a prefix becomes bloat.
        # a law-firm site had 511 `?category=` facet URLs — 51% of the crawl, every one serving the SAME
        # 829-word page and canonical -> /blog. Not thin at all, so the thin test stayed silent while
        # half the crawl was duplicate. Detect repetition of the SAME word_count/title instead.
        _identical = False
        if _src == 'crawl':
            _sig = _C2((p.get('word_count') or 0, (p.get('title') or '').strip())
                       for p in PGS if p.get('status') == 200
                       and (lambda _r: (_r.partition('?')[0].strip('/').split('/')[0] or '(root)')
                            + ('?<query>' if _r.partition('?')[2] else ''))(
                                _r2.sub(r'https?://[^/]+', '', p['url'])) == _seg)
            if _sig:
                _top_n = _sig.most_common(1)[0][1]
                _identical = _top_n >= 25 and _top_n >= _n * 0.8
        if _n >= 25 and (_n * 100 // _tot) >= 25 and (_thin or _identical):
            _flagged.append((_seg, _n, _avg))
    if _flagged:
        critical = True
        for _seg, _n, _avg in _flagged:
            _wtxt = f"avg {_avg} words each (measured)" if _avg is not None else "size unmeasured"
            print(f"   🚩 /{_seg}/ — {_n} pages ({_n * 100 // _tot}% of the site), {_wtxt}.")
            if _avg is not None and _avg >= 350:
                print(f"      (flagged as MASS-IDENTICAL, not thin: these pages are substantial but")
                print(f"       near-duplicates of each other — e.g. filter/facet URLs of one page.)")
            # list the member URLs so the sidecar can enumerate them (owner rule: document affected
            # pages IN FULL — a moving-company site, 2026-08-04: the finding cited "47 pages" but listed none)
            _mem14 = [p.get('url') for p in PGS
                      if p.get('status') == 200 and not p.get('redirected')
                      and re.sub(r'https?://[^/]+', '', (p.get('url') or '')).strip('/').split('/')[0].lower() == _seg.split('?')[0].lower()]
            for _u14 in _mem14[:60]:
                print(f"        MEMBER: {_u14}")
        print("      Mass near-identical thin pages under one prefix = index bloat: they consume")
        print("      crawl budget, dilute site quality signals, and rarely rank for anything.")
        # only mention identity exposure when the slugs actually LOOK like person names —
        # this sentence shipped verbatim to a roofing company (2026-07-31) as leftover
        # boilerplate from the student-records case above
        _slugs = [(_sm_by_seg.get(_seg) or [''])[0] for _seg, _n, _a in _flagged] if _src == 'sitemap' else []
        _namey = sum(1 for _u5 in _slugs
                     if _r2.fullmatch(r'[a-z]{3,12}(-[a-z]{3,12}){1,2}', _u5.rstrip('/').rsplit('/', 1)[-1] or ''))
        if _namey and _namey >= len(_slugs) // 2:
            print("      These URLs look like person names — records pages (students, members,")
            print("      listings) also publish identities.")
        print("      FIX: noindex the prefix (or gate it), drop it from the")
        print("      sitemap, and keep only pages that serve a real search intent.")
    else:
        print("   ok — no single prefix dominates with thin pages")

    # 15. INTERNAL LINKS THAT REDIRECT — every click and every crawl costs an extra hop.
    # LIVE CATCH (an insurance-agency site, 2026-07-31, found by an independent operator): ~40% of crawled URLs
    # were `?hsLang=en` variants that 307 to their clean twin. The audit reported them only as a
    # misframed "79 duplicates with no canonical" finding while the header said "0 errors" — the real
    # defect (the site links to itself through redirects) was never stated.
    print("\n15. INTERNAL LINKS POINTING AT REDIRECTS")
    # SLASH-ONLY "redirects" are the crawler's own normalization, not a network hop (a law-firm site,
    # 2026-08-01: href="https://domain" (no slash) linked 817x was headlined as "9 URLs redirect,
    # linked 828x" — curl shows 0 redirects; a browser requests path / directly). Count a target
    # only when the PATH actually differs after slash normalization.
    def _real_redirect(p):
        if not p.get('redirected'):
            return False
        _a = (p.get('url') or '').split('#')[0]
        _b = (p.get('final_url') or '').split('#')[0]
        import urllib.parse as _up15
        _pa, _pb = _up15.urlparse(_a), _up15.urlparse(_b)
        if (_pa.path.rstrip('/') or '/') == (_pb.path.rstrip('/') or '/') and _pa.query == _pb.query \
           and (_pa.hostname or '').replace('www.', '') == (_pb.hostname or '').replace('www.', '') \
           and _pa.scheme == _pb.scheme:
            return False          # same page: trailing-slash / bare-host normalization only
        return True
    _redir_targets = {p['url'].split('#')[0].rstrip('/') for p in P if _real_redirect(p)}
    if _redir_targets:
        _linking = _C2()
        for p in P_content:
            for l in (p.get('internal_links') or []):
                _h = (l.get('href') or '').split('#')[0].rstrip('/')
                if _h in _redir_targets:
                    _linking[_h] += 1
        _tot_links = sum(_linking.values())
        _share = len(_redir_targets) * 100 // max(1, len(P))
        _top = _linking.most_common(1)[0][1] if _linking else 0
        _template = _top >= 10          # one URL linked 10+ times = a nav/footer template
        if _share >= 20 and _template:
            critical = True
            print(f"   🚩 {len(_redir_targets)} internal URL(s) redirect ({_share}% of everything crawled);")
            print(f"      they are linked {_tot_links} time(s) from this site's own pages.")
            for _u, _n in _linking.most_common(6):
                print(f"     {_n:>4}x  {_u}")
            print("      Every visitor click and every crawl of these pays an extra round trip, and")
            print("      link equity passes through a hop instead of landing directly. FIX at the")
            print("      TEMPLATE: link to the FINAL destination URL, not the redirecting one.")
        elif _tot_links >= 10 and _template:
            print(f"   ⚠️ {len(_redir_targets)} internal URL(s) redirect, linked {_tot_links}x "
                  f"({_share}% of the crawl) — a template links through redirects. Minor: fix the")
            print("      template to point at final URLs, but this is a tidy-up not a critical fault.")
            for _u, _n in _linking.most_common(3):
                print(f"     {_n:>4}x  {_u}")
        else:
            print(f"   ok — {len(_redir_targets)} redirecting URL(s), linked {_tot_links}x "
                  f"({_share}% of crawl). A few redirect hops are normal.")
    else:
        print("   ok — no internal links point at redirects")

    # 16. STRUCTURED-DATA CONSISTENCY (added 2026-07-31, a roofing site — found by a
    # clean-context critique agent, missed by check 3 which only tests PRESENCE): the homepage
    # carried TWO RoofingContractor nodes — plugin-generated vs hand-coded — with different
    # business names (one corrupted by a double-encoded "&amp;amp;" INSIDE the JSON string),
    # geo coordinates ~2.7km apart, and a hardcoded aggregateRating 4.8/500 (self-serving review
    # markup, ineligible for rich results since 2019). Conflicting nodes make Google pick one at
    # random or distrust both.
    print("\n16. STRUCTURED-DATA CONSISTENCY (homepage)")
    try:
        _hp = _ur2.urlopen(_ur2.Request(f'https://{site_host}/', headers=_UA), timeout=25
                           ).read().decode('utf-8', 'replace')
        _nodes = []
        for _blk in _r2.findall(r'(?is)<script[^>]*ld\+json[^>]*>(.*?)</script>', _hp):
            try:
                _d = json.loads(_blk.strip())
            except Exception:
                continue
            _lst = _d if isinstance(_d, list) else [_d]
            for _x in _lst:
                if isinstance(_x, dict):
                    _gr = _x.get('@graph')
                    _nodes += [_y for _y in _gr if isinstance(_y, dict)] if isinstance(_gr, list) else [_x]
        # SAME recognition set as check 3 (a law-firm site, 2026-08-01: 'LegalService' matched neither
        # suffix, so check 16 said "no business-entity schema" while check 3 said present —
        # and a self-authored 5/5 AggregateRating + 5 self-reviews sailed through unflagged)
        # §13: single source of truth — see _LOCAL_SUBTYPES at module scope. check 16 compares
        # business-ENTITY nodes, so a bare Organization counts as an entity here.
        _BIZ16 = _LOCAL_SUBTYPES | {'organization'}
        def _isbiz(n):
            _t = n.get('@type', '')
            _ts = _t if isinstance(_t, list) else [_t]
            return any(str(t).lower() in _BIZ16
                       or str(t).lower().endswith(('business', 'contractor', 'organization',
                                                   'service', 'agency', 'attorney', 'store'))
                       for t in _ts)
        _biz = [n for n in _nodes if isinstance(n, dict) and _isbiz(n)]
        _issues = []
        _names = {}
        for n in _biz:
            _nm = str(n.get('name') or '')
            if _nm:
                _key = _r2.sub(r'[^a-z0-9]', '', _nm.lower().replace('&amp;', 'and').replace('&', 'and'))
                _names.setdefault(_key, _nm)
            if '&amp;' in _nm or '&#' in _nm:
                _issues.append(f"business name contains a double-encoded HTML entity: {_nm!r} — "
                               "Google reads this literally; the entity name is corrupted")
            _ark = next((k for k in n if k.lower() == 'aggregaterating'), None)
            if _ark:
                _ar = n[_ark] if isinstance(n[_ark], dict) else {}
                _badkey = " (also note: the property is written '" + _ark + "' — wrong " \
                          "capitalization, an invalid name Google ignores outright)" \
                          if _ark != 'aggregateRating' else ""
                # a pest-control site (2026-08-05): this branch used to append "reads as fabricated
                # when no reviews appear on the page" on the mere PRESENCE of the property —
                # it never looked. That site's homepage shows "5.0 Google Reviews" + 3 named
                # testimonials, so the client read a claim their own homepage disproves. Same
                # defect class as the WP-version check (detect != evaluate): MEASURE, then assert.
                # NOTE we only have the RAW homepage here. On a client-rendered site the
                # reviews may be real and simply not in this HTML (that site: 0 raw hits,
                # but the rendered page shows 5.0 + 3 named Google testimonials). So: assert
                # the absence ONLY when raw HTML shows a server-rendered page with no review
                # content anywhere. If the page looks JS-driven, stay silent about visibility
                # rather than claim something this fetch cannot see.
                _vis_html = _r2.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', _hp)
                _vis_txt = _r2.sub(r'(?s)<[^>]+>', ' ', _vis_html)
                _has_reviews = bool(_r2.search(
                    r'(?i)(\breviews?\b|\btestimonials?\b|★|⭐|rated?\s+[0-9.]+\s*(out of|/)\s*5'
                    r'|[0-9.]+\s*stars?\b)', _vis_txt))
                # server-rendered enough to trust the absence? (crude but sufficient: real
                # body copy present). Threshold well below any real page's word count.
                _raw_words = len([w for w in _r2.split(r'\s+', _vis_txt) if w.strip()])
                _fab = ("" if (_has_reviews or _raw_words < 120) else
                        " — and with no reviews or testimonials anywhere on the page, a "
                        "hand-written rating reads as fabricated to anyone who checks")
                _issues.append(f"self-authored aggregateRating ({_ar.get('ratingValue','?')}/"
                               f"{_ar.get('reviewCount', _ar.get('ratingCount','?'))} reviews) on the "
                               "business's own markup — ineligible for rich results since 2019"
                               + _fab + _badkey)
            _rvk = next((k for k in n if k.lower() == 'review'), None)
            if _rvk:
                _rv = n[_rvk] if isinstance(n[_rvk], list) else [n[_rvk]]
                _issues.append(f"{len(_rv)} self-authored Review node(s) embedded in the business's "
                               "own markup — same self-serving-review rule: Google shows no rich "
                               "results for a business reviewing itself, and hand-picked 5-star "
                               "reviews in code read as astroturf if inspected")
            _g = n.get('geo')
            _g = _g if isinstance(_g, dict) else {}
            for _coord in (str(_g.get('latitude', '')), str(_g.get('longitude', ''))):
                if _coord and _coord != _coord.strip():
                    _issues.append(f"geo coordinate has stray whitespace: {_coord!r}")
        # a real-estate site (2026-08-05): this fired on ANY two distinct names with zero
        # awareness of entity linkage, so a CORRECT multi-entity graph (Person worksFor
        # Brokerage; ProfessionalService founder/employee that Person) was indistinguishable
        # from a genuine duplicate — and the advice ("delete the hand-coded block") would have
        # destroyed valid markup. A LINKED graph is the schema.org-recommended shape; only
        # UNLINKED same-page business nodes are the ambiguity Google has to guess between.
        _LINK_PROPS = ('@id', 'founder', 'employee', 'worksfor', 'parentorganization',
                       'subordinateorganization', 'memberof', 'member', 'brand', 'owns',
                       'department', 'provider', 'publisher', 'sameas', 'alumniof')
        def _refs(o, out):
            """Every @id-style reference reachable from this node."""
            if isinstance(o, dict):
                for k, v in o.items():
                    if str(k).lower() in _LINK_PROPS:
                        if isinstance(v, str):
                            out.add(v)
                        else:
                            _refs(v, out)
                    elif isinstance(v, (dict, list)):
                        _refs(v, out)
            elif isinstance(o, list):
                for v in o:
                    _refs(v, out)
            return out
        _ids16 = {str(n.get('@id')) for n in _biz if n.get('@id')}
        _all_refs = set()
        for n in _nodes:
            _refs(n, _all_refs)
        # linked when the business nodes reference one another (directly or via a Person/org
        # node on the same page that names them) — i.e. the graph declares the relationship
        _linked16 = bool(_ids16 & _all_refs)
        if len(_names) > 1 and not _linked16:
            _issues.append("conflicting business NAMES across schema nodes, with no @id/founder/"
                           "employee/worksFor link declaring how they relate: "
                           + " vs ".join(repr(v) for v in _names.values()))
        elif len(_names) > 1:
            print(f"   ok — {len(_names)} differently-named business entities, but the graph LINKS "
                  f"them (@id/founder/worksFor present) — a valid multi-entity graph, NOT duplication")
        _geos = []
        for n in _biz:
            _g = n.get('geo')
            _g = _g if isinstance(_g, dict) else {}
            try:
                _geos.append((float(str(_g.get('latitude')).strip()), float(str(_g.get('longitude')).strip())))
            except Exception:
                pass
        if len(_geos) > 1:
            _la = [g[0] for g in _geos]; _lo = [g[1] for g in _geos]
            _dm = int((((max(_la) - min(_la)) * 111320) ** 2 + ((max(_lo) - min(_lo)) * 92000) ** 2) ** 0.5)
            if _dm > 300:
                _issues.append(f"schema nodes place the business at coordinates ~{_dm}m apart — "
                               "two different locations declared for one business")
        # phone-format mismatch across nodes = same number written differently (a client site: "(786) 555
        # 0100" vs "+1-786-555-0100") — normalize digits before comparing; junk values count too
        _phones16 = {}
        for n in _biz:
            _ph = n.get('telephone')
            if isinstance(_ph, str) and _ph.strip():
                _phones16.setdefault(_r2.sub(r'[^\d]', '', _ph)[-10:], set()).add(_ph.strip())
        for _digits16, _forms16 in _phones16.items():
            if len(_forms16) > 1:
                _issues.append("the SAME phone number is written differently across schema nodes: "
                               + " vs ".join(sorted(_forms16)) + " — pick one format everywhere")
        for n in _biz:
            _oh = n.get('openingHours')
            if isinstance(_oh, str) and _r2.fullmatch(r'[\s,]*', _oh):
                _issues.append(f"openingHours is junk punctuation ({_oh!r}) — an empty template "
                               "field published as data; fill it or remove the property")
            _dsc = n.get('description')
            if isinstance(_dsc, str) and _r2.search(r'<[a-z]+[ >]', _dsc):
                _issues.append("raw HTML tags inside the schema description string — Google reads "
                               "the tags as literal text; use plain text in schema values")
        if len(_biz) > 1:
            print(f"   {len(_biz)} business-entity schema nodes on the homepage")
            # IDENTICAL names + zero contradictions = ordinary plugin+hand-coded LAYERING
            # (a cleaning-company site, 2026-08-02: Yoast Organization name+logo alongside a full hand-coded
            # CleaningService with all the NAP — the "delete the hand-coded block" advice would
            # have DESTROYED the site's only address/hours/geo markup). Only real conflicts or
            # DIFFERENT names make multi-node an issue.
            _names_norm16 = { _r2.sub(r'[^a-z0-9]', '', str(n.get('name') or '').lower())
                              for n in _biz if n.get('name') }
            # §62 a real-estate site (2026-08-06): SECOND copy of the name-conflict test,
            # added separately from the one ~1180 lines up and equally blind to entity linkage.
            # Fixing only the first left this one shipping the same false CRITICAL. Reuse the
            # _linked16 verdict already computed above: a graph that DECLARES how its entities
            # relate (@id + founder/employee/worksFor) is valid markup, not a conflict.
            if len(_names_norm16) > 1 and not _linked16:
                _issues.append(f"{len(_biz)} separate business-entity nodes with DIFFERENT names "
                               "on one page and NO link declaring how they relate — Google must "
                               "choose which to believe; consolidate or link them with @id")
            elif len(_names_norm16) > 1:
                print("   (differently-named nodes, but the graph links them — valid multi-entity "
                      "markup, nothing to fix)")
            elif not _issues:
                print("   (multiple nodes but identical name and no contradictions — plugin + "
                      "hand-coded layering; fine, though linking them via @id is cleaner)")
        if _issues:
            critical = True
            print(f"   🚩 {len(_issues)} STRUCTURED-DATA CONFLICT(S):")
            for _i in _issues:
                print(f"     - {_i}")
            # advice must match what actually fired — "delete the duplicate block" is wrong
            # advice on a site whose only issue is self-authored review markup (a law-firm site)
            # a real-estate site (2026-08-05): "delete the hand-coded block" was printed on
            # ANY multi-node page — including a CORRECTLY linked Person/Brokerage/Service graph,
            # where following it would destroy valid markup. Only give delete-advice when an
            # issue about the NODES actually fired (conflicting name/geo/phone), never merely
            # because more than one business node exists.
            _conflict_fired = any(('conflicting business NAMES' in _i)
                                  or ('coordinates ~' in _i)
                                  or ('phone' in _i.lower() and 'node' in _i.lower())
                                  for _i in _issues)
            if len(_biz) > 1 and _conflict_fired:
                print("      Google gets two versions of who/where the business is and picks one")
                print("      (or neither). FIX: keep ONE description of the business — either the")
                print("      SEO plugin's or the hand-coded block, not both saying different things.")
                print("      If these are genuinely DIFFERENT entities (e.g. a person and the firm")
                print("      they work for), keep both and LINK them with @id + worksFor/founder.")
            if any('aggregateRating' in _i for _i in _issues):
                print("      Remove the self-authored star-rating markup: Google has not shown")
                print("      rich results for a business rating its own pages since 2019.")
        elif _biz:
            # COMPLETENESS, not just agreement (a multi-location garage-door site, 2026-08-01: homepage Organization node
            # had telephone:None address:None while inner pages carried full LocalBusiness NAP —
            # the local entity anchor was EMPTY on the page Google weighs most, and "single
            # consistent entity" passed it)
            _has_tel16 = any(isinstance(n.get('telephone'), str) and n.get('telephone').strip()
                             for n in _biz)
            # address arrives as dict, string, OR LIST of PostalAddress (a client site, 2026-08-02)
            _has_addr16 = any(isinstance(n.get('address'), (dict, str, list)) and n.get('address')
                              for n in _biz)
            if (not _has_tel16 or not _has_addr16) and not IS_LOCAL_BIZ:
                print("   (business node without full NAP — but this is an Organization/app "
                      "publisher, where that is correct markup. Not flagged.)")
            elif not _has_tel16 or not _has_addr16:
                _miss16 = [w for w, ok in (("phone", _has_tel16), ("address", _has_addr16)) if not ok]
                print(f"   ⚠️ single consistent business entity — but the homepage node carries "
                      f"NO {' or '.join(_miss16)}. Add the missing propert"
                      f"{'ies' if len(_miss16) > 1 else 'y'} (or reference the fuller "
                      "LocalBusiness node used on inner pages).")
            else:
                print("   ok — single consistent business entity in schema")
        else:
            print("   (no business-entity schema on the homepage — see check 3)")
    except Exception as _e16:
        print(f"   ⚠️ homepage fetch/parse failed: {_e16}")

    # 17. NOINDEX INVENTORY (added 2026-07-31, a roofing site): 23 real content pages
    # (/projects/ customer stories, /products/ brand pages) were noindexed while their hub was
    # indexable. Possibly deliberate — but nobody had ever ASKED. Inventory every noindexed 200
    # page so the owner confirms each group is intentional; unique content excluded from the
    # index is either a mistake or a strategy, never a default.
    print("\n17. NOINDEX INVENTORY")
    # exclude REDIRECT rows (§16): /wp-admin/ is a 302 whose noindex belongs to the login
    # DESTINATION, not to /wp-admin/ itself (a client site, 2026-08-04). Also drop standard
    # WordPress auth defaults (wp-login) — a noindexed login is correct, not "confirm this".
    _noidx = [p for p in PGS if p.get('status') == 200 and not p.get('redirected')
              and _is_noindex_tag(p)
              and not re.search(r'/wp-login\.php|/wp-admin/', p.get('url', ''))]
    if not _noidx:
        print("   ok — no crawled 200-page is noindexed")
    else:
        _byseg = _C2()
        for p in _noidx:
            _pt = _r2.sub(r'https?://[^/]+', '', p['url']).strip('/')
            _byseg[(_pt.split('/')[0] or '(root)')] += 1
        print(f"   ⚠️ {len(_noidx)} crawled page(s) carry noindex — CONFIRM each group is deliberate:")
        for _sg, _n in _byseg.most_common(8):
            print(f"     /{_sg}/ — {_n} page(s)")
        for p in _noidx[:6]:
            print(f"       {p['url']}")
        print("      These pages exist and load but are excluded from Google. If that is strategy,")
        print("      fine — if these are real content (projects, testimonials, products), it is")
        print("      traffic left on the table.")

    # 18. PRE-CONSENT TRACKING EXPOSURE (added 2026-08-01, CIPA pixel-litigation research):
    # demand letters under Cal. Penal Code §§630-638 (CIPA, 1967 — private right of action, up to
    # $5,000/violation claimed, no revenue threshold) are being mailed to SMBs whose sites fire
    # ad/analytics pixels before any consent. The canonical fact pattern is an ORPHANED pixel from
    # a years-dead campaign, still firing on first paint. TWO LAYERS, same doctrine as the render
    # check: LAYER 1 (this, static) reads raw HTML for tracker + consent-manager signatures;
    # LAYER 2 (pixel-consent-check.py, live headless Chrome) cold-loads the page and records which
    # tracking hosts are actually contacted before ANY interaction — the live layer is the
    # authoritative one, because a banner is NOT blocking: compliance theater (banner present,
    # pixels wired outside its control fire anyway — a common GTM misconfig) is widespread, so
    # trackers+consent-manager is ⚠️ "verify blocking", never a pass.
    print("\n18. PRE-CONSENT TRACKING EXPOSURE")
    _TRK = [  # (label, raw-HTML signature) — keep in sync with pixel-consent-check.py's host table
        ("Meta (Facebook) Pixel",   r'connect\.facebook\.net|fbevents\.js|facebook\.com/tr\b'),
        ("LinkedIn Insight Tag",    r'px\.ads\.linkedin\.com|snap\.licdn\.com'),
        ("TikTok Pixel",            r'analytics\.tiktok\.com'),
        ("Microsoft/Bing UET",      r'bat\.bing\.com'),
        ("Microsoft Clarity (session replay)", r'\bclarity\.ms|["\']clarity["\']?\s*,\s*["\']script'),
        ("Hotjar (session replay)", r'(static|script)\.hotjar\.com|hotjar-\d|\bhjSiteSettings\b'),
        ("FullStory (session replay)", r'fullstory\.com/s/fs\.js|\.fullstory\.com|\bwindow\[._fs'),
        # label by tag-ID prefix (a Shopify store, 2026-08-02: an AW- tag shipped labeled "GA4" — AW- is
        # GOOGLE ADS remarketing/conversion, the STRONGER CIPA pattern; G- is GA4; GT- is a
        # container that can load either)
        ("Google Ads (AW- tag: remarketing/conversion)", r'gtag/js\?id=AW-|AW-\d{6,}'),
        ("Google Analytics 4 / gtag", r'gtag/js\?id=G-|google-analytics\.com|\bgtag\('),
        ("Google tag container (GT-)", r'gtag/js\?id=GT-'),
    ]
    # CMP signatures must match LOADER CONTEXT, never a bare vendor name (a cleaning-company site, 2026-08-02:
    # bare 'cookiebot' matched PixelYourSite's DISABLED config keys — "cookiebot_integration_
    # enabled":false — and shipped "confirm the banner blocks" about a banner that does not
    # exist, directly contradicting the no-CMP critical above it). A vendor name inside a config
    # blob is evidence of an integration switched OFF.
    _CMP_SRC = _r2.compile(
        r'<script[^>]+src=["\'][^"\']*'
        r'(usercentrics|cookiebot|cookielaw\.org|termly\.io|complianz|cookieyes|iubenda|'
        r'borlabs|consentmanager|jconsent|cookie-law-info|cookieconsent)[^"\']*["\']', _r2.I)
    _CMPS = [  # runtime/mechanism markers that cannot come from a disabled config blob
        ("consent-manager script", _CMP_SRC.pattern),
        ("Cookiebot runtime", r'window\.Cookiebot|CookiebotDialog'),
        ("OneTrust runtime", r'OptanonWrapper|onetrust-banner-sdk'),
        ("gtag consent-default-denied", r'consent[\'"]?\s*,\s*[\'"]default[\'"][^)]{0,300}denied'),
        ('type="text/plain" script wrapper (blocked-until-consent)',
         r'<script[^>]+type=["\']text/plain["\']'),
    ]
    _GTM18 = _r2.compile(r'googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]{4,}')
    # homepage = the verdict page (fetched live, like check 16); the checks-9-11 live sample
    # (home + deepest page per section) gives the "on N of your pages" count. The crawl JSON
    # stores no raw_html, so counting is over the LIVE SAMPLE — say so, never extrapolate (§19:
    # no arithmetic on unmeasured pages).
    _hp18 = _live(f'https://{site_host}/')
    _docs18 = dict(_htmls)
    if _hp18:
        _docs18[f'https://{site_host}/'] = _hp18
    elif _htmls:
        _hp18 = list(_htmls.values())[0]     # homepage fetch failed; fall back to first sampled page
    if not _hp18:
        print("   ⚠️ could not fetch any live page HTML — pre-consent layer not run")
    else:
        _found18 = []                        # (label, pages-carrying-it, first-matched-signature)
        for _lb, _sig in _TRK:
            _rx = _r2.compile(_sig, _r2.I)
            _on = [u for u, h in _docs18.items() if _rx.search(h)]
            _m = _rx.search(_hp18)
            if _m or _on:
                _found18.append((_lb, len(_on), (_m.group(0) if _m else _sig.split('|')[0])))
        _cmp18 = [_lb for _lb, _sig in _CMPS if _r2.search(_sig, _hp18, _r2.I)]
        _gtm18 = bool(_GTM18.search(_hp18))
        _ns18, _nt18 = len(_docs18), len(PGS)
        if not _found18:
            print("   ok — no ad/analytics tracker signatures in the page HTML "
                  f"({_ns18} live pages checked)")
            if _gtm18:
                print("   note: a Google Tag Manager container IS present — GTM can load trackers")
                print("   that never appear in the HTML, so the static layer cannot fully clear it.")
                print(f"   To be certain: python3 pixel-consent-check.py https://{site_host}/")
        else:
            for _lb, _np, _sg in _found18:
                print(f"   - {_lb} — matched `{_sg}` (on {_np} of {_ns18} pages checked live; "
                      f"{_nt18} crawled)")
            if _gtm18:
                print("   - Google Tag Manager container also present (can load further trackers "
                      "invisible to this static layer)")
            if not _cmp18:
                critical = True
                print(f"   🚩 {len(_found18)} TRACKER(S) IN THE PAGE HTML WITH NO CONSENT MANAGER "
                      "DETECTED — they fire unconditionally, on first paint, before any consent.")
                print("      This is the exact fact pattern behind the current CIPA (California)")
                print("      demand-letter wave: plaintiffs claim up to $5,000/violation, and")
                print("      orphaned pixels from long-dead campaigns are the canonical target.")
                print("      FIX: remove every tracker no active campaign still uses, then put the")
                print("      rest behind a consent tool that actually BLOCKS them until the visitor")
                print("      agrees — a banner alone does not do this.")
                # PRIVACY-POLICY EXISTENCE (a Squarespace site, 2026-08-02): trackers firing with NO privacy
                # policy anywhere is materially worse than a non-blocking banner — zero disclosure.
                _pp_found = None
                for _pp in ('/privacy-policy', '/privacy', '/terms', '/cookie-policy'):
                    try:
                        _rpp = _ur2.urlopen(_ur2.Request(f'https://{site_host}{_pp}',
                                            headers=_UA), timeout=12)
                        if _rpp.status == 200:
                            _pp_found = _pp
                            break
                    except Exception:
                        continue
                # anchor-context only: Squarespace config JSON contains "privacyPolicy" as a KEY
                # (same Squarespace site, 2026-08-02) — a bare substring match suppressed the flag on a site with
                # genuinely NO policy link anywhere
                _pp_link = _r2.search(r'<a[^>]+href="[^"]*(privacy|terms|cookie-policy)[^"]*"', _hp18, _r2.I)
                if not _pp_found and not _pp_link:
                    print("   🚩 AND the site has NO PRIVACY POLICY at all (common paths 404, no")
                    print("      footer link) — visitor data is being collected with zero disclosure,")
                    print("      which independently violates CalOPPA/GDPR-style disclosure duties.")
            else:
                print(f"   ⚠️ tracker(s) AND a consent manager ({', '.join(_cmp18)}) both present — "
                      "VERIFY BLOCKING.")
                print("      Banner presence is NOT blocking; on many sites the trackers are wired")
                print("      outside the consent tool's control and fire anyway (compliance")
                print("      theater). Only a live cold load can rule that out:")
                print(f"      python3 pixel-consent-check.py https://{site_host}/")
                print("      Pre-consent requests to facebook.com/tr, px.ads.linkedin.com etc.")
                print("      must be ABSENT before this passes.")

    # 20. BRAND TRANSITION / PREVIOUS-DOMAIN EQUITY (added 2026-08-01, an insurance site —
    # OWNER caught it: the homepage banner said "<OldBrand> is now part of <NewBrand>" and no
    # check looked. The old brand's domain 301s its bare homepage to the site but 404s EVERY deep URL
    # (old backlink equity stranded), its www host is NXDOMAIN, and the schema carries no
    # alternateName bridge. Bonus: the old domain hosted Chinese spam in 2023 — deep-404s
    # partially protective; the fix is SELECTIVE page-level redirects, not a blanket.)
    print("\n20. BRAND TRANSITION / PREVIOUS-DOMAIN EQUITY")
    try:
        _hp20 = _ur2.urlopen(_ur2.Request(f'https://{site_host}/', headers=_UA), timeout=25
                             ).read().decode('utf-8', 'replace')
    except Exception:
        _hp20 = ''
    # regex runs on TAG-STRIPPED text: the live positive was <strong>OldBrand</strong></a>
    # <strong> is now part of…</strong> — tags inside the phrase defeat any raw-HTML match
    _txt20 = _r2.sub(r'\s+', ' ', _r2.sub(r'<[^>]+>', ' ', _hp20))
    _trans = _r2.search(r'([A-Z][A-Za-z ]{1,30}?)\s+(?:is now part of|has joined|has been '
                        r'acquired by|has merged with)\s+[A-Z]', _txt20) or \
             _r2.search(r'formerly(?: known as)?\s+([A-Z][A-Za-z ]{1,30})', _txt20)
    _apex20 = _r2.sub(r'^www\.', '', site_host)
    # any externally referenced domain that REDIRECTS INTO this site = a previous identity
    _cands = sorted({d.lower() for d in _r2.findall(r'https?://([a-z0-9-]+\.[a-z]{2,})', _hp20, _r2.I)
                     if _apex20 not in d.lower()
                     and not _r2.search(r'google|facebook|linkedin|twitter|instagram|youtube|cdn|'
                                        r'website-files|cloudfront|gstatic|schema\.org|w3\.org|'
                                        r'clarity|hotjar|tiktok|jsdelivr|jquery|unpkg', d, _r2.I)})[:10]
    _olds = []
    for _d20 in _cands:
        try:
            _o20 = _ur2.build_opener(*_PXH, _NR, _ur2.HTTPSHandler(context=_ctx))
            _r20 = _o20.open(_ur2.Request(f'https://{_d20}/', headers=_UA), timeout=12)
            _loc20 = ''
        except _ur2.HTTPError as e:
            _loc20 = e.headers.get('Location', '')
        except Exception:
            continue
        if _apex20 in _loc20:
            _olds.append(_d20)
    if not _trans and not _olds:
        print("   ok — no brand-transition language on the homepage and no external domain "
              "redirecting into this site")
    else:
        if _trans:
            print(f"   TRANSITION LANGUAGE on the homepage: \"{_trans.group(0)[:70]}\"")
        for _d20 in _olds:
            print(f"   PREVIOUS DOMAIN detected: {_d20} (its homepage redirects into this site)")
            # deep-path probe: page-level or homepage-only?
            _deep_ok = _deep_404 = 0
            for _p20 in ('/about', '/blog', '/x-equity-probe-1'):
                try:
                    _o20.open(_ur2.Request(f'https://{_d20}{_p20}', headers=_UA), timeout=12)
                    _deep_ok += 1
                except _ur2.HTTPError as e:
                    (_olds and None)
                    if 300 <= e.code < 400 and _apex20 in (e.headers.get('Location') or ''):
                        _deep_ok += 1
                    else:
                        _deep_404 += 1
                except Exception:
                    pass
            if _deep_404 >= 2:
                critical = True
                print(f"   🚩 {_d20} redirects ONLY its bare homepage — deep URLs return errors, so "
                      "every backlink/citation pointing at any old inner page is stranded. Map the "
                      "old site's REAL pages to their closest new equivalents (selective, page-level "
                      "— check the old domain's history first: spam-era URLs should stay 404).")
            # www variant
            try:
                import socket as _sk
                _sk.getaddrinfo('www.' + _d20, 443)
                print(f"   www.{_d20} resolves")
            except Exception:
                print(f"   ⚠️ www.{_d20} does not resolve (NXDOMAIN) — any www-form links to the "
                      "old brand die entirely; add DNS + redirect")
        # entity bridge
        _has_alt = bool(_r2.search(r'"alternateName"\s*:\s*"[^"]*"', _hp20))
        _old_tok = (_trans.group(1).strip().split()[0].lower() if _trans else
                    (_olds[0].split('.')[0] if _olds else ''))
        _bridged = bool(_old_tok) and _old_tok in _hp20[_hp20.find('"alternateName"'):
                                                         _hp20.find('"alternateName"') + 200].lower() \
                   if _has_alt else False
        if not _bridged:
            print("   ⚠️ no schema alternateName carrying the FORMER brand — Google gets no "
                  "machine-readable statement that the old and new entities are the same. Add "
                  "alternateName (and sameAs to legacy profiles) to the business schema.")

    # 19. LANGUAGE VARIANTS / HREFLANG (added 2026-08-01, critiques of two law-firm sites —
    # flagged TWICE by clean agents: bilingual sites sailed through with no i18n verdict because
    # hreflang lived only in the optional `python -m crawler check` pass the orchestrator never
    # runs). One of them: /espanol/ pages titled "Abogado Defensa Criminal <City>" carried
    # lang="en-US" and ZERO hreflang tags sitewide.
    print("\n19. LANGUAGE VARIANTS / HREFLANG")
    _LANGDIRS = {'es': ('es', 'espanol', 'spanish'), 'fr': ('fr', 'francais'), 'de': ('de',),
                 'pl': ('pl', 'polski'), 'zh': ('zh', 'cn'), 'pt': ('pt',), 'ru': ('ru',),
                 'ko': ('ko',), 'vi': ('vi',), 'ar': ('ar',)}
    _variant_pages = {}
    for p in PGS:
        if p.get('status') != 200:
            continue
        _segs19 = [x for x in _r2.sub(r'https?://[^/]+', '', p['url']).split('/') if x]
        for _lang, _dirs in _LANGDIRS.items():
            if _segs19 and _segs19[0].lower() in _dirs:
                _variant_pages.setdefault(_lang, []).append(p)
    if not _variant_pages:
        print("   ok — no language-variant sections detected in the crawl")
    else:
        for _lang, _vps in _variant_pages.items():
            _sample19 = _vps[0]
            try:
                _vh = _ur2.urlopen(_ur2.Request(_sample19['url'], headers=_UA), timeout=20
                                   ).read().decode('utf-8', 'replace')
            except Exception:
                _vh = ''
            # a bot-walled host serves a challenge to THIS fetch even when the crawl transport
            # worked — prefer the crawl row's stored lang (captured via the working transport)
            _challenged = bool(_r2.search(r'captcha|challenge|checking your browser', _vh, _r2.I)) \
                          or len(_vh) < 2000
            # case-INSENSITIVE: Next/React emit JSX-style hrefLang= (an iOS-app site, 2026-08-02 —
            # the case-sensitive match fired a bogus CRITICAL on a site with textbook hreflang)
            _hreflang_n = 0 if _challenged else len(_r2.findall(r'hreflang=', _vh, _r2.I))
            _lm = _r2.search(r'<html[^>]*\blang=["\']?([A-Za-z-]+)', _vh)
            _declared = (_sample19.get('lang') or (_lm.group(1) if _lm else '')).lower()
            if _challenged:
                print("   (live fetch hit a bot challenge — lang read from crawl data; hreflang "
                      "presence could not be measured live and is reported from absence of evidence)")
            print(f"   /{_lang}/ section: {len(_vps)} crawled page(s); sample {_sample19['url']}")
            if _hreflang_n == 0:
                critical = True
                print(f"   🚩 NO hreflang tags on the {_lang} sample — Google has no signal that "
                      "these pages are translations, so they compete with (and lose to) the "
                      "English versions instead of serving " + _lang + "-language searchers.")
            if _declared and not _declared.startswith(_lang):
                critical = True
                print(f"   🚩 the {_lang} page DECLARES lang=\"{_declared}\" — it tells search "
                      "engines and screen readers it is written in the WRONG language.")
            if _hreflang_n and _declared.startswith(_lang):
                print(f"   ok — hreflang present ({_hreflang_n} tags) and lang=\"{_declared}\" matches")
        print("      FIX: each language version declares its own lang attribute, and every page")
        print("      carries bidirectional hreflang links (each version lists ALL versions + x-default).")

    # 21. IDENTICAL-CONTENT PAGE SETS (added 2026-08-02, a Squarespace site: six /tag/ pages, 6,568 words
    # each, Jaccard 1.00, each SELF-canonical and indexable — check 14 counts pages per prefix and
    # missed a set of identical pages under DIFFERENT paths; the simhash pass lives in the optional
    # analyze module the orchestrator never runs). Signature: identical word_count AND identical
    # H1 set across >=3 distinct 200 pages that are their own canonical.
    print("\n21. IDENTICAL-CONTENT PAGE SETS")
    # PAGINATION EXCLUSION (a WordPress blog, 2026-08-04): paginated archive views (/blog/page/16, /page/22, …)
    # legitimately share an identical word_count (same excerpt-count template) and an empty H1, so the
    # (word_count, h1) signature collides and mislabels them CRITICAL "same page under many addresses"
    # — but each lists an entirely DIFFERENT set of posts. Real duplicate content shows up as pairs
    # with matching TITLES (check 8) or under tag/category archives; paginator pages never are it.
    _PAGINATOR21 = _r2.compile(r'/(page|pagina|seite|p)/\d+/?$|[?&](page|paged|pg)=\d+(&|$)', _r2.I)
    _sig21 = {}
    for p in PGS:
        if p.get('status') != 200 or p.get('redirected'):
            continue
        if _PAGINATOR21.search(p.get('url') or ''):
            continue                                   # paginated archive view — not duplicate content
        # NOINDEXED pages are NOT a duplicate-content problem — they are already excluded from the
        # index, which is the fix this check would recommend. A WooCommerce salon site (2026-08-05): 17 WooCommerce
        # `/cart/?undo_item=…` rows, EVERY ONE `noindex, nofollow` + `indexable: False` in the crawl's
        # own data, shipped as a CRITICAL asserting "every one indexable as its own page" — the
        # finding contradicted the row it was built from, and it masked the REAL duplication (22
        # indexable /shop/?add-to-cart= URLs with no canonical at all).
        if not p.get('indexable') or _is_noindex(p):
            continue
        # canonicalized-elsewhere rows are already handled (check 14 facets); only count pages
        # that claim to be their OWN index entry — otherwise a roofing site's ?category facets would
        # double-report here
        _c21 = (p.get('canonical') or '').split('#')[0].rstrip('/')
        _u21x = (p.get('url') or '').split('#')[0].rstrip('/')
        if _c21 and _r2.sub(r'^https?://(www\.)?', '', _c21) != _r2.sub(r'^https?://(www\.)?', '', _u21x):
            continue
        _wc21 = p.get('word_count') or 0
        if _wc21 < 200:
            continue
        _h121 = tuple((p.get('headings') or {}).get('h1') or []) if isinstance(p.get('headings'), dict) else tuple(p.get('h1') or [])
        _sig21.setdefault((_wc21, _h121), []).append(p['url'])
    _dups21 = {k: v for k, v in _sig21.items() if len(v) >= 3}
    if not _dups21:
        print("   ok — no sets of 3+ pages with identical size and headline structure")
    else:
        for (_wc21, _), _urls21 in sorted(_dups21.items(), key=lambda x: -len(x[1]))[:4]:
            critical = True
            print(f"   🚩 {len(_urls21)} pages serve IDENTICAL content ({_wc21} words each), every "
                  "one indexable as its own page:")
            for _u21 in _urls21[:6]:
                print(f"        {_u21}")
            print("      Google sees the same page under many addresses and must pick one — the")
            print("      others dilute the site. FIX: noindex the variants (tag/category archives)")
            print("      or canonical them to ONE address.")

    # 22. RESIDUE SLUGS / SLUG-CONTENT MISMATCH (added 2026-08-02, a Squarespace site: 11 blog posts under
    # Squarespace duplicate-residue slugs like ...-np4r4-frpjn-c763y whose slug describes a
    # DIFFERENT article than the title; all in the submitted sitemap).
    print("\n22. RESIDUE SLUGS / SLUG-CONTENT MISMATCH")
    _RES22 = _r2.compile(r'((?:-[a-z0-9]{5}){3,})/?$')
    def _randomish22(tok):
        # residue tokens are CMS hashes: digit-bearing or vowel-starved ("np4r4","frpjn","c763y");
        # real 5-letter words ("storm","prone","areas") have vowels — a roofing-site FP 2026-08-02.
        # a Shopify-store FP 2026-09-22: "nylon-solar-cleat-light" (a product name) scored 2 randomish
        # because "nylon" and "light" have ONE vowel. A hash has NONE (count y as a vowel); a word has ≥1.
        return any(c.isdigit() for c in tok) or sum(c in 'aeiouy' for c in tok) == 0
    _bad22 = []
    for p in PGS:
        if p.get('status') != 200 or p.get('redirected'):
            continue
        _u22 = (p.get('url') or '').split('?')[0].split('#')[0]
        _slug22 = _u22.rstrip('/').rsplit('/', 1)[-1]
        _m22 = _RES22.search(_u22)
        if _m22 and sum(1 for t in _m22.group(1).strip('-').split('-') if _randomish22(t)) >= 2:
            _bad22.append((_u22, 'residue suffix'))
            continue
        # WordPress duplicate-collision slug: a bare numeric ID with a -N dedup suffix
        # (/blog/.../1049-2, 1045-2) = an auto-generated duplicate page (a WordPress blog, 2026)
        if re.search(r'/[0-9]{3,}-[0-9]+/?$', _u22):
            _bad22.append((_u22, 'auto-generated duplicate-page slug (numeric ID + -N suffix)'))
            continue
        _t22 = (p.get('title') or '').lower()
        # transliterated slugs (Hindi/Portuguese titles, ASCII slugs) always score zero overlap —
        # skip non-ASCII titles, and fold accents for Latin ones (Natação -> natacao)
        if any(ord(ch) > 0x24F for ch in _t22):
            continue
        import unicodedata as _ud22
        _t22 = _ud22.normalize('NFKD', _t22).encode('ascii', 'ignore').decode()
        _tok22 = [w for w in _r2.findall(r'[a-z]{4,}', _slug22.lower())
                  if w not in ('with', 'from', 'that', 'this', 'your', 'blog', 'page')]
        if len(_tok22) >= 3 and _t22:
            _hits22 = sum(1 for w in _tok22 if w in _t22)
            if _hits22 == 0:
                _bad22.append((_u22, 'slug describes a different topic than the title'))
    if not _bad22:
        print("   ok — slugs match their content")
    else:
        print(f"   ⚠️ {len(_bad22)} page(s) whose ADDRESS does not match their content:")
        for _u22, _why22 in _bad22[:8]:
            print(f"        {_u22}  ({_why22})")
        print("      Usually CMS duplicate-page residue: the page was cloned from another article")
        print("      and kept (part of) the source's address plus random suffixes. The address is")
        print("      a ranking signal and a user trust signal — give each page a clean slug that")
        print("      describes ITS content (with a redirect from the old address).")

    # 23. LEAD CAPTURE EXISTS (added 2026-08-02, a bathroom-remodel site: ZERO <form> elements sitewide — every
    # CTA resolved to tel: only, so after-hours visitors have no way to become leads at all).
    print("\n23. LEAD CAPTURE")
    if not IS_LOCAL_BIZ:
        print("   (organization/app business model — lead-capture checks assume a local/commerce "
              "business; skipped)")
    else:
        _forms23 = sum((p.get('html_tag_inventory') or {}).get('form', 0) for p in PGS
                       if p.get('status') == 200 and not p.get('redirected'))
        if _forms23:
            print(f"   ok — {_forms23} form element(s) across the site")
        else:
            print("   ⚠️ ZERO forms anywhere on the site — if every contact path is a phone number,")
            print("      after-hours visitors (most of them) have no way to become a lead. Add a")
            print("      simple request-a-quote form (name/phone/message) to the money pages.")

    # 25. VISUAL CONTENT (split from check 23 2026-08-02 — a client site: the zero-images warning merged
    # into the contact finding, producing a heading about phones with evidence about photographs)
    print("\n25. VISUAL CONTENT")
    _imgs25 = sum(p.get('images_total') or 0 for p in PGS
                  if p.get('status') == 200 and not p.get('redirected'))
    if _imgs25 == 0 and IS_LOCAL_BIZ:
        print("   ⚠️ ZERO images anywhere on the site — for a business selling physical goods or")
        print("      services this is a trust and conversion gap (no photos of the store, the")
        print("      products, or the people), and it is why social shares have no thumbnail.")
    elif _imgs25 == 0:
        print("   (zero images — org/app model, informational only)")
    else:
        print(f"   ok — {_imgs25} image(s) across the site")

    # 26. SECURITY HEADERS (an iOS-app site, 2026-08-02: a privacy-positioned health app served ZERO
    # security headers incl. on /privacy/ — nothing tested response headers at all)
    print("\n26. SECURITY HEADERS (homepage)")
    try:
        _h26 = _ur2.urlopen(_ur2.Request(f'https://{site_host}/', headers=_UA), timeout=15).headers
        _want26 = ['strict-transport-security', 'content-security-policy', 'x-frame-options',
                   'x-content-type-options', 'referrer-policy']
        _missing26 = [h for h in _want26 if not _h26.get(h)]
        if not _missing26:
            print("   ok — all common security headers present")
        else:
            print(f"   ⚠️ missing {len(_missing26)} of 5 common security headers: "
                  + ", ".join(_missing26))
            print("      Not a ranking factor, but basic hardening — and an easy trust win on a")
            print("      site handling personal data. Set them at the host/CDN level.")
    except Exception as _e26:
        print(f"   (header probe failed: {str(_e26)[:40]})")

    # 27. END-OF-LIFE SERVER SOFTWARE (added 2026-08-06, a painting-company site: the site advertised
    # `x-powered-by: PHP/7.4.33` — unsupported since 2022-11-28, no security patches for ~4 years —
    # and NO stage read that header. Clean-agent had to add the finding by hand. This is the
    # detect-vs-evaluate defect class: read the version, then JUDGE it, don't just report it.
    # EOL dates are historical facts (php.net/supported-versions), safe to hardcode.
    print("\n27. END-OF-LIFE SERVER SOFTWARE (homepage headers)")
    _PHP_EOL = {  # branch -> (security-support-ended, note)
        '5.3': '2014-08', '5.4': '2015-09', '5.5': '2016-07', '5.6': '2018-12',
        '7.0': '2019-01', '7.1': '2019-12', '7.2': '2020-11', '7.3': '2021-12',
        '7.4': '2022-11', '8.0': '2023-11', '8.1': '2025-12',
    }
    try:
        _h27 = _ur2.urlopen(_ur2.Request(f'https://{site_host}/', headers=_UA), timeout=15).headers
        _xp = (_h27.get('x-powered-by') or '')
        _srv = (_h27.get('server') or '')
        _eol_hits = []
        _mphp = _r2.search(r'PHP/(\d+\.\d+)(?:\.\d+)?', _xp, _r2.I)
        if _mphp:
            _branch = _mphp.group(1)
            _full = _r2.search(r'PHP/([\d.]+)', _xp, _r2.I).group(1)
            if _branch in _PHP_EOL:
                _eol_hits.append((f'PHP {_full}', _PHP_EOL[_branch]))
            else:
                print(f"   ok — PHP {_full} is on a supported branch (but see disclosure note below)")
        if _eol_hits:
            critical = True
            for _what, _when in _eol_hits:
                print(f"   🚩 the server runs {_what}, which stopped receiving security updates in "
                      f"{_when}")
                print("      Every security hole found in it since then is public and will never be")
                print("      patched on this version. Hosts usually offer a newer version as a")
                print("      one-click switch in the control panel.")
                print("      FIX: ask your host to move the site to a currently-supported version")
                print("      (test on a staging copy first — very old themes/plugins may need work).")
        elif not _mphp:
            print("   ok — no end-of-life software version advertised in the headers")
        # separate, lesser issue: advertising the exact version at all
        if _xp or _r2.search(r'\d+\.\d+', _srv):
            _adv = "; ".join(x for x in (f"x-powered-by: {_xp}" if _xp else "",
                                         f"server: {_srv}" if _r2.search(r'\d+\.\d+', _srv) else "")
                             if x)
            print(f"   note — the site announces its exact software versions ({_adv}). Harmless on")
            print("      its own, but it hands an attacker a shortlist of exploits to try. Your host")
            print("      can suppress these headers.")
    except Exception as _e27:
        print(f"   (EOL probe failed: {str(_e27)[:40]})")

    # 28. SOFT-404 (added 2026-08-06, a UK Shopify store: EVERY unknown URL returned HTTP 200 with
    # <h1>404</h1> rendered — verified on 3 random paths — and no check compared status against
    # content. Check 13 only catches bracket-placeholders that are already IN the sitemap; this
    # probes the server's actual behaviour for addresses that do not exist. Soft-404s let Google
    # index unlimited junk URLs as real pages and hide genuinely broken links from every tool.
    print("\n28. SOFT-404 BEHAVIOUR (missing-page handling)")
    try:
        _probe_paths = ['/this-page-does-not-exist-audit-probe-a1b2c3/',
                        '/nonexistent-audit-check-x9y8z7/',
                        '/audit-probe-404-test-q4r5s6.html']
        _soft = []
        _hard = 0
        _probe_err = 0
        for _pp in _probe_paths:
            try:
                _rq = _ur2.Request(f'https://{site_host}{_pp}', headers=_UA)
                _rs = _ur2.urlopen(_rq, timeout=20)
                _st28, _bd28 = _rs.status, _rs.read(200000).decode('utf-8', 'replace')
            except Exception as _pe:
                _st28 = getattr(_pe, 'code', None)
                if _st28 in (404, 410):
                    _hard += 1
                    continue
                _probe_err += 1
                continue
            if _st28 == 200:
                # 200 for a URL that cannot exist. Does the page ADMIT it is an error?
                _admits = bool(_r2.search(r'(?i)(\b404\b|page not found|page (does |could )?n[o\']t '
                                          r'(be )?(exist|found)|nothing (was )?found here)', _bd28))
                _soft.append((_pp, _admits))
            elif _st28 in (404, 410):
                _hard += 1
        if _soft:
            critical = True
            _n28 = len(_soft)
            _admit_n = sum(1 for _, a in _soft if a)
            print(f"   🚩 addresses that do not exist on your site answer with HTTP 200 (\"here is "
                  f"the page\") instead of 404 (\"no such page\") — {_n28} of "
                  f"{len(_probe_paths)} made-up test addresses did this:")
            for _pp, _a in _soft:
                print(f"     {_pp}   -> 200 OK" + ("  (page text does say 404/not-found)" if _a
                                                   else "  (page does not even say it is an error)"))
            if _admit_n:
                print("      The page shows a 'not found' message to a human, but the invisible")
                print("      status code says success — and search engines read the status code.")
            print("      WHY IT MATTERS: Google can index an unlimited number of junk addresses as")
            print("      if they were real pages, and every broken link on your site looks fine to")
            print("      link-checking tools, so nobody ever finds them.")
            print("      FIX: ask your web person to return a real 404 status for unknown addresses.")
        elif _hard:
            print(f"   ok — unknown addresses correctly return 404 ({_hard}/{len(_probe_paths)} probes)")
        else:
            print(f"   ⚠️ UNKNOWN — could not measure ({_probe_err} probe(s) failed in transport); "
                  "no soft-404 conclusion drawn")
    except Exception as _e28:
        print(f"   (soft-404 probe failed: {str(_e28)[:40]})")

    # 24. STORE INVENTORY (added 2026-08-02, a client site: a Shopify store with ZERO live products —
    # /products.json empty, no product/collection sitemaps, every "Shop now" a 404. The headline
    # business fact and nothing tested it.)
    print("\n24. STORE INVENTORY (ecommerce only)")
    _shop_urls24 = sum(1 for p in PGS if '/products/' in (p.get('url') or '')
                       or '/collections/' in (p.get('url') or ''))
    _is_shop = _shop_urls24 >= 3 or any('cdn.shopify.com' in str(p.get('url', '')) for p in PGS)
    _shopify24 = any('myshopify' in str(p) or 'cdn/shop' in str(p.get('url', '')) for p in PGS[:5])
    if not _is_shop:
        try:
            _r24 = _ur2.urlopen(_ur2.Request(f'https://{site_host}/products.json?limit=1',
                                headers=_UA), timeout=12)
            _pj = _r24.read().decode('utf-8', 'replace')
            # a 404/error page CONTAINING the word "products" is not a store (an iOS-app site FP)
            _is_shop = _r24.status == 200 and _pj.strip().startswith('{') and '"products"' in _pj
        except Exception:
            _pj = None
            _is_shop = False
    else:
        _pj = None
    if not _is_shop:
        print("   (not an ecommerce platform — skipped)")
    else:
        try:
            if _pj is None:
                _pj = _ur2.urlopen(_ur2.Request(f'https://{site_host}/products.json?limit=250',
                                   headers=_UA), timeout=15).read().decode('utf-8', 'replace')
            import json as _j24
            _prods = (_j24.loads(_pj) or {}).get('products', None)
            if _prods is None:
                print("   ⚠️ could not read the product catalog (blocked) — verify manually")
            elif len(_prods) == 0:
                critical = True
                print("   🚩 THE STORE HAS ZERO LIVE PRODUCTS — the catalog API returns an empty")
                print("      list. Every 'Shop now' path dead-ends. Whatever else this report")
                print("      says, THIS is the headline: there is nothing to buy.")
            else:
                print(f"   ok — {len(_prods)}{'+' if len(_prods) == 250 else ''} live product(s)")
        except Exception as _e24:
            print(f"   ⚠️ catalog probe failed ({str(_e24)[:40]}) — verify manually")

    print(f"\n=== {'CRITICAL flags present — see above' if critical else 'no critical canonical flags'} ===")
    sys.exit(2 if critical else 0)

if __name__ == '__main__':
    main()
