#!/usr/bin/env python3
"""Tests for the robots.txt matcher in crawl_transport.py and the polite stage-1 crawl in
full-extract.py. Run: python3 tests/test_robots.py

Why (owner safety requirement, 2026-10-14): the stage-1 crawl never read robots.txt, and the
crawler/ package's robots_parser.py uses Python's urllib.robotparser, which gets the five RFC 9309
cases below wrong (it takes the FIRST matching rule, has no "*" or "$", and reads only the first
group for an agent). Each RFC case also runs against urllib.robotparser as a control: the control
must still fail, so the test proves it exercises a case the stdlib gets wrong.

A local http.server (started in setUpModule, stopped in tearDownModule) serves the fixture sites.
No real site is contacted. Crawl output is overwritten under tests/_out/robots/ (nothing deleted)."""
import http.server, json, os, socketserver, subprocess, sys, threading, time, unittest
import urllib.robotparser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(HERE, '_out', 'robots')
VPY = os.path.join(ROOT, '.venv', 'bin', 'python')
PY = VPY if os.path.exists(VPY) else sys.executable
sys.path.insert(0, ROOT)
import crawl_transport as tx  # noqa: E402

TOKEN = 'GrokBot-SiteAudit'


def stdlib_allows(robots_txt, path):
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_txt.splitlines())
    return rp.can_fetch(TOKEN, 'http://example.com' + path)


# (name, robots.txt, path, expected_allowed)
RFC_CASES = [
    ('longest match wins over order',
     'User-agent: *\nDisallow: /shop\nAllow: /shop/public\n', '/shop/public/item', True),
    ('wildcard * in a path',
     'User-agent: *\nDisallow: /*.pdf\n', '/files/guide.pdf', False),
    ('$ anchors the end',
     'User-agent: *\nDisallow: /*.php$\n', '/index.php', False),
    ('allow wins a tie',
     'User-agent: *\nDisallow: /page\nAllow: /page\n', '/page', True),
    ('groups for the same agent merge',
     'User-agent: GrokBot-SiteAudit\nDisallow: /a\n\nUser-agent: *\nDisallow: /\n\n'
     'User-agent: GrokBot-SiteAudit\nDisallow: /b\n', '/b/x', False),
]


class Matcher(unittest.TestCase):
    def test_rfc_cases(self):
        for name, txt, path, want in RFC_CASES:
            with self.subTest(name):
                self.assertEqual(tx.Robots(txt, TOKEN).allowed(path), want, name)

    def test_control_stdlib_gets_them_wrong(self):
        for name, txt, path, want in RFC_CASES:
            with self.subTest(name):
                self.assertNotEqual(stdlib_allows(txt, path), want,
                                    f'control: urllib.robotparser now gets "{name}" right; pick a harder case')

    def test_own_group_beats_star(self):
        r = tx.Robots('User-agent: *\nDisallow: /\n\nUser-agent: grokbot-siteaudit\nDisallow: /private\n', TOKEN)
        self.assertTrue(r.allowed('/public'))
        self.assertFalse(r.allowed('/private/x'))

    def test_star_group_when_no_own_group(self):
        r = tx.Robots('User-agent: OtherBot\nDisallow: /\n\nUser-agent: *\nDisallow: /tmp-area/\n', TOKEN)
        self.assertTrue(r.allowed('/'))
        self.assertFalse(r.allowed('/tmp-area/a'))

    def test_empty_disallow_allows_all(self):
        self.assertTrue(tx.Robots('User-agent: *\nDisallow:\n', TOKEN).allowed('/anything'))

    def test_full_url_and_query(self):
        r = tx.Robots('User-agent: *\nDisallow: /search?\n', TOKEN)
        self.assertFalse(r.allowed('https://example.com/search?q=1'))
        self.assertTrue(r.allowed('https://example.com/search'))

    def test_dollar_does_not_match_longer_paths(self):
        r = tx.Robots('User-agent: *\nDisallow: /*.php$\n', TOKEN)
        self.assertTrue(r.allowed('/index.php?x=1'))
        self.assertTrue(r.allowed('/index.php/extra'))

    def test_percent_encoding_equivalence(self):
        r = tx.Robots('User-agent: *\nDisallow: /caf%C3%A9\n', TOKEN)
        self.assertFalse(r.allowed('/caf%c3%a9/menu'))

    def test_crawl_delay(self):
        self.assertEqual(tx.Robots('User-agent: *\nCrawl-delay: 3\nDisallow:\n', TOKEN).crawl_delay, 3.0)
        self.assertIsNone(tx.Robots('User-agent: *\nDisallow:\n', TOKEN).crawl_delay)
        self.assertEqual(tx.Robots('User-agent: OtherBot\nCrawl-delay: 9\n', TOKEN).crawl_delay, None)

    def test_fetch_status_rules(self):
        ok = tx.robots_from_response(200, 'User-agent: *\nDisallow: /x\n', TOKEN)
        self.assertEqual(ok.state, 'ok')
        self.assertFalse(ok.allowed('/x'))
        missing = tx.robots_from_response(404, '', TOKEN)
        self.assertEqual(missing.state, 'missing')
        self.assertTrue(missing.allowed('/x'))
        for st in (500, 503, None):
            with self.subTest(status=st):
                down = tx.robots_from_response(st, '', TOKEN)
                self.assertEqual(down.state, 'unreachable')
                self.assertFalse(down.allowed('/'))

    def test_default_ua(self):
        self.assertTrue(tx.DEFAULT_UA.startswith('GrokBot-SiteAudit'))
        self.assertIn('https://github.com/PensacolaSeoCompany/site-audit-kit', tx.DEFAULT_UA)


