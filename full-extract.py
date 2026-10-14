#!/usr/bin/env python3
"""full-extract.py — Screaming-Frog-style FULL per-URL extraction (datacenter, no proxy).

Crawls EVERY internal URL (sitemap + BFS internal-link discovery) and records nearly every
technical + on-page factor per page, plus every external link found across the site.

Per URL: status, redirect, content-type, indexability, robots(meta+header), title(+len),
meta description(+len), meta keywords, canonical, lang, charset, viewport, ALL OpenGraph tags,
ALL Twitter-card tags, full H1-H6 with text content, word count, HTML tag inventory (tag->count),
image count + missing-alt, JSON-LD schema types, internal links (href+anchor+rel), external links
(href+anchor+rel). After crawl: inlink counts + unique external link status (HEAD).

  .venv/bin/python full-extract.py <seedURL> [--max N] [--check-external] --out file.json

POLITE BY DEFAULT (owner safety rule 2026-10-14), on both the aiohttp and the curl transport:
  pages        --max          env CRAWL_MAXP         default 500
  concurrency  --concurrency  env CRAWL_CONCURRENCY  default 2
  delay        --delay        env CRAWL_DELAY        default 1 s between ANY two requests to a host
                              (a shared, reserved time slot, so it holds at concurrency > 1);
                              robots.txt Crawl-delay raises it: max(Crawl-delay, CRAWL_DELAY)
  user agent   env CRAWL_UA   default "GrokBot-SiteAudit (+https://github.com/PensacolaSeoCompany/site-audit-kit)"
ROBOTS.TXT IS OBEYED: fetched once per host before any page; the group for "GrokBot-SiteAudit"
(else "*") applies, RFC 9309 matching (crawl_transport.Robots). robots.txt 4xx = no rules;
5xx / no answer = crawl NOTHING on that host (fail safe; the seed host's failure ends the run
with exit 4 and summary.robots_txt="unreachable"). Disallowed URLs are not fetched: they are
listed in "robots_disallowed" (status null, note "robots-disallowed") and counted in
summary.robots_disallowed, never as errors.
READ-ONLY: GET requests only (plus HEAD for --check-external). Never submits a form or logs in.
"""
import asyncio, json, re, argparse, sys, os
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlparse, urldefrag
import aiohttp
import yarl
from bs4 import BeautifulSoup
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_env
import crawl_transport as _tx   # ONE noindex definition + URL equivalence (DE-03/DE-04, 2026-10-14)

# URLS GO ON THE WIRE EXACTLY AS WRITTEN (DE-03, a client site 2026-10-14). aiohttp re-quotes through
# yarl, which decoded /terms-%26-conditions-1 to /terms-&-conditions-1 (a different URI; false 404,
# false redirect). encoded=True keeps every escape; wire_url() only encodes space/non-ASCII.
def _wire(u):
    try:
        return yarl.URL(_tx.wire_url(u), encoded=True)
    except Exception:
        return u

# SITEMAP CHILD URLS WITH ESCAPED QUERY STRINGS (a Shopify store 2026-09-22): Shopify's index
# lists children as sitemap_products_1.xml?from=…&amp;to=… — XML-escaped, and with a query after
# .xml. `loc.endswith('.xml')` was False so the children were treated as PAGES (5 "orphans"), and the
# un-unescaped URL fetched with a literal &amp; returned 0 <loc>s (sitemap count 26 instead of 2,285).
import html as _html_sm
from urllib.parse import urlparse as _up_sm
def _sm_unescape(u): return _html_sm.unescape((u or "").strip())
def _sm_is_index(u): return _up_sm(_sm_unescape(u)).path.lower().endswith(".xml")

ap = argparse.ArgumentParser()
ap.add_argument("seed")
ap.add_argument("--max", type=int, default=int(os.environ.get("CRAWL_MAXP") or 500))
ap.add_argument("--concurrency", type=int, default=int(os.environ.get("CRAWL_CONCURRENCY") or 2))
ap.add_argument("--check-external", action="store_true", help="HEAD-check unique external links for status")
ap.add_argument("--curl", action="store_true", default=bool(os.environ.get("CRAWL_CURL")), help="fetch via curl subprocess instead of aiohttp — for sites "
                "(Shopify/Cloudflare) that TLS-fingerprint and 429 Python HTTP clients while serving curl/browsers fine")
