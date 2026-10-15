#!/usr/bin/env python3
"""Convert check/trace/hack logs into the builder's <slug>-findings.json sidecar.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

WHY THIS EXISTS: an independent skill critique (2026-07-30) found that EVERY critical
audit-checks finding terminated in checks.log and never reached the client report — the
deployed deliverable showed only 5 generic auto-findings (broken link, H1s, meta desc, alt)
while the dead nav, the 480-Arizona phone, the indexed auth routes, 58 placeholder titles,
the live lorem page and 76 orphans were all invisible to the reader. The sidecar contract
existed but was MANUAL, and the orchestrator built the report before a human could fill it.
This closes that loop automatically.

Usage: checks-to-findings.py <slug> [audit-dir] [reports-dir]
Writes: <reports-dir>/<slug>-findings.json   (merged at the TOP of the report's findings list)
"""
import json, os, re, sys

def _client_copy(text):
    """Scrub operator-facing prose out of anything the CLIENT will read.
    Internal cross-references leaked into a deployed report on a law-firm site 2026-07-31:
    "Check 12/13 output above", "NOTE: orphaned injections…", "also run a `site:` check".
    A client has no idea what check 12 is. Apply to EVERY detail, whatever log it came from."""
    t = re.sub(r"\n\s*", " ", text or "")
    t = re.sub(r"\s*(See|see)?\s*[Cc]heck \d+(\s*/\s*\d+)*\s*(output)?\s*(above|below)?\.?", " ", t)
    t = re.sub(r"\s*(also )?run a `?site:`? check\.?", " ", t, flags=re.I)
    t = re.sub(r"\s*AUDIT-PROBLEMS(\.md)?\s*§?\s*\d*", " ", t)
    t = re.sub(r"\s*\(?see the .*?runbook.*?\)?", " ", t, flags=re.I)
    t = re.sub(r"\s*NOTE:.*?(?=(\s[A-Z][a-z])|$)", " ", t)          # operator NOTE: asides
    t = re.sub(r"\s*LIVE CATCH:.*?(?:\.|$)", " ", t)                 # internal training lore
    t = re.sub(r"\s*Headline finding:[^.]*\.", " ", t)               # instructions to the auditor
    t = re.sub(r"\b200-status\b", "working", t)                      # HTTP jargon
    t = re.sub(r"\bNAP[- ]signal\b", "business-listing", t, flags=re.I)  # NAP = internal SEO jargon
    t = re.sub(r"\bNAP\b", "name/address/phone", t)                  # spell out NAP for clients
    t = re.sub(r"\s*—?\s*HTTP 200, valid HTML, invisible to every status[^.]*\.", " ", t)
    t = re.sub(r"\s*FIX at the template[^.]*\.", " ", t, flags=re.I)  # internal shorthand
    # a cleaning-company audit (run 3, 2026-08-07) — operator COUNT-FORMS and vocabulary reaching the client verbatim.
    # The deployed report read "⚠️ 1 page(s) whose ADDRESS does not match their content" and
    # "(slug describes a different topic than the title)". "page(s)" is a log convention, the
    # warning glyph is operator signalling, SHOUTED words are log emphasis, and "slug" is jargon
    # the §47 audience does not have. Scrub the FORM here so every check inherits it (§13).
    t = re.sub(r"^\s*[⚠🚩🚨]️?\s*", "", t)                      # leading operator glyph
    t = re.sub(r"\s[⚠🚩🚨]️?\s", " ", t)                        # inline operator glyphs
    t = re.sub(r"\b(\d+)\s+page\(s\)", lambda m: f"{m.group(1)} page" + ("" if m.group(1) == "1" else "s"), t)
    t = re.sub(r"\b(\d+)\s+(\w+?)\(s\)", lambda m: f"{m.group(1)} {m.group(2)}" + ("" if m.group(1) == "1" else "s"), t)
    t = re.sub(r"\bslug\b", "address", t, flags=re.I)                # "slug" is not client vocabulary
    t = re.sub(r"\bADDRESS\b", "address", t)                         # log emphasis -> normal prose
    return re.sub(r"\s{2,}", " ", t).strip()[:1500]


def _r2sub_warn(t):
    """Strip critical-sounding phrasing from a warning-only section title."""
    return re.sub(r'\s*—.*$', '', t).strip() or t

from pathlib import Path
KIT_HOME = Path(__file__).resolve().parent
KIT_WORK = Path(os.environ.get('KIT_WORK') or KIT_HOME / 'work')

slug = sys.argv[1]
adir = sys.argv[2] if len(sys.argv) > 2 else str(KIT_WORK / "cases" / slug)
rdir = sys.argv[3] if len(sys.argv) > 3 else str(KIT_WORK / "crawl")

def read(p):
    try:
        return open(os.path.join(adir, p), encoding="utf-8", errors="replace").read()
    except Exception:
        return ""

checks, trace, hack = read("checks.log"), read("trace.log"), read("hack.log")
findings = []
# base_url — the site's real homepage address, used by the WALK-THROUGH copy so a finding can tell
# the reader the EXACT address to open ("Open your sitemap: https://example.com/sitemap.xml").
base_url = ""
try:
    _bj = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
    base_url = (_bj.get("seed") or "").rstrip("/")
    if not base_url:
        _p0 = (_bj.get("pages") or [{}])[0].get("url", "")
        _m0 = re.match(r"(https?://[^/]+)", _p0)
        base_url = _m0.group(1) if _m0 else ""
except Exception:
    base_url = ""
# RENDER-POISONED guard (a client site, 2026-08-04): render-check uses headless Chrome from THIS machine's IP, which
# a residential-proxy curl crawl bypasses but Chrome does NOT — so on a bot-walled host (SiteGround
# sgcaptcha) the render stage captures the CHALLENGE PAGE, not the site. Its verdict then reads
# "CONTENT: CSR" + "rendered 'Robot Challenge Screen'" and the sidecar would ship false CSR/empty-shell
# /title-rewrite findings — exactly the poisoned artifacts. Detect the challenge-page markers in
# render.log; when present, the render verdict is INVALID. The curl crawl (clean, via proxy) is the
# truth, so force _csr_blind False and let the real server-rendered content stand.
_render_log = read("render.log")
# Marker list grew from real WAFs seen in the wild. Sucuri's RATE-LIMIT interstitial says
# "You are being redirected..." — matched NONE of the original markers, so a throttled render
# produced "CSR / 21 empty shells / 100% blank" as three false client findings
# (a restoration-company site, 2026-08-05). ALSO treat a 0%-raw verdict on a site the CRAWL read
# fine as inherently untrustworthy — see _render_zero below.
_render_poisoned = bool(re.search(
    r"Robot Challenge Screen|sgcaptcha|sg-captcha|Just a moment|Checking your browser|"
    r"Attention Required|challenge-platform|You are being redirected|cloudproxy|"
    r"RENDER TRANSPORT MISMATCH|"
    r"Access Denied|Rate ?limit|Too Many Requests|DDoS protection|Verifying you are human",
    _render_log, re.I))
# CROSS-CHECK: render says the page has ZERO raw content, but the CRAWL captured real words
# from that same site. Both cannot be true — the render fetch was blocked/throttled, not the
# site rebuilt as an SPA. Trust the crawl (it succeeded) over the render (it did not).
_render_zero = bool(re.search(r"CONTENT:\s+CSR\s+\(raw = 0% of rendered words", _render_log))
# CSR verdict: when render-check says the pages are client-rendered, the raw-HTML (curl) crawl is
# blind to JS-rendered links/headings/titles — several content checks below key off this. But NOT
# when render was poisoned by a challenge screen (then "CSR" is the captcha page, not the site).
# If render reported 0% raw content, confirm against the CRAWL before believing it: a crawl that
# captured substantial words proves the raw HTML is NOT empty, so the render fetch was blocked.
if _render_zero and not _render_poisoned:
    try:
        _cz = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
        _wc_ok = sum(1 for _p in (_cz.get("pages") or [])
                     if _p.get("status") == 200 and (_p.get("word_count") or 0) > 100)
        if _wc_ok >= 5:
            _render_poisoned = True     # crawl saw real content; the render did not — render is wrong
    except Exception:
        pass

# CROSS-CHECK 2 (a restoration-company site, 2026-08-06): a THROTTLED fetch need not return 0% raw.
# Sucuri's rate-limit returned a PARTIAL body — "CONTENT: PARTIAL (raw = 92%...)" — so _render_zero
# was False and no WAF string matched, and TWO false findings shipped ("JS rewrites your titles",
# "57% of your page is blank"). The durable signal is STRUCTURAL and independent of the percentage:
# render parsed ZERO links AND ZERO headings AND an EMPTY raw title, while the CRAWL read that same
# URL with a real title, real links and real headings. A document with 800+ words but no <a>, no
# <h*> and no <title> is not a web page — it is a truncated/blocked response.
if not _render_poisoned:
    try:
        _m_struct = re.search(r"links (\d+)/(\d+) · headings (\d+)/(\d+)", _render_log)
        _m_title = re.search(r"TITLE DIFFERS\s+raw ''\s+rendered '[^']+'", _render_log)
        if _m_struct and _m_title:
            _rl, _vl, _rh, _vh = (int(x) for x in _m_struct.groups())
            if _rl == 0 and _rh == 0 and (_vl > 0 or _vh > 0):
                _cz2 = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
                # the crawl proves the raw HTML of this site DOES carry titles+links+headings
                _ok2 = sum(1 for _p in (_cz2.get("pages") or [])
                           if _p.get("status") == 200 and (_p.get("title") or "").strip()
                           and (_p.get("heading_counts") or {}).get("h1", 0) > 0)
                if _ok2 >= 5:
                    _render_poisoned = True
    except Exception:
        pass
# CSR NEEDS EVIDENCE (2026-10-14: a static one-page site, ~30 words and no JavaScript, shipped a
# CRITICAL "1 pages are empty shells until JavaScript runs"). A CSR block in render.log counts only
# when JavaScript demonstrably adds content: the rendered page has >= 50 more words than the raw
# HTML, or render-check saw a script-built root (id=root/app/__next) - and never when render-check
# counted 0 scripts on the page.
def _csr_evidence(log):
    for blk in re.split(r"\n(?=https?://)", log or ""):
        if not re.search(r"CONTENT:\s+CSR", blk):
            continue
        if re.search(r"·\s*scripts 0\b", blk):
            continue
        m = re.search(r"words raw/rendered (\d+)/(\d+)", blk)
        if (m and int(m.group(2)) - int(m.group(1)) >= 50) or "script root" in blk:
            return True
    return False
_csr_blind = bool(re.search(r"CONTENT:\s+CSR", _render_log)) and not _render_poisoned and _csr_evidence(_render_log)
# Owner rule (2026-10-14, example.com came back PARTIAL as "client-rendered"): a site with fewer than
# 3 real pages, or whose pages carry no <script> at all, is never treated as client-rendered. Nothing
# can be "blank until JavaScript runs" on a page that has no JavaScript.
try:
    _cz3 = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
    _rows3 = [_p for _p in (_cz3.get("pages") or [])
              if _p.get("status") == 200 and "html" in str(_p.get("content_type", "")).lower()]
    _real_pages = len(_rows3)
    _site_has_scripts = any(int((_p.get("html_tag_inventory") or {}).get("script", 0) or 0) > 0 for _p in _rows3)
except Exception:
    _real_pages, _site_has_scripts = 99, True
if _real_pages < 3 or not _site_has_scripts:
    _csr_blind = False

# --- DATA THE CRAWL CAPTURED BUT NO STAGE REPORTED (DE-04 / SYNTHESIS D7, 2026-10-14) ------------
# grep on 2026-10-14: no stage read the X-Robots-Tag header or the crawl's missing_title count.
# Both are read here from the crawl rows. Pages a site owner does not want in Google (login, cart,
# search, feeds, admin) are left out of the noindex finding.
import sys as _sys_d4
_sys_d4.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl_transport as _tx_d4
_NOT_WANTED_D4 = re.compile(r"/(wp-admin|wp-login|login|log-in|signin|sign-in|signup|sign-up|register|"
                            r"registration|registrations|password|account|my-account|session|sessions|cart|"
                            r"checkout|basket|search|feed|preview|thank-you|thanks)(/|\?|$|\.)|[?&](s|q|replytocom|"
                            r"add-to-cart|preview)=", re.I)
_ASSET_D4 = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp4|webm|mov|avi|mp3|wav|ogg|pdf|"
                       r"docx?|xlsx?|pptx?|zip|gz|rar|7z|css|js|mjs|json|xml|txt|woff2?|ttf|eot|otf)(\?|#|$)", re.I)
_MISSING_TITLE_EMITTED = False
try:
    _d4 = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
    _rows_d4 = [_p for _p in (_d4.get("pages") or []) if isinstance(_p, dict)]
except Exception:
    _rows_d4 = []
def _html_page_d4(_p):
    _ct = (_p.get("content_type") or "").lower()
    if _p.get("non_html") or (_ct and "html" not in _ct):
        return False
    return not _ASSET_D4.search((_p.get("url") or "").split("#")[0])
_live_d4 = [_p for _p in _rows_d4 if _p.get("status") == 200 and not _p.get("redirected") and _html_page_d4(_p)]
# (a) X-Robots-Tag: noindex/none on HTML pages. Only rows that RECORDED their headers (a list) count;
# older crawls have no xrobots_headers key and are not measured, never "clean".
_xr_noidx = [_p for _p in _live_d4 if isinstance(_p.get("xrobots_headers"), list)
             and _tx_d4.robots_noindex(_p["xrobots_headers"])
             and not _NOT_WANTED_D4.search(_p.get("url") or "")]
if _xr_noidx:
    _n = len(_xr_noidx)
    _pg = "page" if _n == 1 else "pages"
    findings.append({
        "severity": "high",
        "title": f"{_n} {_pg} tell{'s' if _n == 1 else ''} Google not to show {'it' if _n == 1 else 'them'}, in a hidden server setting",
        "detail": (
            f"When we loaded {'this page' if _n == 1 else 'these pages'}, your server sent a hidden "
            "instruction with each one: \"X-Robots-Tag: noindex\". That tells Google not to show the "
            "page in search results. You cannot see it on the page or in the page's code; it travels "
            "with the page, in the server's reply.\n\n"
            f"Google obeys it, so {'this page' if _n == 1 else 'these pages'} will not appear in "
            "search, whatever the page itself says. If you want them found, this setting must go."),
        "fix": (
            "Ask your web person or host to remove the X-Robots-Tag noindex for these addresses. It is "
            "set in the server or CDN settings (for example an .htaccess or nginx rule, a security or "
            "SEO plugin, or a Cloudflare rule), not in the page. If the pages must stay out of Google, "
            "you can ignore this."),
        "urls": [f"{_p.get('url')}  — server sends: X-Robots-Tag: {'; '.join(_p['xrobots_headers'])[:120]}"
                 for _p in _xr_noidx[:40]]})
# (b) missing title on pages that are meant to be indexed. Under CSR the raw title is a shell
# value, so the CSR finding covers it instead.
_no_title = [_p for _p in _live_d4 if not (_p.get("title") or "").strip()
             and _p.get("indexable") is not False and _p.get("noindex") is not True] if not _csr_blind else []
if _no_title:
    _n = len(_no_title)
    _MISSING_TITLE_EMITTED = True
    findings.append({
        "severity": "high",
        "title": (f"{_n} page{'s have' if _n != 1 else ' has'} no title, so Google writes its own "
                  "headline for " + ("them" if _n != 1 else "it")),
        "detail": (
            "A page title is the headline Google shows for a page in search results, and the text on "
            f"the browser tab. {'These pages have' if _n != 1 else 'This page has'} an empty title or "
            "none at all. Google then makes up a headline from other text on the page, and it often "
            "picks the wrong words."),
        "fix": ("Give each page below its own short title that says what the page is about and, for "
                "a local business, where (for example \"Roof Repair in Springfield | Smith Roofing\")."),
        "urls": [_p.get("url") for _p in _no_title[:40]]})
NOT_MEASURED_CSR = []   # check numbers skipped because the raw crawl cannot see a client-rendered site

