#!/usr/bin/env python3
"""PHASE-2: hacked-content / index-bloat scan. A crawl can't find ORPHANED injections
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.
(pharma-hack pages aren't linked from the site). This compares the crawl to (a) every sitemap
and (b) Google's index, and flags spam-signature paths. Found live: a chiropractic site had
~96 /autodiscover/ed/*.php German pharma pages indexed, 0 linked, 0 in our crawl.
Usage: hack-scan.py <crawl.json> <homepage-url>
"""
import sys, json, re, os as _os, urllib.request
from urllib.parse import urlparse
# §65 2026-08-06: shared transport — installs the CRAWL_PROXY opener globally (see crawl_transport.py)
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import crawl_transport as _tx

# SITEMAP CHILD URLS WITH ESCAPED QUERY STRINGS (a Shopify store 2026-09-22): Shopify's index
# lists children as sitemap_products_1.xml?from=…&amp;to=… — XML-escaped, and with a query after
# .xml. `loc.endswith('.xml')` was False so the children were treated as PAGES (5 "orphans"), and the
# un-unescaped URL fetched with a literal &amp; returned 0 <loc>s (sitemap count 26 instead of 2,285).
import html as _html_sm
from urllib.parse import urlparse as _up_sm
def _sm_unescape(u): return _html_sm.unescape((u or "").strip())
def _sm_is_index(u): return _up_sm(_sm_unescape(u)).path.lower().endswith(".xml")

CRAWL, HOME = sys.argv[1], sys.argv[2]
base = re.match(r'https?://[^/]+', HOME).group(0)
def get(u):
    try: return urllib.request.urlopen(urllib.request.Request(u,headers=_tx.HDRS),timeout=25).read().decode('utf-8','replace')   # bot UA, paced, robots-checked
    except Exception: return ''

_crawl_doc = json.load(open(CRAWL))
_pages = _crawl_doc.get('pages', [])
# robots.txt-disallowed URLs (owner safety rule 2026-10-14): the crawl recorded them without fetching
# them, and this scan does not fetch them either. A sitemap URL robots.txt disallows is "not checked
# (robots)", never "not reached by the crawl" (that would read as an orphan or an injection).
_robots_skip = {(d.get('url') or '').split('#')[0].rstrip('/') for d in (_crawl_doc.get('robots_disallowed') or [])}
# DENOMINATOR MUST BE THE INDEXABLE SET (fixed 2026-07-31). This check produced the only critical of
# a clean-context test run and got it BACKWARDS: a law-firm site's 1000 crawl rows included 511 identical
# `?category=` facet URLs (every one canonical -> /blog), 109 redirect rows and 11 non-200s. Google's
# 407 against 1000 read "UNDER-INDEXED, 40%, Google is ignoring most of this site". Against the 444
# genuinely indexable URLs it is 91% — healthy. NEVER divide by raw crawl rows.
def _indexable(p):
    u = (p.get('url') or '').split('#')[0].rstrip('/')
    if p.get('status') != 200 or p.get('redirected'):
        return False
    c = (p.get('canonical') or '').split('#')[0].rstrip('/')
    if c and c != u:                      # canonicalised elsewhere = not its own index entry
        return False
    if 'noindex' in (p.get('meta_robots') or '').lower():
        return False
    return True
crawled = {p['url'].split('#')[0].rstrip('/') for p in _pages if _indexable(p)}
_excluded = len(_pages) - len(crawled)
# REACHED set for the orphan diff (fixed 2026-07-31, a roofing site): the orphan question is
# "did the crawl get there AT ALL", so it must include every fetched row AND redirect destinations
# — NOT the indexable subset. On that roofing site the indexable set collapsed to 8 rows and the orphan diff
# claimed 394 of 395 sitemap URLs were unreachable, including /about linked in the global nav.
reached = set()
for p in _pages:
    reached.add(p.get('url','').split('#')[0].rstrip('/'))
    if p.get('final_url'): reached.add(p['final_url'].split('#')[0].rstrip('/'))

