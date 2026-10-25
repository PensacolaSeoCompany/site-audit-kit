"""Shared HTTP transport for every site-crawler stage.

WHY THIS EXISTS (§65, 2026-08-06). Proxy support was added to whichever file the symptom
appeared in — audit-checks.py, full-extract.py, detect-cms.py, render-check.py,
cms/wordpress.py — and NOT to the five other stages that also make HTTP requests. So a
site behind a WAF that rate-limits our crawl IP (Vercel, Sucuri) crawled fine and then died
in stage 9 with an unhandled 403 traceback. That is the §54 "fixed it in the wrong place"
pattern: the fix belonged to the transport, not to a caller.

RULE: any stage that makes an HTTP request imports from here. Never build a bare
urlopen()/Request() in a stage script — transport config that lives in one file and not
another is a defect waiting for the next WAF.

Env contract (identical for every stage):
  CRAWL_PROXY   http://user:pass@host:port   routes ALL urllib traffic through the proxy
  CRAWL_UA      override User-Agent          default: DEFAULT_UA below (an honest bot name + repo URL)

READ-ONLY: this module only sends GET (and the crawler's HEAD) requests. It never sends a form,
logs in, or changes anything on a site.

Also here: the robots.txt matcher (RFC 9309) the stage-1 crawl obeys, and the request pacer.
"""
import os
import re as _re_tx
import urllib.request as _ur
from urllib.parse import urlsplit as _urlsplit, quote as _quote

__all__ = ["UA", "DEFAULT_UA", "ROBOTS_TOKEN", "HDRS", "PROXY", "opener", "fetch", "fetch_headers",
           "install", "NOINDEX_RE", "robots_noindex", "wire_url", "url_key", "same_url",
           "Robots", "robots_from_response", "Pacer", "is_real_page", "allowed", "before_request",
           "robots", "pacer", "PoliteHandler", "RobotsDisallowed", "THIRD_PARTY_HOSTS"]


# ---- ONE noindex definition (DE-04, 2026-10-14) ------------------------------------------------
# Three stages disagreed: full-extract said `none` was indexable, audit-checks missed `none`, the
# builder's JS regex was case-sensitive. Google's spec ("Robots meta tag, data-nosnippet, and
# X-Robots-Tag specifications"): directives are comma separated and case-insensitive, `none` equals
# `noindex, nofollow`, a directive may carry a user-agent prefix ("googlebot: noindex"), and several
# tags/headers combine. `max-image-preview: none` is the one directive whose VALUE is the word none,
# so it is removed before the test. Every stage uses THIS pattern (audit-checks keeps a byte-identical
# copy, guarded by tests/test_extract_and_findings.py; the builder's JS copy is listed in AP).
NOINDEX_RE = _re_tx.compile(r"(?:^|[\s,:])(?:noindex|none)(?=[\s,;]|$)", _re_tx.I)
_MAX_IMG_RE = _re_tx.compile(r"max-image-preview\s*:\s*[a-z]+", _re_tx.I)


def robots_noindex(*values):
    """True when ANY meta-robots value or X-Robots-Tag header value says noindex or none.
    Accepts strings and lists of strings (pass every tag and every header, not only the first)."""
    for v in values:
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)):
            if robots_noindex(*v):
                return True
            continue
        if NOINDEX_RE.search(_MAX_IMG_RE.sub(" ", str(v))):
            return True
    return False


# ---- URLs exactly as written (DE-03, 2026-10-14) -------------------------------------------------
# aiohttp passes URLs through yarl, which DECODES %26 (and %2F, %3F) in paths: /terms-%26-conditions
# was fetched as /terms-&-conditions (a client site's false 404). RFC 3986 §2.2/§6.2.2.2: %26 and & are
# different URIs; only escapes of UNRESERVED characters may be decoded. wire_url() keeps every
# existing escape and only encodes what a browser would (space, non-ASCII); pass the result to
# yarl.URL(..., encoded=True).
_SAFE = ":/?#[]@!$&'()*+,;=%~-._"


def wire_url(u):
    u = (u or "").strip()
    try:
        host = _urlsplit(u).hostname or ""
        host.encode("ascii")
    except Exception:
        return u          # IDN host: let the client library apply IDNA
    return _quote(u, safe=_SAFE)


_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _norm_escapes(s):
    def fix(m):
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else "%" + m.group(1).upper()
    return _re_tx.sub(r"%([0-9A-Fa-f]{2})", fix, s or "")


