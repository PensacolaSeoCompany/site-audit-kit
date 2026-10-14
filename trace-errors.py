#!/usr/bin/env python3
"""trace-errors.py — for every non-200 URL in a full-extract crawl, report WHERE it is linked from.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

A crawl reports status codes; it does NOT, by default, tell you which page contains the bad link.
`full-extract.py` stores `inlinks` as a COUNT (int), not the source list — so to document the origin
of a 404/401/5xx you must scan every page's `internal_links` for references to the target. This does
that, and is a REQUIRED step whenever a crawl surfaces 401/404 errors (standing rule 2026-07-07):
never report a broken/protected status without its originating page(s).

Verdict hints:
  404 with a source  -> BROKEN internal link (fix or remove the link on the source page)
  404 with NO source -> stale external/sitemap ref (lower priority)
  401 with a source  -> usually an INTENTIONAL password-protected page (leave it) — confirm, don't "fix"

Usage: python3 trace-errors.py $KIT_WORK/crawl/<slug>-full.json [--all]   (--all = include 3xx too)
"""
import json, os, sys, urllib.parse, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crawl_transport import is_real_page   # sources must be real pages, not admin-ajax etc. (2026-10-14)

def norm(u):
    p = urllib.parse.urlparse(u)
    return p._replace(query='', fragment='').geturl().rstrip('/')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('crawl')
    ap.add_argument('--all', action='store_true', help='include 3xx redirects too')
    a = ap.parse_args()
    P = json.load(open(a.crawl)).get('pages', [])

    # index every internal link -> list of source pages
    src_of = {}
    for p in P:
        if not is_real_page(p.get('url'), p.get('content_type')):
            continue
        for l in (p.get('internal_links') or []):
            href = l if isinstance(l, str) else l.get('href', '')
            if not href:
                continue
            tgt = norm(urllib.parse.urljoin(p['url'], href))
            src_of.setdefault(tgt, set()).add(p['url'])

    def bad(st):
        if st is None: return False
        if a.all and 300 <= st < 400: return True
        return st in (401, 403, 404, 410) or (500 <= st < 600)

    errs = [p for p in P if bad(p.get('status'))]
    if not errs:
        print("No non-200 errors in this crawl.")
        return
    print(f"=== {len(errs)} non-200 URL(s) — with originating link(s) ===\n")
    for p in sorted(errs, key=lambda x: x.get('status', 0)):
        st = p.get('status')
        srcs = sorted(src_of.get(norm(p['url']), []))
        hint = {404: 'BROKEN LINK (fix/remove on source)', 401: 'likely INTENTIONAL (password-protected) — confirm, do not "fix"',
                403: 'forbidden — check if intentional', 410: 'gone'}.get(st, '')
        print(f"[{st}] {p['url']}   {('— ' + hint) if hint else ''}")
        if srcs:
            for s in srcs:
                print(f"      linked from: {s}")
        else:
            print(f"      linked from: (no internal source — external/sitemap/nav-template ref)")
        print()

if __name__ == '__main__':
    main()