# 1. gather every sitemap URL (follow sitemap index)
sm_urls=set(); seen=set(); sm_spelling={}
robots=get(base+'/robots.txt')
roots=re.findall(r'Sitemap:\s*(\S+)', robots) or [base+'/sitemap.xml',base+'/sitemap_index.xml']
queue=list(roots)
while queue:
    sm=queue.pop()
    if sm in seen: continue
    seen.add(sm); x=get(sm)
    for loc in re.findall(r'<loc>([^<]+)</loc>', x):
        loc = _sm_unescape(loc)
        if _sm_is_index(loc): queue.append(loc)
        else:
            _as_listed = loc.split('#')[0].strip()
            sm_urls.add(_as_listed.rstrip('/'))
            sm_spelling[_as_listed.rstrip('/')] = _as_listed   # keep the sitemap's own spelling

# 2. spam-signature paths anywhere we can see them
SPAM_PATHS=['/autodiscover/','/wp-content/uploads/']  # dirs that should NOT hold .php
# WORD BOUNDARIES REQUIRED: an unbounded 'cialis' matched 'spe-cialis-ts' and declared a clean
# cleaning-company site HACKED (a cleaning-company site 2026-07-30). Never substring-match pharma terms.
SPAM_KW=re.compile(r'\b(kamagra|viagra|cialis|sildenafil|lovegra|levitra|tadalafil|casino|escort|replica)\b|\.php$',re.I)
suspicious=set()
for u in sm_urls:
    if SPAM_KW.search(u) or any(sp in u for sp in SPAM_PATHS): suspicious.add(u)

print(f"crawled pages: {len(crawled)}")
print(f"sitemap URLs:  {len(sm_urls)}")
# ORPHAN = a page NO INTERNAL LINK points to (fixed again 2026-07-31, same roofing-site run): the
# crawler SEEDS from the sitemap, so "reached by the crawl" is true for every sitemap URL by
# construction and the old set-diff reported 0 orphans while 151 live blog posts had zero inbound
# links. The link graph (`inlinks`, counted across every crawled page) is the real test; URLs the
# crawl never fetched at all (budget cap) still count as unknown-orphans.
_row = {}
for p in _pages:
    _row[p.get('url', '').split('#')[0].rstrip('/')] = p
in_sm_not_crawled = set()
not_checked_robots = set()
for _u in sm_urls:
    if _u in _robots_skip or not _tx.allowed(sm_spelling.get(_u, _u)):
        not_checked_robots.add(_u)
        continue
    _r = _row.get(_u)
    if _r is None:
        if _u not in reached:
            in_sm_not_crawled.add(_u)       # never fetched and never seen as a destination
    elif (_r.get('status') == 200 and not _r.get('redirected')
          and (_r.get('inlinks') or 0) == 0 and urlparse(_u).path not in ('', '/')):
        in_sm_not_crawled.add(_u)           # fetched OK but nothing on the site links to it
# SAMPLE-CRAWL GUARD (a Shopify store 2026-09-22): when the crawl was CAPPED (rows == CRAWL_MAXP)
# and the sitemap is bigger than the crawl, "zero inlinks in the crawl graph" is a property of the
# sample, not of the site — 2,000 products would print as orphans. Say so and skip the orphan test.
_maxp = int(_os.environ.get("CRAWL_MAXP") or 0)
if _maxp and len(_pages) >= _maxp and len(sm_urls) > len(crawled):
    print(f"SAMPLE CRAWL: crawl capped at {_maxp} rows but the sitemap lists {len(sm_urls)} URLs — the orphan test is NOT meaningful on a sample; skipped (0 reported). Size the site from the sitemap instead.")
    in_sm_not_crawled = set()