def url_key(u):
    """RFC 3986 §6.2.2 normal form for COMPARISON only: scheme/host lower case, default port
    dropped, empty path = '/', percent-escape hex upper case, unreserved escapes decoded, fragment
    dropped. Never fetch this form."""
    try:
        p = _urlsplit((u or "").strip())
    except Exception:
        return u or ""
    scheme = (p.scheme or "").lower()
    host = (p.hostname or "").lower()
    port = p.port if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)) else None
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{port}" if port else "")
    path = _norm_escapes(p.path) or "/"
    query = _norm_escapes(p.query)
    return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def same_url(a, b):
    return url_key(a) == url_key(b)

# The kit says who it is (owner safety rule 2026-10-14). ROBOTS_TOKEN is the product token matched
# against robots.txt "User-agent:" lines; CRAWL_UA replaces the header but not the token.
ROBOTS_TOKEN = "GrokBot-SiteAudit"
REPO_URL = "https://github.com/PensacolaSeoCompany/site-audit-kit"
DEFAULT_UA = f"{ROBOTS_TOKEN} (+{REPO_URL})"
UA = os.environ.get("CRAWL_UA") or DEFAULT_UA
HDRS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
PROXY = os.environ.get("CRAWL_PROXY") or None


_ORIG_BUILD_OPENER = _ur.build_opener


def opener(*extra):
    """Opener honouring CRAWL_PROXY + the polite layer (bot UA, pacing, robots.txt). Safe to call repeatedly."""
    return _ur.build_opener(*extra)


def install():
    """Make EVERY urllib request in this process polite (owner safety rule 2026-10-14):
    urllib.request.build_opener is wrapped so every opener (including a tool's private ones) gets
    PoliteHandler and the CRAWL_PROXY handler, and a global opener is installed for bare urlopen()."""
    if _ur.build_opener is not _polite_build_opener:
        _ur.build_opener = _polite_build_opener
    try:
        _ur.install_opener(_polite_build_opener())
        return True
    except Exception:
        return False


class _Undecodable(Exception):
    """A body in a content-encoding this process cannot undo (br without the brotli module)."""


def _brotli():
    try:
        import brotli  # noqa: F401
        return brotli
    except Exception:
        try:
            import brotlicffi as brotli  # noqa: F401
            return brotli
        except Exception:
            return None


def _decode(raw, headers=None):
    """Bytes -> text (UTF-8), decompressing first (see decompress)."""
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    return decompress(raw, headers).decode("utf-8", "replace")


def decompress(raw, headers=None):
    """Bytes -> the decompressed bytes (gzip/deflate/br), NOT decoded, so a caller can apply the page's
    own character set (onpage/fetch.py does, with textutil.decode_html). Raises _Undecodable for br it
    cannot undo.

    §68 (a restoration-company site, 2026-08-07). Some origins/CDNs (Sucuri and friends) send
    `content-encoding: gzip` UNCONDITIONALLY — even when the client sends no Accept-Encoding,
    and even when it explicitly asks for `identity`. urlopen does NOT decompress, so decoding
    the raw bytes as UTF-8 yields mojibake: every regex misses, and checks report "no schema" /
    "no title" about a page that has both. That looked exactly like a WAF poisoning the render,
    and was misdiagnosed as one for two days.

    The AUDIT-PROBLEMS §54 note recorded the WRONG exemption ("urlopen sends no Accept-Encoding,
    so it receives plain bytes"). It is false against an unconditionally-gzipping origin.

    Fixed HERE, in the shared transport, not at each call site (Rule 13: fix the RULE, not the
    instance) — audit-checks.py had its own copy of this logic and the five stages wired to
    crawl_transport had none.

    Never trust the header alone: it can be absent or wrong, so sniff the magic bytes too.
    """
    if not raw:
        return b""
    if isinstance(raw, str):
        return raw.encode("utf-8")
    enc = ""
    if headers is not None:
        try:
            enc = (headers.get("content-encoding") or "").strip().lower()
        except Exception:
            enc = ""
    # magic-byte sniff wins over a missing/lying header
    if raw[:3] == b"\x1f\x8b\x08":
        enc = "gzip"
    elif enc == "br":
        # brotli has no magic bytes; trust the header
        _b = _brotli()
        if _b is None:
            raise _Undecodable("br")
        try:
            return _b.decompress(raw)
        except Exception:
            if raw.lstrip()[:1] in (b"<", b"{", b"["):
                return raw                                 # header lied, body is plain
            raise _Undecodable("br")
    elif enc not in ("gzip", "x-gzip", "deflate"):
        enc = ""
    if enc:
        import gzip as _gz, zlib as _zl
        for attempt in ((_gz.decompress,) if enc in ("gzip", "x-gzip")
                        else (_zl.decompress, lambda b: _zl.decompress(b, -_zl.MAX_WBITS))):
            try:
                raw = attempt(raw)
                break
            except Exception:
                continue
    return raw


