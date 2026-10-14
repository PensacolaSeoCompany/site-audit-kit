#!/usr/bin/env python3
"""social-scan.py — report REAL social profile links on a site, not framework noise.

A raw substring match for "facebook.com" is NOT proof of a real profile. This filters out:
  - namespace / spec URIs (markup declarations, never links):
      facebook.com/2008/fbml, ogp.me, schema.org, w3.org, gmpg.org/xfn, purl.org, xmlns
  - action / share / pixel endpoints with no account:
      facebook.com/sharer, /dialog, /plugins, /tr?, twitter.com/intent, twitter.com/share
  - a BARE facebook.com/profile.php with NO ?id= (resolves to FB login, not the page)
A real, sameAs-eligible profile MUST carry an account identifier.

Found the hard way on a client site (2026-07-07): the footer exposed only the fb OGP
namespace + a bare profile.php -> reported (correctly) as "no functional social profiles".

Usage: python3 social-scan.py <domain-or-url>   (uses curl -4: IPv4 only, the transport the audits were tuned on)
Exit 0 if >=1 real profile found, 2 if none (a finding).
Read-only: GET/HEAD only (curl GET of /, /contact, /about; no POST, no forms, no login).
"""
import os, sys, re, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl_transport as _tx   # polite layer: bot UA, pacing, robots.txt (owner safety rule 2026-10-14)

NAMESPACE = ('/2008/fbml', 'ogp.me', 'schema.org', 'w3.org', 'gmpg.org', 'purl.org', 'xmlns',
             'facebook.com/tr', '/plugins/', '/sharer', '/dialog/', '/share?', '/intent/', 'api.')
# real profile patterns (must carry an account identifier)
REAL = [
    (re.compile(r'facebook\.com/(?!(sharer|dialog|plugins|tr|2008|profile\.php(?!\?id=))\b)[A-Za-z0-9.\-]{3,}/?(\?|$)'), 'facebook'),
    (re.compile(r'facebook\.com/profile\.php\?id=\d+'), 'facebook'),
    (re.compile(r'instagram\.com/[A-Za-z0-9_.]{2,}/?(\?|$)'), 'instagram'),
    (re.compile(r'(youtube\.com/(@[A-Za-z0-9_.\-]+|channel/[A-Za-z0-9_\-]+|c/[A-Za-z0-9_\-]+))'), 'youtube'),
    (re.compile(r'linkedin\.com/(company|in)/[A-Za-z0-9_\-]{2,}'), 'linkedin'),
    (re.compile(r'tiktok\.com/@[A-Za-z0-9_.]{2,}'), 'tiktok'),
    (re.compile(r'(twitter|x)\.com/(?!(intent|share|home)\b)[A-Za-z0-9_]{2,}/?(\?|$)'), 'x/twitter'),
    (re.compile(r'pinterest\.com/[A-Za-z0-9_\-]{2,}'), 'pinterest'),
]