# --- audit-checks: split into numbered sections, keep any that raised a flag -----------------
SEV = {  # section-number -> (severity, human title)
    "1": ("critical", "Canonical points off-domain"),
    "2": ("high",     "Self-canonical mismatch"),
    "2b": ("critical","Canonical resolves to a redirect, not a 200"),
    "3": ("high",     "Structured data missing"),
    "5": ("high",     "Placeholder / demo content rendered to visitors"),
    "6": ("high",     "Links/assets reference the wrong host variant (forced 301s)"),
    "7": ("critical", "Auth / account routes are crawlable and indexable"),
    "8": ("critical", "Placeholder or mass-duplicate page titles"),
    "9": ("critical", "Contact integrity: inconsistent or foreign phone numbers"),
    "10": ("critical","Malformed hrefs — links that navigate nowhere"),
    "11": ("critical","Placeholder/lorem URLs live in production"),
    "12": ("critical","Host duplication — the whole site is served twice (apex + www)"),
    "13": ("critical","Sitemap integrity — junk URLs submitted to Google"),
    "4":  ("high",    "Thin site — almost no organic surface"),
    "16": ("critical","Structured data conflicts — two versions of the business"),
    "17": ("medium",  "Noindexed pages inventory"),
    "18": ("critical","Pre-consent tracking exposure — pixels fire before consent"),
    "19": ("critical","Language variants without hreflang / wrong lang attribute"),
    "20": ("critical","Brand transition — previous-domain equity stranded"),
    "21": ("critical","Identical-content page sets — same page under many addresses"),
    "22": ("medium",  "Residue slugs / slug-content mismatch"),
    "23": ("medium",  "No lead-capture form anywhere"),
    "24": ("critical","Store has zero live products"),
    "25": ("medium",  "Zero images sitewide"),
    "26": ("low",     "Missing security headers"),
}
for m in re.finditer(r"^(\d+b?)\.\s+(.+?)$", checks, re.M):
    num, heading = m.group(1), m.group(2).strip()
    # window must hold a FULL section (30 old->new sitemap pairs ≈ 4,000 chars; the old 2,600
    # silently truncated the list to 10 while the title said 30 — a roofing site 2026-07-31). The
    # split on the next numbered heading below bounds it correctly; the slice is just a guard.
    body = checks[m.end(): m.end() + 60000]
    body = re.split(r"^\d+b?\.\s", body, maxsplit=1, flags=re.M)[0].strip()
    if "🚩" not in body and "⚠️" not in body:
        continue                                    # section passed — skip
    # 🔴 UNKNOWN IS NOT A FINDING (two independent clean-agent catches, 2026-08-05).
    # Checks use ⚠️ for BOTH "here is a warning" and "I could not measure this" (bot challenge,
    # blocked probe, unreachable host). This gate treated the second as a defect, so on every
    # bot-walled host an UNMEASURED check fell through to its canned client copy and fabricated a
    # finding — a client site shipped "Your site answers at two different addresses" while check 12's own
    # log said "⚠️ UNKNOWN — a bot challenge or block interfered" (ground truth: www 301s cleanly).
    # A section whose ONLY warnings are UNKNOWN-shaped has measured nothing and must ship nothing.
    if "🚩" not in body:
        _warn_lines = [l for l in body.splitlines() if "⚠️" in l]
        # a cleaning site 2026-09-23 (and a lawn-care site the day before): check 16's "⚠️ homepage
        # fetch/parse failed: HTTP Error 503" slipped past this list and shipped "two conflicting
        # descriptions of your business". Transport failures are UNKNOWN, whatever the wording.
        if _warn_lines and all(re.search(r"UNKNOWN|could not|cannot be judged|unreachable|"
                                         r"bot challenge|blocked|not probed|interfered|"
                                         r"fetch/parse failed|HTTP Error|probe failed|Service Unavailable|"
                                         r"not measured|re-probe",
                                         l, re.I) for l in _warn_lines):
            continue                                # unmeasured, not defective — never invent copy
    sev, title = SEV.get(num, ("high", heading))
    # Plain-language framing per check: (client title, "what we did / what it means" preamble).
    CONTEXT = {
      "1":  ("Pages tell Google the 'real' version lives on another website",
             "Every page can name its official address. We read that tag on each page and found some "
             "pointing at a different website entirely, which tells Google not to index yours."),
      "2":  ("Some pages name a different page as their official version",
             "We compared each page's declared official address to its own address. Where they "
             "disagree, Google may index the other page instead of this one."),
      "2b": ("Pages declare an 'official version' that redirects instead of loading",
             "Each page carries a hidden tag naming its official version - the address Google should index. That address should load directly. We requested each one and found some "
             "bounce to another URL, which stops Google indexing the page it was told to index."),
      "3":  ("Pages are missing the structured description search engines read",
             "Structured data is a hidden summary that tells Google what a page is about. We checked "
             "every crawled page for it."),
      "4":  ("The site is too small to compete in search",
             "We counted the site's real, unique pages. Search visibility is built page by page — "
             "each service or product category — and each area or topic you serve — needs its own "
             "page before Google has anything to rank."),
      "6":  ("Links and images use the wrong version of your domain",
             "Your site answers on one official hostname. We checked every link and asset reference "
             "and found some using the other version, which forces an extra redirect on each request."),
      "7":  ("Login and account pages can be found and indexed by Google",
             "We requested the sign-in and account URLs the way a search engine would. They returned "
             "a real page rather than being blocked, so Google can list them in search results."),
      "8":  ("Many pages share the same title",
             "A page title is the headline Google shows in results. We collected the title of every "
             "page and grouped the duplicates."),
      "9":  ("The phone numbers on the site are inconsistent",
             "We read every phone number shown and every click-to-call link across a sample of pages "
             "and compared them."),
      "10": ("Some links are written incorrectly and go nowhere when clicked",
             "We examined the link code on each page. These are valid-looking links whose address is "
             "malformed, so a click loads nothing and search engines cannot follow them."),
      "11": ("Unfinished demo pages are live on the site",
             "We looked for placeholder text and demo URLs left over from building the site."),
      "12": ("The whole site loads on two different addresses",
             "We requested the site with and without 'www'. Both answer independently instead of one "
             "sending visitors to the other, so every page effectively exists twice."),
      "13": ("The sitemap you submit to Google contains pages that should not be there",
             "A sitemap is the list of pages you ask Google to index. We fetched yours and checked "
             "what is in it."),
      "14": ("A large group of near-identical pages makes up most of the site",
             "We grouped every URL by its section to see how the site is distributed."),
      "25": ("The site has no photographs at all",
             "We counted the images across every page. For a business selling physical goods or "
             "services, pages without a single real photo cost trust and conversions — and they "
             "are why shared links show no picture."),
      "26": ("The site is missing standard security headers",
             "These are one-line server settings that harden a site against common attacks and "
             "signal good housekeeping. We checked the five most common."),
      "24": ("Your store has nothing in it — every shopping path dead-ends",
             "We read the store's own product catalog the way shopping tools and Google Merchant "
             "do."),
      "23": ("Visitors have no way to contact you except calling",
             "We counted the contact forms across the whole site. Most visitors browse after "
             "hours — a site whose only contact path is a phone number loses the leads who "
             "will not or cannot call right now."),
      "21": ("The same page exists under several different addresses",
             "We compared every page's size and headline structure across the site to find sets of "
             "addresses serving identical content."),
      "22": ("Some page addresses describe a different article than the page contains",
             "A page's web address is both a ranking signal and a trust signal. We compared each "
             "address against what the page is actually about."),
      "20": ("Your previous brand's website is losing everything it earned",
             "Your homepage announces a brand transition, so we tested what happens to the old "
             "brand's web presence — its domain, its inner pages, and whether Google is told the "
             "two brands are the same business."),
      "19": ("Your translated pages are invisible to the people searching in that language",
             "Sites with pages in more than one language must tell Google which page is which "
             "translation (a tag called hreflang) and declare each page's language. We checked "
             "your language sections for both."),
      "16": ("Your website gives Google two conflicting descriptions of your business",
             "Websites carry a hidden 'business card' in their code (called structured data) that "
             "tells Google your exact name, location and details. We read every copy of it on your "
             "homepage and compared them."),
      "17": ("Some pages on the site are set to be hidden from Google",
             "A page can carry an instruction telling Google not to list it in search results. "
             "That is often deliberate — thank-you pages, form and submission pages, legal "
             "boilerplate — so this is a checklist, not an alarm: scan the list below and confirm "
             "nothing you WANT found (services, articles, project pages) is on it."),
      "15": ("Your own pages link through redirects instead of to the final address",
             "We followed the internal links and checked whether each destination loads directly or "
             "bounces to another address first."),
      "27": ("The software running your website is no longer getting security updates",
             "Every website runs on background software (like PHP) that the company behind it "
             "updates for a set number of years, then stops. We read what your server announces "
             "about itself and checked that version against the dates support ended."),
      "28": ("Addresses that don't exist on your site answer as if they do",
             "When someone asks for a page that isn't there, a website is supposed to answer with "
             "a code that means 'no such page' (a 404). We asked your site for three made-up "
             "addresses that cannot possibly exist and read the code it sent back."),
      "18": ("Advertising trackers on your site run before visitors can consent",
             "Advertising and analytics pixels (Facebook, LinkedIn, Google and similar) send data "
             "about each visitor to those companies the moment a page loads. We read the code of "
             "your pages to see which trackers are installed and whether a consent tool is in place "
             "to hold them back. This matters right now because law firms are sending demand "
             "letters — most visibly under California's privacy law, CIPA — to businesses whose "
             "sites share visitor data before asking permission, and leftover pixels from old "
             "campaigns are their most common target."),
    }
    _ctx = CONTEXT.get(num)
    if _ctx:
        title = _ctx[0]
    # 🔴 SIGNAL-SPECIFIC TITLES (§67, a real-estate site, 2026-08-06). CONTEXT maps ONE canned
    # title per check NUMBER, but several checks emit multiple independent signals — so the section
    # gate ("any 🚩 in this block") attaches whichever title is first in the table, regardless of
    # what actually fired. check 16 shipped "two conflicting descriptions of your business" on a
    # site whose ONLY flag was a self-authored star rating: two entities, no conflict, wrong title,
    # and destructive advice attached. Rather than another per-check guard (check 12 already has
    # two), map SIGNAL → title and let the fired signal choose. Add a row here when a check gains
    # a new independent signal.
    _SIGNAL_TITLES = {
        "16": [(r"conflicting business NAMES",
                "Your website gives Google two conflicting descriptions of your business"),
               (r"coordinates ~\d+m apart",
                "Your website lists two different locations for your business"),
               (r"self-authored Review node",
                "Your website publishes reviews it wrote about itself"),
               (r"double-encoded HTML entity",
                "Your business name is corrupted in the code Google reads")],
        "12": [(r"BROKEN HTTPS CERTIFICATE",
                "One of your web addresses shows visitors a security warning"),
               (r"insecure http:// address also serves",
                "Your site also loads over an insecure connection")],
    }
    for _sig_re, _sig_title in _SIGNAL_TITLES.get(num, []):
        if re.search(_sig_re, body):
            title = _sig_title
            break
    # check 3 org-but-no-LocalBusiness case (a pest-control site, 2026-08-04): the site DOES have schema on most
    # pages, so the generic "missing structured description" title is false and the raw "805/980 pages
    # have schema. Types: ['Article',…]" body would ship a Python list to the client. Give it its own
    # title + plain-language detail; the specific gap is a missing local-entity node, not absent schema.
    # check 26 SECURITY HEADERS — scope check (owner correction 2026-08-05): this measures FIVE
    # specific response headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options,
    # Referrer-Policy). HTTPS/the padlock is NOT one of them, so the copy must never imply the
    # site is "not secure" — a site can be fully HTTPS and still miss all five.
    # check 10 MALFORMED HREFS — a link whose address is typed wrong in the code, so clicking it
    # goes nowhere or to a nonsense address. Not a 404 (no page was ever requested) — the address
    # itself is broken.
    # check 12 HOST DUPLICATION — the copy must reflect what was PROVEN, not assume sitewide.
    # Owner catch 2026-08-05: probing only the two homepages and writing "the whole site is served
    # twice" is an unfounded generalisation. The check now samples inner pages; mirror its verdict.
    if num == "12":
        # 🔴 OK-VERDICT GUARD (clean-agent catch, a moving-company site, 2026-08-05). The section-level gate
        # (line ~140) fires this handler whenever the block contains ANY 🚩 — but check 12 emits TWO
        # independent signals: (a) apex-vs-www duplication and (b) http:// not redirecting to https.
        # On that site www 301s to the apex and the check printed "ok — one canonical host", yet
        # the http:// 🚩 dragged in a CRITICAL "Your site answers at two different addresses" that
        # CONTRADICTED the very lines above it (§49 family: a finding must never contradict its own
        # evidence). Only ship the duplication finding when the check actually found duplication.
        # 🔴 UNKNOWN-VERDICT GUARD (clean-agent catch, a client site, 2026-08-05). The guard below only
        # recognised the POSITIVE clean verdict ("ok — one canonical host"). But check 12 has a THIRD
        # outcome: "⚠️ UNKNOWN — a bot challenge or block interfered", emitted when the probe measured
        # a WAF (SiteGround 202) instead of the site. That string is neither "ok" nor a 🚩, so _dup12
        # evaluated True and the handler fell through to the no-evidence `else` branch, shipping a
        # CRITICAL "Your site answers at two different addresses" built from ZERO measurement — on a
        # site whose www host actually 301s correctly (verified live through the residential proxy:
        # https://www.example.org/ -> 301 -> https://example.org/). A check that says "I could not measure
        # this" must NEVER become a client finding (§49: a finding must not contradict its own
        # evidence; §31: UNKNOWN discipline end-to-end).
        # 🔴 THIRD CLEAN VERDICT (an insurance site, 2026-08-05). The guard grew as a DENYLIST of clean
        # phrases ("ok — one canonical host", then "UNKNOWN") and check 12 has a THIRD:
        # "⚠️ NOT duplication — one variant correctly redirects". An insurance site shipped a
        # CRITICAL "Your site answers at two different addresses" while the check's own log said
        # NOT duplication (verified live: apex 301s to www, http 301s to https — the site is clean).
        # A denylist of clean phrasings will keep losing to the next one, so INVERT it: require the
        # POSITIVE duplication evidence — the check states it explicitly when it finds it.
        _unknown12 = ("UNKNOWN" in body)
        _dup12 = bool(re.search(r"🚩 BOTH host variants return 200", body)) and not _unknown12
        _http12 = re.search(r"🚩 the insecure http:// address[^\n]*", body)
        if _unknown12 and not _http12:
            continue
        if not _dup12:
            # www is correctly redirected. Ship ONLY the http://-not-redirecting finding, if present.
            if _http12:
                _httphost = (base_url.replace("://www.", "://").rstrip("/"))
                findings.append({
                    "severity": "medium",
                    "title": "Your site also loads on the older, unencrypted address",
                    "whats_happening": (
                        "Web addresses start with either 'https' (encrypted — the padlock) or the "
                        "older 'http' (not encrypted). Your site correctly uses https, and the 'www' "
                        "version of your address already forwards to the main one.\n\n"
                        "But the plain 'http' version still answers on its own instead of sending "
                        "visitors to the secure one."),
                    "see_it": (
                        f"Type this into your browser exactly as written, with http and no s: "
                        f"{_httphost.replace('https://', 'http://')}/\n\n"
                        "Watch the address bar after the page loads."),
                    "youll_see": (
                        "The page loads and the address bar still shows 'http' without the padlock, "
                        "instead of switching itself to the secure 'https' version."),
                    "why": (
                        "Anything sent over the unencrypted version — including a quote form with a "
                        "customer's name, phone and address — travels in the open where it can be "
                        "read on shared or public wifi.\n\n"
                        "It also gives Google a second copy of every page at a second address, which "
                        "splits the credit your pages have earned instead of adding it all up on one. "
                        "Browsers additionally show a 'Not secure' warning on the http version, which "
                        "costs you enquiries."),
                    "fix": (
                        "Have whoever manages your hosting redirect all http traffic to the https "
                        "version. On most hosts this is a single 'Always use HTTPS' switch and takes "
                        "a minute. Your existing www redirect already does the same job for the other "
                        "half of the problem."),
                    "urls": [f"{_httphost.replace('https://', 'http://')}/"]})
            continue
        # MITIGATION (clean-agent catch 2026-08-05): if every page already self-canonicals to ONE
        # host, Google has an explicit signal for which version is real. That does not remove the
        # problem, but it changes it from "Google must guess" to "clean this up" — and the finding
        # must say so rather than implying the worst case.
        _canon_note12 = ""
        try:
            _cj12 = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
            _p12s = [p for p in (_cj12.get("pages") or [])
                     if p.get("status") == 200 and not p.get("redirected")]
            _selfc = [p for p in _p12s
                      if (p.get("canonical") or "").rstrip("/") == (p.get("url") or "").rstrip("/")]
            if _p12s and len(_selfc) >= len(_p12s) * 0.9:
                _canon_note12 = (
                    "\n\nOne thing works in your favour: every page already carries a tag naming "
                    "itself as the official version, and they all point at the same one. Google "
                    "usually follows that, so this is more of a tidy-up than an emergency — but the "
                    "tag is only a hint, and a redirect settles it for good.")
        except Exception:
            pass
        _m12 = re.search(r"(\d+) of (\d+) inner pages tested serve BYTE-IDENTICAL", body)
        _p12 = re.search(r"of (\d+) inner pages tested only (\d+) are identical", body)
        if _m12:
            _n_same, _n_tot = _m12.group(1), _m12.group(2)
            _title12 = "Your whole site answers at two different addresses"
            _what12 = (
                "A website can be reached with 'www' in front of the address or without it. Normally "
                "one of them is the real one and the other simply forwards to it.\n\n"
                "On your site both answer on their own. Neither forwards to the other, and every "
                f"one of the {_n_tot} inner pages we tested serves byte-for-byte identical content "
                "at both addresses. So this is not just the homepage — every page of your site "
                "exists at two addresses at once, which doubles the number of addresses Google has "
                "to crawl to cover the same site.")
        elif _p12:
            _n_tot, _n_same = _p12.group(1), _p12.group(2)
            _title12 = "Your site answers at two different addresses"
            _what12 = (
                "A website can be reached with 'www' in front of the address or without it. Normally "
                "one is the real one and the other forwards to it. On your site both answer on their "
                f"own.\n\nWe tested {_n_tot} inner pages: {_n_same} were the same at both addresses "
                "and the rest were not — so this is not a clean top-to-bottom copy. Someone should "
                "check which sections exist at both addresses before changing anything.")
        else:
            _title12 = "Your site answers at two different addresses"
            _what12 = (
                "A website can be reached with 'www' in front of the address or without it. Normally "
                "one is the real one and the other forwards to it. On your site both answer on their "
                "own.\n\nWe could only confirm this on the front page — we could not test the inner "
                "pages from here, so we do not know yet how much of the site is affected.")
        findings.append({
            "severity": sev,
            "title": _title12,
            "whats_happening": _what12,
            "see_it": (
                f"Open your site without the www: {base_url.replace('://www.', '://')}/\n"
                f"Now open it with the www in front.\n\n"
                "Watch the address bar each time."),
            "youll_see": (
                "Both load your site, and the address bar keeps whichever version you typed. Neither "
                "one changes to the other. That is the problem — normally one should change."),
            # Owner framing (his own words, from his recorded audits): the headline consequence is
            # SPLIT CRAWL BUDGET and DUPLICATE CONTENT, not just split link equity. "Crawl budget is
            # the amount of times that Google will come by and crawl your website. So if you're
            # serving two different versions, you're essentially cutting that crawl budget in half."
            # Lead with that; it is what makes a business owner act.
            "why": (
                "1) Google has to crawl twice as much. Every site gets a certain amount of crawl "
                "budget — simply how many times Google comes by and crawls your pages. Serving two "
                "versions means Google spends half that budget re-crawling copies of pages it "
                "already has, so your real pages get half the attention they should.\n\n"
                "2) Google sees duplicate content. It visits both versions and finds two identical "
                "sites. As far as Google is concerned these are separate addresses serving the same "
                "content — the textbook definition of duplicate content — so your pages end up "
                "competing with themselves instead of ranking as one strong page.\n\n"
                "3) Any links you earn get split. If one site links to the www version and another "
                "links to the non-www version, that credit is divided between two addresses instead "
                "of building up on one. Google also has to pick which version to show, and it may "
                "not pick the one you have been promoting.\n\n"
                "A useful way to picture it: copy and paste a document on your computer and you get "
                "two files with almost the same name holding identical content. You know they are "
                "the same thing; the computer just sees two files. Google works the same way with "
                "URLs."
                + _canon_note12),
            "fix": (
                "Pick one address as the real one — most businesses use whichever already appears on "
                "their cards, listings and Google Business Profile.\n\n"
                "Then have whoever manages your hosting set the other one to forward to it. After "
                "that, make sure your sitemap and the links inside your own pages all use the "
                "chosen version."),
            "urls": re.findall(r"https?://\S+", body)[:4]})
        continue
    if num == "10":
        # 🔴 OK-VERDICT GUARD (clean-agent catch, a client site, 2026-08-05) — same defect class
        # as the check-12 guard above. Check 10 emits TWO INDEPENDENT signals:
        #   (a) MALFORMED hrefs (address typed wrong in the code), and
        #   (b) PLACEHOLDER links — href="#" on nav text whose page was never built.
        # The section gate fires this handler on ANY ⚠️/🚩, so on a site with only (b) the
        # placeholder signal dragged in the malformed-href copy — a CRITICAL that CONTRADICTED the
        # "ok — no malformed hrefs in the sample" line printed directly above it, and shipped with
        # urls:[] while its body said "the pages listed below" five times (§42 bodiless-accordion +
        # §49 contradicts-its-own-evidence). Only ship the malformed finding when malformed hrefs
        # were actually found; ship the placeholder links as their own honest finding.
        _mal10 = ("ok — no malformed hrefs in the sample" not in body)
        _ph10 = re.search(r"⚠️ (\d+) PLACEHOLDER link\(s\)", body)
        if not _mal10:
            if _ph10:
                _phtexts = re.findall(r'^\s+"([^"]+)" \((\d+)x\)', body, re.M)
                _phn = _ph10.group(1)
                _phlist = ", ".join(t.strip() for t, _ in _phtexts[:6]) or "menu links"
                findings.append({
                    "severity": "medium",
                    "title": "Some menu links lead nowhere because the pages were never built",
                    "whats_happening": (
                        "A link is made of two parts: the words people see, and the address it "
                        "points to, which sits in the page's code where visitors never look.\n\n"
                        f"On {_phn} of your links that address was left empty — a placeholder the "
                        "website builder puts in when a link is added before its page exists. The "
                        "words still appear, styled exactly like every other link, so they look "
                        "completely real.\n\n"
                        f"These are the ones we found: {_phlist}."),
                    "see_it": (
                        "Open your homepage and look at the very bottom, where the small print "
                        f"usually sits. Find the words {_phlist}. Click one."),
                    "youll_see": (
                        "Nothing happens. The page doesn't change and the address at the top of "
                        "your browser stays exactly the same — because there is no page to go to."),
                    "why": (
                        "A visitor who clicks and gets nothing assumes the site is broken, and most "
                        "people don't try a second time.\n\n"
                        "Privacy and terms links matter beyond that. Visitors look for them to "
                        "decide whether a business is legitimate, and the advertising and payment "
                        "tools many sites run expect a real privacy page to exist. A link that "
                        "promises one and delivers nothing is worse than having no link at all."),
                    "fix": (
                        "Two options. Either build the pages — a privacy policy and a terms page "
                        "are short, standard documents your web person can produce quickly — and "
                        "point the links at them. Or, if you don't want those pages, remove the "
                        "links entirely so nobody clicks something that does nothing.\n\n"
                        "Building them is the better choice: a site that collects any visitor "
                        "information is expected to have a privacy page."),
                    "urls": []})
            continue
        _bad10 = re.findall(r"^\s+(https?://\S+|/\S+)", body, re.M)[:20]
        _n10 = len(_bad10)
        findings.append({
            "severity": sev,
            "title": "Some links on your site are typed wrong and go nowhere when clicked",
            "whats_happening": (
                "A link has two parts: the words people see, and the address it points to, which "
                "sits in the page's code where visitors never look.\n\n"
                "On some of your links that address has been typed incorrectly — a missing piece, a "
                "stray character, two addresses stuck together. The link still looks completely "
                "normal on the page. It just doesn't work when someone clicks it."),
            "see_it": (
                "Open one of the pages listed below and find the link. Click it.\n\n"
                "(These are different from the broken links in the other finding. Those point at a "
                "real address where the page was removed. These point at an address that was never "
                "valid in the first place.)"),
            "youll_see": (
                "Usually nothing happens at all — the page just sits there. Sometimes you'll land "
                "on an error page, or on a strange address that has your own address stuck onto "
                "the front of another one."),
            "why": (
                "A customer clicks it, nothing happens, and they assume your site is broken. Most "
                "people don't try twice.\n\n"
                "These are also easy to miss, because the link looks perfectly fine when you're "
                "just reading the page — you only find them by clicking."),
            "fix": (
                "On each page below, find that link and retype the address correctly. Usually it's "
                "one character. If you're not sure what the address should be, open the page you "
                "want it to go to, copy the address from your browser's address bar, and paste it in."),
            "urls": _bad10})
        continue
    # check 17 NOINDEX INVENTORY — mechanism is specifically the `noindex` META TAG on 200 pages
    # (robots.txt is a different thing and is NOT what this measures). Owner teaching 2026-08-05:
    # noindex is a REQUEST, not a guarantee, and the durable answer is to stop linking to it.
    if num == "17":
        _ni_urls = re.findall(r"^\s+(https?://\S+)", body, re.M)[:25]
        _nn = len(re.findall(r"^\s+https?://\S+", body, re.M)) or len(_ni_urls)
        findings.append({
            "severity": "medium",
            "title": (f"{_nn} of your pages are asking Google not to show them — confirm that's on purpose"
                      if _nn != 1 else
                      "1 of your pages is asking Google not to show it — confirm that's on purpose"),
            "whats_happening": (
                "A page can carry a short instruction in its code that says to search engines: "
                "please don't list this one in search results. It's called a noindex tag. Visitors "
                f"never see it; the page works normally for anyone who opens it.\n\n"
                f"{_nn} of your pages carry that instruction right now."),
            "see_it": (
                "Open any address from the list below — it loads like a normal page.\n\n"
                "To see the instruction itself: right-click the page, choose \"View page source\", "
                "then press Ctrl+F (Cmd+F on a Mac) and search for: noindex"),
            "youll_see": (
                "You'll find a line containing the word noindex. That's the page telling search "
                "engines to leave it out of results."),
            "why": (
                "This is often completely intentional — thank-you pages after a form, checkout "
                "steps, legal boilerplate. Nothing wrong with it.\n\n"
                "It's a problem only if something you WANT customers to find is on the list. So "
                "this is a checklist, not an alarm: read the list and make sure no service page, "
                "location page, or article you care about is there.\n\n"
                "One thing worth knowing: noindex is a request, not a lock. Google honours it in "
                "the normal course of things, but a page can still end up listed — usually when "
                "other sites link to it. If a page genuinely must stay out of search, the reliable "
                "approach is to stop linking to it at all, and to put anything truly private "
                "behind a login rather than relying on a tag."),
            "fix": (
                "Go through the list. For anything that should be found, remove the noindex "
                "instruction (on most website platforms it's a per-page toggle labelled something "
                "like \"hide from search engines\").\n\n"
                "For everything that should stay hidden, leave it — and check you're not linking to "
                "it from your menus or footer, since that's what tends to drag hidden pages into "
                "search results anyway."),
            "urls": _ni_urls})
        continue
    if num == "26":
        _missing26 = re.findall(r"(strict-transport-security|content-security-policy|x-frame-options|"
                                r"x-content-type-options|referrer-policy)", body, re.I)
        _mset = sorted(set(m.lower() for m in _missing26))
        _friendly = {
            "strict-transport-security": "always use the secure connection",
            "content-security-policy": "only run code from places you approve",
            "x-frame-options": "don't let other sites embed your pages",
            "x-content-type-options": "don't let files pretend to be something else",
            "referrer-policy": "limit what you tell other sites about your visitors",
        }
        _list26 = "\n".join(f"    • {_friendly[m]}" for m in _mset if m in _friendly)
        findings.append({
            "severity": "low",
            "title": "Your site is missing some optional safety settings",
            "whats_happening": (
                "When someone visits your site, your web server sends the page along with a few "
                "short instructions to their browser about how to handle it safely. These are "
                "separate from the padlock/secure connection — your site can be perfectly secure "
                "and still not send these.\n\n"
                "Yours isn't sending some of them:\n" + (_list26 or "    • (see the list below)")),
            "see_it": (
                "There's nothing to see on the page itself — these are instructions sent behind the "
                "scenes, not anything a visitor notices. Your web host or developer can check them "
                "in seconds with any free \"security headers\" checking tool."),
            "youll_see": (
                "A checker will show which of these instructions are being sent and which aren't. "
                "Yours will come back missing the ones listed above."),
            "why": (
                "These are hardening measures — they make certain attacks harder (someone framing "
                "your site to trick your customers, or slipping in code that shouldn't run). None "
                "of them is an emergency, and missing them does not mean your site is unsafe or "
                "that anything has gone wrong. It's housekeeping that's cheap to do and worth "
                "having."),
            "fix": (
                "This is a job for whoever manages your hosting — adding these is a few lines in a "
                "server setting or a one-click option on many hosts. It usually takes minutes and "
                "changes nothing about how your site looks or works."),
            "urls": []})
        continue
    if num == "3" and "has Organization schema but NO LocalBusiness" in body:
        # SCOPE (owner correction 2026-08-05): this check reads SCHEMA ONLY — the hidden JSON-LD
        # block in the page code. It says NOTHING about whether the address is visible in the
        # footer or elsewhere on the site. Copy must not imply "Google can't tell where you are"
        # (the visible address may well be there) — it's specifically the machine-readable label.
        findings.append({
            "severity": "medium",
            "title": "The hidden business label in your page code doesn't say you're a local business",
            "whats_happening": (
                "Besides the words people read on your pages, a website can carry a hidden label in "
                "its code — a short block that spells out your business name, address, phone, hours "
                "and the areas you serve in a fixed format built for search engines to read. Think of "
                "it as a business card written in a language only computers read.\n\n"
                "Your site has one of these labels, but it's the generic \"company\" version. There's "
                "a more specific \"local business\" version that has slots for your address, your "
                "hours, and the areas you serve. Yours doesn't use it, so those slots are empty.\n\n"
                "To be clear: this is only about the hidden label. Your address may well be printed "
                "on your pages where customers can see it — this is a separate thing."),
            "see_it": (
                f"Open your homepage: {base_url}/\n"
                "Right-click anywhere on the page and choose \"View page source\" (or \"View source\").\n"
                "A page of code opens. Press Ctrl+F (Cmd+F on a Mac) and search for: LocalBusiness"),
            "youll_see": (
                "The search finds nothing. If you search for \"Organization\" instead, you'll find "
                "that one — that's the generic label your site does have. The local-business one, "
                "with the address and hours slots, isn't there."),
            "why": (
                "When Google decides which businesses to show for a search like \"near me\", it "
                "leans on this label to confirm what kind of business you are, where you are, and "
                "what you cover. Without it, Google has to guess from the rest of your page. "
                "Guessing works sometimes, but telling it directly works more reliably."),
            "fix": (
                "Ask whoever manages your website to add a LocalBusiness label with your full "
                "business name, street address, phone number, hours, and the areas you serve. On "
                "most website platforms this is a setting or a small plugin, not custom code. If "
                "there's a type that matches your trade specifically, use that one."),
            "urls": []})
        continue
    # check 6 wrong-host case (a SaaS site owner, 2026-08-02: "2 URLs" chip vs "1 URL" body, plural
    # "links and images" for one link, source page listed as if it were a second problem)
    if num == "6" and "WRONG host variant" in body:
        _pairs6 = [(_t, _s) for _t, _s in re.findall(r"(https?://\S+?)\s+referenced from:\s+(https?://\S+)", body)
                   if _tx_d4.is_real_page(_s.rstrip(":,)"))]   # sources must be real pages (2026-10-14)
        _n6 = len(_pairs6) or int((re.search(r"(\d+) URL\(s\) referenced", body) or ["", "1"])[1])
        _www6 = _pairs6 and _pairs6[0][0].startswith("https://www.")
        title = (f"{_n6} link{'s' if _n6 != 1 else ''} use{'s' if _n6 == 1 else ''} the "
                 f"{'www.' if _www6 else 'wrong'} version of your own address")
        # which of these wrong-host targets actually 404 after the redirect? (a finance site, 2026-08-04:
        # 3 of 5 www links terminate in a 404, so "the page still loads" was false for them)
        _dead6 = set()
        try:
            _cr6 = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
            _apex6 = re.sub(r"^www\.", "", (_cr6.get("site") or ""))
            _st6 = {}
            for _p in (_cr6.get("pages") or []):
                _u = re.sub(r"^https?://(www\.)?", "", (_p.get("url") or "")).rstrip("/")
                _st6[_u] = _p.get("status")
            for _t6, _ in _pairs6:
                _key = re.sub(r"^https?://(www\.)?", "", _t6.rstrip(":,)")).rstrip("/")
                if _st6.get(_key) in (404, 410) or _st6.get(_key.split("?")[0]) in (404, 410):
                    _dead6.add(_t6)
        except Exception:
            pass
        _any_load = len(_pairs6) > len(_dead6)
        _all_dead = _pairs6 and len(_dead6) == len(_pairs6)
        detail = (
            f"Your site lives at one official address ({'without' if _www6 else 'with'} the "
            f"\"www.\"). {_n6} internal link{'s are' if _n6 != 1 else ' is'} written with the "
            "other version. "
            + ("Every one of these forwards to an address that does not exist — a wrong-host link "
               "AND a dead end. " if _all_dead else
               ("Most still load, but each takes a redirect detour first; some also forward to a "
                "page that no longer exists (marked below). " if _dead6 else
                "The page still loads, but every click and every crawl takes a redirect detour "
                "first. "))
            + "Update each link to the correct official address; the page carrying it is listed below.")
        urls = []
        for _t6, _s6 in _pairs6[:20]:
            _mark = "  — wrong host AND the target 404s (dead end)" if _t6 in _dead6 else "  — written with the wrong host (extra redirect)"
            urls.append(f"{_t6.rstrip(':,)')}{_mark}")
            urls.append(f"      ↳ found on: {_s6.rstrip(':,)')}")
        findings.append({"severity": "high" if _n6 > 3 else "medium",
                         "title": title, "detail": detail, "urls": urls})
        continue
    # check 24 probe-failed case: an operator caution, never the zero-products client title
    if num == "24" and ("probe failed" in body or "could not read" in body) and "🚩" not in body:
        continue
    # check 9 fake-area-code case: a fabricated phone is its own critical, not "inconsistent numbers"
    if num == "9" and "INVALID / unassigned area code" in body:
        _bad_num = re.search(r"not a real line: ([^.]+)\.", body)
        title = "The phone number on your site is not a working number"
        detail = ("We checked the phone number shown across your site. Its area code is not a real, "
                  "assigned US/Canada area code" + (f" ({_bad_num.group(1).strip()})" if _bad_num else "")
                  + " — so it is almost certainly a placeholder left in from a template, not a line "
                  "that rings. On a live site that takes customers or payments, every call to it is "
                  "lost. Replace it with your real business number everywhere it appears.")
        findings.append({"severity": "critical", "title": title, "detail": detail, "urls": []})
        continue
    # check 9 no-phone case
    if num == "9" and "NO PHONE NUMBER anywhere" in body and "🚩" not in body:
        if "CONTENT: CSR" in read("render.log"):
            continue        # client-rendered site: the phone exists only after scripts run
        title = "No phone number anywhere on the site"
        sev = "medium"
    # check 11 broken-localization case
    if num == "11" and "BROKEN LOCALIZATION WIDGET" in body:
        title = "A broken currency picker has become most of your page's visible text"
        sev = "critical"
    # check 11 fabricated-faces case: the demo-pages title must not cover fake testimonials
    if num == "11" and "FABRICATED TESTIMONIAL FACES" in body:
        title = "The customer photos in your testimonials are computer-generated stock faces"
        sev = "critical"
    # check 9 multi-location case: "inconsistent" overstates a deliberate per-branch setup
    if num == "9" and "MULTI-LOCATION setup" in body and "🚩" not in body:
        _tels9 = re.findall(r"(\+?[\d][\d\-\.\(\) ]{8,}\d)", body)[:8]
        findings.append({
            "severity": "low",
            "title": "Your site shows several different phone numbers — worth double-checking",
            "whats_happening": (
                "We found more than one phone number across your site. That's completely normal if "
                "you have more than one location or a separate number for a particular service — "
                "it's only a problem if one of them is wrong or out of date.\n\n"
                "We're not flagging this as broken. We're flagging it so you can confirm each "
                "number is the one you want on that page."),
            "see_it": (
                "Look at the numbers listed below. For each one, ask: is this still a number we "
                "answer, and is it on the right page?"),
            "youll_see": (
                "Most likely they're all correct and each belongs to its own location. Occasionally "
                "an old number survives on one page after a move or a line change, and nobody "
                "notices because that page doesn't get looked at often."),
            "why": (
                "Google cross-checks the phone number on your website against your Google Business "
                "Profile and other listings. When they agree, it's a small confirmation you're the "
                "same business. A stale number on a forgotten page doesn't just miss calls — it's "
                "one more version of you out there that doesn't match."),
            "fix": (
                "Confirm each number below. Fix or remove any that are out of date. If you have "
                "several locations, make sure each location's page shows that location's number "
                "and matches its Google Business Profile."),
            "urls": _tels9})
        continue
    # check 16 NAP-blank case: agreement passed but the homepage entity anchor is EMPTY
    if num == "21" and "IDENTICAL content" in body and "CONTENT: CSR" in read("render.log"):
        continue            # the "identical" pages are the app shell — covered by the CSR finding
    # CSR guard for checks 8 (duplicate title) and 22 (slug/title mismatch): both read the crawl
    # `title` field, which on a client-rendered SPA is the SAME pre-hydration shell title on every
    # page (a JS-rendered site 2026-08-04: 20 routes, 1 shell title; render.log TITLE DIFFERS shows the real
    # per-route titles are helmet-injected). So under CSR these two ALWAYS misfire — check 8 sees
    # "N pages share the same title", check 22 sees every slug "mismatching" the shell title. The raw
    # crawl cannot see the rendered titles, so suppress both (the empty-shells CSR critical already
    # tells the client their content — titles included — only exists after JavaScript runs).
    if num in ("8", "22") and _csr_blind:
        continue
    # the blank-title lines of check 8 are reported by the dedicated missing-title finding above
    # (DE-04); keep check 8 for duplicate/placeholder titles only, never the same pages twice.
    if num == "8" and _MISSING_TITLE_EMITTED:
        body = re.sub(r"[^\n]*BLANK <title>[^\n]*\n?(?:[ \t]+https?://\S+[ \t]*\n?)*", "", body)
        if "🚩" not in body and "⚠️" not in body:
            continue
    # 2026-10-13 (docs/06 fix 1, objections: Japan "not measured, not silent"): under CSR the raw
    # crawl also cannot see rendered schema (3), page count (4), forms (23) or images (25). These
    # produced 8 false findings on two client sites (a tree-service site and a tax firm). Skip them AND record them as not measured.
    if num in ("3", "4", "23", "25") and _csr_blind:
        NOT_MEASURED_CSR.append(num); continue
    if num == "16" and re.search(r"NO (phone|address|phone or address)", body) and "🚩" not in body:
        # SCOPE (owner correction 2026-08-05): check 16 reads the homepage SCHEMA NODE's telephone/
        # address properties — NOT whether the number is visible on the page. Check 9 does the
        # visible one. Never phrase this as "you don't have your phone number on the homepage": the
        # number may be in the header in 40px type while the hidden label omits it, and a client who
        # sees it there will conclude the whole report is wrong.
        _m16 = re.search(r"NO (phone or address|phone|address)", body)
        _w16 = _m16.group(1) if _m16 else "phone or address"
        title = f"The hidden business label on your homepage is missing your {_w16}"
        sev = "medium"
        findings.append({
            "severity": sev,
            "title": title,
            "whats_happening": (
                "Your homepage carries a hidden block of code that lists your business details for "
                "search engines — name, phone, address, hours. It's separate from the details your "
                f"customers see on the page.\n\nIn that hidden block, your {_w16} is missing.\n\n"
                f"Important: this does not mean your {_w16} is missing from the page itself. It may "
                "be right there in your header where everyone can see it. This is only about the "
                "machine-readable copy."),
            "see_it": (
                f"Open your homepage: {base_url}/\n"
                "Right-click and choose \"View page source\", then press Ctrl+F (Cmd+F on a Mac) "
                "and search for: telephone"),
            "youll_see": (
                "Inside the block of business details, the line for your "
                f"{_w16} either isn't there or is empty — while the other details (like your "
                "business name) are filled in."),
            "why": (
                "Search engines use this hidden block to confirm your business details match what "
                "they see elsewhere — your Google Business Profile, directories, other listings. "
                "When a detail is missing here, there's one less confirmation that the business in "
                "the listing and the business on the website are the same one."),
            "fix": (
                f"Have whoever manages your site add your {_w16} to that block. On most platforms "
                "this is a field in your SEO or business-info settings, not hand-written code — "
                "and it should exactly match what's on your Google Business Profile."),
            "urls": []})
        continue
    # check 9 editorial-mention case: the "inconsistent" title overstates a verify-this warning
    if num == "9" and "usually editorial content" in body and "🚩" not in body:
        title = "One page shows a phone number that is not yours — confirm it is quoted content"
        sev = "low"
    # UNKNOWN verdicts are operator matters, never client findings (RECURRING: one site NXDOMAIN,
    # another 429 — both shipped "site loads on two addresses" over an UNKNOWN body)
    if "UNKNOWN — a bot challenge" in body or "UNKNOWN —" in body and "cannot be judged" in body:
        continue
    # check 12 warning-only ("NOT duplication — one variant correctly redirects") must not wear
    # the "whole site loads twice" title it immediately retracts (an insurance site, 2026-08-01). The wrong-host-links
    # fact is check 6's finding; skip the duplicate accordion when 6 fired, else retitle.
    if num == "12" and "NOT duplication" in body:
        if any(f.get("title", "").startswith("Links and images use the wrong version") for f in findings):
            continue
        title = "Some links use the non-canonical version of your address (extra redirect per click)"
        sev = "low"
    # A section with ONLY ⚠️ (no 🚩) is a warning, not a critical — and must not inherit the canned
    # critical title. LIVE BUG (2026-07-31): check 12 warned "links reference the non-canonical host"
    # on a site whose apex correctly 301s, and shipped as CRITICAL "the whole site is served twice".
    if "🚩" not in body:
        sev = "medium"        # warning-only: soften severity, never replace a human-written title
        if num not in SEV and num not in CONTEXT and heading:
            title = _r2sub_warn(heading).title()
    # Check 16 title must match what FIRED (2026-07-31, a law-firm site): "two conflicting
    # descriptions" is wrong when the only issue is self-authored review markup on a single node.
    if num == "16":
        _multi = ("conflicting business NAMES" in body or "business-entity schema nodes" in body
                  or "coordinates" in body)
        _ncf = re.search(r"(\d+) STRUCTURED-DATA CONFLICT", body)
        _n16 = int(_ncf.group(1)) if _ncf else 0
        # 3-issue single-node case (a client site, 2026-08-01): routing by "not multi-node" sent a corrupted
        # name + invalid rating + 5 self-reviews into the rating-ONLY branch, dropping two issues
        # from the client copy. The single-issue branch applies ONLY when the rating is the sole flag.
        if not _multi and _n16 > 1:
            title = "The hidden business details your website gives Google contain errors"
            detail = ("Websites carry a hidden 'business card' in their code (structured data) that "
                      "tells Google the business's exact name and details. We read yours and found "
                      f"{_n16} problems with it: " + _client_copy(body))
            findings.append({"severity": "high", "title": title, "detail": detail, "urls": []})
            continue
    # Check 13 robots-404-only case (a cleaning site: canned junk-sitemap title over a robots body)
    if num == "13" and "NO robots.txt" in body and body.count("⚠️") == 1 and "🚩" not in body:
        findings.append({
            "severity": "medium",
            "title": "Your site has no robots.txt file",
            "detail": (
                "robots.txt is the small file crawlers read first — it declares your sitemap and "
                "sets crawl rules. Yours returns an error, so crawlers get no sitemap declaration "
                "at all. Most platforms can generate one; publish it with a Sitemap: line."),
            "urls": []})
        continue
    # Check 13 no-sitemap case: the canned "contains pages that should not be there" title must
    # never ship over a "no sitemap URLs found" body (a remodeling site, 2026-08-02)
    if num == "13" and "no sitemap URLs found" in body:
        findings.append({
            "severity": "medium",
            "title": "Your site has no sitemap at all",
            "detail": (
                "A sitemap is the list of pages you hand to Google so nothing gets missed. We "
                "looked for yours at the standard address and in your robots.txt file — there "
                "isn't one. Google can still find pages by following links, but a sitemap makes "
                "indexing faster and more complete, and it is the reference point several of the "
                "other checks in this report rely on. Most website platforms can generate one "
                "automatically — turn that on and reference it from robots.txt."),
            "urls": []})
        continue
    # Check 13 dedupe (2026-07-31): its "N sitemap URL(s) not reached by the crawl" warning is the
    # SAME fact the hack-scan orphan finding reports — with better copy, the full URL list and a
    # live-probe verdict. Shipping both = two accordions about one problem. Strip the duplicate
    # line; if nothing else in the section warns, skip the finding entirely.
    _horph = re.search(r"in sitemap but NOT reached by crawl[^:]*:\s*(\d+)", hack)
    if num == "13" and _horph and int(_horph.group(1)) >= 10:
        body = re.sub(r"⚠️\s*\d+ sitemap URL\(s\) not reached by the crawl[^\n]*\n(\s+\S+\n)*", "", body)
        if "⚠️" not in body and "🚩" not in body:
            continue
    # Check 13 presentation (2026-07-31): the log body leads with an informational dump
    # ("sitemap URLs: 395 · hosts in sitemap: {'…': 395}") — a Python dict shipped to a client.
    # When the section's content is the redirecting-sitemap-URLs flag, rebuild it as labelled
    # old → new pairs and drop the raw counters.
    if num == "13" and "REDIRECT instead of loading" in body:
        _pairs = re.findall(r"(https?://\S+)\s*\n\s*-> now forwards to:\s*(https?://\S+)", body)
        _npair = re.search(r"(\d+) sitemap URL\(s\) REDIRECT", body)
        _np = int(_npair.group(1)) if _npair else len(_pairs)
        # WALK-THROUGH copy (owner directive 2026-08-05). The mail analogy is the owner's and it
        # carries the whole lesson: a forwarded letter still arrives, so why not just write the
        # right address the first time? Reader is assumed to know nothing about URLs or redirects.
        _first_old = _pairs[0][0] if _pairs else ""
        _first_new = _pairs[0][1] if _pairs else ""
        title = (f"Your sitemap sends Google to {_np} old address"
                 + ("es" if _np != 1 else "") + " that forward somewhere else")
        _walk = {
            "whats_happening": (
                "Think of a web address (a URL) like a street address — it's where you send people who "
                "want to reach a page. Your sitemap is the list of addresses your website hands to "
                "Google so Google knows what you have.\n\n"
                f"{_np} of the addresses on that list are OLD ones. They still work, but only because "
                "your site quietly forwards anyone who goes there over to a different address — like "
                "mail forwarding from a house you moved out of."),
            "see_it": (
                f"Open your sitemap: {base_url}/sitemap.xml\n"
                + (f"Find this address in the list: {_first_old}\n"
                   "Copy it, paste it into your browser, and press enter. Watch the address bar at the "
                   "top of the browser as the page loads." if _first_old else "")),
            "youll_see": (
                "The page opens fine — but the address in the bar is not the one you typed. It changed to:\n"
                + (f"    {_first_new}\n\n" if _first_new else "\n")
                + "That change is called a redirect. Your site took the old address and sent the visitor "
                  "to the new one. Every old address in the list below does this."),
            "why": (
                "Nothing is broken for your visitors — they land on a real page. The problem is that "
                "you're handing Google a list of old addresses and making it follow a forward every "
                "single time.\n\n"
                "Redirects aren't bad on their own. Having a few is completely normal and expected. "
                "What you don't want is to keep pointing Google at addresses you already know are "
                "old, when you could just give it the correct one. Why forward the mail when you can "
                "write the right address the first time?"),
            "fix": (
                "Update your sitemap so it lists the NEW address for each page instead of the old one. "
                "Most website platforms rebuild the sitemap for you when you save settings — your web "
                "person will know where.\n\n"
                "While you're there, click each new address to make sure it's actually the right page. "
                "Sometimes a system guesses wrong and forwards one page's address to a completely "
                "different page."),
        }
        urls = []
        for _o, _nw in _pairs:      # ALL of them — completeness rule, no truncation
            urls.append(f"{_o}  — old address in your sitemap")
            urls.append(f"      ↳ forwards to: {_nw}   ← put THIS one in the sitemap instead")
        findings.append({"severity": sev, "title": title, "urls": urls, **_walk})
        continue
    # Check 15 special case (2026-07-31, owner: "this accordion doesn't make sense"). The log body
    # is counts + delimiters; the reader's real questions are WHICH link, forwards to WHERE, and
    # what to write instead. The crawl JSON has the answer (url -> final_url) — use it, and split
    # genuinely-renamed pages (path changed) from www/trailing-slash variants (same page, minor).
    if num == "15":
        try:
            from urllib.parse import urlparse as _up
            _cr = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
            _pgs = _cr.get("pages", _cr) if isinstance(_cr, dict) else _cr
            _nh = lambda h: re.sub(r"^www\.", "", h or "")
            _counts = {u: int(n) for n, u in re.findall(r"(\d+)x\s+(https?://\S+)", body)}
            _renamed, _slashonly = [], 0
            for _p in _pgs:
                if not _p.get("redirected"):
                    continue
                _u, _f = _p.get("url", ""), _p.get("final_url", "")
                _a, _b = _up(_u), _up(_f)
                if _nh(_a.hostname) != _nh(_b.hostname):
                    continue                       # forwards off-site — not this finding
                if _a.path.rstrip("/") == _b.path.rstrip("/"):
                    _slashonly += 1                # same page, www/slash variant only
                else:
                    _renamed.append((_u, _f, _counts.get(_u, 0)))
            if _renamed:
                _renamed.sort(key=lambda x: -x[2])
                _nr = len(_renamed)
                title = "Links on your pages use old addresses for pages that have moved"
                detail = (
                    f"{_nr} pages on the site were moved or renamed at some point — the old address still "
                    "works, but only because the server forwards each visit to the new address. "
                    "The links written into your pages were never updated to match: they still use "
                    "the old addresses. Visitors don't notice because the forward is instant, but "
                    "every click takes an extra step, and search engines prefer internal links that "
                    "point straight at a page's current address. "
                    "The fix is to update each link below to the address the page lives at now."
                    + (f" (A further {_slashonly} linked addresses differ only by 'www' or a "
                       "trailing slash — the same template fix applies, lower priority.)"
                       if _slashonly else ""))
                _big = [(_u, _c) for _u, _f, _c in _renamed if _c > 100]
                _tmpl = ""
                if _big:
                    _tmpl = ("\n\nOne of these is doing most of the damage on its own: "
                             f"{_big[0][0]} is linked {_big[0][1]} times because it sits in your "
                             "menu or footer, which appears on every page. Fixing that ONE link in "
                             f"the template fixes {_big[0][1]} of them at once.")
                _ex_old = _renamed[0][0] if _renamed else ""
                _ex_new = _renamed[0][1] if _renamed else ""
                urls = []
                for _u, _f, _c in _renamed[:20]:
                    urls.append(f"{_u}  — old address"
                                + (f", linked {_c} times" if _c else ""))
                    urls.append(f"      ↳ now lives at: {_f}   ← link to THIS one instead")
                findings.append({
                    "severity": sev,
                    "title": f"Your own links point at {_nr} old addresses instead of the current ones",
                    "whats_happening": (
                        "Think of a web address like a street address. At some point "
                        f"{_nr} of your pages moved to a new address. The old ones still work, but "
                        "only because your site forwards anyone who goes there over to the new "
                        "one — like mail forwarding.\n\n"
                        "The problem is that your own links were never updated. Your menus, your "
                        "buttons, your text links still send people to the old addresses, so every "
                        "one of those clicks gets forwarded." + _tmpl),
                    "see_it": (
                        (f"Open any page with one of these links and click through to: {_ex_old}\n"
                         "Watch the address bar at the top as it loads.\n\n" if _ex_old else "")
                        + "The full list is below — old address first, current address underneath."),
                    "youll_see": (
                        "The page loads, but the address in the bar changes to a different one"
                        + (f":\n    {_ex_new}" if _ex_new else "")
                        + "\n\nThat's the forward happening. The visitor never notices, but it "
                          "happened."),
                    "why": (
                        "Forwards aren't bad in themselves — a few are completely normal, and you "
                        "need them so old links from other sites still work.\n\n"
                        "What you don't want is to keep pointing at addresses you already know are "
                        "old. Every time Google follows one of your own links to an old address, "
                        "you're feeding it the outdated version instead of the real one. You know "
                        "the correct address — so give it the correct address rather than making it "
                        "take the extra hop. Why forward the mail when you can just write the right "
                        "address?"),
                    "fix": (
                        "Update each link below to point straight at the current address instead of "
                        "the old one.\n\n"
                        "Start with anything in your menu or footer — those appear on every page, so "
                        "one fix there clears hundreds of hops.\n\n"
                        "This applies to links pointing at other people's sites too: if you know a "
                        "page has moved and you can edit the link, point it at where it lives now."),
                    "urls": urls})
                continue
        except Exception:
            pass                                   # fall through to the generic wording
    # Check 18 (2026-08-01): the log body is operator copy — signature regexes, the follow-up
    # script's command line, "compliance theater". Rebuild the client detail from the parsed
    # facts, CONDITIONAL on what fired: a no-banner site (🚩) is told to remove/gate the trackers;
    # a banner site (⚠️) is told the banner alone is unverified — NEVER the other way round.
    if num == "18":
        # LIVE layer (pixel.log from orchestrator stage 6c) is authoritative over the static read
        # (an insurance site, 2026-08-01: static saw "1 tracker + GTM can load more"; live saw 2 GA4
        # properties + 2 Clarity session-replay tags, 14 requests pre-consent — Clarity loaded via
        # GTM, structurally invisible to any static scan).
        _plog = read("pixel.log")
        _plive = re.findall(r"^\s*- (.+?) — \d+ request", _plog, re.M)
        _plive_n = re.search(r"(\d+) TRACKING SERVICE", _plog)
        # \s* not \s+: the section body is .strip()ed above, so the FIRST tracker line has no
        # leading whitespace — \s+ silently dropped it (Meta Pixel vanished from the finding).
        _tl = re.findall(r"^\s*- (.+?) — matched", body, re.M)
        _tn = len(_tl)
        if _tn == 0:
            # static layer saw nothing — but the LIVE layer is authoritative: a GTM/GT- container
            # loads trackers no static read can see (a garage-door site, 2026-08-01: GA4 fired pre-consent,
            # stage 6c logged 🚩, and the client report said nothing)
            if _plog and "🚩" in _plog and _plive:
                findings.append({
                    "severity": "critical",
                    "title": CONTEXT["18"][0],
                    "detail": (CONTEXT["18"][1] + " The page's own code shows no tracker — but "
                               "watching a real browser load the site told a different story: "
                               + (f"{_plive_n.group(1)} tracking service(s) " if _plive_n else "tracking services ")
                               + f"({', '.join(dict.fromkeys(_plive))}) sent visitor data before any "
                               "consent was possible, loaded through a tag-manager container. "
                               "The fix: gate the tag-manager (or the individual tags inside it) "
                               "behind a consent tool that blocks them until the visitor agrees."),
                    "urls": []})
            continue
        _tlist = ", ".join(_tl)
        _gtm = "Google Tag Manager container also present" in body
        _cm = re.search(r"consent manager \(([^)]+)\)", body)
        if "🚩" in body:
            detail = (
                CONTEXT["18"][1] + f" What we found: {_tn} tracker"
                + ("s" if _tn != 1 else "") + f" installed in your pages ({_tlist})"
                + (" plus a Google Tag Manager container that can load more" if _gtm else "")
                + " — and no consent tool anywhere on the site to hold them back. They send data "
                "about every visitor the moment a page loads, before anyone can agree or decline. "
                "The fix is two steps: remove any tracker tied to a campaign you no longer run "
                "(leftover pixels are the most common target of these letters), and put the ones "
                "you still use behind a consent tool that actually blocks them until the visitor "
                "agrees."
                + ("\n\nOn top of that, the site has NO privacy policy at all — the standard "
                   "addresses return errors and no page links to one. Collecting visitor data "
                   "with zero disclosure is its own legal exposure, separate from the consent "
                   "question: publish a privacy policy first, it is the cheapest fix on this list."
                   if "NO PRIVACY POLICY" in body else ""))
            if _plog and (_plive or _plive_n):
                detail += (" We also verified this live in a real browser: "
                           + (f"{_plive_n.group(1)} tracking services were contacted " if _plive_n else "tracking requests left the page ")
                           + "before any consent was possible"
                           + (f" ({', '.join(dict.fromkeys(_plive))})" if _plive else "") + ".")
            findings.append({"severity": sev, "title": CONTEXT["18"][0],
                             "detail": detail, "urls": []})
        else:
            detail = (
                CONTEXT["18"][1] + f" What we found: {_tn} tracker"
                + ("s" if _tn != 1 else "") + f" ({_tlist}) and a consent tool "
                + (f"({_cm.group(1)}) " if _cm else "")
                + "are both installed. That is the right setup — but a consent banner on its own "
                "is not proof of protection: on many sites the trackers are wired outside the "
                "consent tool's control and fire anyway, before the visitor answers. The way to "
                "confirm is a live test that loads the site fresh and records which tracking "
                "companies are contacted before the banner is touched — we run that as a "
                "follow-up. Nothing needs changing unless that test shows a tracker firing "
                "early.")
            findings.append({"severity": "medium",
                             "title": "Your site has trackers and a consent banner — confirm the "
                                      "banner actually blocks them",
                             "detail": detail, "urls": []})
        continue
    urls = [re.sub(r"[)\]:,.\"']+$", "", u) for u in re.findall(r"https?://\S+", body)]
    detail = _client_copy(body)
    if _ctx:
        detail = f"{_ctx[1]} What we found: {detail}"
    findings.append({"severity": sev, "title": f"{title}", "detail": detail,
                     "urls": list(dict.fromkeys(urls))[:(60 if num in ("14",) else 25)]})

