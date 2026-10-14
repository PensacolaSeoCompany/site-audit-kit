#!/usr/bin/env python3
"""cms/wordpress.py — WordPress deep-dive (audit LOGIC TREE branch, added 2026-08-05).
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

    cms/wordpress.py <crawl.json> [homepage-url]      # dispatched by run-full-audit.sh stage 6e

WHY
---
WordPress auto-generates whole classes of URL that no one authored: an archive page per tag, per
category, per author, and a standalone page per uploaded image. Whether those reach Google is decided
entirely by the SEO plugin's archive settings — not by the site owner's intent. Two 2026-08-04 audits
proved both failure directions:

  a home-builder site — Yoast set PERMISSIVELY: /category/ AND /author/ indexable and in the sitemap,
                      including /author/<first>-<last><domain>-com/ built from a staff EMAIL used
                      as a username. Nobody made that page; WordPress did.
  a pest-control site — tags (315) + categories (43) correctly noindex and NOT in any sitemap, but
                      Yoast's "Redirect attachment URLs to the attachment itself" was OFF, leaving
                      215 image-attachment pages indexable and declared in attachment-sitemap.xml.

The generic crawl finds SYMPTOMS ("orphan pages", "thin pages"). This module names the CAUSE and the
exact setting that fixes it.

WHAT IT CHECKS  (4 groups — owner-selected 2026-08-05)
  W1 ARCHIVES     tag / category / author / attachment: count, index state, sitemap presence
  W2 SEO PLUGIN   which plugin, and its REAL archive config inferred from the sitemaps it publishes
  W3 EXPOSURE     wp-json user enumeration, xmlrpc, readme.html version disclosure, login exposure
  W4 BLOAT        page-builder weight, plugin count, uploads not using WP's responsive sizes

EVIDENCE RULES (house doctrine)
  * Counts come from the crawl JSON and the site's OWN declared sitemaps — never from synthesized
    URLs (AUDIT-PROBLEMS §37). A sitemap count can legitimately EXCEED the crawl count: the pest-control site's
    crawl reached 5 attachment URLs, its attachment-sitemap declared 215. Report the declared number.
  * Every live probe honors CRAWL_PROXY + CRAWL_UA (§43/§45) and treats a challenge page as UNKNOWN,
    never as "absent". A 403 must never become "this site has no sitemap".
"""
import json, os, re, sys
import urllib.request as _ur
from urllib.parse import urlparse

# polite layer (owner safety rule 2026-10-14): bot UA, pacing, robots.txt for every urllib request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import crawl_transport as _tx
UA = _tx.UA
HDRS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml"}
PROXY = os.environ.get("CRAWL_PROXY")
if PROXY:
    try:
        _ur.install_opener(_ur.build_opener(_ur.ProxyHandler({"http": PROXY, "https": PROXY})))
    except Exception:
        pass

WAF = re.compile(r"Robot Challenge Screen|sgcaptcha|Just a moment|Checking your browser|"
                 r"Attention Required|cf-browser-verification", re.I)


