"""Fetch one page for the on-page comparison: raw server HTML and (by default) the rendered DOM.

fetch(url, mode, browser_retry=False, save_to=None) -> record dict (onpage/schema/result.schema.json "fetch"):
  url, final_url, status, mode_used ("rendered" | "raw"), html (str or None), raw_words,
  rendered_words, js_dependent (bool or None), blocked (bool), error (str or None), ms,
  redirected_to_other_page (bool), redirected_to_other_site (bool), soft_404 (bool), chain (Chrome's
  main-frame steps), sandbox (bool: Chrome ran with its sandbox), sandbox_retry (bool: the sandboxed start
  failed and Chrome was started once more without it), viewport, blocked_requests,
  saved ({file: {path, sha256, bytes}} when save_to is given),
  browser_log (list of str or None): Chrome's raw error words, for run.log ONLY (never the report; error
  and note carry plain words),
  block_kind ("refusal" | "check" | None): why a blocked page was blocked. "refusal" = the site said no
  to the bot name (a read as a normal browser, or a copy the user saves, can work); "check" = a bot
  check, never bypassed (only a copy the user saves can work).
Rules:
- Read-only GETs through crawl_transport, robots.txt obeyed, paced. User agent: the bot name
  (GrokBot-SiteAudit) inside a phone Chrome string on the phone viewport (see bot_user_agent), the same
  form Chrome's visit sends; never another crawler's name. The server HTML is decoded with its own
  character set (byte-order mark, Content-Type charset, <meta charset>, else UTF-8 / windows-1252).
- robots.txt disallows the URL -> not fetched at all (error "robots-disallowed").
- 401/403/429 or a refusal page ("Access Denied" style, small; the firewall's "Sorry, you have been
  blocked" page with nothing to solve) -> blocked, not measured, block_kind "refusal".
- A bot check is never bypassed: blocked at ANY status, seen at ANY step: the server
  HTML, any document Chrome loaded on the way (a check that solves itself and reloads still counts), or
  the final DOM. Evidence (see _check_reason): a check URL or the cf-mitigated header; a known check
  marker in the HTML; or a check title / check wording on a SMALL page that also refuses (status not 2xx)
  or has no form and no menu. One title word ("challenge") on a normal-sized page is not a check, nor is
  a reCAPTCHA label on a thin contact page. A document Chrome only had the start of (over 30,000
  characters) is judged by the size-free evidence only. block_kind "check".
- Not HTML (PDF, image, JSON) -> not measured.
- Another SITE is never measured: redirected_to_other_site = the address ended on another website (another
  registrable domain: sdplumbing.com -> sdair.com; a move between www and no www, http and https, or to a
  subdomain of the same site is fine). The error says where it went; no browser visit and no browser
  retry follow (a block on that other site is not this page's block).
- A different page is not measured, the error says why: redirected_to_other_page = the path of the
  last DOCUMENT loaded differs from the requested one beyond trailing slash, letter case and query (a
  script that only rewrites the address bar, history.replaceState, loads no new document); soft_404 = status 200
  with a title or H1 that says "not found"/"404" on a page under 400 words.
- mode "rendered" (default): measure Chrome's DOM; if Chrome fails (including its own error page),
  fall back to the server HTML: mode_used "raw", note "measured without a browser: <short plain reason>"
  (see browser_reason), error None; a DOM Chrome had to cut (over 8 MB) is measured with a note
  saying so. Chrome's sandbox: see render.js (one start without it when the sandbox cannot start). mode "raw": measure the server HTML only (no Chrome).
- browser_retry=True (the user's own page always; a competitor page only when the user said yes
  for that page): when the bot name is refused (401/403/429), read the page ONCE more
  in Chrome with a normal Chrome user agent (also in raw mode; the note says Chrome was used).
  robots.txt was already obeyed. A bot check is never retried and stays "blocked". user_agent "browser".
- save_to=<folder>: server.html (the server's HTML) and browser.html (the last DOM Chrome captured) are
  written there whenever they exist, also for pages that end blocked or not measured.
- read_saved(url, path): measure a page the user saved from their own browser (no request is sent).
"""
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse as up
import urllib.request as ur

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_HOME = os.path.dirname(HERE)
sys.path.insert(0, KIT_HOME)
import crawl_transport as ct  # noqa: E402  (installs the polite urllib layer)
sys.path.insert(0, HERE)

from measure import Page  # noqa: E402
from textutil import tokens as _tokens  # noqa: E402
from textutil import decode_html  # noqa: E402