# --- trace-errors: broken internal links WITH their source pages ----------------------------
# RE-VERIFY before shipping (2026-07-31): a crawl-time 5xx is often a transient hiccup. Four
# "Broken internal link (504)" CRITICALS shipped on a chiropractic site whose URLs were
# 301/200/200/200 minutes later. Re-fetch each non-200 once; drop it if it now resolves.
import urllib.request as _u
import os as _os_tx, sys as _sys_tx
_sys_tx.path.insert(0, _os_tx.path.dirname(_os_tx.path.abspath(__file__)))
import crawl_transport as _tx          # honours CRAWL_PROXY + CRAWL_UA (§45, a garage-door site 2026-09-22)
_UAH = dict(_tx.HDRS)
# challenge markers in a re-check answer (Cloudflare, SiteGround, Sucuri, generic WAF pages)
_CHALLENGE_RE = re.compile(r"Just a moment|Checking your browser|cf-chl|challenge-platform|Attention Required|"
                           r"sgcaptcha|sg-captcha|Robot Challenge|captcha|Verifying you are human|Access Denied",
                           re.I)
def _recheck_full(u):
    """-> (status or None, challenged: bool). Never raises."""
    try:
        r = _u.urlopen(_u.Request(u, headers=_UAH), timeout=15)
        st, hdrs, body = r.status, r.headers, r.read(65536)
    except Exception as e:
        st, hdrs, body = getattr(e, "code", None), getattr(e, "headers", None), b""
        try:
            body = e.read(65536)
        except Exception:
            pass
    _ch = False
    try:
        _ch = bool(hdrs and (hdrs.get("cf-mitigated") or "").lower() == "challenge")
    except Exception:
        pass
    if not _ch and st in (202, 403, 429, 503):
        _ch = bool(_CHALLENGE_RE.search((body or b"").decode("utf-8", "replace")))
    return st, _ch