def fetch(url, timeout=20):
    """-> (status, body). status None = transport failure. Challenge pages are flagged by caller."""
    try:
        r = _ur.urlopen(_ur.Request(url, headers=HDRS), timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        return getattr(e, "code", None), body


def blocked(status, body):
    """True when the response is a WAF challenge/refusal rather than the real resource."""
    return status in (401, 403, 429) or bool(body and WAF.search(body))


def locs(xml):
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "", re.I)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: cms/wordpress.py <crawl.json> [homepage-url]")
    D = json.load(open(sys.argv[1]))
    P = D.get("pages") or []
    site = (D.get("site") or "").replace("www.", "")
    base = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else f"https://{site}"
    host = urlparse(base).hostname or site
    flags = 0

    print("=== WORDPRESS DEEP-DIVE ===")
    print(f"site: {host}")

    # ---------------------------------------------------------------- W1: auto-generated archives
    print("\nW1. AUTO-GENERATED ARCHIVES (tag / category / author / attachment)")

    # PAGINATOR EXCLUSION (clean-agent catch, a carpet-cleaning run 2026-08-05). An archive's page 2/3
    # is the SAME archive continued, not another auto-generated archive page. Counting them inflated
    # the headline AND made it disagree with the source it cites: that site's category-sitemap
    # declares 2 URLs and author-sitemap 1, but the crawl matched 4 and 3 (2 paginators each), so the
    # report read "4 category page(s) are INDEXABLE (declared in category-sitemap.xml)" — a number
    # that is nowhere in that sitemap. Same exclusion §42 applied to check 21 for the same reason.
    _PAGINATOR = re.compile(r"/(?:page|p)/\d+/?$|[?&]paged=\d+", re.I)

    def bucket(pat):
        return [p for p in P if re.search(pat, (p.get("url") or ""), re.I)
                and not _PAGINATOR.search(p.get("url") or "")]

    groups = {
        "tag":        bucket(r"/tag/"),
        "category":   bucket(r"/category/"),
        "author":     bucket(r"/author/"),
        "attachment": bucket(r"[?&]attachment_id=|/attachment/"),
    }

    # sitemap presence is the AUTHORITATIVE signal: an SEO plugin only publishes an archive sitemap
    # for archive types it has set to indexable. It also gives the DECLARED count, which can far
    # exceed what the crawl reached (the pest-control site: crawl 5, sitemap 215).
    sm_names = {
        "tag":        ["post_tag-sitemap.xml", "tag-sitemap.xml"],
        "category":   ["category-sitemap.xml"],
        "author":     ["author-sitemap.xml"],
        "attachment": ["attachment-sitemap.xml"],
    }
    declared, sm_blocked = {}, False
    for kind, names in sm_names.items():
        for n in names:
            st, body = fetch(f"{base}/{n}")
            if blocked(st, body):
                sm_blocked = True
                continue
            if st == 200 and "<loc" in (body or "").lower():
                declared[kind] = (n, len(locs(body)), locs(body)[:6])
                break

    if sm_blocked and not declared:
        print("   ⚠️ UNKNOWN — archive sitemaps could not be read from this host (bot challenge/403).")
        print("      Re-run with CRAWL_PROXY + CRAWL_UA (AUDIT-PROBLEMS §43/§45) before concluding.")

    LABEL = {"tag": "tag", "category": "category", "author": "author", "attachment": "media attachment"}
    for kind in ("tag", "category", "author", "attachment"):
        ps = groups[kind]
        crawled = len(ps)
        idx = [p for p in ps if p.get("indexable")]
        noidx = [p for p in ps if "noindex" in (p.get("meta_robots") or "").lower()]
        dec = declared.get(kind)
        n_dec = dec[1] if dec else 0

        if not crawled and not dec:
            print(f"   ok — no {LABEL[kind]} pages found")
            continue

        # The DEFECT is: this archive type is reachable AND indexable (in-sitemap or index,follow).
        leaking = bool(dec) or bool(idx)
        headline = max(n_dec, crawled)
        if leaking:
            flags += 1
            src = f"declared in {dec[0]}" if dec else "indexable in the crawl"
            print(f"   🚩 {headline} {LABEL[kind]} page(s) are INDEXABLE ({src}).")
            print(f"      WordPress creates one automatically per {LABEL[kind]}; nobody authored these.")
            if crawled and n_dec and n_dec > crawled:
                print(f"      (crawl reached {crawled}; the site's own sitemap declares {n_dec} — "
                      f"report the declared number)")
            for u in (dec[2] if dec else [p.get('url') for p in idx[:5]])[:5]:
                print(f"        {u}")
            if kind == "attachment":
                print("      CAUSE: the SEO plugin's 'Redirect attachment URLs to the attachment itself'")
                print("      setting is OFF, so every uploaded image keeps its own near-empty page.")
                print("      FIX: turn that setting ON (Yoast: Search Appearance → Media).")
            elif kind == "author":
                print("      CAUSE: author archives are set to 'show in search results'.")
                print("      FIX: turn author archives OFF (Yoast: Search Appearance → Archives) —")
                print("      on a single-author business site they duplicate the blog with no new content.")
            else:
                print(f"      CAUSE: {LABEL[kind]} archives are set to 'show in search results'.")
                print(f"      FIX: set {LABEL[kind]} archives to noindex unless a specific one is a")
                print("      landing page you actively want ranked.")
        else:
            print(f"   ok — {crawled} {LABEL[kind]} page(s) exist but are noindex "
                  f"({len(noidx)} confirmed) and not in any sitemap — correctly handled")

    # AUTHOR-SLUG HYGIENE: WordPress derives the author slug from the user account. An account created
    # with an EMAIL as the username produces /author/name-at-domain-com/ — a published page exposing
    # a staff email pattern (a home-builder site 2026-08-04).
    bad_authors = [p.get("url") for p in groups["author"]
                   if re.search(r"/author/[^/]*(?:%40|-at-|[a-z0-9]+(?:gmail|yahoo|hotmail|outlook)"
                                r"|[a-z]+[a-z0-9-]*\.?com-?/?$)", (p.get("url") or ""), re.I)]
    if bad_authors:
        flags += 1
        print(f"   🚩 {len(bad_authors)} author URL(s) look built from an EMAIL ADDRESS used as the")
        print("      WordPress username — this publishes a staff email pattern as a page address:")
        for u in bad_authors[:4]:
            print(f"        {u}")
        print("      FIX: edit the user's username/nicename, or reassign their posts and delete it.")

    # ---------------------------------------------------------------- W2: SEO plugin + real config
    print("\nW2. SEO PLUGIN CONFIGURATION")
    st_home, home_html = fetch(base + "/")
    home_ok = st_home == 200 and not blocked(st_home, home_html) and len(home_html or "") > 2000
    plugin = ""
    if home_ok:
        if re.search(r"yoast|wpseo", home_html, re.I):
            plugin = "Yoast SEO"
        elif re.search(r"rank ?math", home_html, re.I):
            plugin = "Rank Math"
        elif re.search(r"aioseo|all in one seo", home_html, re.I):
            plugin = "All in One SEO"
        elif re.search(r"autodescription|the seo framework", home_html, re.I):
            plugin = "The SEO Framework"

    st_idx, idx_body = fetch(f"{base}/sitemap_index.xml")
    if st_idx != 200 or not locs(idx_body):
        st_idx, idx_body = fetch(f"{base}/sitemap.xml")
    index_maps = [u.split("/")[-1] for u in locs(idx_body)] if st_idx == 200 else []

    # SECOND SOURCE — the SITEMAP (a salon site 2026-08-05). Homepage markup alone is NOT enough:
    # an optimizer plugin that strips HTML comments removes Yoast's fingerprint entirely, and the
    # module then printed "NO SEO plugin detected … no sitemap control at all" DIRECTLY ABOVE a list
    # of five Yoast-generated sitemaps — a self-contradiction, and the §46 rule-4 inversion (absence
    # of a signal is not evidence of absence). What a plugin DOES is more reliable than what it
    # stamps: read its XSL path / generator comment out of the sitemap we already fetched.
    if not plugin and idx_body:
        if re.search(r"wordpress-seo|Yoast SEO", idx_body, re.I):
            plugin = "Yoast SEO"
        elif re.search(r"seo-by-rank-math|rank ?math", idx_body, re.I):
            plugin = "Rank Math"
        elif re.search(r"all-in-one-seo|aioseo", idx_body, re.I):
            plugin = "All in One SEO"
        elif re.search(r"autodescription|the-seo-framework", idx_body, re.I):
            plugin = "The SEO Framework"
    # third source: plugin asset paths in the homepage HTML (the plugin DIRECTORY name survives even
    # when comments are stripped, because the CSS/JS URLs still point at /wp-content/plugins/<name>/)
    if not plugin and home_ok:
        _pl = " ".join(re.findall(r"/wp-content/plugins/([a-z0-9_-]+)", home_html, re.I))
        if re.search(r"wordpress-seo", _pl, re.I):
            plugin = "Yoast SEO"
        elif re.search(r"seo-by-rank-math", _pl, re.I):
            plugin = "Rank Math"
        elif re.search(r"all-in-one-seo", _pl, re.I):
            plugin = "All in One SEO"

    if plugin:
        print(f"   plugin: {plugin}")
    elif not home_ok:
        print("   ⚠️ UNKNOWN — homepage markup unavailable from this host; plugin not identified.")
    elif index_maps:
        # sitemaps exist but no plugin fingerprint anywhere — SOMETHING is managing them. Never
        # claim "no sitemap control" while listing the sitemaps that prove otherwise.
        print("   note: an SEO plugin is publishing sitemaps but did not identify itself in the "
              "page markup (common when an optimizer strips HTML comments). Archive settings below "
              "are read from the sitemaps it publishes.")
    else:
        flags += 1
        print("   🚩 NO SEO plugin detected and no sitemaps found. WordPress then publishes every")
        print("      auto-generated archive with default settings and there is no sitemap control.")
    if index_maps:
        print(f"   sitemaps published ({len(index_maps)}): {', '.join(index_maps[:12])}")
        print("      (an archive sitemap here = that archive type is set to INDEXABLE)")
    elif blocked(st_idx, idx_body):
        print("   ⚠️ UNKNOWN — sitemap index unreadable from this host (challenge/403), not absent.")
    else:
        print("   note: no sitemap index found at /sitemap_index.xml or /sitemap.xml")

    # ---------------------------------------------------------------- W3: exposure / attack surface
    print("\nW3. EXPOSURE SURFACE")

    # wp-json user enumeration: publishes every author's slug + display name to anyone, and is the
    # standard first step for credential-stuffing a WP login.
    st, body = fetch(f"{base}/wp-json/wp/v2/users")
    if st == 200 and body.strip().startswith("["):
        try:
            users = json.loads(body)
        except Exception:
            users = []
        if users:
            flags += 1
            names = [u.get("slug") or u.get("name") for u in users][:6]
            print(f"   🚩 /wp-json/wp/v2/users lists {len(users)} user account(s) to anyone: "
                  f"{', '.join(str(n) for n in names)}")
            print("      This hands out valid usernames for login attacks. FIX: disable REST user")
            print("      enumeration (most security plugins have a one-click toggle).")
    elif blocked(st, body):
        print("   ok — REST user list is blocked")
    else:
        print("   ok — REST user list not exposed")

    # READ-ONLY (owner safety rule 2026-10-14): the kit never POSTs to a site, so xmlrpc.php is
    # judged from a GET alone. WordPress answers a GET with 405 + "XML-RPC server accepts POST
    # requests only" when the endpoint is live. A throttled or walled answer (a cleaning-company
    # site 2026-08-07 returned GET 405 + a plain 429/error body under its resource cap) is UNKNOWN,
    # never "absent" (§55/§66). The old paced POST of system.listMethods was removed: it is a write
    # request in form, even though that method changes nothing.
    st, body = fetch(f"{base}/xmlrpc.php")
    _xr_live = st in (200, 405) and bool(re.search(
        r"XML-RPC server accepts POST requests only|methodResponse", body or "", re.I))
    _xr_unknown = (not _xr_live) and st in (401, 403, 405, 408, 429, 500, 502, 503, 504, 508, None)
    if _xr_live:
        flags += 1
        print("   🚩 xmlrpc.php is enabled — a legacy endpoint used for brute-force amplification")
        print("      and pingback abuse. FIX: disable it unless something actively needs it.")
    elif _xr_unknown:
        print(f"   ⚠️ UNKNOWN — xmlrpc.php could not be measured from here (HTTP {st}); "
              "re-probe when the host is idle. Not reported as either enabled or disabled.")
    else:
        print("   ok — xmlrpc.php not answering")

    # VERSION SOURCE (clean-agent catch, a carpet-cleaning run 2026-08-05 — §49 defect 4 recorded but
    # never actually fixed). A bare `[Vv]ersion\s+([0-9.]+)` matches the FIRST "version" anywhere in
    # readme.html, which on modern WP is the GPL notice — "released under the terms of the GPL
    # version 2". That run shipped "discloses the WordPress version (2)", a number that is not a
    # WordPress version at all. Modern readme.html does NOT print the core version; the real
    # disclosure is the generator meta tag (here: WordPress 7.0.2). Take the version from the
    # generator, never from the readme prose, and report readme.html only as a file that should go.
    _genver = None
    _m_gen = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']WordPress\s+'
                       r'([0-9][0-9.]*)', home_html or "", re.I)
    if _m_gen:
        _genver = _m_gen.group(1)
    st, body = fetch(f"{base}/readme.html")
    if st == 200 and re.search(r"wordpress", body or "", re.I):
        flags += 1
        print("   🚩 /readme.html is public — the stock WordPress release-notes file is still")
        print("      installed. FIX: delete readme.html (it is regenerated on core updates).")
    else:
        print("   ok — readme.html not exposed")
    if _genver:
        flags += 1
        print(f"   🚩 the pages announce the exact WordPress version ({_genver}) in a generator tag")
        print("      — this tells an attacker precisely which published exploits apply.")
        print("      FIX: ask your web person to remove the generator meta tag (most security and")
        print("      SEO plugins have a one-click option), and keep WordPress updated.")

    # a painting-company run 2026-08-05: we DETECTED version 4.9.22 (2018) and said only "keep
    # WordPress updated" — the version string was treated as a thing to find, never a value to
    # EVALUATE. Same defect class as aggregateRating asserting without measuring. A ~7-year-old
    # core is a far bigger problem than announcing which version it is, so score it separately.
    # Major-release year table (WP ships ~3 majors/yr; branch year is what matters, not exactness).
    # 🔴 a cleaning-company run 2026-08-07 — THIS TABLE WAS ITSELF A §62 DEFECT. Keying on the MAJOR digit
    # alone maps every 6.x to 2022, so WordPress **6.9.5 (a current 2026 release)** was reported as
    # "(6.x, ~2022) ... a supported line but not the newest" and nudged for updating. The check
    # detected the version and then judged the BRANCH, not the RELEASE — the exact defect §62 was
    # written to kill, reproduced inside §62's own fix. WP ships ~3 minors per year, so the minor
    # is what dates a release. Judge on (major, minor) against the newest release we know of, and
    # when the site is at/above that, say so plainly instead of inventing a maintenance nag.
    _WP_BRANCH_YEAR = {4: 2015, 5: 2019, 6: 2022, 7: 2025}
    _WP_LATEST_KNOWN = (6, 9)   # newest (major, minor) as of this build — bump on refresh
    def _wp_year(_mj, _mn):
        """Approximate RELEASE year: branch start + ~1 year per 3 minors."""
        _base = _WP_BRANCH_YEAR.get(_mj)
        return None if _base is None else _base + int((_mn or 0) / 3)
    if _genver:
        _maj = _min = None
        try:
            _parts = str(_genver).split('.')
            _maj = int(_parts[0])
            _min = int(_parts[1]) if len(_parts) > 1 and _parts[1].isdigit() else 0
        except Exception:
            _maj = None
        if _maj and _maj in _WP_BRANCH_YEAR:
            _yr = _wp_year(_maj, _min) or _WP_BRANCH_YEAR[_maj]
            _age = 2026 - _yr
            # CURRENT (or newer than our table) -> state it; never nag a fully-patched site
            if (_maj, _min) >= _WP_LATEST_KNOWN:
                print(f"   ok — WordPress {_genver} is a current release "
                      f"(the {_maj}.{_min} line is the newest we track); core is up to date.")
                _maj = None   # fall through past the stale-version branches below
        if _maj and _maj in _WP_BRANCH_YEAR:
            if _maj <= 5:
                flags += 1
                print(f"   🚩 WordPress {_genver} is SEVERELY out of date — the {_maj}.x line dates")
                print(f"      to roughly {_yr}, about {_age} years ago. Every security hole found in")
                print("      WordPress since then is public, documented, and unpatched on this site.")
                print("      This is the most urgent item in this report: a site this far behind is")
                print("      routinely broken into by automated scanners, not by targeted attackers.")
                print("      FIX: have your web person take a full backup, then update WordPress core,")
                print("      theme and plugins to current. Expect some plugins to need replacing —")
                print("      that is normal for a gap this size and is not a reason to postpone.")
            elif _age >= 2:
                print(f"   note — WordPress {_genver} (released roughly {_yr}, about {_age} years")
                print("      ago) is behind the current release; update on the normal maintenance")
                print("      schedule so security fixes keep arriving.")
            else:
                print(f"   ok — WordPress {_genver} is on a current release line")

    # ---------------------------------------------------------------- W4: bloat / performance shape
    print("\nW4. PAGE-BUILDER / BLOAT SHAPE")
    plug_seen = set()
    builder = set()
    img_uploads = 0
    for p in P[:400]:
        for l in (p.get("internal_links") or [])[:40]:
            href = (l.get("href") if isinstance(l, dict) else l) or ""
            for m in re.finditer(r"/wp-content/plugins/([a-z0-9_-]+)", href, re.I):
                plug_seen.add(m.group(1))
        u = p.get("url") or ""
        if re.search(r"/wp-content/uploads/.+\.(jpe?g|png|gif|webp)$", u, re.I):
            img_uploads += 1
    if home_ok:
        for m in re.finditer(r"/wp-content/plugins/([a-z0-9_-]+)", home_html, re.I):
            plug_seen.add(m.group(1))
        # Builder signatures must be ANCHORED to markup/asset paths. A bare `divi` substring matches
        # "divider" and "individual" and falsely reported Divi on a home-builder site (2026-08-05) — a site
        # with zero `et_pb_` occurrences. Match theme/plugin paths and real class prefixes only.
        for b, pat in [("Elementor", r"elementor-(?:widget|element|section)|/plugins/elementor"),
                       ("WPBakery", r"js_composer|\bvc_row\b"),
                       ("Divi", r"\bet_pb_|/themes/[Dd]ivi/"),
                       ("Beaver Builder", r"\bfl-builder\b"),
                       ("Bricks", r"\bbrxe-"),
                       ("Gutenberg blocks", r"\bwp-block-")]:
            if re.search(pat, home_html, re.I):
                builder.add(b)
        theme = re.search(r"/wp-content/themes/([a-z0-9_-]+)", home_html, re.I)
        print(f"   theme: {theme.group(1) if theme else '(not detected)'}")
    print(f"   page builder(s): {', '.join(sorted(builder)) if builder else '(none detected)'}")
    print(f"   distinct plugins referenced: {len(plug_seen)}"
          + (f" — {', '.join(sorted(plug_seen)[:12])}" if plug_seen else ""))
    if len(plug_seen) >= 20:
        flags += 1
        print(f"   🚩 {len(plug_seen)} plugins load assets on the front end. Each adds CSS/JS to every")
        print("      page. Audit for ones that are inactive-but-loaded or duplicate each other.")
    if "Elementor" in builder and len(plug_seen) >= 10:
        print("   note: Elementor + a large plugin set is the most common cause of the slow mobile")
        print("   paint measured in the performance section — the fix is fewer/lighter plugins, not")
        print("   image tweaks.")
    if img_uploads:
        print(f"   note: {img_uploads} raw /wp-content/uploads/ image URL(s) were crawled as pages —")
        print("   these should be served through the page templates, not linked directly.")

    print(f"\nWORDPRESS DEEP-DIVE COMPLETE — {flags} flag(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