def fetch(url, timeout=25, max_bytes=2_000_000, headers=None):
    """-> (status, body_text). status None = transport failure (never raises).

    Error bodies are returned too, so a caller can tell a WAF challenge page from a real
    404 instead of getting an unhandled HTTPError traceback (the property-valuation site
    stage-9 crash).
    """
    h = dict(HDRS)
    if headers:
        h.update(headers)
    # CHALLENGE-SHELL RETRY (a garage-door site 2026-09-22). Through a ROTATING residential proxy a
    # single fetch can land on a flagged exit and get SiteGround's 202 sg-captcha shell while the
    # next rotation gets the real page (url-discovery read "1 URL across 3 sitemap files" once,
    # 46 on replay). Same rule full-extract.fetch_curl applies: retry a MARKED 202 with backoff.
    import re as _re, time as _time
    for _attempt in range(3):
        try:
            r = opener().open(_ur.Request(url, headers=h), timeout=timeout)
            st, body = getattr(r, "status", r.getcode()), _decode(r.read(max_bytes), r.headers)
        except Exception as e:
            body = ""
            try:
                body = _decode(e.read(max_bytes), getattr(e, "headers", None))
            except Exception:
                pass
            st = getattr(e, "code", None)
        if st == 202 and _attempt < 2 and _re.search(r"sgcaptcha|sg-captcha|Robot Challenge", body or "", _re.I):
            _time.sleep(3 * (_attempt + 1))
            continue
        return st, body
    return st, body


def fetch_headers(url, timeout=20, headers=None):
    """-> (status, headers_obj_or_None). Never raises."""
    h = dict(HDRS)
    if headers:
        h.update(headers)
    try:
        r = opener().open(_ur.Request(url, headers=h), timeout=timeout)
        return getattr(r, "status", r.getcode()), r.headers
    except Exception as e:
        return getattr(e, "code", None), getattr(e, "headers", None)


# ---- is this crawled URL a real PAGE? ---------------------------------------------------------
# 2026-10-14 (a live report): "is linked from N pages" counted WordPress like-button requests
# (/wp-admin/admin-ajax.php?action=process_simple_like&post_id=...) as pages. A link SOURCE must be
# a real page: not admin-ajax.php, wp-login.php, /wp-admin/, /wp-json/, xmlrpc.php, /cdn-cgi/, a
# URL with ?action=, or a non-HTML asset. Same rule as isRealPage() in build-crawl-report.js.
_NOT_PAGE_RE = _re_tx.compile(r"/wp-admin/|admin-ajax\.php|wp-login\.php|/wp-json(/|$|\?)|xmlrpc\.php|/cdn-cgi/|[?&]action=", _re_tx.I)
_ASSET_RE = _re_tx.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp4|webm|mov|avi|mp3|wav|ogg|pdf|docx?|"
                           r"xlsx?|pptx?|zip|gz|rar|7z|css|js|mjs|json|xml|txt|woff2?|ttf|eot|otf)$", _re_tx.I)


def is_real_page(url, content_type=None):
    try:
        sp = _urlsplit(url or "")
    except ValueError:
        return False
    if _NOT_PAGE_RE.search(sp.path + ("?" + sp.query if sp.query else "")):
        return False
    if _ASSET_RE.search(sp.path):
        return False
    if content_type and "html" not in content_type.lower():
        return False
    return True


# ---- robots.txt (RFC 9309) ---------------------------------------------------------------------
# urllib.robotparser takes the FIRST matching rule, has no "*" / "$" patterns, and reads only the
# first group for an agent, so it is wrong on five ordinary RFC 9309 cases (tests/test_robots.py).
# Rules here: pick the groups whose User-agent equals our token (case-insensitive), else the "*"
# groups; groups for the same agent merge; the LONGEST matching pattern wins; Allow wins a tie;
# "*" matches any run of characters and a final "$" anchors the end; an empty Disallow is no rule.
def _robots_norm(s):
    """Percent-encode non-ASCII, uppercase %xx, decode unreserved escapes (RFC 3986 §6.2.2)."""
    s = _quote(s, safe=_SAFE + "*$")
    return _norm_escapes(s)


