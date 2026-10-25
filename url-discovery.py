#!/usr/bin/env python3
"""Find URLs a link-crawl STRUCTURALLY CANNOT reach, then diff them against what we crawled.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

WHY THIS EXISTS (owner question, 2026-07-31): "besides me manually searching site: in Google and
getting lucky, how else could we have found those URLs?" A BFS crawl only reaches LINKED pages, so
orphaned content — injected hack files, abandoned sections, staging hosts — is invisible to it.
There is NO request that makes a server enumerate itself (HTTP has no such verb, and a server that
offered one would be a security hole). But five INDEPENDENT channels each know about URLs the crawl
does not, and their union is far larger than any single one.

MEASURED on a chiropractic site (the pharma-hacked site):
  · CT logs      -> dev. / dev1. / old. / blog. subdomains  (no crawl or site: would see these)
  · Wayback CDX  -> 2,245 URLs incl. whole orphaned sections; found ZERO pharma (too recent)
  · WP REST API  -> fully open, self-enumerating (/wp-json/ = 256KB of route definitions)
  · sitemaps     -> 5 sub-sitemaps
  · path probe   -> /autodiscover/ed/ = HTTP 200, 184KB German pharma page  <- THE ONE THAT HITS
KEY LESSON: injected pages are FILES, not CMS content — WordPress doesn't know they exist, nothing
links to them, and archives are too slow. They live only on the filesystem, in Google's index, and
in the attacker's own sitemap. That is why `site:` worked and every content channel failed, and why
a PATH PROBE is the durable detector.

Usage: url-discovery.py <domain-or-url> [crawl.json]
"""
import json, os, re, sys, urllib.request
from urllib.parse import urlparse
# Shared transport (2026-08-06): installs the CRAWL_PROXY opener globally (see crawl_transport.py)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl_transport as _tx
import kit_env

# SITEMAP CHILD URLS WITH ESCAPED QUERY STRINGS (a Shopify store 2026-09-22): Shopify's index
# lists children as sitemap_products_1.xml?from=…&amp;to=… — XML-escaped, and with a query after
# .xml. `loc.endswith('.xml')` was False so the children were treated as PAGES (5 "orphans"), and the
# un-unescaped URL fetched with a literal &amp; returned 0 <loc>s (sitemap count 26 instead of 2,285).
import html as _html_sm
from urllib.parse import urlparse as _up_sm
def _sm_unescape(u): return _html_sm.unescape((u or "").strip())
def _sm_is_index(u): return _up_sm(_sm_unescape(u)).path.lower().endswith(".xml")

raw = sys.argv[1]
dom = urlparse(raw if "://" in raw else "https://" + raw).netloc or raw
SCHEME = urlparse(raw).scheme if "://" in raw else "https"   # keep the given scheme (http fixtures, http-only sites)
BASE = f"{SCHEME}://{dom}"
# KIT_NO_THIRD_PARTY=1 skips the outside services (crt.sh, the Wayback Machine): offline runs and tests
NO_THIRD = bool(os.environ.get("KIT_NO_THIRD_PARTY"))
apex = re.sub(r"^www\.", "", dom)
crawl_json = sys.argv[2] if len(sys.argv) > 2 else None

# UA comes from the shared transport so CRAWL_UA is honoured (some hosts, e.g. SiteGround, 403 the Chrome UA
# even through the residential proxy — a garage-door site 2026-09-22: CT hosts 403, "0 sitemap URLs",
# "no open CMS API" while check 13 read 45 sitemap URLs with the Safari UA on the same proxy).
UA = dict(_tx.HDRS)

def get(u, timeout=30):
    # Shared transport: proxy + CRAWL_UA + gzip sniff. The bare urlopen here decoded an
    # unconditionally-gzipped sitemap as mojibake -> "1 URL across 3 sitemap files" while check 13
    # read 46 from the same files (a garage-door site 2026-09-22).
    return _tx.fetch(u, timeout=timeout)[1] or ""

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

def status(u, timeout=15, follow=True):
    """follow=False returns the REAL status + Location. Probing MUST not follow redirects: many
    CMSs (WordPress fuzzy slug matching) 301 any unknown path to the nearest post, which makes a
    redirect-following probe report a phantom 200 with a large body."""
    try:
        if follow:
            return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=timeout).status
        o = urllib.request.build_opener(*([urllib.request.ProxyHandler({'http': _tx.PROXY, 'https': _tx.PROXY})] if _tx.PROXY else []), _NoRedirect)  # proxy-aware (2026-09-22)
        return o.open(urllib.request.Request(u, headers=UA), timeout=timeout).status
    except urllib.error.HTTPError as e:
        return (e.code, e.headers.get("Location", "")) if not follow else e.code
    except Exception as e:
        return getattr(e, "code", None)

def norm(u):
    return re.sub(r"^https?://(www\.)?", "", (u or "").split("#")[0]).rstrip("/").lower()

found = {}                      # normalised url -> set of channels
def add(u, ch):
    if u and "://" in u:
        found.setdefault(norm(u), set()).add(ch)