ap.add_argument("--delay", type=float, default=float(os.environ.get("CRAWL_DELAY") or 1), help="minimum seconds between any two "
                "requests to one host (both transports; robots.txt Crawl-delay can raise it)")
ap.add_argument("--curl-ssh", default=os.environ.get("CRAWL_CURL_SSH") or None,
                help="run the curl on a remote host over ssh (e.g. user@second-host.example) — "
                "fresh IP when this machine is velocity-throttled (429) by the target; env CRAWL_CURL_SSH sets the default")
ap.add_argument("--proxy", default=os.environ.get("CRAWL_PROXY") or None,
                help="route curl through this proxy (e.g. http://user:pass@proxy.example:80) — a RESIDENTIAL "
                "proxy defeats IP-reputation WAFs (SiteGround sg-captcha 202) that datacenter IPs can't pass. "
                "Implies --curl. env CRAWL_PROXY sets the default. (a client site, 2026-08-04)")
ap.add_argument("--out", default=None)
A = ap.parse_args()
if A.proxy:
    A.curl = True     # proxy routing only applies to the curl transport
SEED = A.seed if A.seed.startswith("http") else "https://" + A.seed
HOST = urlparse(SEED).hostname
REG = HOST[4:] if HOST.lower().startswith("www.") else HOST   # strip a LEADING www. only
OUT = A.out or str(kit_env.CRAWL_DIR / f"{REG}-full-extract.json")
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
# env CRAWL_UA overrides (some WAFs hard-403 a "bot"-named UA); the default names the kit honestly
UA = _tx.UA
A.concurrency = max(1, A.concurrency)
A.delay = max(0.0, A.delay)

# ---- robots.txt + pacing, per host --------------------------------------------------------------
ROBOTS = {}        # host -> crawl_transport.Robots
PACERS = {}        # host -> crawl_transport.Pacer (interval = max(--delay, Crawl-delay))
import threading as _th
_PACE_LOCK = _th.Lock()
def pacer_for(url):
    h = (urlparse(url).netloc or "").lower()
    with _PACE_LOCK:
        if h not in PACERS:
            PACERS[h] = _tx.Pacer(A.delay)
        return PACERS[h]

# in-scope = the audited host OR any SUBDOMAIN of it (same registrable domain). A subdomain of the
# audited site (studio./blog./uat./phpmyadmin. …) IS part of the site and must be crawled — a report
# that only sees www. is blind to duplicate staging apps, exposed admin panels, orphaned blogs, etc.
# (owner 2026-08-04). We derive scope from the KNOWN seed host, so no Public Suffix List guessing is
# needed: REG is the literal registrable host from the seed (www. stripped), and multi-part TLDs
# (example.co.uk, example.ca) scope correctly because we match against that literal, not a guessed eTLD+1.
def same_site(u):
    h = (urlparse(u).hostname or "").lower().lstrip(".")
    if h.startswith("www."):
        h = h[4:]
    return h == REG or h.endswith("." + REG)

def norm(u):
    u, _ = urldefrag(u)
    return u.rstrip("/") if urlparse(u).path not in ("", "/") else u

import itertools as _it
_RR_EXITS = _it.cycle([h.strip() for h in (A.curl_ssh or "").split(",") if h.strip()]) if A.curl_ssh else None