def _recheck(u):
    return _recheck_full(u)[0]

# DE-05 (a client site behind Cloudflare, 2026-10-14): 46 Cloudflare bot-check 403s + 3 real 404s shipped as "49 links on
# your site lead to pages that aren't there". RFC 9110 §15.5.5/§15.5.11: only 404 and 410 say a page
# is not there. 401/403/406/429, "no answer" and challenge pages mean the site refused our checker:
# we could not check. Persistent 5xx (crawl AND re-check) stays a real error; visitors get it too.
_NOT_FOUND = {"404", "410"}
_REFUSED = {"401", "403", "406", "429", "000", "0"}
_could_not = []          # (code, url, why)
_broken = []
for blk in re.finditer(r"^\[(\d{1,3})\]\s+(\S+)(.*?)(?=^\[|\Z)", trace, re.M | re.S):
    code, url, rest = blk.group(1), blk.group(2), blk.group(3)
    # sources must be real pages (2026-10-14: admin-ajax like-button URLs were counted as pages)
    srcs = [x for x in re.findall(r"linked from:\s*(\S+)", rest) if x.startswith("http") and _tx.is_real_page(x)]
    _now, _chal = _recheck_full(url)
    if _now and 200 <= _now < 400 and _now != 202:
        print(f"   (re-verified OK, dropped: [{code}] -> {_now}  {url})")
        continue
    if code in _REFUSED or _chal:
        _could_not.append((code, url, "challenge" if _chal else "refused"))
        continue
    if code.startswith("5") and _now in (404, 410):
        code = str(_now)                     # the server error became a real not-found
    elif code.startswith("5") and not (_now and _now >= 500):
        # the server error did not repeat; the re-check got another non-answer: not measured
        _could_not.append((code, url, "no answer on re-check" if _now is None else f"re-check {_now}"))
        continue
    if code not in _NOT_FOUND and not code.startswith("5"):
        _could_not.append((code, url, "other status"))
        continue
    # /cdn-cgi/l/email-protection is CLOUDFLARE's email-obfuscation endpoint, not a link the
    # owner wrote: with JS it becomes a working mailto:. It shipped as TWO separate HIGH broken-
    # link findings on a roofing site (2026-07-31). Note it once, at LOW, in plain words.
    if "/cdn-cgi/l/email-protection" in url:
        if not any("email address is hidden" in f.get("title", "") for f in findings):
            findings.append({
                "severity": "low",
                "title": "Your email address is hidden behind a Cloudflare protection link",
                "detail": (
                    "Your site runs behind Cloudflare, which disguises email addresses to stop "
                    "spam bots harvesting them. The link works normally for visitors (their "
                    "browser turns it back into your real email address), but crawlers that do "
                    "not run JavaScript see a special Cloudflare address instead. No action "
                    "needed — this is Cloudflare working as designed, listed here only so the "
                    "entry does not look like a broken link in other tools."),
                "urls": []})
        continue
    _broken.append((code, url, srcs))
    continue
    findings.append({
        "severity": "high" if code.startswith("4") else "critical",
        "title": f"Broken internal link ({code})",
        "detail": f"{url} returns {code}. Linked from {len(srcs)} page(s) — fix or remove the link "
                  f"at the source (a 404 reached from your own navigation wastes crawl budget and "
                  f"loses visitors).",
        "urls": [url] + srcs[:8]})