MAX_RAW = 8_000_000
_HTML_CT = re.compile(r"text/html|application/xhtml", re.I)


def _words(html):
    """Body word count (the same tokens measure.py counts)."""
    if not html:
        return 0
    pg = Page(html, "https://x.invalid/")
    t = getattr(pg, "body_tokens", None)   # older measure.py
    return len(t if t is not None else _tokens(pg.body_text or ""))


def bot_user_agent():
    """The user agent of the plain request: on the phone viewport (the default) an Android Chrome string
    that ends with "(compatible; <bot name>)", like the browser visit, so a site that picks phone or desktop
    HTML by user agent gives both reads the same page; RENDER_VIEWPORT=desktop: the bot name alone."""
    if os.environ.get("RENDER_VIEWPORT") == "desktop":
        return ct.UA
    return ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
            f"Mobile Safari/537.36 (compatible; {ct.UA})")


def _raw(url):
    """-> (status, final_url, content_type, text or None, error). The text is decoded with the page's own
    character set (byte-order mark, Content-Type charset, <meta charset>, else UTF-8 / windows-1252)."""
    h = dict(ct.HDRS)
    h["User-Agent"] = bot_user_agent()
    h["Accept-Encoding"] = "gzip, deflate" + (", br" if ct._brotli() else "")
    try:
        r = ct.opener().open(ur.Request(ct.wire_url(url), headers=h), timeout=30)
        st, final, hd, raw = getattr(r, "status", r.getcode()), r.geturl(), r.headers, r.read(MAX_RAW)
    except ct.RobotsDisallowed:
        return None, url, None, None, "robots-disallowed"
    except Exception as e:  # HTTPError carries a status and maybe a body; others are network errors
        st = getattr(e, "code", None)
        hd = getattr(e, "headers", None)
        final = getattr(e, "url", url) or url
        raw = b""
        try:
            raw = e.read(MAX_RAW)
        except Exception:
            pass
        if st is None:
            return None, url, None, None, f"network: {str(getattr(e, 'reason', e))[:160]}"
    ctype = (hd.get("Content-Type") if hd else "") or ""
    try:
        text = decode_html(ct.decompress(raw, hd), ctype) if raw else ""
    except Exception:
        return st, final, ctype, None, "undecodable body"
    return st, final, ctype, text, None


RENDER_TIMEOUT_S = 110   # render.js stops itself at 75 s; this is the backstop