# ------------------------------------------------------------------ crawl integration
ROBOTS = {'body': 'User-agent: *\nDisallow: /private/\nCrawl-delay: 1\n', 'status': 200}
HITS = []      # (time, path, user-agent)


def _page(links):
    a = ''.join(f'<a href="{l}">{l}</a>' for l in links)
    return f'<!doctype html><html><head><title>T</title></head><body><h1>x</h1>{a}</body></html>'


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS.append((time.monotonic(), self.path, self.headers.get('User-Agent', '')))
        if self.path == '/robots.txt':
            b = ROBOTS['body'].encode()
            self.send_response(ROBOTS['status'])
            self.send_header('Content-Type', 'text/plain')
        elif self.path == '/sitemap.xml':
            locs = ''.join(f'<url><loc>http://127.0.0.1:{PORT}{p}</loc></url>' for p in ('/', '/a', '/b', '/private/secret'))
            b = f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/xml')
        elif self.path in ('/', '/a', '/b', '/private/secret'):
            b = _page(['/a', '/b', '/private/secret'] if self.path == '/' else ['/']).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
        else:
            b = b'nope'
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class _TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


SRV = PORT = None


def setUpModule():
    global SRV, PORT
    os.makedirs(OUT, exist_ok=True)
    SRV = _TS(('127.0.0.1', 0), Handler)
    PORT = SRV.server_address[1]
    threading.Thread(target=SRV.serve_forever, daemon=True).start()


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()


def crawl(name, extra_env=None, args=()):
    out = os.path.join(OUT, f'{name}.json')
    if os.path.exists(out):
        open(out, 'w').close()
    env = {k: v for k, v in os.environ.items() if not k.startswith('CRAWL_') and k.lower() not in ('http_proxy', 'https_proxy')}
    env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
    env.update(extra_env or {})
    HITS.clear()
    r = subprocess.run([PY, os.path.join(ROOT, 'full-extract.py'), f'http://127.0.0.1:{PORT}/', '--out', out, *args],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=180)
    d = {}
    if os.path.getsize(out):
        with open(out) as f:
            d = json.load(f)
    return r, d, list(HITS)