if _broken:
    _urls = []
    # DISGUISED 404s (a client site, 2026-08-02, owner: "this doesn't return as 404 for me"): a custom
    # error page can serve a full normal-looking page (here: a copy of "Our Story") while the
    # status line says 404. The owner clicks, sees content, and distrusts the report — say it.
    _rows_by_url = {}
    _crawled_n = 0
    try:
        _cr9 = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
        for _p9 in (_cr9.get("pages") or []):
            _rows_by_url[(_p9.get("url") or "").rstrip("/")] = _p9
        # §66: the sample size we can honestly claim to have followed links across — real
        # HTML pages only, not image/CSS/PDF rows (§66 asset exclusion).
        _ASSET_F = re.compile(r'\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp4|webm|mov|avi|'
                              r'mp3|wav|ogg|pdf|docx?|xlsx?|pptx?|zip|gz|rar|7z|css|js|mjs|json|'
                              r'xml|txt|woff2?|ttf|eot|otf)(\?|#|$)', re.I)
        _crawled_n = sum(1 for _p9 in (_cr9.get("pages") or [])
                         if not _p9.get("redirected")
                         and not _ASSET_F.search((_p9.get("url") or "").split("#")[0]))
    except Exception:
        pass
    _srcpages = {x for _, _, ss in _broken for x in ss}
    if not _crawled_n:
        _crawled_n = len(_srcpages) or 1
    # "N of M checked links" (DE-05): M = distinct internal link targets that the crawl fetched
    # (each one has a status in the crawl). Falls back to the broken + could-not-check count.
    _checked_m = 0
    try:
        _kn = lambda x: (x or "").split("#")[0].rstrip("/")
        _fetched = {_kn(_p9.get("url")) for _p9 in (_cr9.get("pages") or [])}
        _targets = {_kn(l.get("href") if isinstance(l, dict) else l)
                    for _p9 in (_cr9.get("pages") or []) for l in (_p9.get("internal_links") or [])}
        _checked_m = len(_targets & _fetched)
    except Exception:
        pass
    _checked_m = max(_checked_m, len(_broken))
    for _c, _u, _s in _broken:
        _row9 = _rows_by_url.get(_u.rstrip("/"), {})
        if _c == "404" and (_row9.get("word_count") or 0) > 100:
            _t9 = (_row9.get("title") or "").split("—")[0].strip()
            _urls.append(f"{_u}  — returns error {_c}. IMPORTANT: when you open this in a browser "
                         f"it LOOKS fine — the site shows its error page dressed as a normal page"
                         + (f" (a copy of your \"{_t9}\" page)" if _t9 else "")
                         + ", while telling search engines 'error 404'. Google goes by the error, "
                         "not the view.")
        elif _c.startswith("5"):
            _urls.append(f"{_u}  — returns error {_c} (the server fails to load this page; it did "
                         "the same when we checked again)")
        else:
            _urls.append(f"{_u}  — returns error {_c} (this page does not exist)")
        for _src in _s[:3]:
            _urls.append(f"      ↳ linked from: {_src}")
    _n = len(_broken)
    _all_nf = all(c in _NOT_FOUND for c, _, _ in _broken)
    _any_nf = any(c in _NOT_FOUND for c, _, _ in _broken)
    # WALK-THROUGH copy (owner directive 2026-08-05): name the page the link SITS ON, the page it
    # POINTS TO, and tell the reader exactly what to click and what they'll see.
    _ex_src = (_broken[0][2][0] if _broken and _broken[0][2] else "")
    _ex_dst = _broken[0][1] if _broken else ""
    findings.append({
        "severity": "high" if all(c.startswith("4") for c, _, _ in _broken) else "critical",
        # "N of M checked links" (DE-05). Keep "aren't there" / "broken link" in the title: the
        # builder merges its own broken-link finding by matching those words (AP 2026-09-30).
        "title": (f"{_n} of {_checked_m} checked link{'s' if _checked_m != 1 else ''} on your site "
                  + ("lead to pages that aren't there" if _all_nf and _n > 1 else
                     "leads to a page that isn't there" if _all_nf else
                     "lead to pages that aren't there or fail to load" if _any_nf else
                     "lead to pages that fail to load (broken links)")),
        "whats_happening": (
            "Every page on your site has its own address (a URL) — like a street address for that "
            "page. A link is just text or a button that sends someone to one of those addresses.\n\n"
            # §66: "we clicked every link on your site" is a SITEWIDE claim from a bounded
            # crawl (a pest-control site: 38 URLs). §4 requires stating the sample, not implying
            # exhaustiveness we did not achieve.
            f"We followed the links across the {_crawled_n} page"
            f"{'s' if _crawled_n != 1 else ''} of your site that we crawled. {_n} of them "
            f"{'point' if _n > 1 else 'points'} at an address where there is no longer a page"
            + ("" if _all_nf else ", or where the server fails to load the page") + ". "
            f"{'They sit' if _n > 1 else 'It sits'} on {len(_srcpages)} of your own page"
            f"{'s' if len(_srcpages) != 1 else ''} — those pages themselves are fine; it's the link "
            "inside them that goes nowhere."),
        "see_it": (
            (f"Open this page of yours: {_ex_src}\n" if _ex_src else "")
            + (f"Somewhere on it is a link pointing to: {_ex_dst}\n" if _ex_dst else "")
            + "Click that link and watch what loads.\n\n"
              "The full list is below: each broken address first, then underneath it the page (or "
              "pages) of yours that link to it."),
        "youll_see": (
            "Instead of a real page you'll get an error page — usually the words \"404\" or \"page "
            "not found\". That's a dead end. The address exists in the link, but nothing lives there "
            "anymore."),
        "why": (
            "Two things go wrong. A customer who clicks it hits a dead end and often leaves. And "
            "Google follows your links to learn what your site has — every time it follows one of "
            "these it records an error and spends its time on a page that isn't there instead of on "
            "the pages you actually want found."),
        "fix": (
            "On each page listed below, find that link and either point it at the correct address or "
            "remove it. If the page it used to go to has a new address, use the new one.\n\n"
            "If you're not sure which link it is, the list below shows the exact text of the link as "
            "it's written on the page, so you or your web person can search for it."),
        "urls": _urls[:40]})

# --- url-discovery: injections/staging dirs a crawl can't reach ------------------------------
_NONPAGE_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|heic|mp4|webm|mov|avi|m4v|mp3|wav|ogg|"
                          r"pdf|docx?|xlsx?|pptx?|csv|zip|gz|rar|7z|css|js|mjs|map|json|xml|txt|woff2?|ttf|"
                          r"eot|otf|swf|flv)$", re.I)
_ASSET_DIRS = {"assets", "asset", "images", "image", "img", "imgs", "static", "fonts", "-fonts", "css", "js",
               "scripts", "-stylesheets", "stylesheets", "cached-images", "cached-content", "wp-content",
               "wp-includes", "nitropack_static", "uploads", "media", "cdn-cgi", "_next", "_nuxt"}
def _is_page_url(u):
    """False for archived FILES (by extension or asset folder) and Cloudflare /cdn-cgi/ endpoints."""
    _path = re.sub(r"^(https?://)?[^/]+", "", (u or "").split("#")[0]).split("?")[0]
    if "/cdn-cgi/" in _path + "/":
        return False
    if _NONPAGE_EXT.search(_path):
        return False
    return not any(seg.lower() in _ASSET_DIRS for seg in _path.strip("/").split("/")[:-1] or [])
disc = read("discovery.log")
if "🚨" in disc:
    _lines = [l.strip() for l in disc.splitlines() if "🚨" in l]
    findings.append({"severity": "critical",
                     "title": "Live injected/spam directory found by path probe (not linked, not in any sitemap)",
                     "detail": _client_copy(" ".join(_lines)) +
                               " — found by probing paths directly; a link-crawl and the CMS API "
                               "cannot see these because injected pages are FILES, not CMS content.",
                     "urls": re.findall(r"https?://\S+", disc)[:15]})
elif "🚩" in disc and not _csr_blind:
    # under CSR the curl crawl cannot see JS-rendered nav, so "nothing links to them" is
    # crawler-blindness, not a real orphan set (a JS-rendered site, 2026-08-04)
    _disc_flags = [l.strip() for l in disc.splitlines() if "🚩" in l]
    # DISTINGUISH the two 🚩 kinds (a client site, 2026-08-04): a Wayback-CDX "MOST ARCHIVED URLs ARE NOW
    # DEAD — the previous site's URLs 404" line is NOT an orphan-pages-on-your-site finding. It says
    # the PRIOR site's archived URLs are dead — a lost-equity observation, not crawlable orphans on
    # the current site. Mis-mapping it produced a wrong title, a mid-sentence-truncated body and ZERO
    # URLs. Split: real orphans (URLs the crawl found unlinked) vs. the archived-dead signal.
    _orphan_flags = [l for l in _disc_flags if "ARCHIVED URLs ARE NOW DEAD" not in l
                     and "previous site" not in l]
    _archived_dead = [l for l in _disc_flags if "ARCHIVED URLs ARE NOW DEAD" in l
                      or "previous site" in l]
    # ORPHAN finding only when it actually enumerates unlinked pages on THIS site (real URLs present)
    _orphan_urls = re.findall(r"https?://\S+", "\n".join(_orphan_flags))
    if _orphan_flags and _orphan_urls:
        findings.append({"severity": "high",
                         "title": "Pages exist on your site that nothing links to and no list mentions",
                         "detail": _client_copy(" ".join(_orphan_flags)),
                         "urls": list(dict.fromkeys(_orphan_urls))[:25]})
    # ARCHIVED-DEAD finding: only worth surfacing if there is reclaimable equity — i.e. the prior
    # site's dead URLs are a redirect-mapping opportunity. Frame it honestly as a lost-equity note,
    # NOT as orphan pages, and pull the sample URLs from the discovery log's Wayback section so the
    # finding is never a bodiless/URL-less accordion.
    # ARCHIVED ASSETS ARE NOT DEAD PAGES (JP-05, two client sites 2026-10-14;
    # a third 2026-10-10). url-discovery samples every archived URL, so old images, fonts, CSS,
    # scripts and Cloudflare /cdn-cgi/ endpoints counted as "dead old pages": one site had 12 of
    # 13 dead were /_webedit/cached-images/, the other 8 of 8 were /assets/ + /cdn-cgi/. Judge
    # the dead prefixes on the archived URLs under each one: a prefix whose archived URLs are mostly
    # files (by extension, asset folder, or /cdn-cgi/) is not a page prefix. The finding needs at
    # least 3 dead PAGE URLs in the sample and must list page URLs only.
    if _archived_dead:
        try:
            _dj = json.load(open(os.path.join(rdir, f"{slug}-discovery.json")))
            _wb_all = [u for u, src in (_dj.get("found") or {}).items() if "wayback" in (src or [])]
        except Exception:
            _wb_all = []
        _wb_pages = [u for u in _wb_all if _is_page_url(u)]
        _dead_pref = re.findall(r"/([^/\s]+)/ \((\d+)\)", (re.search(r"dead prefixes in the sample:([^\n]*)", disc) or [None, ""])[1])
        _dead_pages = 0
        for _pf, _cnt in _dead_pref:
            _under = [u for u in _wb_all if re.sub(r"^(https?://)?[^/]+", "", u).lstrip("/").split("/")[0] == _pf]
            _pg_share = (sum(1 for u in _under if _is_page_url(u)) / len(_under)) if _under else \
                        (0.0 if _pf.lower() in _ASSET_DIRS or _pf.lower() == "cdn-cgi" else 1.0)
            if _pg_share >= 0.5:
                _dead_pages += int(_cnt)
        if _dead_pref and _dead_pages < 3:
            print(f"   (archived-dead signal dropped: {_dead_pages} dead PAGE URL(s) in the sample; the rest are "
                  f"old files or /cdn-cgi/ endpoints, not pages)")
            _archived_dead = []
    if _archived_dead:
        # archived PAGE addresses from the discovery JSON first (the log lists only a few, often files)
        _wb_sample = [("https://" + u if not u.startswith("http") else u) for u in _wb_pages][:15] or \
                     [u for u in re.findall(r"https?://\S+", disc) if _is_page_url(u)][:15]
        _wb_dead = re.search(r"(\d+)\s*live\D+(\d+)\s*dead", disc)
        _deadtxt = (f" A liveness sample found {_wb_dead.group(2)} of "
                    f"{int(_wb_dead.group(1))+int(_wb_dead.group(2))} archived addresses now return an "
                    "error." if _wb_dead else "")
        findings.append({"severity": "medium",
                         "title": "Old addresses from a previous version of this site are now dead ends",
                         "detail": ("Web archives still hold addresses from an earlier version of this "
                                    "site. We checked whether those old addresses still work today and "
                                    "most of them return a 'not found' error." + _deadtxt +
                                    " If any of those old pages had earned links or traffic, that value "
                                    "is currently being lost — set up redirects from the old addresses "
                                    "to the closest matching current page to recover it. If the old site "
                                    "had no meaningful links, this is safe to ignore."),
                         "urls": list(dict.fromkeys(_wb_sample))[:15]})

# --- inner pages canonicaling to the HOMEPAGE (a JS-rendered site, 2026-08-04: 5 routes incl the money
# page all declared canonical=/, telling Google they are duplicates of the homepage — check 2
# caught it, it never became a client finding) --------------------------------------------------
try:
    _cr2 = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
    _apex2 = re.sub(r"^www\.", "", (_cr2.get("site") or "")).rstrip("/")
    _home_forms = {f"https://{_apex2}", f"https://www.{_apex2}", f"http://{_apex2}"}
    _home_canon = []
    for _p in (_cr2.get("pages") or []):
        if _p.get("status") != 200 or _p.get("redirected"):
            continue
        _u = (_p.get("url") or "").rstrip("/")
        _c = (_p.get("canonical") or "").rstrip("/")
        if not _c:
            continue
        # exclude WP media attachments (?attachment_id=) — they correctly canonical to a parent/home
        # (a pest-control site, 2026-08-04: 5 attachment URLs flagged as a homepage-canonical defect, false)
        if _c in _home_forms and _u not in _home_forms and 'attachment_id=' not in _u \
           and '?' not in _u.split('#')[0]:
            _home_canon.append(_u)
    if len(_home_canon) >= 2:
        findings.append({
            "severity": "high",
            "title": f"{len(_home_canon)} inner pages tell Google they are copies of your homepage",
            "detail": (
                "Every page carries a hidden tag naming its 'official version' for Google. On "
                f"{len(_home_canon)} of your pages — including real, distinct pages — that tag "
                "points at your HOMEPAGE instead of the page itself. You are telling Google these "
                "pages are duplicates of the home page, which stops them from ranking on their own "
                "for their own topics. Each page's canonical tag should point at ITS OWN address. "
                "This is usually one template/config setting."),
            "urls": [f"{u}  — its canonical points at the homepage, not itself" for u in _home_canon[:20]]})