def _robots_pattern(p):
    p = _robots_norm(p)
    anchored = p.endswith("$")
    if anchored:
        p = p[:-1]
    rx = ".*".join(_re_tx.escape(part) for part in p.split("*"))
    return _re_tx.compile(rx + ("$" if anchored else ""), _re_tx.S)


class Robots:
    """Parsed robots.txt for one product token. .allowed(url_or_path) / .crawl_delay / .state"""

    def __init__(self, text, token=ROBOTS_TOKEN, state="ok"):
        self.state = state
        self.rules = []          # (length, allow, compiled, raw)
        self.crawl_delay = None
        groups, cur, last_was_agent = [], None, False
        for line in (text or "").splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "user-agent":
                if not last_was_agent:
                    cur = {"agents": [], "lines": []}
                    groups.append(cur)
                cur["agents"].append(v.lower())
                last_was_agent = True
            elif cur is not None:
                last_was_agent = False
                cur["lines"].append((k, v))
        tok = token.lower()
        mine = [g for g in groups if tok in g["agents"]]
        chosen = mine or [g for g in groups if "*" in g["agents"]]
        for g in chosen:
            for k, v in g["lines"]:
                if k in ("allow", "disallow") and v:
                    self.rules.append((len(_robots_norm(v)), k == "allow", _robots_pattern(v), v))
                elif k == "crawl-delay":
                    try:
                        d = float(v)
                        if d >= 0:
                            self.crawl_delay = max(self.crawl_delay or 0.0, d)
                    except ValueError:
                        pass

    def allowed(self, url):
        if self.state == "unreachable":
            return False
        if self.state == "missing":
            return True
        sp = _urlsplit(url)
        path = (sp.path or "/") + (("?" + sp.query) if sp.query else "")
        if not url.startswith(("http://", "https://")):
            path = url if url.startswith("/") else "/" + url
        path = _robots_norm(path)
        best = None                      # (length, allow)
        for ln, allow, rx, _raw in self.rules:
            if rx.match(path):
                if best is None or ln > best[0] or (ln == best[0] and allow and not best[1]):
                    best = (ln, allow)
        return True if best is None else best[1]


def robots_from_response(status, body, token=ROBOTS_TOKEN):
    """RFC 9309 §2.3.1: 2xx = parse; 4xx = no rules (allow all); 5xx or no answer = crawl nothing."""
    if status is not None and 200 <= status < 300:
        return Robots(body or "", token, "ok")
    if status is not None and 400 <= status < 500:
        return Robots("", token, "missing")
    return Robots("", token, "unreachable")


class Pacer:
    """One shared time slot per request: a caller RESERVES the next slot under a lock, then waits
    for it, so N concurrent workers can never fire together (the old per-worker sleep let every
    worker wake at the same moment). interval = seconds between any two request starts."""

    def __init__(self, interval):
        import threading
        self.interval = max(0.0, float(interval or 0))
        self._lock = threading.Lock()
        self._next = 0.0

    def raise_interval(self, interval):
        """Lengthen the gap (e.g. robots.txt Crawl-delay), including the slot already reserved."""
        with self._lock:
            if interval > self.interval:
                if self._next:
                    self._next += interval - self.interval
                self.interval = float(interval)

    def reserve(self):
        """-> seconds the caller must wait before sending."""
        import time
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.interval
            return slot - now

    def wait(self):
        import time
        d = self.reserve()
        if d > 0:
            time.sleep(d)

    async def await_slot(self):
        import asyncio
        d = self.reserve()
        if d > 0:
            await asyncio.sleep(d)


# ---- the polite layer: bot UA + pacing + robots.txt for EVERY request ------------------------
# Owner safety rule 2026-10-14: every request any kit tool sends to the audited site carries the
# GrokBot-SiteAudit user agent, waits its turn (CRAWL_DELAY, default 1 s per host, raised by the
# host's robots.txt Crawl-delay) and skips URLs the host's robots.txt disallows. urllib tools get
# it through PoliteHandler (installed by importing this module); curl and Chrome tools call
# before_request(url) themselves and send UA. Third-party APIs (THIRD_PARTY_HOSTS) are paced and
# carry the UA but are not robots-checked (their robots.txt is about crawling, not their API).
import threading as _threading
import urllib.error as _ue
CRAWL_DELAY = float(os.environ.get("CRAWL_DELAY") or 1)
THIRD_PARTY_HOSTS = ("serpapi.com", "crt.sh", "web.archive.org", "archive.org")
_ROBOTS_CACHE, _PACERS = {}, {}
_POLITE_LOCK = _threading.Lock()