print(f"in sitemap but NOT reached by crawl (orphans/injections): {len(in_sm_not_crawled)}")
print(f"in sitemap but not checked (robots): {len(not_checked_robots)}   (robots.txt asks crawlers to stay out; not counted as orphans)")
print("   (orphan test = zero internal links pointing at the page, from the crawl's link graph)")
# List them + probe a sample (2026-07-31, owner: "is this bad internal linking?"). The count alone
# can't answer the reader's question: a LIVE unreached URL is an orphan page (internal-linking
# problem); a REDIRECTING one is a stale sitemap entry (sitemap problem). Probe 12 to say which.
if in_sm_not_crawled:
    import random, concurrent.futures as _cf
    random.seed(0)          # deterministic sample so re-runs are comparable
    _sample = [sm_spelling.get(u, u) for u in random.sample(sorted(in_sm_not_crawled), min(12, len(in_sm_not_crawled)))]
    class _NR(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    # Private opener must carry the proxy too — bare build_opener() bypasses the CRAWL_PROXY opener
    # installed by crawl_transport, so on a walled host every orphan probe 'error'ed from the crawl IP
    # (a garage-door site 2026-09-22: ORPHAN-SAMPLE error=3 of 3 on pages that are live 200 via proxy).
    _op = urllib.request.build_opener(*([urllib.request.ProxyHandler({'http': _tx.PROXY, 'https': _tx.PROXY})] if _tx.PROXY else []), _NR)
    def _probe(u):
        try:
            r = _op.open(urllib.request.Request(u, method='HEAD',
                headers=_tx.HDRS), timeout=15)   # honours CRAWL_UA (§45, a garage-door site 2026-09-22)
            return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return 0
    with _cf.ThreadPoolExecutor(6) as _ex:
        _st = list(_ex.map(_probe, _sample))
    _live = sum(1 for c in _st if c == 200)
    _redir = sum(1 for c in _st if 300 <= c < 400)
    _err = len(_st) - _live - _redir
    print(f"ORPHAN-SAMPLE: live={_live} redirect={_redir} error={_err} of {len(_st)}")
    # WHY are they orphaned? (added 2026-07-31, a roofing site: ~270 live blog posts had zero
    # internal links because /blog/ pagination is a FacetWP <a data-page="2"> BUTTON with no href —
    # a crawler can never advance past page one.) Fetch the likely index page(s) and look for
    # JS-only pagination markers so the report can name the CAUSE, not just the count.
    from collections import Counter as _Ct
    _segs = _Ct()
    for _u9 in in_sm_not_crawled:
        _p9 = re.sub(r'https?://[^/]+', '', _u9).strip('/')
        _segs[_p9.split('/')[0] if _p9 else '(root)'] += 1
    _cands = ['/blog/', '/news/', '/articles/']
    _top_seg = _segs.most_common(1)[0][0] if _segs else ''
    if _top_seg not in ('', '(root)'):
        _cands.insert(0, f'/{_top_seg}/')
    for _idx in dict.fromkeys(_cands):
        _html9 = get(base + _idx)
        if not _html9:
            continue
        _jsmark = []
        if re.search(r'<a[^>]*data-page=[^>]*>', _html9):
            for _m9 in re.finditer(r'<a[^>]*data-page=[^>]*>', _html9):
                if 'href' not in _m9.group(0):
                    _jsmark.append('pagination <a data-page> without href'); break
        if 'facetwp' in _html9.lower():
            _jsmark.append('FacetWP (JS-driven listing)')
        if re.search(r'load[_ -]?more', _html9, re.I):
            _jsmark.append('load-more button')
        if _jsmark:
            print(f"ORPHAN-CAUSE: {base + _idx} paginates with JavaScript only "
                  f"({'; '.join(dict.fromkeys(_jsmark))}) — crawlers cannot advance past page one")
            # SECOND LOCK (a roofing site 2026-07-31, owner spotted it): the WP paginated
            # archive /page/2/ EXISTS but canonicals to the base page — so even with real pager
            # links Google is told pages 2..N are duplicates of page 1 and discards the post
            # links they carry. Paginated pages must SELF-canonicalize. The crawl never fetches
            # /page/2/ (nothing links to it — that is the orphan finding), so check 2 cannot see
            # this; probe it here where we already know pagination is the story.
            _p2 = get(base + _idx + 'page/2/')
            if _p2:
                _cm = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', _p2)                       or re.search(r'href=["\']([^"\']+)["\'][^>]*rel=["\']canonical', _p2)
                if _cm:
                    _cv = _cm.group(1).rstrip('/')
                    if _cv == (base + _idx).rstrip('/'):
                        print(f"PAGINATION-CANONICAL-TRAP: {base + _idx}page/2/ exists but declares "
                              f"canonical -> {base + _idx} — paginated pages must self-canonicalize, "
                              f"or Google discards pages 2+ (and every post link on them) as duplicates")
            # THIRD LOCK (a roofing site 2026-07-31, owner: "it compounds the number of URLs"):
            # FacetWP also mints PARAMETER URLs (?_paged=N, ?_<facet>=X) via pushState. If the
            # server returns 200 for ANY parameter value with the same page-one content (no
            # server-side bound check — ?_paged=999 works), the URL space is unbounded: facet
            # values × page numbers × junk = thousands of crawlable duplicates, none blocked by
            # robots.txt, burning crawl budget at the exact spot real posts are starving for it.
            _q1 = get(base + _idx + '?_paged=2')
            _q2 = get(base + _idx + '?_paged=999')
            if _q1 and _q2:
                def _firstpost(h9):
                    _m9 = re.search(r'href="(https?://[^"]+' + re.escape(_idx) + r'[a-z0-9-]+/)"', h9)
                    return _m9.group(1) if _m9 else None
                if _firstpost(_q1) and _firstpost(_q1) == _firstpost(_q2) == _firstpost(_html9):
                    print(f"INFINITE-FACET-URLS: {base + _idx}?_paged=<any value> returns 200 with "
                          f"page-one content (tested 2 and 999) — filter/page parameters mint an "
                          f"unbounded space of duplicate URLs, and robots.txt does not block them")
                    # REAL combinations, not synthetic ones (owner correction 2026-07-31): Google
                    # doesn't invent ?_paged=999 — it interacts with the filters, and each
                    # filter-value × page-number pair becomes its own URL. Enumerate the actual
                    # space from the facet values on the page and probe one real combination.
                    # NOTE the \\?" — the facet markup lives inside FWP_JSON as JSON-encoded
                    # HTML, so every quote is backslash-escaped in the raw page source
                    _fvals = [v for v in re.findall(r'facetwp-(?:radio|checkbox)[^>]*data-value=\\?"([a-z0-9_-]+)', _html9) if v]
                    _fname = None
                    _mj = re.search(r'"facets":\s*\{\s*"([a-z0-9_]+)"', _html9)
                    if _mj and _mj.group(1) != 'pagination':
                        _fname = _mj.group(1)
                    _lastpg = re.findall(r'data-page=\\?"(\d+)', _html9)
                    _npages = max((int(x) for x in _lastpg), default=0)
                    if _fname and _fvals:
                        _cu = f"{base}{_idx}?_{_fname}={_fvals[0]}&_paged=2"
                        _ch = get(_cu)
                        if _ch:
                            _emptyc = 'no results' in _ch.lower() or _firstpost(_ch) == _firstpost(_html9)
                            print(f"FACET-COMBO: {len(_fvals)}+ filter values x {_npages} pager pages "
                                  f"= {len(_fvals) * max(1, _npages)}+ real combination URLs. Probed "
                                  f"{_cu}: loads (200), "
                                  f"{'serves the default/empty list' if _emptyc else 'serves filtered content'}, "
                                  f"canonical -> {base + _idx}")
            break
    for u in sorted(in_sm_not_crawled)[:300]:
        print(f"   ORPHAN: {sm_spelling.get(u, u)}")
if suspicious:
    print(f"\n🚨 SPAM-SIGNATURE URLs ({len(suspicious)}) — likely HACKED CONTENT:")
    for u in sorted(suspicious)[:15]: print(f"   {u}")
    print("   -> verify HTTP 200 + foreign/pharma title; if live, site is compromised. Remove files,")
    print("      patch WP+plugins, rotate passwords, GSC removal + security-review request.")
else:
    print("\n   no spam-signature paths in sitemaps")

# 3. Google index count vs crawl (the ONLY reliable orphan-hack detector) + spam-path site: probe
import os
key=os.environ.get('SERPAPI_KEY') or os.environ.get('SERP_API_KEY')   # optional; unset = index probe skipped
if not key:
    print("\n   index probe skipped: no SERPAPI_KEY (Google index count UNKNOWN, not zero)")
if key:
    dom=urlparse(HOME).netloc
    # `site:` must use the APEX domain, never www and never a scheme (owner correction 2026-07-31).
    # `site:www.example.com` scopes the query to that ONE subdomain and silently omits everything
    # indexed on the apex (or any other subdomain) — on a site serving BOTH hosts that under-counts
    # badly and we then read the shortfall as "Google is ignoring this site". `site:example.com`
    # covers the domain and its subdomains.
    dom = re.sub(r'^www\.', '', dom)
    def serp(q):
        u='https://serpapi.com/search.json?engine=google&q='+urllib.request.quote(q)+'&num=20&api_key='+key
        try: return json.loads(get(u) or '{}')
        except Exception: return {}
    idx=serp('site:'+dom)
    total=(idx.get('search_information') or {}).get('total_results')
    print(f"\nGoogle `site:` ESTIMATE for {dom}: ~{total}  vs {len(crawled)} INDEXABLE URLs we found "
          f"({_excluded} of {len(_pages)} crawl rows excluded — redirects, non-200s, "
          f"canonicalised-elsewhere, noindex)")
    if not total:
        print("   ⚠️ NO ESTIMATE RETURNED this run — `site:` gave nothing back. That is UNKNOWN, not")
        print("      'nothing indexed'. Do not draw any index conclusion from a null result; retry")
        print("      later or confirm in Search Console. (Observed live 2026-07-31: the same query")
        print("      for the same site returned ~71 minutes earlier and null on the next run — the")
        print("      clearest possible proof that `site:` is an estimate, not a measurement.)")
    print("   NOTE: `site:` is Google's own ESTIMATE, not a count. It varies between queries and")
    print("   pagination, and Google never exposes a true index total. Treat it as a CONFIDENCE")
    print("   SIGNAL about scale — a direction to investigate, never a metric to report as fact.")
    if total and total > len(crawled)*1.3:
        print(f"   🚩 Google's ESTIMATE (~{total}) is ~{total/max(1,len(crawled)):.1f}x the indexable "
              f"URLs we found — investigate the surplus (index bloat or injected pages).")
        print("      Estimate-based: a direction to investigate, not a measured count.")
    elif total and len(crawled) > 20 and total < len(crawled)*0.65:
        # TIERED, because `site:` is an ESTIMATE: a single cutoff either cries wolf on estimator
        # drift or goes silent on a real gap. Ratio <0.35 = wide enough to flag; 0.35-0.65 = worth
        # stating with an explicit caveat (that cleaning site sits at 0.40 and its shortfall IS real —
        # www/non-www duplication splitting signals — so silencing that band lost a true finding).
        # UNDER-INDEXING (added 2026-07-31): the ratio guard only ever fired on index >> crawl, so
        # the INVERSE — Google ignoring most of the site — printed as a bare informational line and
        # never reached the report. LIVE: that cleaning site ~70 indexed vs 451 crawled (~238 unique after
        # host duplication) = Google indexing well under half of what exists.
        _pct = total * 100 // max(1, len(crawled))
        _wide = total < len(crawled) * 0.35
        _mark = "🚩" if _wide else "⚠️"
        print(f"   {_mark} POSSIBLE UNDER-INDEXING: Google's estimate (~{total}) is below the "
              f"{len(crawled)} indexable URLs we found (roughly {_pct}%, ESTIMATE-BASED).")
        print("      This is a signal to INVESTIGATE, not a measured shortfall. Confirm in Search")
        console = "Console"  # noqa: F841 - kept explicit for the printed guidance below
        print("      Console (Pages report) before telling a client how much is unindexed.")
        print("      Usual causes, in order: host duplication (same page on www + apex splits"
              " signals), orphaned pages (nothing links to them), thin/duplicate content, or a")
        print("      sitemap that disagrees with the canonical host. Check 12/13 output above.")
    # probe common hack dirs directly in the index
    for probe in ['site:%s/autodiscover'%dom,'site:%s inurl:.php'%dom,'site:%s kamagra OR viagra OR cialis'%dom]:
        r=serp(probe); hits=r.get('organic_results') or []
        if hits:
            print(f"   🚨 `{probe}` -> {(r.get('search_information') or {}).get('total_results')} results:")
            for h in hits[:6]: print(f"        {h.get('link')}")

print("\nNOTE: orphaned injections may NOT appear in the sitemap either — also run a")
print("`site:<domain>` index-count check and compare to crawl count. A big gap = investigate.")