except Exception:
    pass

# --- CSR shells: pages that are EMPTY until JavaScript runs (unified finding) -----------------
_render_all = read("render.log")
_csr_mode = _csr_blind
_shells = []
_shell_titled = 0   # how many listed shells DO have a title  (§66: copy must match evidence)
_shell_h1 = 0       # how many listed shells DO have an H1
if _csr_mode:
    try:
        _crs = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
        for _p in (_crs.get("pages") or []):
            if _p.get("status") != 200 or _p.get("redirected"):
                continue
            # a page with no <script> cannot be empty "until JavaScript runs" (2026-10-14)
            _inv = _p.get("html_tag_inventory")
            if isinstance(_inv, dict) and not _inv.get("script"):
                continue
            _empty_shell = (not (_p.get("title") or "").strip()
                            and not ((_p.get("headings") or {}).get("h1"))
                            and (_p.get("word_count") or 0) < 400)
            # under a CSR verdict, a curl crawl records low word counts even when the RENDERED
            # page is full — count any thin 200 HTML row as a shell (a client SPA)
            _csr_thin = _csr_blind and (_p.get("word_count") or 0) < 200
            if _empty_shell or _csr_thin:
                _shells.append(_p["url"])
                # §66 (a JS-rendered site, 2026-08-06): the copy below asserted "no title, no headline"
                # about EVERY listed page, but _csr_thin admits pages on word-count alone — and
                # that site's 6 pages all carry titles, meta descriptions, OG tags and 3 JSON-LD
                # blocks each. The HEAD was fully populated; only the BODY was empty. Track what
                # is actually absent so the sentence can match the evidence.
                if (_p.get("title") or "").strip():
                    _shell_titled += 1
                if ((_p.get("headings") or {}).get("h1")):
                    _shell_h1 += 1
    except Exception:
        pass
# never from a single page (2026-10-14): one thin page is not evidence of a client-rendered site
if len(_shells) >= 2:
    # describe only what is genuinely missing across the listed pages
    _n_sh = len(_shells)
    if _shell_titled >= _n_sh and _shell_h1 >= _n_sh:
        _absent = "almost no visible text in the code"
    elif _shell_titled >= _n_sh:
        _absent = "a title but no headline and almost no visible text in the code"
    elif _shell_h1 >= _n_sh:
        _absent = "no title and almost no visible text in the code"
    else:
        _absent = "no title, no headline and almost no text"
    findings.append({
        "severity": "critical",
        "title": f"{len(_shells)} pages are empty shells until JavaScript runs",
        "detail": (
            f"These pages' code contains {_absent} — the visible content "
            "is assembled by JavaScript after loading. Google's first indexing pass reads the "
            "code as-is (its JavaScript pass is deferred and not guaranteed), and most AI "
            "crawlers never run JavaScript at all — so to them, these pages are blank. This one "
            "cause also explains several symptoms measured elsewhere in this report (missing "
            "titles, missing headlines, pages that look identical): fix it once at the source "
            "by making the site generator pre-render these pages' content into the HTML "
            "(server-side rendering / static generation), and the symptoms disappear together."),
        "urls": _shells[:40] + ([f"… and {len(_shells)-40} more"] if len(_shells) > 40 else [])})

# --- render-check: JS-rewritten titles (an insurance site, 2026-08-01: GTM SEO-experiment rewrote the
# money page's <title>; render.log said "TITLE DIFFERS" and no finding ever surfaced it) ---------
render_log = read("render.log")
_tds = re.findall(r"TITLE DIFFERS\s+raw '([^']*)'\s+rendered '([^']*)'", render_log)
# drop poisoned pairs: when render captured a challenge page, the "rendered" title is the captcha
# screen ("Robot Challenge Screen"), not a real JS title rewrite (a client site, 2026-08-04).
_tds = [(r, v) for (r, v) in _tds
        if not re.search(r"Robot Challenge Screen|Just a moment|Checking your browser|Attention Required", v, re.I)]
if _tds and not _render_poisoned:
    _urls_td = []
    for _raw_t, _ren_t in _tds[:6]:
        _urls_td.append(f"raw HTML title:  {_raw_t}")
        _urls_td.append(f"      ↳ after JavaScript runs: {_ren_t}")
    findings.append({
        "severity": "medium",
        "title": "JavaScript rewrites your page titles after the page loads",
        "detail": (
            "A page's title is the headline Google shows in search results. On "
            f"{len(_tds)} checked page(s), the title written in the page's code is REPLACED by "
            "a different one when scripts run — either a tag-manager experiment or the site's "
            "own JavaScript filling in titles after load. Which "
            "version Google indexes depends on whether its rendering pass runs for that page — "
            "so your search headline is effectively random. Pick ONE title per page and put it "
            "in the page's code; retire the script that swaps it."),
        "urls": _urls_td})