class Crawl(unittest.TestCase):
    def setUp(self):
        ROBOTS.update(body='User-agent: *\nDisallow: /private/\nCrawl-delay: 1\n', status=200)

    def _check_polite(self, curl):
        r, d, hits = crawl('polite-curl' if curl else 'polite', {'CRAWL_CURL': '1'} if curl else None)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        paths = [h[1] for h in hits]
        self.assertEqual(paths.count('/robots.txt'), 1, paths)
        self.assertNotIn('/private/secret', paths, 'a disallowed URL was fetched')
        for _, p, ua in hits:
            self.assertTrue(ua.startswith('GrokBot-SiteAudit'), (p, ua))
        page_hits = sorted(h[0] for h in hits)
        gaps = [b - a for a, b in zip(page_hits, page_hits[1:])]
        self.assertTrue(gaps and min(gaps) >= 0.9, f'requests closer than 1 s: {gaps}')
        dis = d.get('robots_disallowed') or []
        self.assertEqual([x['url'] for x in dis], [f'http://127.0.0.1:{PORT}/private/secret'])
        self.assertIsNone(dis[0]['status'])
        self.assertEqual(dis[0]['note'], 'robots-disallowed')
        self.assertEqual(d['summary']['robots_disallowed'], 1)
        self.assertNotIn('/private/secret', json.dumps([p['url'] for p in d['pages']]))
        self.assertEqual(d['summary']['robots_txt'], 'ok')
        self.assertEqual(d['summary']['max_pages'], 500)

    def test_polite_aiohttp(self):
        self._check_polite(False)

    def test_polite_curl(self):
        self._check_polite(True)

    def test_crawl_delay_raises_the_wait(self):
        ROBOTS['body'] = 'User-agent: *\nCrawl-delay: 2\n'
        r, d, hits = crawl('delay2', {'CRAWL_DELAY': '0'})
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        t = sorted(h[0] for h in hits)
        gaps = [b - a for a, b in zip(t, t[1:])]
        self.assertTrue(gaps and min(gaps) >= 1.9, gaps)
        self.assertEqual(d['summary']['crawl_delay_s'], 2.0)

    def test_robots_5xx_crawls_nothing(self):
        ROBOTS['status'] = 503
        r, d, hits = crawl('down', {'CRAWL_DELAY': '0'})
        self.assertEqual([h[1] for h in hits], ['/robots.txt'], 'pages fetched although robots.txt failed')
        self.assertEqual(d['pages'], [])
        self.assertEqual(d['summary']['robots_txt'], 'unreachable')
        self.assertTrue(d.get('partial'))
        self.assertIn('robots.txt', r.stderr)

    def test_robots_404_allows_all(self):
        ROBOTS.update(status=404, body='')
        r, d, hits = crawl('missing', {'CRAWL_DELAY': '0'})
        self.assertIn('/private/secret', [h[1] for h in hits])
        self.assertEqual(d['summary']['robots_txt'], 'missing')
        self.assertEqual(d['summary']['robots_disallowed'], 0)

    def test_page_cap_flagged(self):
        r, d, hits = crawl('cap', {'CRAWL_DELAY': '0', 'CRAWL_MAXP': '2'})
        self.assertEqual(len(d['pages']), 2)
        self.assertTrue(d['summary']['page_cap_hit'])