def _kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def _render(url, as_browser=False):
    node = os.environ.get("NODE_BIN") or _kit_node()
    env = dict(os.environ, RENDER_AS_BROWSER="1" if as_browser else "0")
    # render.js keeps Chrome's profile next to r.json, so this folder takes it away on every path
    with tempfile.TemporaryDirectory(prefix="onpage-", ignore_cleanup_errors=True) as td:
        out = os.path.join(td, "r.json")
        try:
            p = subprocess.Popen([node, os.path.join(HERE, "render.js"), url, out], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        except OSError as e:
            return {"ok": False, "error": f"render did not run: {str(e)[:160]}"}
        try:
            _, err = p.communicate(timeout=RENDER_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _kill_group(p.pid)          # node and anything it started in its session
            try:
                with open(out + ".pid") as f:
                    _kill_group(int(f.read().strip()))   # Chrome runs in its own process group
            except (OSError, ValueError):
                pass
            p.communicate()
            return {"ok": False, "error": f"render did not finish in {RENDER_TIMEOUT_S} s (stopped)"}
        try:
            with open(out, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"ok": False, "error": f"render wrote nothing (exit {p.returncode}): {(err or '')[-200:]}"}


def _kit_node():
    for c in (os.path.join(KIT_HOME, ".node", "bin", "node"),):
        if os.access(c, os.X_OK):
            return c
    return "node"


# Bot checks (captcha, "Just a moment", self-solving JavaScript checks): NEVER retried in any way.
# A plain refusal ("Access Denied", 401/403/429) is the site saying no to the bot NAME: the one-time
# browser read applies.
# Evidence must be stronger than one title word: "How to Challenge a DUI Breath Test" is a page.
SMALL_WORDS = 150        # a check or refusal page is small (real ones: 0-100 words); a real page is not
NOT_FOUND_WORDS = 400    # an error page with menus and footer runs to a few hundred words
MARKER_WORDS = 1000      # markers below are check-page code, but a long page is the real page
_CHECK_TITLE = re.compile(
    r"^\W*(just a moment|attention required|one moment,? please|please wait|please stand by|"
    r"checking (your|the) (browser|connection)|security check(point)?|ddos[- ]guard|human verification|"
    r"verify(ing)? (that )?(you are|you're) (a )?human|are you (a )?(robot|human)|robot challenge|"
    r"bot (check|verification)|captcha|access to this page has been denied|pardon our interruption)\b"
    r"|checking your browser|ddos protection|attention required! \| cloudflare", re.I)
_CHECK_TEXT = re.compile(
    r"checking (if the site connection is secure|your browser before accessing)|enable javascript and cookies to continue|"
    r"verify (that )?(you are|you're) (a )?human|press (&|&amp;|and) hold|complete the security check|"
    r"needs to review the security of your connection", re.I)
# Check-page code. NOT the plain Cloudflare "/cdn-cgi/challenge-platform/.../scripts/jsd/" beacon or the
# Imperva/DataDome tags that normal pages carry: those are on real pages too.
_CHECK_MARKER = re.compile(
    r"_cf_chl_opt|/cdn-cgi/challenge-platform/h/[a-z]/orchestrate|/\.well-known/sgcaptcha|"
    r"geo\.captcha-delivery\.com|Incapsula incident ID|_Incapsula_Resource\?CWUDNSAI|px-captcha|gokuProps", re.I)
_CHECK_URL = re.compile(r"/\.well-known/sgcaptcha|/cdn-cgi/challenge-platform/|captcha-delivery\.com|"
                        r"/_Incapsula_Resource|/sorry/index|[?&]__cf_chl_", re.I)
# Whole phrases: a bare word ("blocked", "forbidden", "access denied") only as the whole title or followed
# by a separator, so "Blocked Drains ..." or "Forbidden Fruit Bakery" is a page, not a refusal.
_REFUSAL_TITLE = re.compile(r"^\W*(403\b|request (was )?(blocked|rejected)|(sorry, )?you have been blocked|"
                            r"error 10(06|07|08|20)\b|"
                            r"(access denied|forbidden|blocked|not acceptable)\W*($|[|:\-\u2013\u2014]))", re.I)
_FORM_OR_MENU = re.compile(r"<(form|nav)\b", re.I)


_CF_BLOCK = re.compile(r"you have been blocked", re.I)


def _title(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _h1(html):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.I | re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def _check_reason(html, url=None, mitigated=None, truncated=False, status=None):
    """-> why this is a bot check (str), or None. html may be None (a step Chrome no longer had).
    truncated=True: html is only the start of a longer document, so its word count says nothing about
    the page's size; only the size-free evidence (header, address) is used.
    A check title or check wording on a small page needs a second piece of evidence: a refusing status
    (not 2xx) or a page with no form and no menu. A thin contact page whose reCAPTCHA label says
    "Please verify you are human" is a page."""
    if mitigated and str(mitigated).lower() == "challenge":
        return "the site's firewall marked the answer as a challenge (cf-mitigated)"
    if url and _CHECK_URL.search(url):
        return f"the browser was sent to a check address ({url[:80]})"
    if not html or truncated:
        return None
    n = _words(html)
    if n < MARKER_WORDS and _CHECK_MARKER.search(html):
        return "the HTML carries a bot-check script"
    if _CF_BLOCK.search(html) and not (_CHECK_MARKER.search(html) or _CHECK_TEXT.search(html)):
        return None   # Cloudflare's "Sorry, you have been blocked" page: a refusal (see _refusal), nothing to solve
    if n < SMALL_WORDS and (_CHECK_TITLE.search(_title(html)) or _CHECK_TEXT.search(html)):
        refused = status is not None and not 200 <= status < 300
        if refused or not _FORM_OR_MENU.search(html):
            return f"a bot-check page (title {_title(html)[:60]!r})"
    return None


def _challenge(html):
    """Interactive bot check: never bypassed."""
    return bool(_check_reason(html))


def _refusal(html):
    """A small block page that refuses the bot (e.g. "Access Denied"), not an interactive check."""
    small = _words(html) < SMALL_WORDS
    if small and _CF_BLOCK.search(html or "") and not _challenge(html):
        return True
    return bool(_REFUSAL_TITLE.search(_title(html))) and small and not _challenge(html)


def _chain_check(r):
    """-> (chain without bodies, reason or None): a check seen at ANY step of Chrome's visit."""
    steps, why = [], None
    for st in r.get("chain") or []:
        w = _check_reason(st.get("body"), st.get("url"), st.get("mitigated"), st.get("truncated", False), st.get("status"))
        steps.append({"url": st.get("url"), "status": st.get("status"), "check": bool(w)})
        why = why or w
    return steps, why or _check_reason(r.get("html"), r.get("final_url"), status=r.get("status"))


def _doc_url(r):
    """The address of the last DOCUMENT Chrome loaded (the chain's last step). A script that only rewrites
    the address bar (history.replaceState / pushState, a hash route) loads no new document, so the page
    measured is still the one asked for; a script redirect (location.href) is a new step."""
    chain = r.get("chain") or []
    return (chain[-1].get("url") if chain else None) or r.get("final_url")


_NOT_FOUND = re.compile(r"\b404\b|not found|page (does not|doesn't|cannot|can't) (exist|be found)|no longer (exists|available)", re.I)


def _norm_path(u):
    p = up.urlsplit(u or "").path or "/"
    return up.unquote(p).rstrip("/").lower()


# Second-level labels under a country code ("example.co.uk", "example.com.au"): the site is the last THREE labels.
_SLD = {"co", "com", "net", "org", "gov", "edu", "ac", "gob", "ne", "or", "go", "ltd", "plc", "sch", "nhs", "nic", "mil", "info", "biz", "nom", "gen"}


def site_of(host):
    """The registrable domain of a host, without a public-suffix list: "www.example.com" -> "example.com",
    "shop.example.co.uk" -> "example.co.uk"; an IP address stays whole."""
    h = (host or "").lower().strip().rstrip(".")
    if not h or re.fullmatch(r"[\d.]+|\[?[0-9a-f:]*:[0-9a-f:]*\]?", h):
        return h
    parts = h.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def other_site(url, final):
    """-> True when final is on another website than url (another registrable domain). Scheme, www and
    subdomains of the same site do not count; an unknown address is not a move."""
    a = up.urlsplit(url or "").hostname
    b = up.urlsplit(final or "").hostname
    if not a or not b:
        return False
    return site_of(a) != site_of(b)


def _other_site_error(final):
    return (f"not measured: the address redirected to another website ({final}), so it is not this "
            "competitor's page; check the address, or pick another page that ranks")


def browser_reason(err):
    """Chrome's raw error -> a short plain reason for people. The raw words stay in browser_log (run.log)."""
    e = str(err or "")
    if not e:
        return "the browser gave no answer"
    if re.match(r"the browser got HTTP \d+$", e):
        return e
    if re.search(r"no Chrome found|render did not run", e, re.I):
        return "Chrome was not found"
    if re.search(r"Failed to launch|sandbox|zygote|Code: null", e, re.I):
        return "Chrome did not start"
    if re.search(r"timeout|did not finish|timed out", e, re.I):
        return "the browser took too long"
    if re.search(r"net::ERR_|chrome-error|Chrome error page", e, re.I):
        return "the browser could not load the page"
    if re.search(r"could not read the page DOM", e, re.I):
        return "the browser could not read the page"
    return "the browser failed"


def _wrong_page(url, final, html, status):
    """-> (redirected_to_other_page, soft_404, error or None). Another website is caught before this (see
    fetch.finish), so a move here is to another page of the same site."""
    moved = bool(final) and _norm_path(final) != _norm_path(url)
    soft = status == 200 and _words(html) < NOT_FOUND_WORDS and bool(_NOT_FOUND.search(_title(html)) or _NOT_FOUND.search(_h1(html)))
    if moved:
        return moved, soft, f"not measured: the address redirected to another page ({final}); that page is not the one asked for"
    if soft:
        return moved, soft, f"not measured: the page says it was not found (title {_title(html)[:60]!r}) although the server answered 200"
    return moved, soft, None


MAX_SAVED = 15_000_000


def _record(url):
    return {"url": url, "final_url": None, "status": None, "mode_used": None, "html": None, "raw_words": None,
            "rendered_words": None, "js_dependent": None, "blocked": False, "error": None, "ms": 0,
            "user_agent": "bot", "source": "live", "note": None, "redirected_to_other_page": False,
            "redirected_to_other_site": False, "soft_404": False, "block_kind": None, "browser_log": None}


def read_saved(url, path):
    """A page the user saved in their own browser ("Save page as", HTML). No network."""
    rec = _record(url)
    rec.update(source="saved-file", user_agent=None, final_url=url)
    try:
        size = os.path.getsize(path)
        if size > MAX_SAVED:
            rec["error"] = f"saved file is too big ({size // 1_000_000} MB; limit {MAX_SAVED // 1_000_000} MB)"
            return rec
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        rec["error"] = f"cannot read the saved file: {e.strerror or e}"
        return rec
    text = decode_html(raw)  # byte-order mark, <meta charset>, UTF-8, else windows-1252 (as a browser does)
    if "<html" not in text[:5000].lower() and "<body" not in text.lower() and "<p" not in text.lower():
        rec["error"] = "the saved file is not a web page (save it as 'Webpage, HTML only')"
        return rec
    if _challenge(text) or _refusal(text):
        kind = "check" if _challenge(text) else "refusal"
        rec.update(blocked=True, block_kind=kind, error="the saved file is a " + ("bot-check page" if kind == "check" else
                   "block page ('access denied' style)") + ", not the page itself")
        return rec
    rec.update(html=text, mode_used="saved", raw_words=_words(text), note="measured from a copy the user saved in their own browser")
    return rec


def _render_ok(r):
    """Chrome produced the page itself: not its own error page, not a load with no answer."""
    return bool(r.get("ok") and r.get("html") and r.get("status") is not None
                and re.match(r"https?://", r.get("final_url") or "", re.I))


def _cut(r, note=None):
    """Chrome's own remark on a DOM it did produce (e.g. "html truncated at 8 MB"), added to the note."""
    extra = r.get("error") if r.get("ok") else None
    if not extra:
        return note
    return (f"{note} " if note else "") + f"The browser copy is incomplete: {extra}."


def _refused_by_robots(url):
    """Why the robots check said no. A site that does not answer (robots.txt unreadable: no connection, 5xx)
    is "unreachable", which the robots standard also treats as "do not crawl"; only a real rule is "robots-disallowed"."""
    try:
        state = ct.robots(url).state
    except Exception:
        state = None
    if state == "unreachable":
        return "unreachable: the site did not answer (its robots.txt could not be read), so the page was not read"
    return "robots-disallowed: the site's robots.txt does not allow this bot to read the page"


def fetch(url, mode="rendered", browser_retry=False, save_to=None):
    t0 = time.time()
    rec = _record(url)

    def done(**kw):
        rec.update(kw)
        rec["ms"] = int((time.time() - t0) * 1000)
        return rec

    def save(name, text):
        if not save_to or text is None:
            return
        os.makedirs(save_to, exist_ok=True)
        b = text.encode("utf-8", errors="replace")
        fp = os.path.join(save_to, name)
        with open(fp, "wb") as f:
            f.write(b)
        rec.setdefault("saved", {})[name] = {"path": fp, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}

    def take(r):
        """Copy what Chrome reported into the record; returns (ok, check reason or None)."""
        steps, chk = _chain_check(r)
        rec.update(chain=steps, sandbox=r.get("sandbox"), sandbox_retry=bool(r.get("sandbox_retry")),
                   viewport=r.get("viewport"), blocked_requests=r.get("blocked_requests"))
        raw_words = list(r.get("log") or [])
        ok = _render_ok(r)
        if not ok and r.get("error"):
            raw_words.append(f"browser error: {r['error']}")
        if raw_words:
            rec["browser_log"] = (rec.get("browser_log") or []) + raw_words
        save("browser.html", r.get("html"))
        return ok, chk

    def finish(html, final, status, mode_used, doc_url=None, **kw):
        """Measure html, unless it is another page than the one asked for. doc_url: the address of the
        document Chrome last loaded (see _doc_url); the redirect test uses it instead of the address bar."""
        where = doc_url or final
        if other_site(url, where):   # nothing measured is kept: it is another website's page
            keep = {k: v for k, v in kw.items() if k in ("user_agent",)}
            return done(status=status, final_url=where, redirected_to_other_site=True, error=_other_site_error(where), **keep)
        moved, soft, why = _wrong_page(url, doc_url or final, html, status)
        rec.update(redirected_to_other_page=moved, soft_404=soft)
        if why:
            keep = {k: v for k, v in kw.items() if k in ("user_agent", "note", "rendered_words")}
            return done(status=status, final_url=final, error=why, **keep)
        return done(html=html, final_url=final, status=status, mode_used=mode_used, **kw)

    def as_browser(why):
        """One Chrome visit with a normal browser user agent (bot name refused; see module doc)."""
        ct.before_request(url)
        r = _render(url, as_browser=True)
        ok, chk = take(r)
        rs = r.get("status")
        used = " Chrome was used for this one read although --raw was asked." if mode == "raw" else ""
        if chk:
            return done(status=rs, final_url=r.get("final_url"), blocked=True, block_kind="check", user_agent="browser",
                        error=f"{why}; as a normal browser it got a bot check page ({chk}), which is never bypassed")
        if not ok:
            return done(blocked=True, block_kind="refusal", user_agent="browser",
                        error=f"{why}; reading it as a normal browser also failed ({browser_reason(r.get('error'))})")
        if rs in (401, 403, 429) or _refusal(r["html"]):
            return done(status=rs, final_url=r.get("final_url"), blocked=True, block_kind="refusal", user_agent="browser",
                        error=f"{why}; as a normal browser it was refused too (HTTP {rs}" + (", an 'access denied' page)" if _refusal(r["html"]) else ")"))
        if rs != 200:
            return done(status=rs, final_url=r.get("final_url"), user_agent="browser", error=f"HTTP {rs}")
        return finish(r["html"], r.get("final_url") or url, rs, "rendered", doc_url=_doc_url(r), rendered_words=_words(r["html"]),
                      user_agent="browser", note=_cut(r, f"{why}, so the page was read once as a normal Chrome browser.{used}"))

    if not ct.allowed(url):
        return done(error=_refused_by_robots(url))
    st, final, ctype, raw, err = _raw(url)
    rec.update(status=st, final_url=final)
    if err == "robots-disallowed":
        return done(error=_refused_by_robots(url))
    save("server.html", raw)
    if other_site(url, final):   # before any block test: that other site's block is not this page's
        return done(redirected_to_other_site=True, error=_other_site_error(final))
    chk = _check_reason(raw, status=st) if raw else None
    if chk:   # at ANY status: a check served 200 or 202 is still a check
        return done(blocked=True, block_kind="check", error=f"blocked: the site served a bot check (status {st}): {chk}")
    if st in (401, 403, 429) or (raw and _refusal(raw)):
        why = f"the site answered {st} to this bot's name" + (" (an 'access denied' page)" if raw and _refusal(raw) else "")
        if browser_retry:
            return as_browser(why)
        return done(blocked=True, block_kind="refusal", error=f"blocked: {why}")
    if ctype and not _HTML_CT.search(ctype) and raw is not None and "<html" not in raw[:2000].lower():
        return done(error=f"not a web page (content type {ctype.split(';')[0]})")
    if raw is not None:
        rec["raw_words"] = _words(raw)
    if mode == "raw":
        if err or raw is None:
            return done(error=err or "no HTML")
        if st != 200:
            return done(error=f"HTTP {st}")
        return finish(raw, final, st, "raw")
    # rendered (default)
    ct.before_request(url)  # robots + pacing for the browser visit too
    r = _render(url)
    ok, chk = take(r)
    if chk:   # a check seen at any step of Chrome's visit, even one that solved itself (never bypassed)
        return done(status=r.get("status"), final_url=r.get("final_url"), blocked=True, block_kind="check",
                    error=f"blocked: the browser got a bot check page: {chk}")
    rs = r.get("status")
    if ok:
        if rs in (401, 403, 429) or _refusal(r["html"]):
            why = f"the browser visit got {rs} with this bot's name"
            if browser_retry:
                return as_browser(why)
            return done(status=rs, final_url=r.get("final_url"), blocked=True, block_kind="refusal", error=f"blocked: {why}")
        if rs == 200:
            rw = _words(r["html"])
            jsd = (rec["raw_words"] < 0.5 * rw) if (rec["raw_words"] is not None and rw) else None
            return finish(r["html"], r.get("final_url") or final, rs, "rendered", doc_url=_doc_url(r), rendered_words=rw,
                          js_dependent=jsd, note=_cut(r))
        if st != 200:
            return done(status=rs, final_url=r.get("final_url"), error=f"HTTP {rs}")
        r = dict(r, error=f"the browser got HTTP {rs}")
    # Chrome failed (incl. its own error page): fall back to the raw HTML when we have a real page
    if raw and st == 200 and not _refusal(raw):
        return finish(raw, final, st, "raw", note=f"measured without a browser: {browser_reason(r.get('error'))}")
    return done(error=err or (f"HTTP {st}" if st not in (None, 200) else None)
                or f"not measured: {browser_reason(r.get('error'))}, and the server HTML was not usable")