# --- filter/facet URL space (filter-url-scan stage 6d) ---------------------------------------
# Consumes the scanner's FILTER-MATH line. Owner reframe (2026-08-02): Google crawls LINKED URLs,
# not synthetic ones the server happens to accept — so the HEADLINE number is the REAL exposed
# count (URLs the site actually links / its filter UI generates / the sitemap declares), carried
# as `linked=N`. A separate `unvalidated=yes` flag means the server serves arbitrary filter values
# (?page=999, ?category=nonsense both 200), which is a GROWTH RISK — any such URL linked or shared
# from elsewhere also resolves — NOT a claim that infinite addresses exist today. The dimension
# breakdown (C/T/L/pages) is a supporting anchor for the linked count, not a "cross-product to
# infinity". FILTER-MATH is authoritative; if absent (older scanner / format still settling) we
# fall back to the legacy FILTER-COMBOS floor so the block never crashes mid-transition. See §21
# (real linked combinations, not invented ?page=999) and §36 (always-on check).
_fscan = read("filterscan.log")
_fverdict = re.search(r"FILTER-VERDICT: (\S+) \(([^)]+)\)", _fscan)
# FILTER-MATH: tolerant parse — every field optional and order-independent. Ints, or the literal
# UNBOUNDED for a dimension whose real max we couldn't bound. `unvalidated=yes/no` is the risk flag.
#   e.g. "FILTER-MATH: linked=184 C=8 T=23 L=8 pages=12 unvalidated=yes"
_fmath = re.search(r"FILTER-MATH:\s*(.+)", _fscan)
_fcombos = re.search(r"FILTER-COMBOS: (\d+) filter dimension\(s\); at least (\d+)", _fscan)
if _fverdict and (_fmath or _fcombos):
    # Dimension labels the client understands, keyed off the scanner's short codes.
    _dim_names = {"C": "categories", "T": "tags", "L": "languages", "S": "sort orders",
                  "page": "pages", "P": "pages", "paged": "pages", "pages": "pages"}
    _fvtoken = _fverdict.group(1)
    _unbounded = _fvtoken == "UNBOUNDED-INDEXABLE"   # self-canonical / indexable param URLs
    _interaction_only = _fvtoken == "INTERACTION-ONLY"  # click-generated, unlinked → Google can't crawl
    _linked = None            # REAL exposed count — the headline number
    _unvalidated = False      # server accepts arbitrary filter values -> growth risk
    _dims = []                # (label, value-or-"UNBOUNDED") supporting-breakdown dimensions
    if _fmath:
        _mtext = _fmath.group(1)
        # The risk flag: unvalidated=yes|true|1 (tolerant to phrasing).
        _muv = re.search(r"unvalidated\s*=\s*(yes|true|1|no|false|0)", _mtext, re.I)
        if _muv:
            _unvalidated = _muv.group(1).lower() in ("yes", "true", "1")
        # Every NAME=VALUE token where VALUE is an int or UNBOUNDED. `linked` is pulled out as the
        # headline; the rest become supporting dimensions.
        for _k, _v in re.findall(r"(\w+)\s*=\s*(\d+|UNBOUNDED)", _mtext, re.I):
            _kl = _k.lower()
            if _kl in ("unvalidated",):
                continue
            if _kl == "linked":
                if _v.upper() != "UNBOUNDED":
                    _linked = int(_v)
                continue
            _label = _dim_names.get(_k, _dim_names.get(_kl, _k))
            _dims.append((_label, "UNBOUNDED" if _v.upper() == "UNBOUNDED" else f"{int(_v):,}"))
    if _linked is None and _fcombos:
        # Legacy fallback: no `linked=` field. FILTER-COMBOS gives only a finite floor; use it as
        # the best available exposed-count estimate rather than crashing.
        _linked = int(_fcombos.group(2))
    _linked = _linked or 0

    # Human-readable supporting breakdown, e.g. "8 categories, 23 tags, 8 languages, 12 pages".
    _breakdown = ", ".join(f"{v} {l}" for l, v in _dims) if _dims else ""

    # Role-labeled probe list: say what each probed URL demonstrates.
    # robots= is the rest of the line ("index, follow"); `\S+` kept only "index,".
    _probes = re.findall(
        r"FILTER-PROBE: (\S+)\s+->\s+(\d+)\s+canonical=(\S+)\s+robots=(.*?)\s*$", _fscan, re.M)
    _url_lines = []
    for _u, _st, _canon, _rob in _probes[:6]:
        if _canon in ("none", "None", "") or _canon is None:
            _role = "no canonical — the filtered view presents itself as a real page"
        elif "?" in _canon:
            _role = "self-canonical — the filtered view claims to be its own indexable page"
        else:
            _role = "canonical points to the clean listing — protected from duplicate-content harm"
        # DE-01 (2026-10-14): `robots=` is the page's META robots tag (filter-url-scan reads
        # <meta name="robots">), NOT robots.txt. Any value, even "index, follow", used to print
        # "blocked in robots.txt" (public pages on four client sites). Say what
        # was measured. robots.txt is not fetched here, so it is never named. The scanner prints
        # the literal "none" when the tag is ABSENT, so "none" reads as "no tag" (a real
        # content="none" tag cannot be told apart in this log; the crawl row has the exact value).
        _rob = (_rob or "").strip()
        if _rob in ("", "none", "None"):
            _role += "; the page has no robots tag, so search engines may list it"
        elif _tx.robots_noindex(_rob):
            _role += "; the page's robots tag says noindex (it asks search engines not to list it)"
        else:
            _role += f"; the page's robots tag allows indexing (it says \"{_rob[:60]}\")"
        _url_lines.append(f"{_u}  — probed filter URL, returned HTTP {_st}; {_role}")
    if not _url_lines:  # scanner emitted math but no per-URL probes (shouldn't happen, be safe)
        _url_lines = re.findall(r"FILTER-PROBE: (\S+)", _fscan)[:6]

    # SEVERITY: HIGH when the real linked set is large AND the server is unvalidated (it can grow);
    # otherwise MEDIUM (modest linked set, or canonical-protected so no duplicate-content harm).
    _large_linked = _linked >= 500
    if _interaction_only:
        _severity = "medium"   # reachable via interaction + unvalidated server = real, if secondary
    elif _large_linked and _unbounded:
        _severity = "high"
    else:
        _severity = "medium"

    _title = f"Your listing links {_linked:,} filtered and paginated addresses"
    if _interaction_only:
        _title = "Your blog filters build extra addresses through JavaScript, not real links"

    _detail = (
        "Your blog/shop listing has filter controls (categories, tags, language, sorting) and page "
        "numbers. Each choice builds its own web address with the filter baked in (like "
        "?category=x&tag=y). We counted the ones your own site actually exposes — the URLs its "
        "filter buttons generate, that it links internally, and that its sitemap declares — and "
        f"that comes to {_linked:,} filtered/paginated addresses"
        + (f" ({_breakdown})" if _breakdown else "") + ". "
        "These are real pages Google can reach and crawl.\n\n")

    if _unvalidated:
        _detail += (
            "There is also a growth risk: your server accepts any filter value we send it — "
            "nonsense category names and page numbers like ?page=999 still return a normal page "
            "(HTTP 200) instead of a 404. That means any such address that gets linked or shared "
            "from somewhere else will also resolve as a live page, so this number can grow beyond "
            "what your own site links today. Have the server reject invalid filter values (return "
            "a 404) so only real filtered views exist.\n\n")

    if _unbounded:
        _detail += (
            "These filtered pages tell Google each is its own page (self-canonical / indexable), "
            "so they compete with your real listing as duplicate content on top of consuming crawl "
            "budget. Fix: block the filter parameters in robots.txt AND set the filtered views to "
            "canonical back to the clean listing.")
    else:
        _detail += (
            "The good news is each filtered page already names the clean listing as the real one "
            "(canonical), which protects you from duplicate-content harm — but Google still crawls "
            "all of them, spending crawl budget on filter combinations instead of your content. "
            "Fix: block the filter parameters in robots.txt so crawlers skip them.")

    # DE-COLLISION (reconciliation 2026-08-02): when the sitemap-orphan finding already owns the
    # same tag/category SLUGS as path archives (self-canonical, its own HIGH fix), and 6d's verdict
    # is CANONICAL-PROTECTED, the two describe the same taxonomy two ways and both leading with the
    # same count reads as one fact twice. Match on SLUG-SET SUBSET + canonical posture (never on the
    # number). Keep the orphan finding primary; reframe 6d as the query-param twin and cross-ref.
    _orphan_fx = next((f for f in findings if "sitemap but nothing" in f.get("title", "")), None)
    if _orphan_fx and not _unbounded:
        _orphan_slugs = set(re.findall(r"/(?:tag|category)/([a-z0-9-]+)", str(_orphan_fx.get("urls", []))))
        _filter_slugs = set(re.findall(r"[?&](?:category|tag)=([a-z0-9-]+)", _fscan))
        if _filter_slugs and _orphan_slugs and len(_filter_slugs & _orphan_slugs) >= max(2, len(_filter_slugs) // 2):
            _title = ("The same blog filters also generate duplicate query-string versions of "
                      "those pages")
            _detail = (
                "Separately from the unlinked tag pages above, your blog's filter buttons build a "
                "SECOND version of those same categories and tags as query-string web addresses "
                f"(like ?category=x&amp;tag=y) — {_linked:,} of them"
                + (f" ({_breakdown})" if _breakdown else "") + ". "
                "Unlike the tag pages above, these already point Google back to your clean blog "
                "listing (canonical), so they will not compete as duplicates — but Google still "
                "crawls them, and your server accepts any value (so shared links to invalid "
                "filters resolve too). Fixing the tag-page linking above is the priority; "
                "additionally block the filter parameters in robots.txt to stop the crawl waste."
                if _unvalidated else
                "Unlike the tag pages above, these already point Google back to your clean blog "
                "listing (canonical), so they will not compete as duplicates — but Google still "
                "crawls them. Block the filter parameters in robots.txt to stop the crawl waste.")
            _severity = "low"
    findings.append({
        "severity": _severity,
        "title": _title,
        "detail": _detail,
        "urls": _url_lines})

# --- render-check: VISUALLY GATED pages (a client site, 2026-08-02: homepage no-JS paint = 32%,
# every project image a data-src blank; the stage even printed "report it as a finding" and the
# sidecar never consumed render.log's VISUAL verdicts — §11 again) --------------------------------
_gated = re.findall(r"VISUAL:\s+GATED\s+\(no-JS paint covers (\d+)%", read("render.log"))
# The VISUAL verdict comes from the SAME render fetch as CONTENT. If that fetch was blocked or
# throttled, the "no-JS paint" it measured is a WAF interstitial, not the site — the finding then
# tells the client "100% of your page is blank" about a page that renders perfectly
# (a restoration-company site, 2026-08-05, Sucuri rate-limit "You are being redirected...").
if _render_poisoned or _real_pages < 3 or not _site_has_scripts:
    _gated = []
if _gated:
    _pct_g = _gated[0]
    # Explain SCRIPT and RENDERING in plain words (owner directive 2026-08-05) — the reader does
    # not know what either word means. Measured signal: pixel coverage of the no-JS paint vs the
    # JS paint (<70% = GATED).
    _is_anim = bool(re.search(r"elementor|entrance animation|animated|aos[- ]", read("render.log"), re.I))
    _is_img = bool(re.search(r"data-src|data-lazy", read("render.log"), re.I))
    findings.append({
        "severity": "medium",
        "title": f"About {100-int(_pct_g)}% of your page is blank until extra code finishes running",
        "whats_happening": (
            "Your pages are built in two parts. First the browser gets the plain page. Then it runs "
            "little programs attached to the page — those are called scripts — and the scripts fill "
            "in or reveal the rest. That second step, where everything gets drawn on screen, is "
            "called rendering.\n\n"
            "We loaded your pages both ways: normally, and again without letting the scripts run. "
            f"Without the scripts, only about {_pct_g}% of your page actually appears. The rest is "
            "blank space until the scripts finish."
            + ("\n\nHere the gap is mostly your images — they're loaded by a script instead of being "
               "in the page directly, so without it they're empty boxes." if _is_img else "")
            + ("\n\nHere the cause is the \"fade in as you scroll\" effects your page builder adds. "
               "Those work by starting everything hidden and revealing it with a script." if _is_anim else "")),
        "see_it": (
            f"Open your homepage: {base_url}/\n"
            "Watch closely the instant it loads — you may see sections pop in a moment after the "
            "rest. That pause is the scripts running.\n\n"
            "(Your web person can prove it properly by turning JavaScript off in the browser "
            "settings and reloading.)"),
        "youll_see": (
            "With scripts blocked, big parts of the page are simply missing — blank areas where "
            "text or images should be. That's the version some visitors and some search tools get."),
        "why": (
            "Google does come back and run the scripts, but that second pass happens later and "
            "isn't guaranteed for every page. Plenty of other tools — including the AI assistants "
            "people now ask for recommendations — never run scripts at all. Whatever only appears "
            "after the scripts run is content those tools simply never see.\n\n"
            "Anything you need found should be in the page from the start, not added afterward."),
        "fix": (
            ("Turn off the entrance/scroll animations on the top sections of the page — in most page "
             "builders that's a single setting per section, and the content then shows immediately."
             if _is_anim else
             "Have your web person make sure the main text and images are in the page itself rather "
             "than being inserted by a script.")
            + (" For images, they should have a normal image address in the page, with the "
               "load-later behaviour added on top rather than replacing it." if _is_img else "")),
        "urls": []})

# --- live pixel layer standalone fallback: static check 18 said ok (section never entered the
# loop above), but the LIVE browser capture saw trackers fire pre-consent (a garage-door site: GA4 via a
# GT- container). Live evidence is authoritative.
_plog_sa = read("pixel.log")
if _plog_sa and "🚩" in _plog_sa and not any("before visitors can consent" in f.get("title", "")
                                             for f in findings):
    _sa_live = re.findall(r"^\s*- (.+?) — \d+ request", _plog_sa, re.M)
    _sa_n = re.search(r"(\d+) TRACKING SERVICE", _plog_sa)
    findings.append({
        "severity": "critical",
        "title": "Advertising trackers on your site run before visitors can consent",
        "detail": (
            "Advertising and analytics pixels send data about each visitor to those companies the "
            "moment a page loads. Your pages' own code shows no tracker — but watching a real "
            "browser load the site told a different story: "
            + (f"{_sa_n.group(1)} tracking service(s) " if _sa_n else "tracking services ")
            + (f"({', '.join(dict.fromkeys(_sa_live))}) " if _sa_live else "")
            + "sent visitor data before any consent was possible, loaded through a tag-manager "
            "container that static analysis cannot see into.\n\n"
            "This is the pattern behind the current wave of privacy demand letters.\n\n"
            "The fix: gate the tag manager (or each tag inside it) behind a consent tool that "
            "blocks it until the visitor agrees."),
        "urls": []})

# --- LIVE subdomains that are not the main site -----------------------------------------------
# TWO sources (a SaaS site, 2026-08-02: blog.example.com — 362 links from the crawled site, ZERO
# CT-log hits, zero crawl rows, zero disclosure — a whole blog silently out of scope):
# (a) discovery's CT-log lines, (b) same-domain subdomains referenced by the CRAWLED PAGES' links.
discovery_log = read("discovery.log")
_subs = re.findall(r"^\s+([a-z0-9.-]+)\s+\d{3}\s+<-- LIVE, not the main site", discovery_log, re.M)
_sub_links = {}
try:
    _crsub = json.load(open(os.path.join(rdir, f"{slug}-full.json")))
    _site_apex = re.sub(r"^www\.", "", (_crsub.get("site") or ""))
    for _p in (_crsub.get("pages") or []):
        for _l in (_p.get("internal_links") or []) + (_p.get("external_links") or []):
            _h = (_l.get("href") if isinstance(_l, dict) else _l) or ""
            _m = re.match(r"https?://([a-z0-9.-]+)", _h.lower())
            if not _m:
                continue
            _hst = _m.group(1)
            if _hst.endswith("." + _site_apex) and _hst not in (_site_apex, "www." + _site_apex):
                _sub_links[_hst] = _sub_links.get(_hst, 0) + 1
    _subs += [h for h, c in _sub_links.items() if c >= 3]
except Exception:
    pass
_subs = [x for x in dict.fromkeys(_subs) if not x.startswith("www.")]
# ENRICH with what the crawl actually saw on each subdomain (2026-08-04): the crawler now crawls
# subdomains of the audited site, so instead of "N additional sites we didn't look at", report each
# subdomain's REAL crawled status — pages seen, whether any are indexable, and whether it duplicates
# the main site. Build a per-subdomain summary from the crawl JSON.
_sub_crawled = {}      # host -> {"pages": n, "indexable": n, "titles": set, "sample": [urls]}
try:
    _main_titles = set()
    for _p in (_crsub.get("pages") or []):
        _hh = (re.match(r"https?://([a-z0-9.-]+)", (_p.get("url") or "").lower()) or [None, ""])[1] \
              if re.match(r"https?://([a-z0-9.-]+)", (_p.get("url") or "").lower()) else ""
        _hh = re.sub(r"^www\.", "", _hh)
        if _hh in (_site_apex, "www." + _site_apex):
            if _p.get("status") == 200 and (_p.get("title") or "").strip():
                _main_titles.add((_p.get("title") or "").strip().lower())
    for _p in (_crsub.get("pages") or []):
        _m2 = re.match(r"https?://([a-z0-9.-]+)", (_p.get("url") or "").lower())
        if not _m2:
            continue
        _hh = _m2.group(1)
        if _hh.endswith("." + _site_apex) and _hh not in (_site_apex, "www." + _site_apex):
            d = _sub_crawled.setdefault(_hh, {"pages": 0, "indexable": 0, "dup": 0, "sample": []})
            d["pages"] += 1
            if _p.get("status") == 200 and _p.get("indexable"):
                d["indexable"] += 1
            if (_p.get("title") or "").strip().lower() in _main_titles:
                d["dup"] += 1
            if len(d["sample"]) < 3 and _p.get("status") == 200:
                d["sample"].append(_p.get("url"))
    # any crawled subdomain host is definitely a real subdomain to report
    for _h in _sub_crawled:
        if _h not in _subs:
            _subs.append(_h)
except Exception:
    pass
if _subs:
    def _sub_line(x):
        c = _sub_crawled.get(x)
        if c and c["pages"]:
            bits = [f"{c['pages']} page" + ("s" if c["pages"] != 1 else "") + " crawled"]
            bits.append(f"{c['indexable']} indexable" if c["indexable"] else "not indexable")
            if c["dup"]:
                bits.append(f"{c['dup']} duplicate your main site")
            return f"https://{x}/  — " + ", ".join(bits)
        lk = _sub_links.get(x)
        return f"https://{x}/" + (f"  — linked {lk} times from your main site, not crawled" if lk else "  — not crawled")
    _any_crawled = any(_sub_crawled.get(x, {}).get("pages") for x in _subs)
    findings.append({
        "severity": "medium",
        "title": f"{len(_subs)} additional live website{'s' if len(_subs) > 1 else ''} found on your domain",
        "detail": (
            "Beyond your main site, these addresses on your own domain answer with live content. "
            "Anything live on your domain can be found and indexed by Google, can duplicate your "
            "main site's content, and — if it is an old test copy or a campaign-landing system — "
            "can leak pages you never meant to publish. Confirm each one is meant to be public and "
            "block indexing on any that are tooling rather than content." + (
                " We crawled the ones on your own domain as part of this audit — the status beside "
                "each shows how many pages we saw, whether they are open to Google, and whether they "
                "repeat your main site's content." if _any_crawled else
                " IMPORTANT SCOPE NOTE: anything on these separate addresses was NOT examined and "
                "needs its own audit.")),
        "urls": [_sub_line(x) for x in _subs[:12]]})

# --- CMS deep-dive (logic tree, stage 6e) -> client findings ---------------------------------
# The platform module names the CAUSE behind symptoms the generic crawl only sees as "orphan/thin
# pages". Copy is written for a NON-TECHNICAL reader: say what exists, why it exists, what it costs,
# and the exact setting to change. No plugin jargon beyond the setting's real on-screen name.
_cms_deep = read("cms-deepdive.log")
if _cms_deep:
    # W1 archive leaks — one finding per leaking archive type, each with its own plain-English cause.
    _ARCH_COPY = {
        "tag": ("Tag pages are being added to Google",
                "Every time a blog post gets a tag, your website automatically builds a separate page "
                "that lists just the posts with that tag. Nobody writes these pages — the website "
                "creates them on its own. Each one is a near-copy of your blog with almost no writing "
                "of its own, and right now they are being handed to Google alongside your real pages, "
                "so they compete with the pages you actually want found."),
        "category": ("Category pages are being added to Google",
                "Every blog category automatically gets its own page listing the posts in it. Nobody "
                "writes these — the website builds them for you. They repeat content that already "
                "lives on your blog, and right now Google is being told to treat them as real pages, "
                "so they compete for attention with the service pages you want people to land on."),
        "author": ("Author pages are being added to Google",
                "Your website automatically creates a page for each person who has ever published a "
                "post, listing everything they wrote. On a business site these just duplicate your "
                "blog under a second address, and they are currently being handed to Google as pages "
                "worth showing in search results."),
        "attachment": ("Every uploaded image has its own blank page in Google",
                "When you upload a photo, your website quietly creates a whole separate page for that "
                "one image — a page with the picture and a title and essentially nothing else. These "
                "are being handed to Google as if they were real pages. Someone searching can land on "
                "a bare image page instead of the service page it belongs to."),
    }
    for _kind, (_t, _body) in _ARCH_COPY.items():
        _label = "media attachment" if _kind == "attachment" else _kind
        _m = re.search(rf"🚩 (\d+) {re.escape(_label)} page\(s\) are INDEXABLE \(([^)]+)\)", _cms_deep)
        if not _m:
            continue
        _n, _src = int(_m.group(1)), _m.group(2)
        # pull the sample URLs printed under that flag.
        # SECTION-BOUND (clean-agent catch, a client site, 2026-08-05): a FIXED 1400-char window
        # ran past the end of its own block into the NEXT archive type's flag, so the category
        # finding listed .../author/<name>/ among its "category pages" — a URL that visibly is not
        # one, in a report whose whole premise is "open these and see for yourself". Cut the segment
        # at the next 🚩/ok line so a block can only ever contribute its OWN sample URLs.
        _seg = _cms_deep[_m.end(): _m.end() + 1400]
        _cut = re.search(r"^\s*(?:🚩|ok —)", _seg, re.M)
        if _cut:
            _seg = _seg[:_cut.start()]
        _urls = re.findall(r"^\s{8}(https?://\S+)", _seg, re.M)[:8]
        # and belt-and-braces: a URL must actually belong to this archive type
        _kindpat = {"tag": "/tag/", "category": "/category/", "author": "/author/"}.get(_kind)
        if _kindpat:
            _urls = [u for u in _urls if _kindpat in u]
        _fixline = ""
        if _kind == "attachment":
            _fixline = (" The fix is one setting in your SEO plugin: turn ON “Redirect attachment URLs "
                        "to the attachment itself”. Every one of these image pages then points at the "
                        "actual picture file instead of standing as its own page.")
        elif _kind == "author":
            _fixline = (" The fix is one setting: turn author pages OFF in your SEO plugin’s archive "
                        "settings. Nothing on your site breaks — the posts stay exactly where they are.")
        else:
            _fixline = (f" The fix is one setting: tell your SEO plugin to keep {_label} pages out of "
                        "search results, unless there is a specific one you deliberately want people "
                        "to find.")
        _see = {
            "attachment": ("Open any address from the list below. It's one of your own photos, but "
                           "sitting on a page all by itself."),
            "author": ("Open any address from the list below — it's a page listing posts by one "
                       "person, with no other content on it."),
        }.get(_kind, f"Open any address from the list below — it's an automatic list of posts "
                     f"filed under one {_label}, with no writing of its own.")
        _saw = {
            "attachment": ("A nearly empty page: the picture, maybe a title, and nothing else. No "
                           "explanation, no way to get to your services, nothing to do next."),
            "author": ("A list of blog posts — the same posts already on your blog, just gathered "
                       "under a name. Nothing here that isn't somewhere else."),
        }.get(_kind, "A list of links to posts you've already published elsewhere on the site. "
                     "Nothing on the page is new writing.")
        findings.append({
            "severity": "medium",
            "title": _t,
            "whats_happening": _body + (
                f"\n\nThere is 1 of them ({_src})." if _n == 1
                else f"\n\nWe counted {_n} of them ({_src})."),
            "see_it": _see,
            "youll_see": _saw,
            "why": ("Google judges a site partly on whether its pages are worth showing. Pages that "
                    "repeat content from elsewhere with nothing added dilute that — they compete "
                    "with your real pages for attention without ever winning a customer.\n\n"
                    "Nobody searches for these. They exist because the website software makes them, "
                    "not because anyone needed them."),
            "fix": _fixline.strip(),
            "urls": _urls})

    # author slug built from an email address
    if "look built from an EMAIL ADDRESS" in _cms_deep:
        _eu = re.findall(r"^\s{8}(https?://\S*/author/\S+)", _cms_deep, re.M)[:5]
        findings.append({
            "severity": "medium",
            "title": "A staff email address is showing up as a public web address",
            "whats_happening": (
                "When someone is set up to write posts on your site, the website builds them a page "
                "and makes the web address for it out of their username.\n\n"
                "One of your accounts was created using an email address as the username. So the "
                "website took that email and turned it into a public page address that anyone can "
                "see and search for."),
            "see_it": "Look at the address below and read it out loud — you'll recognise the email in it.",
            "youll_see": (
                "The email address, with the @ and the dots swapped for dashes, sitting in the "
                "middle of a normal-looking web address."),
            "why": (
                "Programs crawl the web collecting email addresses to sell to spammers, and they "
                "read web addresses too. There's no benefit to having a staff email exposed this "
                "way, and it's not something a visitor ever needed to see."),
            "fix": (
                "In your website's user settings, edit that person's username so it's a name rather "
                "than an email. If they no longer work on the site, move their posts to another "
                "account and delete theirs."),
            "urls": _eu})

    # W3 exposure — usernames handed out publicly
    _mu = re.search(r"🚩 /wp-json/wp/v2/users lists (\d+) user account\(s\) to anyone: ([^\n]+)", _cms_deep)
    if _mu:
        findings.append({
            "severity": "medium",
            "title": "Anyone can look up the usernames people use to log into your site",
            "whats_happening": (
                "Your website has an address that, when opened, lists the account names used "
                "to log in to it. Right now it returns " + _mu.group(1) + " of them: "
                + _mu.group(2).strip() + ".\n\n"
                "You don't have to be logged in to see this. Anyone who knows where to look can."),
            "see_it": f"Open this in your browser: {base_url}/wp-json/wp/v2/users",
            "youll_see": (
                "A page of plain text - not a normal web page - containing the account names. "
                "It looks like technical gibberish, but the names in it are real login names."),
            "why": (
                "Breaking into an account needs two things: the username and the password. "
                "Handing out the usernames means an attacker only has to work on the passwords "
                "- it turns random guessing into a targeted attempt on a name they know is real."),
            "fix": (
                "Most website security plugins can switch this off with one setting, often "
                "called something like \"disable user enumeration\" or \"hide user list\". "
                "Nothing on your public site depends on it, so turning it off changes nothing "
                "visitors see."),
            "urls": []})

    if "xmlrpc.php is enabled" in _cms_deep:
        findings.append({
            "severity": "low",
            "title": "An old back-door feature is still switched on",
            "whats_happening": (
                "Years ago, websites needed a special doorway so phone apps and other sites "
                "could post to them remotely. Almost nothing uses it now, but yours still has "
                "it open.\n\n"
                "It is a single file sitting on your site, answering requests."),
            "see_it": f"Open this in your browser: {base_url}/xmlrpc.php",
            "youll_see": (
                "A short line of text saying it only accepts a certain kind of request - which "
                "confirms it is switched on and listening."),
            "why": (
                "Attackers use this doorway to try huge numbers of passwords very quickly, far "
                "faster than they could through your normal login page. It is one of the most "
                "common ways sites get broken into."),
            "fix": (
                "Ask whoever manages your site to turn it off - most security plugins have a "
                "switch for it. Unless you know something specific is using it, nothing will "
                "change."),
            "urls": []})

    # VERSION DISCLOSURE (rewritten 2026-08-05 alongside the wordpress.py fix).
    # The old matcher read the version out of readme.html prose, which on modern WordPress yields the
    # GPL licence number ("version 2") — we shipped "the exact version ... (2)". The version now comes
    # from the generator meta tag and readme.html is reported separately as a file to delete.
    # §47: keyed to MEANING, and tolerant of both emitters being present or only one.
    _ver = None
    _mv = re.search(r"🚩 the pages announce the exact WordPress version \(([0-9][0-9.]*)\)", _cms_deep)
    if _mv:
        _ver = _mv.group(1)
    _readme_pub = "🚩 /readme.html is public" in _cms_deep
    if _mv or _readme_pub:
        _bits = []
        if _mv:
            _bits.append("every page carries a hidden line naming the exact version of the software "
                         f"your site runs ({_ver})")
        if _readme_pub:
            _bits.append("a leftover release-notes file from the original install is still sitting on "
                         "your site for anyone to open")
        findings.append({
            "severity": "low",
            "title": "Your site announces exactly which software version it runs",
            "whats_happening": (
                "Your website is built on WordPress, and like any software it gets updated over "
                "time.\n\n"
                "Right now " + " and ".join(_bits) + "."),
            "see_it": (
                (f"Open your homepage, right-click anywhere on it and choose \"View page source\" "
                 f"(the wording varies by browser). Press Ctrl-F and search for the word "
                 f"generator.")
                if _mv else
                f"Open this in your browser: {base_url.rstrip('/')}/readme.html"),
            "youll_see": (
                (f"A line of code naming the software and its version number — {_ver}. Visitors "
                 "never see this on the page itself, but it is sent to everyone who loads it.")
                if _mv else
                "A plain page of release notes from the software that runs your site."),
            "why": (
                "When a security hole is found in a piece of software, it gets published along with "
                "the exact versions it affects.\n\n"
                "Announcing your version means someone looking for sites to break into doesn't have "
                "to guess — they can search for every site running a version they already know how "
                "to get into. Removing it doesn't make your site more secure on its own; it just "
                "stops advertising which keys to try. Keeping the software updated is what actually "
                "closes the holes."),
            "fix": (
                "Ask whoever manages your site to remove the version line — most security and SEO "
                "plugins have a single setting for it"
                + (", and to delete the leftover release-notes file, which nothing needs"
                   if _readme_pub else "")
                + ". Neither changes anything a visitor sees.\n\n"
                "More important than either: keep WordPress and its add-ons updated."),
            "urls": []})

    # OUTDATED CORE — a SEPARATE, far more serious finding than announcing the version (§62/§67,
    # a painting-company site, 2026-08-06). cms/wordpress.py correctly printed "WordPress 4.9.22 is
    # SEVERELY out of date" but NOTHING mapped that flag to a client-facing finding, so the single
    # most urgent item on the site never reached the report — while the milder *disclosure* line
    # above it did. Adding a check/flag without adding its finding mapping ships a silent gap.
    _mold = re.search(r"🚩 WordPress ([0-9][0-9.]*) is SEVERELY out of date — the (\d+)\.x line "
                      r"dates\s*\n?\s*to roughly (\d{4}), about (\d+) years ago", _cms_deep)
    if not _mold:
        _mold = re.search(r"🚩 WordPress ([0-9][0-9.]*) is SEVERELY out of date", _cms_deep)
    if _mold:
        _ov = _mold.group(1)
        _oyr = _mold.group(3) if _mold.lastindex and _mold.lastindex >= 3 else None
        _oage = _mold.group(4) if _mold.lastindex and _mold.lastindex >= 4 else None
        _age_txt = (f"about {_oage} years old" if _oage else "many years old")
        findings.append({
            "severity": "critical",
            "title": "The WordPress version running your site is years out of date",
            "whats_happening": (
                "Your site runs on WordPress. Like a phone or a laptop, it receives updates that "
                "close security holes as they are discovered.\n\n"
                f"Your site is running WordPress {_ov}"
                + (f", which came out around {_oyr} — {_age_txt}." if _oyr else f" — {_age_txt}.")
                + "\n\nWhen a hole is found in WordPress, it is published openly, along with the "
                "exact versions it affects. Every hole found since your version came out is public, "
                "documented, and still open on your site."),
            "see_it": (
                "Open your homepage, right-click anywhere on it and choose \"View page source\" "
                "(the wording varies by browser). Press Ctrl-F and search for the word generator."),
            "youll_see": (
                f"A line naming WordPress and the version number {_ov}. You can compare that against "
                "the current version shown at wordpress.org — the gap is the number of years of "
                "security fixes this site has not received."),
            "why": (
                "This is the most urgent item in this report.\n\n"
                "Sites this far behind are not usually broken into by someone who chose them — they "
                "are found by automated programs that scan the whole internet looking for exactly "
                "this. What follows is normally spam pages added to the site, visitors redirected "
                "elsewhere, or the site going down entirely. Recovering afterwards costs far more "
                "than updating does, and Google may flag the site as unsafe in the meantime."),
            "fix": (
                "Ask whoever manages your site to take a full backup first, then update WordPress "
                "along with the theme and add-ons.\n\n"
                "Expect some add-ons to need replacing — after a gap this size that is normal, and "
                "it is not a reason to put the update off. If nobody currently manages the site, "
                "this is the job to hire for before anything else on this list."),
            "urls": []})

    if "plugins load assets on the front end" in _cms_deep:
        _mp = re.search(r"🚩 (\d+) plugins load assets", _cms_deep)
        findings.append({
            "severity": "low",
            "title": "A large number of add-ons load on every page",
            "detail": (f"We counted {_mp.group(1) if _mp else 'many'} separate add-ons loading their own "
                       "code on your pages. Each one adds to what a visitor's phone has to download and "
                       "run before your page appears, which is usually the real reason a site feels slow "
                       "on mobile. Worth reviewing which ones are still doing a job."),
            "urls": []})

# --- hack-scan: only real alarms ------------------------------------------------------------
if "🚨" in hack:
    urls = re.findall(r"https?://\S+", hack)
    findings.append({"severity": "critical", "title": "Possible hacked / injected content indexed",
                     "detail": _client_copy(hack), "urls": urls[:20]})
# --- sitemap orphans: crawl-vs-sitemap needs NO Google data, so this must NOT be gated on the
# 🚩 index-estimate flag (2026-07-31: with SerpAPI quota exhausted there was no 🚩 and the orphan
# finding silently never fired — the check's input is the crawl and the sitemap, nothing else).
if "🚨" not in hack:
    import re as _re2
    _orph = _re2.search(r"in sitemap but NOT reached by crawl[^:]*:\s*(\d+)", hack)
    _n_orph = int(_orph.group(1)) if _orph else 0
    # threshold: a handful of unreached sitemap URLs is normal churn; 10+ is a real pattern
    if 3 <= _n_orph < 10 and not _csr_blind:
        _ourls_s = _re2.findall(r"^\s+ORPHAN:\s+(\S+)", hack, _re2.M)
        # Internal-linking teaching (owner directive 2026-08-05): the sitemap alone is not enough —
        # Google wants multiple reference points, and the more important the page (money page), the
        # more links it should have pointing at it.
        findings.append({
            "severity": "medium",
            "title": f"{_n_orph} of your pages can only be found through your sitemap",
            "whats_happening": (
                "There are two ways Google finds your pages. One is your sitemap — the list your "
                "site hands over. The other, and the one that carries more weight, is by following "
                "links from one of your pages to another.\n\n"
                f"{_n_orph} of your pages are only in the list. Not one other page on your site "
                "links to them. They're real, working pages — they just sit on their own with "
                "nothing pointing at them."),
            "see_it": (
                "Pick any address from the list below and open it — it loads normally.\n\n"
                "Now try to reach that same page starting from your homepage, only by clicking "
                "links, the way a visitor would."),
            "youll_see": (
                "You can't get there. There's no path of links from your homepage to that page. "
                "The only way to land on it is to already know the address."),
            "why": (
                "Being in the sitemap gets a page noticed. Links are what tell Google a page "
                "matters — every link pointing at a page is another vote for it, and Google wants "
                "more than one reference point.\n\n"
                "This matters most for what's called a money page: the page you actually want to "
                "rank, usually your main service. A money page should have several links pointing "
                "at it from around your site. A page with none is telling Google it isn't "
                "important — even if it's the page you care about most."),
            "fix": (
                "Go through the list below and decide what each page is for.\n\n"
                "If it's a page you want customers to find — a service, a location, anything you'd "
                "want to rank — add links to it from related pages. From your services page, from "
                "relevant blog posts, from your main menu if it belongs there. More than one.\n\n"
                "If it's a utility page nobody needs to find (a thank-you page after a form, an "
                "empty archive), that's fine — it just shouldn't be in your sitemap."),
            "urls": _ourls_s[:9]})
    if _n_orph >= 10 and not _csr_blind:
        _ourls = _re2.findall(r"^\s+ORPHAN:\s+(\S+)", hack, _re2.M)
        # SECTION/LOCALE concentration (a multilingual client site, 2026-08-02, owner: "most of those pages
        # are in a different language"): 184 orphans = tag archives x 8 locales with translated
        # slugs — ONE template gap, not 184 page problems. Name the pattern and the single fix.
        _LANGP_O = ('/de/', '/es/', '/fr/', '/nl/', '/hi/', '/pt-br/', '/af/', '/pt/', '/it/',
                    '/ja/', '/zh/', '/pl/', '/ru/', '/ko/')
        _sec_o = {}
        _langed_o = 0
        for _u_o in _ourls:
            _p_o = _re2.sub(r"https?://[^/]+", "", _u_o.lower())
            for _lp_o in _LANGP_O:
                if _p_o.startswith(_lp_o):
                    _langed_o += 1
                    _p_o = _p_o[len(_lp_o) - 1:]
                    break
            _seg_o = "/".join(_p_o.split("/")[:3]) + "/"
            _sec_o[_seg_o] = _sec_o.get(_seg_o, 0) + 1
        _top_o = max(_sec_o.items(), key=lambda x: x[1]) if _sec_o else None
        _concentrated = _top_o and _top_o[1] >= max(3, int(len(_ourls) * 0.7))
        _lang_note = (f" Most are language versions ({_langed_o} of {len(_ourls)} sit under "
                      "your translated sections), so this is one template gap multiplied by "
                      "your languages — not that many separate problems.") if _langed_o >= len(_ourls) * 0.4 else ""
        _sec_note = (f" Nearly all of them are one page type ({_top_o[0]} archive pages): one "
                     "change to that section's template — linking these pages from the posts "
                     "or index that they belong to, or noindexing the whole archive type — "
                     "fixes every language at once.") if _concentrated else ""
        _smp = _re2.search(r"ORPHAN-SAMPLE: live=(\d+) redirect=(\d+) error=(\d+) of (\d+)", hack)
        _verdict = ""
        if _smp:
            _lv, _rd, _er, _tot = map(int, _smp.groups())
            _cause = _re2.search(r"ORPHAN-CAUSE: (\S+) paginates with JavaScript only \(([^)]+)\)", hack)
            if _lv >= _tot * 0.7:
                _verdict = (f" To confirm which, we tested {_tot} of them directly: {_lv} loaded as "
                            "normal, live pages. So these are real pages the site simply never links "
                            "to — an internal-linking gap, not a sitemap error."
                            + ((f" We also found why: the page-to-page navigation on {_cause.group(1)} "
                                "is a JavaScript button rather than a real link, so a crawler that "
                                "lands on the first page has no link to follow to page two and "
                                "beyond — everything past page one is invisible to it."
                                + (( "\n\nThere is a second problem compounding it: the numbered "
                                     "archive pages (page/2/, page/3/, …) do exist, but each one "
                                     "carries a tag telling Google it is merely a copy of page one. "
                                     "So even a crawler that finds them is instructed to discard "
                                     "them — and the article links they carry. Two fixes, both "
                                     "needed: make each page number an ordinary link (a real href), "
                                     "and set the numbered pages to declare themselves (not page "
                                     "one) as their official address."
)
                                   if _re2.search(r"PAGINATION-CANONICAL-TRAP:", hack) else
                                   " The fix is to make the pagination use ordinary links (each "
                                   "page number an <a href>), and to add links to these pages "
                                   "from related content."))
                               if _cause else
                               " Adding links to them from related pages (city hubs, blog posts, "
                               "footers) is the fix; pages no link points to rarely rank, because "
                               "internal links are one of the main signals Google uses to decide "
                               "which pages matter.")) + _sec_note + _lang_note
            elif _rd >= _tot * 0.7:
                _verdict = (f" To confirm which, we tested {_tot} of them directly: {_rd} no longer "
                            "load at their listed address and forward elsewhere. So the sitemap is "
                            "out of date — regenerate it so it lists only current addresses.")
            else:
                _verdict = (f" We tested {_tot} of them directly: {_lv} are live pages nothing links "
                            f"to (add internal links), {_rd} are out-of-date sitemap entries that "
                            f"forward elsewhere (regenerate the sitemap)"
                            + (f", and {_er} failed to load at all (remove them)." if _er else "."))
        findings.append({
            "severity": "high",
            "title": f"{_n_orph} of your pages can only be found through your sitemap",
            "whats_happening": (
                "There are two ways Google finds your pages. One is your sitemap — the list your "
                "site hands over. The other, and the one that carries more weight, is by following "
                "links from one of your pages to another.\n\n"
                f"{_n_orph} of your pages are only in the list. Not one other page on your site "
                "links to them."
                + _verdict),
            "see_it": (
                "Pick any address from the list below and open it — it loads normally.\n\n"
                "Now try to reach that same page starting from your homepage, clicking only links, "
                "the way a visitor would."),
            "youll_see": (
                "You can't get there. There's no path of links from your homepage to that page. The "
                "only way to land on it is to already know the address."),
            "why": (
                "Being in the sitemap gets a page noticed. Links are what tell Google a page "
                "matters — every link pointing at a page is another vote for it, and Google wants "
                "more than one reference point.\n\n"
                "This matters most for a money page: the page you actually want to rank, usually "
                "your main service. A money page should have several links pointing at it from "
                "around your site. A page with none is telling Google it isn't important — even if "
                "it's the page you care about most."),
            "fix": (
                "Go through the list and decide what each page is for.\n\n"
                "If it's a page you want customers to find, add links to it from related pages — "
                "your services page, relevant blog posts, your menu if it belongs there. More than "
                "one.\n\n"
                "If it's a utility page nobody needs to find, that's fine — it just shouldn't be in "
                "your sitemap."),
            "urls": _ourls + ([f"… and {_n_orph - len(_ourls)} more not shown here"]
                               if _n_orph > len(_ourls) else [])})
    # URL bloat from filter/page combinations. SUPERSEDED by stage-6d filter-url-scan (2026-08-03):
    # 6d measures the CRAWLABLE linked set and grades three tiers; this hack-scan FACET-COMBO path
    # counted synthetic combos and shipped a fixed "N+ duplicate" HIGH (a roofing site got BOTH). Only
    # emit this when 6d did NOT produce a filter finding (older runs / no filterscan.log).
    _fc = _re2.search(r"FACET-COMBO: (\d+)\+ filter values x (\d+) pager pages = \d+\+ "
                      r"real combination URLs\. Probed (\S+): loads \(200\), ([^,]+),", hack)
    _has_6d = any("filter" in f.get("title", "").lower() or "only when clicked" in f.get("title", "").lower()
                  for f in findings)
    if _fc and not _has_6d:
        _nv, _np2, _ex, _served = _fc.group(1), _fc.group(2), _fc.group(3), _fc.group(4).strip()
        findings.append({
            "severity": "high",
            "title": f"The blog's filters create {int(_nv) * int(_np2):,}+ duplicate web addresses",
            "detail": (
                "The blog's filter and page controls give every combination of choices its own web "
                f"address. With {_nv} filter values and {_np2} pages, that is over "
                f"{int(_nv) * int(_np2):,} possible addresses — each one loads like a normal page, "
                "and most show a duplicate or empty list rather than anything unique. We tested one "
                f"({_ex}) and it {_served}. "
                "Google collects these addresses as people use the filters, then spends its limited "
                "attention crawling them — at the exact spot where hundreds of real articles have "
                "no links pointing to them at all. Blocking these parameter addresses in robots.txt "
                "(or the filter plugin's own indexing setting) stops the waste."),
            "urls": [f"{_ex}  — example combination: loads, but {_served}"]})
    # DIRECTION-AWARE COPY (a client site, 2026-09-22): hack.log flagged the estimate at ~74x MORE than
    # the indexable URLs we found and this block still shipped "came back noticeably lower". Read
    # the direction from the log; a SURPLUS is an index-bloat lead, a DEFICIT a coverage lead.
    _surplus = bool(re.search(r"is ~[\d.]+x the indexable", hack))
    if "🚩" in hack:
        findings.append({
            "severity": "medium",
            "title": ("Google lists far more pages for this site than we could find"
                      if _surplus else "Google may not have indexed as much of the site as expected"),
            "detail": (
                "We compared the pages we could crawl against Google's own rough estimate of how "
                "many pages of the site it has indexed. "
                + ("The estimate came back much HIGHER — a sign of duplicate or parameter addresses "
                   "(filters, sorting, collection-scoped product paths) being indexed alongside the real pages. "
                   if _surplus else "The estimate came back noticeably lower. ")
                +
                "Google never publishes an exact figure and this number moves between checks, so "
                "treat it as a prompt to look rather than a measurement — the reliable place to "
                "confirm coverage is the Pages report in Google Search Console."),
            "urls": []})

# INDEXED PARAMETER URLS (owner correction, a client site 2026-09-22). filter-url-scan.py now asks
# Google's index directly (site:<host> "?sort" / "?q=" / "?page=" / "?filter" / detected params).
# The markup/robots verdict above says what COULD be crawled; this says what IS indexed. A
# robots-blocked URL that the site links to gets indexed URL-only (Disallow hides the canonical).
_ipm = re.search(r"INDEXED-PARAMS: (\d+) distinct on-site parameter URL\(s\) in Google's index \(kinds (\{[^}]*\}); estimates (\{[^}]*\})\)", _fscan or "")
if _ipm and int(_ipm.group(1)) > 0:
    _ipn = int(_ipm.group(1))
    try:
        _ipk = json.loads(_ipm.group(2)); _ipe = json.loads(_ipm.group(3))
    except Exception:
        _ipk, _ipe = {}, {}
    _ipurls = re.findall(r"^\s+INDEXED: (\S+)", _fscan, re.M)[:8]
    _kind_words = {"q=": "search/type/vendor (?q=) pages", "page=": "paginated (?page=) pages",
                   "sort_by": "sort-order addresses", "filter": "filter addresses", "other": "other parameter addresses"}
    _kinds_txt = ", ".join(f"{v} {_kind_words.get(k, k)}" for k, v in _ipk.items())
    _est_txt = ", ".join(f'"{k}" ≈ {v}' for k, v in _ipe.items() if v)
    findings.append({
        "severity": "high" if _ipn >= 10 else "medium",
        "title": "Google has indexed your filter, sort and search addresses",
        "detail": (f"We checked Google's index directly with site: searches on your domain. Google's own estimates: {_est_txt}. "
                   f"From the first result pages we pulled {_ipn} distinct parameter addresses on your site: {_kinds_txt}. "
                   "Each one is a duplicate or near-empty version of a real page competing with it. "
                   "Where robots.txt blocks a parameter, Google cannot read the canonical on it, so any internal link to it "
                   "(a 'clear filter' chip, a sort link) indexes the bare address. Fix: point those links at the plain listing URL, "
                   "give parameter pages a noindex (or a canonical to the clean listing) and let Google crawl them so it can see it, "
                   "then request removal of the indexed ones in Search Console."),
        "urls": [f"{u}  — in Google's index" for u in _ipurls]})
elif re.search(r"INDEX-CHECK: skipped", _fscan or ""):
    pass   # UNKNOWN — never claim clean

order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
findings.sort(key=lambda f: order.get(f["severity"], 9))

# NAMING (critical): build-crawl-report.js derives its slug from the crawl JSON's `site` field
# (e.g. "examplecleaning.com"), NOT the audit slug ("example-cleaning"). Writing the audit slug
# meant the builder silently ignored the sidecar and reported "5 findings" while 7 sat on disk.
# Write BOTH spellings so either lookup succeeds.
# 2026-10-13 (objections/SYNTHESIS.md item 3): ONE sidecar, named by the RUN slug, which is the
# name build-crawl-report.js reads first. The host-named alias was a decoy: hand edits to it were
# silently ignored (a client run). And the pre-consent tracker finding NEVER goes into the client
# sidecar (owner rule); it is written to an operator-only file instead.
_OPERATOR = re.compile(r"consent|tracker", re.I)
operator_only = [f for f in findings if _OPERATOR.search(f.get("title", ""))]
findings = [f for f in findings if not _OPERATOR.search(f.get("title", ""))]
out = os.path.join(rdir, f"{slug}-findings.json")
# DE-05: linked addresses the site refused to show our checker (401/403/406/429, no answer, bot
# challenge). Not findings and not passes: listed for the "What we could not check" section. The
# builder already lists the same crawl rows as "refused our checker", so this key is for the verifier
# and the gate, and it is NOT copied into not_measured (that would list them twice).
_cnc = []
if _could_not:
    _by = {}
    for _c, _uu, _why in _could_not:
        _by.setdefault(_why, []).append(f"{_uu}  ({'no answer' if _c in ('0', '000') else _c})")
    _whytxt = {"challenge": "the site's security showed our checker a bot check instead of the page",
               "refused": "the site answered Forbidden, Unauthorized or Too Many Requests, or did not answer",
               "other status": "the site answered with a status that does not say whether the page exists"}
    for _why, _ul in _by.items():
        _cnc.append({"what": f"{len(_ul)} linked address{'es' if len(_ul) != 1 else ''} could not be checked",
                     "why": _whytxt.get(_why, f"the server error did not repeat when we checked again ({_why})"),
                     "urls": _ul})
    print(f"   could not check {len(_could_not)} linked address(es) (refused/challenge/no answer) — not reported as broken")
json.dump({"findings": findings,
           "perf_note": "See the Performance accordion; reference only, not a priority fix.",
           "could_not_check": _cnc},
          open(out, "w"), indent=1)
json.dump({"operator_only": operator_only, "not_measured_csr_checks": sorted(set(NOT_MEASURED_CSR)),
           "note": "Never shown to clients (owner rule). Review by hand if useful."},
          open(os.path.join(rdir, f"{slug}-operator-findings.json"), "w"), indent=1)
if operator_only:
    print(f"   operator-only (not in client report): {len(operator_only)} finding(s)")
if NOT_MEASURED_CSR:
    print(f"   NOT MEASURED (client-rendered site; check on rendered pages by hand): checks {', '.join(sorted(set(NOT_MEASURED_CSR)))}")
print(f"{out}: {len(findings)} finding(s) "
      f"({sum(1 for f in findings if f['severity']=='critical')} critical)")
for f in findings[:12]:
    print(f"   [{f['severity']:8}] {f['title']}")