def fetch_curl(url):
    """curl transport (subprocess). Used with --curl for hosts that 429 Python clients.
    Retries 429 with backoff (Shopify storefront velocity limits) and paces via --delay."""
    import subprocess, time
    for attempt in range(4):
        pacer_for(url).wait()        # reserved slot: holds at any concurrency
        try:
            # ACCEPT-ENCODING WALL (2026-08-10, a carpet-cleaning site §62). Some WAFs
            # ("Checking your browser before accessing. Just a moment...") fingerprint on the
            # PRESENCE of an Accept-Encoding header — curl --compressed sends one, so every page
            # came back as the challenge shell while the SAME curl without --compressed returned
            # the real HTML (deterministic 3/3 both ways). CRAWL_NO_AE=1 drops --compressed so
            # curl sends NO Accept-Encoding at all. The raw-gzip guard below still applies: an
            # origin that gzips unconditionally would abort the run rather than ship mojibake.
            # -D - puts every response's header block (each redirect hop, then the final answer)
            # on stdout ahead of the body: the curl path recorded NO X-Robots-Tag and no hop list
            # before 2026-10-14 (DE-04: "xrobots" was hard-coded "").
            cmd = ["curl", "-sL", "-m", "25", "-A", UA, "-D", "-",
                   "-o", "-", "-w", "\n===CURLMETA===%{http_code}\t%{url_effective}\t%{content_type}", url]
            if not os.environ.get("CRAWL_NO_AE"):
                cmd[2:2] = ["--compressed"]
            if A.proxy:
                cmd[1:1] = ["-x", A.proxy]     # residential proxy to pass IP-reputation WAFs
            # MULTI-EXIT ROUND-ROBIN (a Shopify store 2026-09-22: a second
            # crawl host was available). --curl-ssh accepts a comma list; "local" means run curl here. Each request takes
            # the next exit, so a per-IP velocity limit (Shopify ~1 req/s) is paid per exit — two
            # exits = twice the safe throughput. Single-host value behaves exactly as before.
            _exit = next(_RR_EXITS) if _RR_EXITS else None
            if _exit and _exit != "local":
                import shlex
                cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", _exit, " ".join(shlex.quote(c) for c in cmd)]
            r = subprocess.run(cmd, capture_output=True, timeout=45)
            _hdr_blocks, _raw = _split_curl_headers(r.stdout)
            # BINARY/COMPRESSED-BODY GUARD (2026-08-05). curl must send --compressed or an origin
            # that gzips unconditionally hands back raw gzip, which decodes to mojibake: the parser
            # then records a page with a WORD COUNT but no title, no canonical, no headings — and
            # the audit ships "66 pages have no title tag" as a client finding. Sucuri behind the
            # residential proxy does exactly this (it gzips even with no Accept-Encoding, because
            # the proxy downgrades to HTTP/1.0 + `vary: Accept-Encoding`). --compressed is set
            # above; this guard catches any future path that loses it, loudly.
            if _raw[:3] == b"\x1f\x8b\x08":
                print(f"[FATAL] {url}: response is RAW GZIP — curl lost --compressed. Every page "
                      f"would parse as titleless mojibake. Aborting rather than shipping false "
                      f"'missing title/canonical' findings.", file=sys.stderr)
                sys.exit(3)
            out = _raw.decode("utf-8", "replace")
            body, _, meta = out.rpartition("\n===CURLMETA===")
            if not meta:
                return {"status": None, "error": "curl no output", "final_url": url, "ct": "",
                        "xrobots_headers": None, "redirect_hops": None, "body": "", "redirected": False}
            code, final, ct = (meta.split("\t") + ["", ""])[:3]
            status = int(code) if code.isdigit() else None
            if status == 429 and attempt < 3:
                time.sleep(5 * (2 ** attempt))   # 5s, 10s, 20s
                continue
            # CHALLENGE-PAGE RETRY (a garage-door site 2026-09-22, §43). SiteGround's sg-captcha
            # answers HTTP 202 with a 167-byte meta-refresh to /.well-known/sgcaptcha/. Even through
            # the residential pool 2 of 49 pages drew a flagged exit IP and came back as that shell;
            # the SAME URLs re-fetched 200 on the next rotation. Un-retried, each shell becomes a
            # titleless 0-word row that ships as "missing title / H1 / meta description". Retry with
            # backoff exactly like a 429 — a rotating proxy hands us a new exit each attempt.
            if status == 202 and attempt < 3 and re.search(r"sgcaptcha|sg-captcha|Robot Challenge", body, re.I):
                print(f"[challenge] {url}: 202 sg-captcha shell, retry {attempt+1}/3", file=sys.stderr)
                time.sleep(5 * (2 ** attempt))
                continue
            _hops, _xr = _curl_hops_and_xrobots(url, _hdr_blocks)
            _final = final or url
            return {"status": status, "final_url": _final, "ct": ct,
                    "xrobots_headers": _xr, "redirect_hops": _hops, "body": body,
                    # DE-03: a redirect is a real hop, not a string difference (74 of 451 rows
                    # were encoding-only "redirects" that dropped 200 pages out of content checks)
                    "redirected": bool(_hops) or not _tx.same_url(_final, url)}
        except Exception as e:
            return {"status": None, "error": str(e), "final_url": url, "ct": "",
                    "xrobots_headers": None, "redirect_hops": None, "body": "", "redirected": False}