# ------------------------------------------------------------------ every tool: bot UA + robots
class ToolsUseBotUA(unittest.TestCase):
    """Owner rule 2026-10-14: every request any kit tool sends to the audited site carries the
    GrokBot-SiteAudit user agent, is paced, and skips robots-disallowed URLs. The fixture server
    records the User-Agent of every hit; /private/ is disallowed and must never be requested."""
    W = os.path.join(OUT, 'tools')

    @classmethod
    def setUpClass(cls):
        os.makedirs(cls.W, exist_ok=True)
        cls.crawl = os.path.join(cls.W, 'fx-full.json')
        U = lambda p: f'http://127.0.0.1:{PORT}{p}'
        pages = [{'url': U(p), 'final_url': U(p), 'status': 200, 'redirected': False, 'content_type': 'text/html',
                  'title': 'T', 'meta_robots': '', 'word_count': 300, 'heading_counts': {'h1': 1}, 'indexable': True,
                  'inlinks': 1, 'html_tag_inventory': {'p': 1},
                  'internal_links': [{'href': U(q), 'anchor': q} for q in ('/', '/a', '/b', '/private/secret', '/gone')]}
                 for p in ('/', '/a', '/b')]
        pages.append({'url': U('/gone'), 'status': 404, 'content_type': 'text/html', 'internal_links': []})
        with open(cls.crawl, 'w') as f:
            json.dump({'site': f'127.0.0.1:{PORT}', 'seed': U('/'), 'pages': pages,
                       'robots_disallowed': [{'url': U('/private/secret'), 'status': None, 'note': 'robots-disallowed'}]}, f)

    def setUp(self):
        ROBOTS.update(body='User-agent: *\nDisallow: /private/\n', status=200)

    def run_tool(self, args, name, cwd=None, extra_env=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CRAWL_', 'SERP')) and k.lower() not in ('http_proxy', 'https_proxy')}
        env.update(NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost', CRAWL_DELAY='0',
                   KIT_NO_THIRD_PARTY='1', KIT_WORK=os.path.join(self.W, 'work'),
                   VERIFY_CASE_ROOT=os.path.join(self.W, 'cases'), VERIFY_REPORTS_ROOT=self.W)
        env.update(extra_env or {})
        HITS.clear()
        cmd = (['node'] if args[0].endswith('.js') else [PY]) + [os.path.join(ROOT, args[0])] + list(args[1:])
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd or self.W,
                           stdin=subprocess.DEVNULL, timeout=400)
        return r, list(HITS)

    def check(self, args, name, expect_hits=True, **kw):
        r, hits = self.run_tool(args, name, **kw)
        log = f'{name} rc={r.returncode}\n' + r.stdout[-1500:] + r.stderr[-1500:]
        self.assertNotIn('Traceback', r.stderr, log)
        if expect_hits:
            self.assertTrue(hits, 'the tool made no request to the fixture (test exercises nothing)\n' + log)
        bad = [(p, ua) for _, p, ua in hits if not ua.startswith('GrokBot-SiteAudit')]
        self.assertEqual(bad, [], f'{name}: requests without the bot user agent')
        self.assertNotIn('/private/secret', [p for _, p, _ in hits], f'{name}: fetched a robots-disallowed URL')
        return r, hits

    def U(self, p='/'):
        return f'http://127.0.0.1:{PORT}{p}'

    def test_audit_checks(self):
        self.check(['audit-checks.py', self.crawl], 'audit-checks')

    def test_verify_audit(self):
        self.check(['verify-audit.py', 'fx', '--max-pages', '5', '--delay', '0'], 'verify-audit')

    def test_url_discovery(self):
        self.check(['url-discovery.py', self.U(), self.crawl], 'url-discovery')

    def test_trace_errors(self):
        self.check(['trace-errors.py', self.crawl], 'trace-errors', expect_hits=False)

    def test_hack_scan(self):
        r, _ = self.check(['hack-scan.py', self.crawl, self.U()], 'hack-scan')
        self.assertRegex(r.stdout, r'not checked \(robots\): 1', r.stdout[-2000:])

    def test_social_scan(self):
        self.check(['social-scan.py', self.U()], 'social-scan')

    def test_detect_cms(self):
        self.check(['detect-cms.py', self.crawl, self.U()], 'detect-cms')

    def test_wordpress(self):
        self.check(['cms/wordpress.py', self.crawl, self.U()], 'cms/wordpress')

    def test_checks_to_findings_recheck(self):
        case = os.path.join(self.W, 'c2f-case')
        os.makedirs(case, exist_ok=True)
        with open(os.path.join(case, 'trace.log'), 'w') as f:
            f.write(f"[404] {self.U('/gone')}   — BROKEN LINK\n      linked from: {self.U('/')}\n")
        self.check(['checks-to-findings.py', 'fx', case, self.W], 'checks-to-findings')

    def test_render_check(self):
        self.check(['render-check.py', self.U()], 'render-check')

    def test_pixel_consent(self):
        self.check(['pixel-consent-check.py', self.U()], 'pixel-consent-check')

    def test_content_attribution(self):
        self.check(['content-attribution.py', self.U()], 'content-attribution')

    def test_filter_url_scan(self):
        self.check(['filter-url-scan.py', self.U()], 'filter-url-scan')

    def test_click_test(self):
        self.check(['click-test.js', self.U(), os.path.join(self.W, 'click.json')], 'click-test')

    def test_chrome_tools_skip_a_disallowed_target(self):
        for args in (['pixel-consent-check.py', self.U('/private/secret')],
                     ['content-attribution.py', self.U('/private/secret')],
                     ['render-check.py', self.U('/private/secret')],
                     ['click-test.js', self.U('/private/secret'), os.path.join(self.W, 'click-private.json')]):
            with self.subTest(args[0]):
                r, hits = self.check(args, args[0], expect_hits=False)
                self.assertIn('robots', (r.stdout + r.stderr).lower(), r.stdout[-800:] + r.stderr[-800:])

    def test_urllib_handler_paces(self):
        code = ("import sys,time,urllib.request; sys.path.insert(0, %r); import crawl_transport as tx; "
                "t=time.monotonic(); [urllib.request.urlopen(%r, timeout=5).read() for _ in range(3)]; "
                "print(round(time.monotonic()-t, 2))") % (ROOT, self.U('/a'))
        env = {k: v for k, v in os.environ.items() if not k.startswith('CRAWL_')}
        env.update(NO_PROXY='127.0.0.1', no_proxy='127.0.0.1', CRAWL_DELAY='1')
        HITS.clear()
        r = subprocess.run([PY, '-c', code], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreaterEqual(float(r.stdout.strip()), 1.8, 'three requests at CRAWL_DELAY=1 took under 2 s')


if __name__ == '__main__':
    unittest.main(verbosity=2)