# --- 1. CERTIFICATE TRANSPARENCY: every cert ever issued for the domain ----------------------
print("1. Certificate Transparency (hidden/forgotten HOSTS)")
hosts = set()
try:
    ct = [] if NO_THIRD else json.loads(get(f"https://crt.sh/?q=%25.{apex}&output=json", 45) or "[]")
    hosts = {n.strip().lower() for e in ct for n in (e.get("name_value") or "").split("\n")
             if n.strip() and "*" not in n}
except Exception:
    pass
if hosts:
    print(f"   {len(hosts)} hostname(s) ever certificated:")
    for h in sorted(hosts):
        live = status(f"https://{h}/")
        mark = "  <-- LIVE, not the main site" if live and live < 400 and h not in (apex, "www." + apex) else ""
        print(f"     {h:44} {live}{mark}")
        if live and live < 400:
            add(f"https://{h}/", "ct")
else:
    print("   ⚠️ crt.sh returned NOTHING — this is UNKNOWN, not clean. Retry later; do not treat")
    print("      the absence of results as 'no hidden hosts'.")

# --- 2. WAYBACK CDX: everything the Internet Archive ever saw --------------------------------
print("\n2. Wayback CDX (orphaned / deleted / never-linked pages)")
wb = "" if NO_THIRD else get(f"http://web.archive.org/cdx/search/cdx?url={apex}*&output=text&fl=original"
         f"&collapse=urlkey&limit=5000", 60)
wb_urls = [l.strip() for l in wb.splitlines() if l.strip()]
for u in wb_urls:
    add(u, "wayback")