def _split_curl_headers(raw):
    """curl -D - output -> ([header blocks], rest). Consumes 1xx blocks, proxy CONNECT blocks and
    redirect blocks (3xx + Location), then exactly one final block; the rest is body + -w meta."""
    blocks = []
    while raw.startswith(b"HTTP/"):
        cands = [(raw.find(sep), len(sep)) for sep in (b"\r\n\r\n", b"\n\n") if raw.find(sep) >= 0]
        if not cands:
            break
        i, n = min(cands)
        blk = raw[:i].decode("latin-1", "replace")
        blocks.append(blk)
        raw = raw[i + n:]
        first = blk.split("\n", 1)[0]
        parts = first.split()
        st = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        is_redirect = 300 <= st < 400 and re.search(r"(?im)^location:", blk)
        if not (100 <= st < 200 or is_redirect or "connection established" in first.lower()):
            break                                   # the final response: what follows is the body
    return blocks, raw

def _curl_hops_and_xrobots(url, blocks):
    """-> (redirect hop list, every X-Robots-Tag value of the FINAL response). None = not recorded."""
    if not blocks:
        return None, None
    hops, cur = [], url
    for blk in blocks[:-1]:
        first = blk.split("\n", 1)[0].split()
        st = int(first[1]) if len(first) > 1 and first[1].isdigit() else 0
        loc = re.search(r"(?im)^location:\s*(\S+)", blk)
        if 300 <= st < 400 and loc:
            hops.append({"url": cur, "status": st})
            cur = urljoin(cur, loc.group(1).strip())
    xr = [m.strip() for m in re.findall(r"(?im)^x-robots-tag:\s*(.*?)\s*$", blocks[-1])]
    return hops, xr

async def fetch(session, url):
    if A.curl:
        return await asyncio.get_event_loop().run_in_executor(None, fetch_curl, url)
    await pacer_for(url).await_slot()
    try:
        async with session.get(_wire(url), allow_redirects=True, timeout=aiohttp.ClientTimeout(total=25)) as r:
            body = await r.text(errors="ignore")
            final = str(r.url)
            hops = [{"url": str(h.url), "status": h.status} for h in r.history]
            return {"status": r.status, "final_url": final, "ct": r.headers.get("content-type",""),
                    # DE-04: EVERY X-Robots-Tag header (.get returned only the first)
                    "xrobots_headers": list(r.headers.getall("x-robots-tag", [])), "body": body,
                    "redirect_hops": hops,
                    "redirected": bool(hops) or not _tx.same_url(final, url)}
    except Exception as e:
        return {"status": None, "error": str(e), "final_url": url, "ct": "",
                "xrobots_headers": None, "redirect_hops": None, "body": "", "redirected": False}