class RobotsDisallowed(_ue.URLError):
    """Raised instead of sending a request robots.txt disallows (callers see a failed fetch)."""


def _host_key(url):
    sp = _urlsplit(url)
    return (sp.scheme or "http").lower(), (sp.netloc or "").lower()


def _third_party(netloc):
    h = netloc.split(":")[0]
    return any(h == t or h.endswith("." + t) for t in THIRD_PARTY_HOSTS)


def pacer(url):
    _, net = _host_key(url)
    with _POLITE_LOCK:
        if net not in _PACERS:
            _PACERS[net] = Pacer(CRAWL_DELAY)
        return _PACERS[net]


def robots(url):
    """Robots for url's host, fetched once per process (2xx parse, 4xx allow all, else deny all)."""
    scheme, net = _host_key(url)
    with _POLITE_LOCK:
        hit = _ROBOTS_CACHE.get((scheme, net))
    if hit is not None:
        return hit
    rurl = f"{scheme}://{net}/robots.txt"
    pacer(url).wait()
    st, body = None, ""
    try:
        o = _ORIG_BUILD_OPENER(*([_ur.ProxyHandler({"http": PROXY, "https": PROXY})] if PROXY else []))
        r = o.open(_ur.Request(rurl, headers={"User-Agent": UA}), timeout=20)
        st, body = getattr(r, "status", r.getcode()), _decode(r.read(500_000), r.headers)
    except _ue.HTTPError as e:
        st = e.code
    except Exception:
        st = None
    rb = robots_from_response(st, body)
    if rb.crawl_delay:
        pacer(url).raise_interval(rb.crawl_delay)
    with _POLITE_LOCK:
        _ROBOTS_CACHE[(scheme, net)] = rb
    return rb


def allowed(url):
    """True if robots.txt lets the kit fetch url (robots.txt itself and third-party APIs: always)."""
    sp = _urlsplit(url)
    if sp.scheme not in ("http", "https") or sp.path == "/robots.txt" or _third_party((sp.netloc or "").lower()):
        return True
    return robots(url).allowed(url)


def before_request(url):
    """Call before any non-urllib request (curl, Chrome): raises RobotsDisallowed, else waits its turn."""
    if not allowed(url):
        raise RobotsDisallowed(f"robots-disallowed: robots.txt disallows {url}")
    pacer(url).wait()


class PoliteHandler(_ur.BaseHandler):
    handler_order = 100          # before the HTTP handlers add their defaults

    def http_request(self, req):
        # A caller's user agent is kept only when it still carries the bot's own name (onpage/fetch.py
        # adds phone tokens around it); anything else is replaced, so the bot never hides who it is.
        mine = req.get_header("User-agent") or req.unredirected_hdrs.get("User-agent") or ""
        if UA in mine:
            before_request(req.full_url)
            return req
        for k in [k for k in req.headers if k.lower() == "user-agent"]:
            del req.headers[k]
        for k in [k for k in req.unredirected_hdrs if k.lower() == "user-agent"]:
            del req.unredirected_hdrs[k]
        req.add_header("User-Agent", UA)
        before_request(req.full_url)
        return req

    https_request = http_request


def _polite_build_opener(*handlers):
    hs = list(handlers)
    if not any(isinstance(h, PoliteHandler) or h is PoliteHandler for h in hs):
        hs.append(PoliteHandler())
    if PROXY and not any(isinstance(h, _ur.ProxyHandler) or h is _ur.ProxyHandler for h in hs):
        hs.append(_ur.ProxyHandler({"http": PROXY, "https": PROXY}))
    return _ORIG_BUILD_OPENER(*hs)


# Importing this module makes every urllib request in the process polite and proxied.
install()


if __name__ == "__main__":
    # `python3 crawl_transport.py allowed <url>` -> exit 0 allowed, 3 disallowed (used by click-test.js)
    import sys as _sys
    if len(_sys.argv) == 3 and _sys.argv[1] == "allowed":
        ok = allowed(_sys.argv[2])
        print("allowed" if ok else f"robots-disallowed ({robots(_sys.argv[2]).state})")
        _sys.exit(0 if ok else 3)
    print("usage: crawl_transport.py allowed <url>")
    _sys.exit(2)