def fetch(url):
    # 2026-08-06: this stage had no proxy support, so on a site whose WAF rate-limits our
    # server IP it fetched a challenge page and (before the fix below) reported "no profiles".
    # curl needs --proxy; the urllib opener used by the python stages does not apply here.
    # --compressed is mandatory for ANY body we then pattern-match (a gzip body matches nothing).
    cmd = ['curl', '-4', '-sL', '--compressed', '--max-time', '25']
    _px = __import__('os').environ.get('CRAWL_PROXY')
    if _px:
        cmd += ['--proxy', _px]
    cmd += ['-A', _tx.UA]
    try:
        _tx.before_request(url)
    except _tx.RobotsDisallowed:
        print(f"   (skipped: robots.txt disallows {url})", file=sys.stderr)
        return ''
    try:
        return subprocess.run(cmd + [url], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ''

def scan(domain):
    if not domain.startswith('http'):
        domain = 'https://' + domain
    base = domain.rstrip('/')
    html = ''
    for path in ('/', '/contact', '/about'):
        html += fetch(base + path)
    hrefs = re.findall(r'https?://[^\s"\'<>)]+', html)
    real, junk = {}, set()
    for h in hrefs:
        hl = h.lower().rstrip('/')
        if any(n in hl for n in NAMESPACE):
            if any(s in hl for s in ('facebook', 'instagram', 'twitter', 'x.com', 'tiktok', 'youtube', 'linkedin')):
                junk.add(hl)
            continue
        for rx, net in REAL:
            if rx.search(h):
                real.setdefault(net, set()).add(h.split('?')[0] if net != 'facebook' or 'profile.php' not in h else h)
                break
    return real, junk, html, base

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("usage: social-scan.py <domain-or-url>")
    real, junk, _scan_html, base = scan(sys.argv[1])
    _unknown_transport = False   # set when the fetch was blocked — suppresses the canned negative
    print(f"=== social scan: {sys.argv[1]} ===")
    if real:
        print("REAL profiles (sameAs-eligible):")
        # PERSONAL-vs-BUSINESS (a client site, 2026-08-01): a facebook.com/first.last.123 profile was
        # endorsed as the firm's social presence — a personal profile, not a business PAGE. Existence is
        # not fitness; annotate the pattern so the finding says which it is.
        import re as _rps
        def _personal(u):
            return bool(_rps.search(r'facebook\.com/[a-z]+\.[a-z]+\.\d+/?$', u, _rps.I)
                        or _rps.search(r'facebook\.com/profile\.php\?id=\d+$', u)
                        or _rps.search(r'linkedin\.com/in/', u, _rps.I))
        for net, urls in real.items():
            for u in sorted(urls):
                _tag = "  ⚠️ PERSONAL profile (not a business page — weak/wrong sameAs target)" \
                       if _personal(u) else ""
                print(f"  ✓ {net:10} {u}{_tag}")
    else:
        # TRANSPORT VALIDITY (a client site, 2026-08-02): a Vercel challenge page was scanned and
        # converted into a confident "NO social profiles" about a business with an active 1.8K-
        # follower Facebook page. A blocked fetch must say UNKNOWN, never a negative — the same
        # discipline checks 12/18/24 already have.
        _home_html = _scan_html or fetch(base)
        _blockmark = __import__('re').search(
            r'security checkpoint|captcha|challenge|checking your browser|just a moment',
            _home_html[:4000], __import__('re').I) if _home_html else None
        if _blockmark or (_home_html and len(_home_html) < 2000):
            print("  ⚠️ UNKNOWN — the fetch hit a bot challenge/blocked page; social presence "
                  "CANNOT be judged from this transport. Re-run with CRAWL_PROXY set. Never "
                  "report this as 'no profiles'.")
            _unknown_transport = True
        else:
            print("  ✗ NO functional social profiles found")
    if junk:
        print("Framework/namespace noise (NOT profiles — ignore):")
        for j in sorted(junk):
            print(f"  · {j}")
    # a client site, 2026-08-05: this FINDING line printed DIRECTLY BENEATH the
    # UNKNOWN above — the branch set the right verdict and then fell through to the canned
    # negative anyway. Ground truth was three live profiles in sameAs. A downstream gate
    # caught it, but a finding string must never be emitted by a run that just said it
    # cannot measure. UNKNOWN exits 0 with no finding: absence of evidence, not evidence.
    if not real and not _unknown_transport:
        print("\nFINDING: broken/absent social icons + empty sameAs + missing social-proof signal.")
        sys.exit(2)


# ---- ENTITY-MATCH WARNING (appended 2026-07-31) -------------------------------------------------
# LIVE FAILURE (a client site, 2026-07): this scan certified the Facebook page of a DIFFERENT company with
# a similar name and pinterest.com/shopify (the platform's own account) as the business's "REAL
# sameAs-eligible profiles". Both are genuinely in the footer, so presence != ownership. Endorsing a
# wrong-entity profile is worse than reporting none: it launders a real defect into a green check.
# Callers MUST compare each handle against the business name before treating it as sameAs-eligible.
def entity_match_warn(business_name, urls, out=print):
    import re as _re
    _toks = [t for t in _re.split(r'[^a-z0-9]+', (business_name or '').lower()) if len(t) > 3]
    for _u in urls or []:
        _handle = _re.sub(r'[^a-z0-9]+', '', (_u.rstrip('/').split('/')[-1] or '').lower())
        if _toks and not any(t[:5] in _handle for t in _toks):
            out(f"   WARNING: {_u} does not match the business name '{business_name}' — verify "
                f"ownership before using it as sameAs (platform/vendor/other-company account?).")