def extract(url, resp):
    html = resp["body"]
    _xr = resp.get("xrobots_headers")          # list; None = not recorded (transport error)
    if "html" not in resp["ct"] and not html.lstrip().lower().startswith("<!doctype html"):
        # DE-04: a PDF's X-Robots-Tag: noindex was never stored (non-HTML rows returned first)
        return {"url": url, "final_url": resp["final_url"], "status": resp["status"],
                "redirected": resp["redirected"], "redirect_hops": resp.get("redirect_hops"),
                "content_type": resp["ct"], "non_html": True,
                "xrobots_headers": _xr, "noindex": _tx.robots_noindex(_xr or []),
                "internal_links": [], "external_links": []}
    soup = BeautifulSoup(html, "lxml")
    def metas(attr, pref):
        out = {}
        for m in soup.find_all("meta"):
            k = (m.get(attr) or "").lower()
            if k.startswith(pref): out[k] = m.get("content", "")
        return out
    title = (soup.title.get_text(strip=True) if soup.title else "")
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    meta_desc = md.get("content","") if md else ""
    mk = soup.find("meta", attrs={"name": re.compile("^keywords$", re.I)})
    # every <meta name=robots|googlebot> (only the first robots tag was read before DE-04)
    robots_tags = [m.get("content", "") for m in soup.find_all("meta", attrs={"name": re.compile(r"^(robots|googlebot)$", re.I)})]
    canon = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    vp = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    charset = ""
    cm = soup.find("meta", charset=True)
    if cm: charset = cm.get("charset","")
    headings = {h: [t.get_text(" ", strip=True) for t in soup.find_all(h)] for h in ["h1","h2","h3","h4","h5","h6"]}
    text = soup.get_text(" ", strip=True)
    wc = len(re.findall(r"\b\w+\b", text))
    tag_inv = dict(Counter(t.name for t in soup.find_all(True)))
    imgs = soup.find_all("img")
    # tracking pixels (FB noscript 1x1 etc.) are not content images — an empty alt on them is
    # CORRECT, so exclude them from the missing-alt audit (a title-company site false-positive 2026-07-18:
    # "41 images missing alt" was the facebook.com/tr pixel once per page)
    def _is_pixel(i):
        src = i.get("src") or ""
        return ("facebook.com/tr" in src or "google-analytics.com/collect" in src
                or (i.get("width") == "1" and i.get("height") == "1"))
    imgs = [i for i in imgs if not _is_pixel(i)]
    # Lazy-load placeholder pairs (WP Rocket etc., a roofing site 2026-07-31): the real image sits in
    # data-lazy-src while src holds a data: URI stub — counting both DOUBLED the missing-alt
    # number. A data:-src image WITH a data-lazy-src twin is the same image; keep one.
    def _is_lazy_stub(i):
        return (i.get("src") or "").startswith("data:") and (i.get("data-lazy-src") or i.get("data-src"))
    imgs = [i for i in imgs if not _is_lazy_stub(i)]
    jsonld = []
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            data = json.loads(s.string or "{}")
            nodes = data if isinstance(data, list) else [data]
            # @graph wrapper: nodes live one level down (missed a title-company site's homepage
            # LocalBusiness 2026-07-18 — the top-level dict has no @type, only @graph)
            expanded = []
            for d in nodes:
                if isinstance(d, dict) and isinstance(d.get("@graph"), list):
                    expanded.extend(d["@graph"])
                else:
                    expanded.append(d)
            for d in expanded:
                t = d.get("@type") if isinstance(d, dict) else None
                if t: jsonld.append(t if isinstance(t,str) else ",".join(t))
        except Exception: pass
    internal, external = [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:","tel:","javascript:","#")): continue
        absu = urljoin(resp["final_url"], href)
        # Browsers percent-encode a literal space before requesting; curl exits 3 on it, so
        # every href with a space became a status-0 "broken link" (a Shopify store 2026-09-22:
        # 5 header quick-search links /search?q=Dock Hardware). Encode like a browser would.
        absu = absu.replace(" ", "%20")
        if not absu.startswith("http"): continue
        rec = {"href": absu, "anchor": a.get_text(" ", strip=True)[:80], "rel": " ".join(a.get("rel", []))}
        # the href as literally written in the source — resolved URLs (e.g. a protocol-less
        # href="www.x.com" becoming /blog/www.x.com) never appear verbatim in the code,
        # so reports must show the raw string to make the link findable
        if href != absu: rec["raw_href"] = href[:200]
        cls = " ".join(a.get("class", []))[:80]
        if cls and not rec["anchor"]: rec["cls"] = cls
        (internal if same_site(absu) else external).append(rec)
    # ONE noindex definition (crawl_transport.robots_noindex, DE-04): meta tags AND every header.
    # meta_robots keeps its old shape (tags + headers joined) for older readers; meta_robots_tag is
    # the page's own tag only, xrobots_headers the server's headers only (None = not recorded).
    meta_tag_val = ", ".join(t for t in robots_tags if t)
    robots_val = meta_tag_val + (" " + ", ".join(_xr) if _xr else "")
    noindex = _tx.robots_noindex(robots_tags, _xr or [])
    indexable = not noindex and resp["status"] == 200
    return {
        "url": url, "final_url": resp["final_url"], "status": resp["status"], "redirected": resp["redirected"],
        "redirect_hops": resp.get("redirect_hops"),
        "content_type": resp["ct"], "indexable": indexable, "noindex": noindex,
        "meta_robots_tag": meta_tag_val, "xrobots_headers": _xr,
        "title": title, "title_len": len(title),
        "meta_description": meta_desc, "meta_desc_len": len(meta_desc),
        "meta_keywords": (mk.get("content","") if mk else ""),
        "meta_robots": robots_val.strip(), "canonical": (canon.get("href","") if canon else ""),
        "lang": (soup.html.get("lang","") if soup.html else ""), "charset": charset,
        "viewport": (vp.get("content","") if vp else ""),
        "open_graph": metas("property","og:") | metas("name","og:"),
        "twitter": metas("name","twitter:") | metas("property","twitter:"),
        "headings": headings,
        "heading_counts": {h: len(v) for h,v in headings.items()},
        # ⚠️ FIELD-NAME TRAP GUARD (2026-08-05). There is NO scalar "h1" key — headings live in
        # `headings.h1` (list) and `heading_counts.h1` (int). Two independent clean agents probed
        # `p.get("h1")`, got None on every row, and nearly reported a fabricated sitewide
        # "no page has an H1" critical. A dict `.get()` on a missing key is indistinguishable from
        # a real "no H1" answer, so we make the absence SPEAK: anything reading the obvious name
        # gets a string telling it where the data actually is, which cannot be mistaken for "none".
        "h1": "USE headings.h1 (list) or heading_counts.h1 (int) — this key is not the H1 data",
        "word_count": wc,
        "html_tag_inventory": dict(sorted(tag_inv.items(), key=lambda x:-x[1])),
        "images_total": len(imgs),
        # alt="" is valid, deliberate markup for DECORATIVE images (screen readers skip them) —
        # counting it sent an owner off to caption icon frames (a roofing site 2026-07-31, ~2x overcount).
        # Only an ABSENT alt attribute is the accessibility/SEO defect.
        "images_missing_alt": sum(1 for i in imgs if not i.has_attr("alt")),
        "images_lazy": sum(1 for i in imgs if i.get("loading", "") == "lazy"
                           or i.get("data-lazy-src") or i.get("data-src")),
        "jsonld_types": sorted(set(jsonld)),
        "internal_links": internal, "external_links": external,
    }

async def main():
    # FETCH URLS AS WRITTEN; norm() is only the DEDUP KEY (fixed 2026-07-31, a roofing site).
    # The queue used to hold norm()'d (slash-stripped) URLs, so on a trailing-slash-canonical site
    # every fetch requested /page, the server 301'd it to /page/, and the crawl recorded a
    # "redirect" that exists nowhere in the site's own links or sitemap — 131 of 141 rows (93%)
    # were fake redirects, which poisoned checks 13/14/15 and hack-scan downstream.
    seen, pages = set(), []
    # PARTIAL WRITE ON TIMEOUT (a Shopify store 2026-09-22). run-full-audit.sh wraps this crawl in
    # `timeout`; when the paced curl fallback hit its cap it was SIGTERM'd after 364 pages and
    # wrote NOTHING, so every later stage silently consumed the PREVIOUS pass's JSON (934 throttled
    # 429 rows) and the guard even printed "after curl fallback: 58 real-content pages" from it.
    # On SIGTERM dump what we have, flagged partial:true, so downstream sees the real sample.
    import signal as _sig
    def _on_term(signum, frame):
        try:
            out = {"site": REG, "seed": SEED, "pages_crawled": len(pages), "partial": True,
                   "summary": {"pages": len(pages), "partial": True,
                               "note": "crawl was terminated (timeout) before finishing; inlinks/external status not computed"},
                   "pages": pages, "external_links": []}
            json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
            print(f"[partial] SIGTERM after {len(pages)} pages -> {OUT} (partial:true)", file=sys.stderr)
        finally:
            os._exit(124)
    _sig.signal(_sig.SIGTERM, _on_term)
    queue = [SEED]
    disallowed = {}                              # norm key -> record (never fetched)
    cap_hit = False
    conn = aiohttp.TCPConnector(limit=A.concurrency, ssl=False)
    async with aiohttp.ClientSession(headers={"User-Agent": UA}, connector=conn) as s:
        async def robots_for(u):
            pr = urlparse(u)
            h = (pr.netloc or "").lower()
            if h not in ROBOTS:
                r = await fetch(s, f"{pr.scheme}://{pr.netloc}/robots.txt")
                rb = _tx.robots_from_response(r.get("status"), r.get("body") or "")
                ROBOTS[h] = rb
                if rb.crawl_delay:
                    pacer_for(u).raise_interval(rb.crawl_delay)
                print(f"[robots] {h}: robots.txt {rb.state} (HTTP {r.get('status')}), {len(rb.rules)} rule(s), "
                      f"crawl-delay {rb.crawl_delay}", file=sys.stderr)
            return ROBOTS[h]

        async def may_fetch(u):
            rb = await robots_for(u)
            if rb.allowed(u):
                return True
            k = norm(u)
            if k not in disallowed:
                disallowed[k] = {"url": u, "status": None, "note": "robots-disallowed",
                                 "reason": "robots.txt unreachable" if rb.state == "unreachable" else "robots.txt Disallow"}
            return False

        seed_rb = await robots_for(SEED)
        if seed_rb.state == "unreachable":
            print(f"[robots] FAIL SAFE: robots.txt for {urlparse(SEED).netloc} did not answer (5xx or no response); "
                  f"crawled NOTHING. Re-run when the site's robots.txt answers.", file=sys.stderr)
            out = {"site": REG, "seed": SEED, "pages_crawled": 0, "partial": True,
                   "summary": {"pages": 0, "partial": True, "robots_txt": "unreachable", "robots_disallowed": 0,
                               "crawl_delay_s": None, "max_pages": A.max, "concurrency": A.concurrency,
                               "delay_s": A.delay, "user_agent": UA, "page_cap_hit": False,
                               "avg_word_count": 0,
                               "note": "robots.txt did not answer (5xx or no response): nothing was crawled"},
                   "pages": [], "robots_disallowed": [], "external_links": []}
            json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
            sys.exit(4)
        # seed from sitemap too
        try:
            sm = await fetch(s, urljoin(SEED, "/sitemap.xml")) if await may_fetch(urljoin(SEED, "/sitemap.xml")) else {"body": ""}
            _locs = [_sm_unescape(x) for x in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", sm["body"])]
            # a sitemap INDEX lists child .xml sitemaps — expand them into page URLs instead of
            # crawling the XML files as if they were pages (a roofing site: post-sitemap1.xml etc.)
            for loc in list(_locs):
                if _sm_is_index(loc) and same_site(loc) and await may_fetch(loc.strip()):
                    try:
                        child = await fetch(s, loc.strip())
                        _locs += [_sm_unescape(x) for x in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", child["body"])]
                    except Exception: pass
            for loc in _locs:
                if same_site(loc) and not _sm_is_index(loc):
                    queue.append(loc.strip())
        except Exception: pass
        # dedupe by norm key but KEEP the first as-written spelling
        _dd = {}
        for u in queue: _dd.setdefault(norm(u), u)
        queue = list(_dd.values())
        queued = set(_dd)                       # norm keys currently in the queue
        while queue and len(seen) < A.max:
            batch = []
            while queue and len(batch) < A.concurrency and len(seen) + len(batch) < A.max:
                u = queue.pop(0)
                queued.discard(norm(u))
                if await may_fetch(u):
                    batch.append(u)
            if not batch:
                continue
            for u in batch:
                seen.add(norm(u))
            resps = await asyncio.gather(*[fetch(s, u) for u in batch])
            for u, r in zip(batch, resps):
                rec = extract(u, r)
                pages.append(rec)
                for l in rec.get("internal_links", []):
                    href = urldefrag(l["href"])[0]
                    k = norm(href)
                    if k not in seen and k not in queued and k not in disallowed:
                        if len(seen)+len(queue) < A.max:
                            queue.append(href); queued.add(k)
                        else:
                            cap_hit = True
            print(f"[crawl] {len(pages)} pages, {len(queue)} queued", file=sys.stderr)
        if queue:
            cap_hit = True

        # inlink counts
        inl = Counter()
        for p in pages:
            for l in p.get("internal_links", []): inl[norm(l["href"])] += 1
        for p in pages: p["inlinks"] = inl.get(norm(p["url"]), 0)

        # external link inventory (+ optional status check)
        ext = defaultdict(list)
        for p in pages:
            for l in p.get("external_links", []): ext[l["href"]].append(p["url"])
        ext_status = {}
        if A.check_external:
            uniq = list(ext.keys())
            for i in range(0, len(uniq), A.concurrency):
                chunk = uniq[i:i+A.concurrency]
                async def head(u):
                    await pacer_for(u).await_slot()
                    try:
                        async with s.head(_wire(u), allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15)) as r: return u, r.status
                    except Exception: return u, None
                for u, st in await asyncio.gather(*[head(u) for u in chunk]): ext_status[u] = st
                print(f"[ext] checked {min(i+A.concurrency,len(uniq))}/{len(uniq)}", file=sys.stderr)

    external_inventory = [{"url": u, "status": ext_status.get(u), "linked_from": srcs, "count": len(srcs)} for u, srcs in sorted(ext.items(), key=lambda x:-len(x[1]))]
    out = {
        "site": REG, "seed": SEED, "pages_crawled": len(pages),
        "summary": {
            "pages": len(pages),
            "indexable": sum(1 for p in pages if p.get("indexable")),
            "missing_title": sum(1 for p in pages if not p.get("title")),
            "missing_meta_desc": sum(1 for p in pages if not p.get("meta_description")),
            "missing_h1": sum(1 for p in pages if p.get("heading_counts",{}).get("h1",0)==0),
            "missing_og": sum(1 for p in pages if not p.get("open_graph")),
            "missing_twitter": sum(1 for p in pages if not p.get("twitter")),
            "total_images": sum(p.get("images_total",0) for p in pages),
            "images_missing_alt": sum(p.get("images_missing_alt",0) for p in pages),
            "unique_external_links": len(external_inventory),
            "avg_word_count": round(sum(p.get("word_count",0) for p in pages)/max(len(pages),1)),
            # polite-crawl record (owner safety rule 2026-10-14): what limited this crawl
            "robots_txt": ROBOTS[(urlparse(SEED).netloc or "").lower()].state,
            "robots_disallowed": len(disallowed),
            "crawl_delay_s": ROBOTS[(urlparse(SEED).netloc or "").lower()].crawl_delay,
            "max_pages": A.max, "concurrency": A.concurrency, "delay_s": A.delay, "user_agent": UA,
            "page_cap_hit": cap_hit,
        },
        "pages": pages, "robots_disallowed": sorted(disallowed.values(), key=lambda x: x["url"]),
        "external_links": external_inventory,
    }
    json.dump(out, open(OUT,"w"), ensure_ascii=False, indent=2)
    print(json.dumps(out["summary"], indent=2))
    print(f"[done] {len(pages)} pages -> {OUT}", file=sys.stderr)

asyncio.run(main())