print(f"   {len(wb_urls)} URL(s) ever archived")
# LIVENESS SAMPLE: archived != live. Classify a sample so the diff below is not just old-site noise.
if wb_urls:
    import random as _rnd
    _rnd.seed(7)                                   # deterministic sample
    _samp = _rnd.sample(wb_urls, min(15, len(wb_urls)))
    _live = _dead = 0
    _dead_paths = []
    for _u in _samp:
        _st = status(_u, 10)
        if _st and _st < 400:
            _live += 1
        elif _st in (404, 410):
            _dead += 1
            _dead_paths.append(re.sub(r"https?://[^/]+", "", _u))
    _pct = _dead * 100 // max(1, len(_samp))
    print(f"   liveness sample ({len(_samp)} URLs): {_live} live · {_dead} dead ({_pct}% 404/410)")
    _conc = 0
    if _dead_paths:
        _pc = {}
        for _p in _dead_paths:
            _sg = _p.strip("/").split("/")[0]
            if _sg:
                _pc[_sg] = _pc.get(_sg, 0) + 1
        _conc = (max(_pc.values()) * 100 // max(1, len(_dead_paths))) if _pc else 0
    if _pct >= 60 and _conc >= 60:
        # ONE prefix dominating the dead set = a migration that dropped its redirects (not churn)
        _pref = {}
        for _p in _dead_paths:
            _seg = _p.strip("/").split("/")[0]
            if _seg:
                _pref[_seg] = _pref.get(_seg, 0) + 1
        _top = sorted(_pref.items(), key=lambda x: -x[1])[:3]
        print(f"   🚩 MOST ARCHIVED URLs ARE NOW DEAD — the previous site's URLs 404 with no")
        print(f"      redirects. Every inbound link and all accumulated equity to them is lost.")
        if _top:
            print(f"      dead prefixes in the sample: " +
                  ", ".join(f"/{k}/ ({v})" for k, v in _top))
        print("      FIX: 301 the old URL patterns to their closest current equivalents.")
    elif _pct >= 60:
        print(f"   ⚠️ {_pct}% of sampled archived URLs are dead but spread across prefixes — likely")
        print("      ordinary content churn over the years, not a failed migration. Worth a look if")
        print("      any of those old URLs still earn links.")

# --- 3. CMS SELF-ENUMERATION: the CMS lists its own content, no links required ----------------
print("\n3. CMS API self-enumeration")
cms = 0
for ep, label in [("wp-json/wp/v2/pages?per_page=100&_fields=link", "WP pages"),
                  ("wp-json/wp/v2/posts?per_page=100&_fields=link", "WP posts"),
                  ("wp-json/wp/v2/media?per_page=100&_fields=link", "WP media")]:
    try:
        for it in json.loads(get(f"{BASE}/{ep}", 25) or "[]"):
            if it.get("link"):
                add(it["link"], "cms"); cms += 1
    except Exception:
        pass
if cms:
    print(f"   {cms} URL(s) from the CMS API")
else:
    _root = get(f"{BASE}/wp-json/", 20)
    if '"namespaces"' in _root or '"routes"' in _root:
        print("   ⚠️ /wp-json/ IS open (WordPress confirmed) but the v2 collections returned nothing —")
        print("      likely a custom post type. Enumerate types: /wp-json/wp/v2/types")
    else:
        print("   (no open CMS API — not WordPress, or locked down)")

# --- 4. SITEMAPS (follow the index) ----------------------------------------------------------
print("\n4. Sitemaps")
sm_seen, q, sm_n = set(), [], 0
rb = get(f"{BASE}/robots.txt", 20)
q = re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", rb) or \
    [f"{BASE}/sitemap.xml", f"{BASE}/sitemap_index.xml"]
while q and len(sm_seen) < 30:
    s = q.pop()
    if s in sm_seen:
        continue
    sm_seen.add(s)
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", get(s, 25)):
        loc = _sm_unescape(loc)
        if _sm_is_index(loc):
            q.append(loc)
        else:
            add(loc, "sitemap"); sm_n += 1
print(f"   {sm_n} URL(s) across {len(sm_seen)} sitemap file(s)")

# --- 5. PATH PROBE: the ONLY channel that finds a live injection ------------------------------
# Injected pages are FILES, not CMS content: unlinked, un-archived, invisible to any API. Probe the
# directories attack kits actually use. /autodiscover/ed/ on a chiropractic site returned 200 + 184KB.
print("\n5. Hack/staging path probe (finds live injections nothing else can)")
PROBE = ["autodiscover/", "autodiscover/ed/", ".well-known/", "wp-content/uploads/", "tmp/",
         "cache/", "old/", "backup/", "staging/", "dev/", "test/", "new/", "_vti_bin/",
         "cgi-bin/", "shop/", "goods/", "wp-content/plugins/", "assets/", "files/", "media/"]
hits = 0
for p in PROBE:
    u = f"{BASE}/{p}"
    _r = status(u, 12, follow=False)
    if isinstance(_r, tuple):
        _code, _loc = _r
        if 300 <= (_code or 0) < 400:
            # DISTINGUISH two very different 3xx cases (learned the hard way 2026-07-31):
            #  (a) SAME PATH on a host variant (apex->www, http->https) = canonicalisation. The
            #      resource EXISTS; follow it and keep probing. Suppressing these hid the REAL
            #      a chiropractic site's pharma injection (/autodiscover/ed/ -> www/autodiscover/ed/).
            #  (b) DIFFERENT PATH = CMS fuzzy-slug matching to unrelated content (WordPress sends
            #      /dev/ -> /<cpt>_student/<name>.../). That IS a false positive; skip it.
            _tp = re.sub(r"^https?://[^/]+", "", _loc or "").rstrip("/").lower()
            _sp = "/" + p.rstrip("/").lower()
            if _tp == _sp:
                u = _loc                      # canonical host variant — probe the real URL
                st = status(u, 12, follow=False)
                st = st[0] if isinstance(st, tuple) else st
                if st != 200:
                    continue
            else:
                print(f"   {_code} /{p}  -> {(_loc or '')[:66]}")
                print(f"        (CMS fuzzy-slug redirect to DIFFERENT content — not an injection)")
                continue
        else:
            st = _code
    else:
        st = _r
    if st and st == 200:
        body = get(u, 20)
        spam = re.findall(r"(?i)\b(kamagra|viagra|cialis|sildenafil|casino|potenzmittel|rezeptfrei)\b", body)
        listing = bool(re.search(r"(?i)index of /", body))
        flag = ""
        if spam:
            flag = f"   🚨 SPAM CONTENT ({len(spam)} pharma terms) — LIKELY INJECTION"
        elif listing:
            flag = "   🚩 DIRECTORY LISTING enabled — every file here is discoverable"
        if spam or listing or len(body) > 5000:
            hits += 1
            print(f"   {st} {len(body):>7}B  /{p}{flag}")
            add(u, "probe")
if not hits:
    print("   (no unexpected 200s on probed paths)")

# --- DIFF vs the crawl ------------------------------------------------------------------------
print("\n" + "=" * 78)
if crawl_json and os.path.exists(crawl_json):
    crawled = {norm(p["url"]) for p in (json.load(open(crawl_json)).get("pages") or [])}
    missed = {u: c for u, c in found.items() if u not in crawled}
    print(f"DISCOVERY DIFF: {len(found)} URLs found across channels · {len(crawled)} crawled · "
          f"{len(missed)} NOT REACHED BY THE CRAWL")
    by_ch = {}
    for u, chs in missed.items():
        by_ch.setdefault(",".join(sorted(chs)), []).append(u)
    for ch, us in sorted(by_ch.items(), key=lambda x: -len(x[1])):
        print(f"\n  [{ch}] {len(us)} URL(s) a link-crawl could never reach:")
        for u in sorted(us)[:12]:
            print(f"     {u}")
        if len(us) > 12:
            print(f"     … +{len(us) - 12} more")
    out = (crawl_json.rsplit("-full.json", 1)[0] + "-discovery.json") if crawl_json.endswith("-full.json") \
        else str(kit_env.CRAWL_DIR / "url-discovery.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump({"domain": dom, "found": {u: sorted(c) for u, c in found.items()},
               "not_crawled": sorted(missed)}, open(out, "w"), indent=1)
    print(f"\n  -> {out}")
else:
    print(f"DISCOVERY TOTAL: {len(found)} unique URL(s) across all channels "
          f"(pass a crawl.json to diff against what was actually reached)")
