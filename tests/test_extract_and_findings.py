#!/usr/bin/env python3
"""Tests for full-extract.py, crawl_transport.py, checks-to-findings.py and audit-checks.py.
Run: python3 tests/test_extract_and_findings.py

Each test names the incident or objection it guards (2026-10-14 six-reviewer round:
germany.md DE-01/03/04/05, japan.md JP-05). A local http.server fixture (started in setUpModule,
stopped in tearDownModule) records the raw request line of every hit, so a test can see exactly what
the crawler sent. Nothing here touches a client site. Output files are overwritten in place under
tests/_out/extract-findings/ (nothing is deleted)."""
import http.server, importlib.util, json, os, re, socketserver, subprocess, sys, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(HERE, '_out', 'extract-findings')
VPY = os.path.join(ROOT, '.venv', 'bin', 'python')
PY = VPY if os.path.exists(VPY) else sys.executable
sys.path.insert(0, ROOT)

SEEN = []          # (method, raw request target) for every request the fixture server received
PORT = None
SRV = None

def H(path=''):
    return f'http://127.0.0.1:{PORT}{path}'

def _html(title='Page', robots=None, body='Hello world text.'):
    m = f'<meta name="robots" content="{robots}">' if robots is not None else ''
    t = f'<title>{title}</title>' if title is not None else ''
    return f'<!doctype html><html><head>{t}{m}</head><body><h1>x</h1><p>{body}</p></body></html>'

def _home():
    links = ['/terms-%26-conditions', '/a%7Eb', '/old', '/xr2', '/none-meta', '/upper', '/maxprev',
             '/doc.pdf', '/nt', '/scoped-news']
    a = ''.join(f'<a href="{l}">{l}</a>' for l in links)
    a += f'<a href="http://localhost:{PORT}/ext-%26-x">ext</a>'
    return f'<!doctype html><html><head><title>Home</title></head><body><h1>Home</h1>{a}</body></html>'

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def log_message(self, *a):
        pass
    def _send(self, code, body=b'', ctype='text/html; charset=utf-8', headers=()):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
    def do_HEAD(self):
        self.do_GET()
    def do_GET(self):
        p = self.path
        SEEN.append((self.command, p))
        h = lambda s: s.encode()
        if p == '/':
            return self._send(200, h(_home()))
        if p == '/terms-%26-conditions':
            return self._send(200, h(_html('Terms')))
        if p in ('/a%7Eb', '/a~b'):                       # RFC 3986: %7E and ~ are the same URI
            return self._send(200, h(_html('Tilde')))
        if p == '/old':
            return self._send(301, b'', headers=[('Location', '/new')])
        if p == '/new':
            return self._send(200, h(_html('New')))
        if p == '/xr2':                                   # two X-Robots-Tag headers (DE-04)
            return self._send(200, h(_html('Two headers')),
                              headers=[('X-Robots-Tag', 'max-image-preview:large'), ('X-Robots-Tag', 'noindex')])
        if p == '/none-meta':
            return self._send(200, h(_html('None', robots='none')))
        if p == '/upper':
            return self._send(200, h(_html('Upper', robots='NOINDEX, nofollow')))
        if p == '/maxprev':
            return self._send(200, h(_html('Maxprev', robots='max-image-preview:none')))
        if p == '/scoped-news':
            return self._send(200, h(_html('Scoped', robots='index, follow')))
        if p == '/doc.pdf':
            return self._send(200, b'%PDF-1.4 fake', ctype='application/pdf', headers=[('X-Robots-Tag', 'noindex')])
        if p == '/nt':
            return self._send(200, h(_html(title=None)))
        if p.startswith('/ext-'):
            return self._send(200, h(_html('Ext')))
        # DE-05 fixtures: a Cloudflare challenge, real not-found pages, a persistent server error
        if p.startswith('/cf-'):
            return self._send(403, h('<html><title>Just a moment...</title></html>'),
                              headers=[('cf-mitigated', 'challenge')])
        if p.startswith('/gone-'):
            return self._send(404, h(_html('Not found')))
        if p.startswith('/err-'):
            return self._send(503, h(_html('Down')))
        return self._send(404, h(_html('Not found')))

class _TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def setUpModule():
    global SRV, PORT
    os.makedirs(OUT, exist_ok=True)
    SRV = _TS(('127.0.0.1', 0), Handler)
    PORT = SRV.server_address[1]
    threading.Thread(target=SRV.serve_forever, daemon=True).start()

def tearDownModule():
    if SRV:
        SRV.shutdown()
        SRV.server_close()

def crawl(name, curl=False, extra=()):
    out = os.path.join(OUT, f'{name}.json')
    env = dict(os.environ)
    for k in ('CRAWL_CURL', 'CRAWL_PROXY', 'CRAWL_CURL_SSH', 'CRAWL_DELAY', 'http_proxy', 'https_proxy',
              'HTTP_PROXY', 'HTTPS_PROXY'):
        env.pop(k, None)
    env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
    if curl:
        env['CRAWL_CURL'] = '1'
    SEEN.clear()
    r = subprocess.run([PY, os.path.join(ROOT, 'full-extract.py'), H('/'), '--out', out, *extra],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=240)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    d = json.load(open(out))
    rows = {}
    for p in d['pages']:
        rows[p['url'].replace(H(), '') or '/'] = p
    return d, rows, list(SEEN)

def load_module(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ full-extract (aiohttp + curl)
class ExtractAiohttp(unittest.TestCase):
    CURL = False
    @classmethod
    def setUpClass(cls):
        cls.d, cls.rows, cls.seen = crawl('fx-curl' if cls.CURL else 'fx-aiohttp', curl=cls.CURL,
                                          extra=('--check-external',))

    def test_de03_client_percent26_path_sent_encoded(self):
        """A client site 2026-10-14: /terms-%26-conditions-1 was requested as /terms-&-conditions-1 (false 404)."""
        self.assertIn(('GET', '/terms-%26-conditions'), self.seen)
        self.assertNotIn(('GET', '/terms-&-conditions'), self.seen)
        self.assertEqual(self.rows['/terms-%26-conditions']['status'], 200)

    def test_de03_encoding_only_difference_is_not_a_redirect(self):
        """A client site, 60 rows: a 200 page whose URL differs only in percent-encoding was marked redirected."""
        for path in ('/a%7Eb', '/terms-%26-conditions'):
            row = self.rows[path]
            self.assertEqual(row['status'], 200, path)
            self.assertIs(row['redirected'], False, path)
            self.assertEqual(row.get('redirect_hops'), [], path)   # recorded: zero hops

    def test_de03_real_redirect_keeps_hop_list(self):
        row = self.rows['/old']
        self.assertIs(row['redirected'], True)
        self.assertTrue(row['final_url'].endswith('/new'))
        hops = row.get('redirect_hops')
        self.assertIsInstance(hops, list)
        self.assertEqual([h['status'] for h in hops], [301])
        self.assertTrue(hops[0]['url'].endswith('/old'))

    def test_de04_every_xrobots_header_kept(self):
        row = self.rows['/xr2']
        self.assertEqual(row.get('xrobots_headers'), ['max-image-preview:large', 'noindex'])
        self.assertIs(row['indexable'], False)
        self.assertIs(row.get('noindex'), True)

    def test_de04_none_and_upper_case_mean_noindex(self):
        """A client site: 7 rows with robots=none were stored indexable:true."""
        self.assertIs(self.rows['/none-meta']['indexable'], False)
        self.assertIs(self.rows['/upper']['indexable'], False)
        self.assertIs(self.rows['/maxprev']['indexable'], True)       # max-image-preview:none is not noindex
        self.assertIs(self.rows['/scoped-news']['indexable'], True)

    def test_de04_pdf_xrobots_header_recorded(self):
        row = self.rows['/doc.pdf']
        self.assertEqual(row.get('xrobots_headers'), ['noindex'])
        self.assertIs(row.get('noindex'), True)

    def test_de03_external_head_sent_encoded(self):
        self.assertIn(('HEAD', '/ext-%26-x'), self.seen)


class ExtractCurl(ExtractAiohttp):
    """Same controls on the curl fallback path (CRAWL_CURL=1): it hard-coded xrobots "" (DE-04)."""
    CURL = True


# ------------------------------------------------------------------ shared definitions
class NoindexDefinition(unittest.TestCase):
    CASES = [('noindex', True), ('NOINDEX, nofollow', True), ('none', True), ('None', True),
             ('index, follow', False), ('max-image-preview:none', False), ('max-image-preview: none, index', False),
             ('googlebot: noindex', True), ('nofollow', False), ('', False), ('noindex,nofollow', True),
             ('noimageindex', False), ('unavailable_after: 2026-01-01', False)]

    def test_de04_one_definition_crawl_transport(self):
        import crawl_transport as tx
        for v, want in self.CASES:
            self.assertIs(tx.robots_noindex(v), want, v)
        self.assertIs(tx.robots_noindex(['max-image-preview:large', 'noindex']), True)

    def test_de04_audit_checks_mirrors_crawl_transport(self):
        import crawl_transport as tx
        ac = load_module('audit_checks_t', 'audit-checks.py')
        self.assertEqual(ac.NOINDEX_RE.pattern, tx.NOINDEX_RE.pattern)
        self.assertEqual(ac.NOINDEX_RE.flags, tx.NOINDEX_RE.flags)
        for v, want in self.CASES:
            self.assertIs(ac._is_noindex({'meta_robots': v}), want, v)
        self.assertIs(ac._is_noindex({'meta_robots': '', 'noindex': True}), True)   # stored field wins


class UrlKey(unittest.TestCase):
    def test_de03_url_equivalence(self):
        import crawl_transport as tx
        self.assertTrue(tx.same_url('https://EXAMPLE.com:443/a%7Eb', 'https://example.com/a~b'))
        self.assertTrue(tx.same_url('http://x.com/a%2f', 'http://x.com/a%2F'))
        self.assertFalse(tx.same_url('http://x.com/a-%26-b', 'http://x.com/a-&-b'))
        self.assertFalse(tx.same_url('http://x.com/a/b', 'http://x.com/a%2Fb'))
        self.assertFalse(tx.same_url('http://x.com/a', 'http://x.com/a/'))


# ------------------------------------------------------------------ sources that are not pages
AJAX_SRC = ['/wp-admin/admin-ajax.php?action=process_simple_like&post_id=%d' % i for i in (1, 2, 3)]

class NotRealSources(unittest.TestCase):
    """A live report (2026-10-14) counted WordPress like-button requests as pages linking to a
    broken address. Sources must be real pages, in trace-errors.py and checks-to-findings.py."""
    def test_trace_and_converter_count_only_real_pages(self):
        tgt = '/gone-sources'
        pages = [row('/', internal_links=[{'href': H(tgt)}]), row('/about', internal_links=[{'href': H(tgt)}]),
                 row(tgt, status=404, indexable=False)]
        pages += [row(u, internal_links=[{'href': H(tgt)}]) for u in AJAX_SRC]
        cj = os.path.join(OUT, 'sources-full.json')
        with open(cj, 'w') as f:
            json.dump({'site': f'127.0.0.1:{PORT}', 'pages': pages}, f)
        r = subprocess.run([PY, os.path.join(ROOT, 'trace-errors.py'), cj], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.stdout.count('linked from: '), 2, r.stdout)
        self.assertNotIn('admin-ajax', r.stdout)
        # the converter, fed an OLD trace.log that still lists the ajax sources
        trace = f"[404] {H(tgt)}   — BROKEN LINK\n" + ''.join(f"      linked from: {H(u)}\n" for u in ['/', '/about'] + AJAX_SRC)
        f, _ = c2f('sources', {'trace.log': trace}, crawl_pages=pages)
        t = [x for x in f['findings'] if "isn't there" in x['title'] or 'broken' in x['title'].lower() or 'not there' in x['title']]
        self.assertEqual(len(t), 1, [x['title'] for x in f['findings']])
        blob = json.dumps(t[0])
        self.assertNotIn('admin-ajax', blob)
        self.assertRegex(blob, r'on 2 of your own pages', blob[:1500])


# ------------------------------------------------------------------ CSR / empty-shell finding
STATIC_WORDS = ('This domain is for use in documentation examples without needing permission. '
                'Avoid use in operations. Learn more about example domains and how to use them.')

def _render_block(url, content, wr, raw_w, dom_w, scripts=None):
    sc = '' if scripts is None else f' · scripts {scripts}'
    return (f"\n{url}\n  CONTENT: {content}   (raw = {wr}% of rendered words, 100% links, 50% vocabulary)\n"
            f"  VISUAL:  OK    (no-JS paint covers 100% of the JS paint)\n"
            f"  words raw/rendered {raw_w}/{dom_w} · links 1/1 · headings 1/1 · jsonld 0/0{sc}\n")


class CsrShell(unittest.TestCase):
    """Coordinator 2026-10-14: a run on a static one-page site (about 30 words, no JavaScript)
    shipped a CRITICAL "1 pages are empty shells until JavaScript runs". The finding needs proof
    that JavaScript adds content, never fires on a page with no scripts, and never from one page."""

    def _shell(self, f):
        return [x for x in f['findings'] if 'empty shells' in x['title']]

    def test_static_one_page_site_is_not_a_shell(self):
        page = row('/', word_count=30, html_tag_inventory={'html': 1, 'p': 2, 'a': 1, 'h1': 1})
        log = "=== render check ===" + _render_block(H('/'), 'CSR', 30, 9, 30)
        f, _ = c2f('staticone', {'render.log': log}, crawl_pages=[page])
        self.assertEqual(self._shell(f), [], [x['title'] for x in f['findings']])

    def test_no_scripts_is_never_a_shell(self):
        pages = [row(p, word_count=30, html_tag_inventory={'p': 2}) for p in ('/', '/a', '/b')]
        log = "=== render check ===" + ''.join(_render_block(H(p), 'CSR', 10, 30, 300) for p in ('/', '/a', '/b'))
        f, _ = c2f('noscripts', {'render.log': log}, crawl_pages=pages)
        self.assertEqual(self._shell(f), [], [x['title'] for x in f['findings']])

    def test_real_spa_still_reported(self):  # positive control: scripts, JS adds hundreds of words
        pages = [row(p, word_count=12, title='', html_tag_inventory={'div': 1, 'script': 4}) for p in ('/', '/a', '/b')]
        for p in pages:
            p['headings'] = {}
            p['heading_counts'] = {'h1': 0}
        log = "=== render check ===" + ''.join(_render_block(H(p), 'CSR', 3, 12, 420, scripts=4) for p in ('/', '/a', '/b'))
        f, _ = c2f('spa', {'render.log': log}, crawl_pages=pages)
        sh = self._shell(f)
        self.assertEqual(len(sh), 1, [x['title'] for x in f['findings']])
        self.assertTrue(sh[0]['title'].startswith('3 pages'), sh[0]['title'])


class RenderCheckStatic(unittest.TestCase):
    """render-check.py on a page with no scripts must never print CONTENT: CSR, even when the raw
    fetch and the browser fetch get different bodies (the server below sends the browser more text)."""
    def test_no_script_page_is_not_csr(self):
        import http.server as hs
        long = '<p>' + ' '.join(['extra words from a different fetch'] * 40) + '</p>'
        class Hd(hs.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_GET(self):
                more = 'HeadlessChrome' in (self.headers.get('User-Agent') or '')
                b = (f'<!doctype html><html><head><title>Example Domain</title></head><body><h1>Example Domain</h1>'
                     f'<p>{STATIC_WORDS}</p>{long if more else ""}<a href="https://www.iana.org/">More</a></body></html>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)
        srv = hs.ThreadingHTTPServer(('127.0.0.1', 0), Hd)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            d = os.path.join(OUT, 'render-static'); os.makedirs(d, exist_ok=True)
            env = {k: v for k, v in os.environ.items() if k not in ('CRAWL_PROXY',)}
            env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
            r = subprocess.run([PY, os.path.join(ROOT, 'render-check.py'), f'http://127.0.0.1:{srv.server_address[1]}/'],
                               capture_output=True, text=True, env=env, cwd=d, stdin=subprocess.DEVNULL, timeout=240)
        finally:
            srv.shutdown(); srv.server_close()
        self.assertIn('CONTENT:', r.stdout, r.stdout[-1500:] + r.stderr[-1500:])
        self.assertNotIn('CONTENT: CSR', r.stdout, r.stdout[-1500:])
        self.assertNotEqual(r.returncode, 2, r.stdout[-1500:])


# ------------------------------------------------------------------ static fixture sites, end to end
FIXTURES = os.path.join(HERE, 'fixtures')

def _serve_dir(path):
    import functools, http.server as hs
    class Q(hs.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = hs.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Q, directory=path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def _clean_env():
    env = {k: v for k, v in os.environ.items()
           if k not in ('CRAWL_PROXY', 'CRAWL_CURL', 'CRAWL_CURL_SSH', 'CRAWL_DELAY',
                        'http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY')}
    env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
    return env


class StaticFixtureNoShell(unittest.TestCase):
    """Owner item 4 (2026-10-14): example.com, one plain static page, came back "empty shells until
    JavaScript runs" and the run was marked PARTIAL. End to end on tiny static fixture sites
    (tests/fixtures/static-one-page, tests/fixtures/static-two-page with a tiny inline script):
    full-extract.py crawls, render-check.py renders every crawled page, checks-to-findings.py reads
    both. There must be no CSR verdict, no shell finding and no "client-rendered" not-measured list
    (that list is what makes audit-lib/summary.py mark the run PARTIAL)."""

    def _run(self, name, n_pages):
        srv = _serve_dir(os.path.join(FIXTURES, name))
        base = f'http://127.0.0.1:{srv.server_address[1]}'
        try:
            env = _clean_env()
            out = os.path.join(OUT, f'fixture-{name}.json')
            r = subprocess.run([PY, os.path.join(ROOT, 'full-extract.py'), base + '/', '--out', out],
                               capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=240)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            pages = [p for p in json.load(open(out))['pages']
                     if p.get('status') == 200 and not p.get('non_html')]
            self.assertEqual(len(pages), n_pages, [p.get('url') for p in pages])
            d = os.path.join(OUT, f'render-{name}'); os.makedirs(d, exist_ok=True)
            rc = subprocess.run([PY, os.path.join(ROOT, 'render-check.py'), *[p['url'] for p in pages]],
                                capture_output=True, text=True, env=env, cwd=d, stdin=subprocess.DEVNULL,
                                timeout=400)
        finally:
            srv.shutdown(); srv.server_close()
        self.assertEqual(rc.stdout.count('CONTENT:'), n_pages, rc.stdout[-2000:] + rc.stderr[-1500:])
        self.assertNotIn('CONTENT: CSR', rc.stdout, rc.stdout[-2000:])
        self.assertNotEqual(rc.returncode, 2, rc.stdout[-2000:])
        f, _ = c2f(f'fx-{name}', {'render.log': rc.stdout}, crawl_pages=pages, seed=base + '/',
                   site=base.split('://', 1)[1])
        titles = [x['title'] for x in f['findings']]
        self.assertEqual([t for t in titles if re.search(r'empty shells|until JavaScript', t)], [], titles)
        op = json.load(open(os.path.join(OUT, 'c2f', f'fx-{name}', 'reports', f'fx-{name}-operator-findings.json')))
        self.assertEqual(op.get('not_measured_csr_checks'), [], op)

    def test_static_one_page_no_script(self):
        self._run('static-one-page', 1)

    def test_static_two_pages_tiny_inline_script(self):
        self._run('static-two-page', 2)


class CsrNeedsRealEvidence(unittest.TestCase):
    """The two hard rules (owner item 4, 2026-10-14): no CSR finding when the raw HTML already has
    the H1 and >= 100 words of main text, and none when fewer than 3 real pages were crawled.
    Both logs below carry scripts and JavaScript that adds hundreds of words, so only these rules
    can stop the finding."""

    def _shell(self, f):
        return [x for x in f['findings'] if 'empty shells' in x['title']]

    def _op(self, case):
        return json.load(open(os.path.join(OUT, 'c2f', case, 'reports', f'{case}-operator-findings.json')))

    @unittest.expectedFailure   # rule not in code yet (owner item 4); remove this line when it is
    def test_raw_h1_and_100_words_is_not_csr(self):
        paths = ('/', '/a', '/b')
        pages = [row(p, word_count=140, html_tag_inventory={'h1': 1, 'p': 3, 'script': 2}) for p in paths]
        log = "=== render check ===" + ''.join(
            _render_block(H(p), 'CSR', 30, 140, 470, scripts=2).replace(' · scripts 2', ' · scripts 2 · h1 1/1 · main words 140')
            for p in paths)
        f, _ = c2f('rawh1', {'render.log': log}, crawl_pages=pages)
        self.assertEqual(self._shell(f), [], [x['title'] for x in f['findings']])
        self.assertEqual(self._op('rawh1').get('not_measured_csr_checks'), [])

    def test_fewer_than_three_pages_is_not_csr(self):
        paths = ('/', '/a')
        pages = [row(p, word_count=12, title='', html_tag_inventory={'div': 1, 'script': 4}) for p in paths]
        for p in pages:
            p['headings'] = {}
            p['heading_counts'] = {'h1': 0}
        log = "=== render check ===" + ''.join(_render_block(H(p), 'CSR', 3, 12, 420, scripts=4) for p in paths)
        f, _ = c2f('twopages', {'render.log': log}, crawl_pages=pages)
        self.assertEqual(self._shell(f), [], [x['title'] for x in f['findings']])
        self.assertEqual(self._op('twopages').get('not_measured_csr_checks'), [])

    @unittest.expectedFailure   # rule not in code yet (owner item 4); remove this line when it is
    def test_render_check_raw_h1_and_100_words_is_not_csr(self):
        """render-check.py itself: raw page has an H1 and ~120 words, a script adds 300 more."""
        import http.server as hs
        main = ('<main><h1>Example Domain</h1><p>' + ' '.join(['plain server text for this test page'] * 20)
                + '</p></main>')
        js = ("<script>var p=document.createElement('p');p.textContent=Array(60).fill('added by script now').join(' ');"
              "document.body.appendChild(p);</script>")
        class Hd(hs.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_GET(self):
                b = f'<!doctype html><html><head><title>T</title></head><body>{main}{js}</body></html>'.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)
        srv = hs.ThreadingHTTPServer(('127.0.0.1', 0), Hd)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            d = os.path.join(OUT, 'render-rawh1'); os.makedirs(d, exist_ok=True)
            r = subprocess.run([PY, os.path.join(ROOT, 'render-check.py'), f'http://127.0.0.1:{srv.server_address[1]}/'],
                               capture_output=True, text=True, env=_clean_env(), cwd=d, stdin=subprocess.DEVNULL,
                               timeout=240)
        finally:
            srv.shutdown(); srv.server_close()
        self.assertIn('CONTENT:', r.stdout, r.stdout[-1500:] + r.stderr[-1500:])
        self.assertNotIn('CONTENT: CSR', r.stdout, r.stdout[-1500:])


# ------------------------------------------------------------------ audit-checks check 6 (www prefix)
class WwwPrefix(unittest.TestCase):
    """Engineering review 2026-10-14: check 6 compared hosts with lstrip('w.'), which strips the
    CHARACTERS w and ., so wexample.com counted as the same site as example.com."""
    @classmethod
    def setUpClass(cls):
        cls.ac = load_module('audit_checks_www', 'audit-checks.py')

    def test_strip_www_is_a_prefix(self):
        self.assertEqual(self.ac._strip_www('www.example.com'), 'example.com')
        self.assertEqual(self.ac._strip_www('wexample.com'), 'wexample.com')
        self.assertEqual(self.ac._strip_www('w.example.com'), 'w.example.com')
        self.assertEqual(self.ac.host('http://shop.www.example.com/'), 'shop.www.example.com')

    def test_check6_ignores_lookalike_host(self):
        site = 'http://www.shop.test'
        page = {'url': site + '/', 'status': 200, 'content_type': 'text/html', 'title': 'Home',
                'internal_links': [{'href': 'http://shop.test/a', 'anchor': 'variant'}],
                'external_links': [{'href': 'http://wshop.test/b', 'anchor': 'lookalike'},
                                   {'href': 'http://w.shop.test/c', 'anchor': 'other sub'}]}
        out = os.path.join(OUT, 'www-check6-full.json')
        with open(out, 'w') as f:
            json.dump({'site': 'www.shop.test', 'seed': site + '/', 'pages': [page]}, f)
        env = {k: v for k, v in os.environ.items() if k not in ('CRAWL_PROXY', 'http_proxy', 'https_proxy')}
        r = subprocess.run([PY, os.path.join(ROOT, 'audit-checks.py'), out], capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL, timeout=300)
        sec = r.stdout[r.stdout.find('\n6. RESOURCE'):]
        sec = sec[:sec.find('\n7.')] if '\n7.' in sec else sec[:1500]
        self.assertIn('http://shop.test/a', sec, 'control: the real www/non-www variant must be flagged\n' + r.stdout[-3000:] + r.stderr[-2000:])
        self.assertNotIn('http://wshop.test', sec)
        self.assertNotIn('http://w.shop.test', sec)


# ------------------------------------------------------------------ hack-scan: SerpAPI key optional
class HackScanKey(unittest.TestCase):
    def test_absent_key_says_so_once(self):
        out = os.path.join(OUT, 'hackkey-full.json')
        with open(out, 'w') as f:
            json.dump({'site': '127.0.0.1', 'pages': [{'url': H('/'), 'status': 200, 'indexable': True,
                                                       'content_type': 'text/html', 'internal_links': []}]}, f)
        env = {k: v for k, v in os.environ.items() if k not in ('SERPAPI_KEY', 'SERP_API_KEY', 'CRAWL_PROXY')}
        env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
        r = subprocess.run([PY, os.path.join(ROOT, 'hack-scan.py'), out, H('/')], capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(r.stdout.count('index probe skipped: no SERPAPI_KEY'), 1, r.stdout[-2000:])
        self.assertNotIn('Traceback', r.stderr)


# ------------------------------------------------------------------ audit-checks
class LocalLeak(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ac = load_module('audit_checks_t2', 'audit-checks.py')

    def test_jp05_tests_com_is_a_real_site(self):
        """A client site 2026-10-14: https://www.tests.com/practice/... fired LOCAL_LEAK (CRITICAL, dropped as false)."""
        self.assertFalse(self.ac._local_leak('https://www.tests.com/practice/x', 'example.com'))
        self.assertFalse(self.ac._local_leak('https://www.localprofile.com/business/x', 'example.org'))
        self.assertFalse(self.ac._local_leak('https://www.testimonialtree.com/', 'example.org'))
        self.assertFalse(self.ac._local_leak('https://stagingpros.com/', 'example.com'))
        self.assertFalse(self.ac._local_leak('/blog/localhost-vs-staging/', 'example.com'))

    def test_jp05_real_local_and_staging_hosts_fire(self):
        for href in ('http://localhost:8080/', 'http://127.0.0.1/x', 'http://127.4.5.6/', 'http://0.0.0.0/',
                     'http://[::1]/', 'https://dev.local/', 'https://site.test/a', 'http://app.localhost/',
                     'https://x.invalid/', 'https://staging.example.com/', 'https://dev.example.com/p',
                     'https://staging-2.example.com/'):
            self.assertTrue(self.ac._local_leak(href, 'example.com'), href)


# ------------------------------------------------------------------ checks-to-findings
def c2f(case, files, crawl_pages=None, discovery=None, site=None, seed=None):
    adir = os.path.join(OUT, 'c2f', case, 'audit')
    rdir = os.path.join(OUT, 'c2f', case, 'reports')
    os.makedirs(adir, exist_ok=True); os.makedirs(rdir, exist_ok=True)
    for name in ('checks.log', 'trace.log', 'hack.log', 'discovery.log', 'filterscan.log', 'render.log'):
        with open(os.path.join(adir, name), 'w') as f:
            f.write(files.get(name, ''))
    site = site or f'127.0.0.1:{PORT}'
    with open(os.path.join(rdir, f'{case}-full.json'), 'w') as f:
        json.dump({'site': site, 'seed': seed or (H('/') if site.startswith('127.') else f'https://{site}'),
                   'pages': crawl_pages or []}, f)
    if discovery is not None:
        with open(os.path.join(rdir, f'{case}-discovery.json'), 'w') as f:
            json.dump(discovery, f)
    env = dict(os.environ)
    for k in ('CRAWL_PROXY', 'http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
        env.pop(k, None)
    r = subprocess.run(['python3', os.path.join(ROOT, 'checks-to-findings.py'), case, adir, rdir],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    with open(os.path.join(rdir, f'{case}-findings.json')) as fh:
        return json.load(fh), r.stdout

def row(path, status=200, **kw):
    d = {'url': H(path), 'final_url': H(path), 'status': status, 'redirected': False,
         'content_type': 'text/html; charset=utf-8', 'indexable': status == 200, 'title': 'T ' + path,
         'meta_robots': '', 'word_count': 300, 'heading_counts': {'h1': 1}, 'internal_links': []}
    d.update(kw)
    return d

PAGED_FILTERSCAN = """FILTER-SCAN: parameters = {"page": 4}
FILTER-COMBOS: 0 filter dimension(s); at least 0 filter combinations (~0 with pagination) combination URLs the site itself links or generates
FILTER-PROBE: https://example.com/?page=1  ->  200  canonical=https://example.com/  robots=index, follow
FILTER-PROBE: https://example.com/?page=2  ->  200  canonical=https://example.com/  robots=noindex, follow
FILTER-PROBE: https://example.com/?page=3  ->  200  canonical=https://example.com/  robots=none
FILTER-MATH: none L=1 page=16-UNBOUNDED cross-linked=no unvalidated=yes → exposed(linked/generated)=0
FILTER-VERDICT: INTERACTION-ONLY (0 filters generate 200-OK URLs; the filter controls are <button> elements)
"""

class Findings(unittest.TestCase):
    def test_de01_meta_robots_is_not_robots_txt(self):
        """A client run (and 3 more public pages): "blocked in robots.txt" came from the META robots tag."""
        f, _ = c2f('pagedsite', {'filterscan.log': PAGED_FILTERSCAN})
        fx = [x for x in f['findings'] if 'filter' in x['title'].lower()]
        self.assertTrue(fx, [x['title'] for x in f['findings']])
        blob = json.dumps(fx)
        self.assertNotIn('in robots.txt"', blob)
        self.assertNotIn('blocked in robots.txt', blob)
        lines = '\n'.join(u for x in fx for u in x.get('urls', []))
        self.assertIn("the page's robots tag allows indexing", lines)
        self.assertIn("the page's robots tag says noindex", lines)
        self.assertIn('has no robots tag', lines)

    def test_de05_403_challenge_is_not_isnt_there(self):
        """A client site: 46 Cloudflare-challenge 403s + 3 real 404s shipped as "49 links ... aren't there"."""
        cf = [f'/cf-{i}' for i in range(46)]
        gone = ['/gone-1', '/gone-2', '/gone-3']
        trace = '=== 50 non-200 URL(s) ===\n\n'
        for p in cf:
            trace += f'[403] {H(p)}   — forbidden\n      linked from: {H("/")}\n\n'
        for p in gone:
            trace += f'[404] {H(p)}   — BROKEN LINK\n      linked from: {H("/")}\n\n'
        trace += f'[429] {H("/cf-rate")}\n      linked from: {H("/")}\n\n'
        home = row('/', internal_links=[{'href': H(p)} for p in cf + gone + ['/ok-1', '/ok-2', '/cf-rate']])
        pages = [home] + [row(p, 403) for p in cf] + [row(p, 404) for p in gone] + [row('/ok-1'), row('/ok-2'), row('/cf-rate', 429)]
        f, out = c2f('challenged', {'trace.log': trace}, crawl_pages=pages)
        there = [x for x in f['findings'] if "isn't there" in x['title'] or "aren't there" in x['title']]
        self.assertEqual(len(there), 1, [x['title'] for x in f['findings']])
        self.assertRegex(there[0]['title'], r'^3 of \d+ checked links ')
        self.assertNotIn('/cf-', json.dumps(there[0]))
        cnc = f.get('could_not_check') or []
        self.assertEqual(len([u for c in cnc for u in c.get('urls', [])]), 47, cnc)

    def test_de05_persistent_5xx_stays_an_error(self):
        trace = f'[503] {H("/err-1")}\n      linked from: {H("/")}\n\n[404] {H("/gone-9")}\n      linked from: {H("/")}\n\n'
        pages = [row('/', internal_links=[{'href': H('/err-1')}, {'href': H('/gone-9')}]), row('/err-1', 503), row('/gone-9', 404)]
        f, _ = c2f('fivexx', {'trace.log': trace}, crawl_pages=pages)
        t = [x for x in f['findings'] if 'checked links' in x['title']]
        self.assertEqual(len(t), 1, [x['title'] for x in f['findings']])
        self.assertIn('/err-1', json.dumps(t[0]))
        self.assertTrue(t[0]['title'].startswith('2 of 2 checked links'), t[0]['title'])

    def test_de04_xrobots_noindex_header_is_reported(self):
        pages = [row('/'), row('/services', indexable=False, noindex=True, xrobots_headers=['noindex'],
                               meta_robots='noindex'),
                 row('/about', xrobots_headers=[]), row('/login', indexable=False, noindex=True, xrobots_headers=['noindex'])]
        f, _ = c2f('xrobots', {}, crawl_pages=pages)
        t = [x for x in f['findings'] if 'hidden server setting' in x['title']]
        self.assertEqual(len(t), 1, [x['title'] for x in f['findings']])
        self.assertTrue(t[0]['title'].startswith('1 page '), t[0]['title'])
        self.assertIn('/services', json.dumps(t[0]['urls']))
        self.assertNotIn('/login', json.dumps(t[0]['urls']))

    def test_de04_missing_title_is_reported(self):
        pages = [row('/'), row('/a', title=''), row('/b', title='   '), row('/c', title='', status=404, indexable=False),
                 row('/d.pdf', title='', content_type='application/pdf')]
        f, _ = c2f('notitle', {}, crawl_pages=pages)
        t = [x for x in f['findings'] if 'no title' in x['title'].lower()]
        self.assertEqual(len(t), 1, [x['title'] for x in f['findings']])
        self.assertTrue(t[0]['title'].startswith('2 pages'), t[0]['title'])

    WEBEDIT_DISC = """2. Wayback CDX (orphaned / deleted / never-linked pages)
   885 URL(s) ever archived
   liveness sample (15 URLs): 2 live · 13 dead (86% 404/410)
   🚩 MOST ARCHIVED URLs ARE NOW DEAD — the previous site's URLs 404 with no
      redirects. Every inbound link and all accumulated equity to them is lost.
      dead prefixes in the sample: /_webedit/ (12), /gallery/ (1)
      FIX: 301 the old URL patterns to their closest current equivalents.
"""
    CDN_DISC = """2. Wayback CDX (orphaned / deleted / never-linked pages)
   9 URL(s) ever archived
   liveness sample (9 URLs): 1 live · 8 dead (88% 404/410)
   🚩 MOST ARCHIVED URLs ARE NOW DEAD — the previous site's URLs 404 with no
      redirects. Every inbound link and all accumulated equity to them is lost.
      dead prefixes in the sample: /assets/ (6), /cdn-cgi/ (2)
      FIX: 301 the old URL patterns to their closest current equivalents.
"""

    def test_jp05_old_builder_archived_assets_are_not_dead_pages(self):
        found = {f'example.co.uk/_webedit/cached-images/{i}-220-165-0-0-220-165': ['wayback'] for i in range(40)}
        found.update({'example.co.uk/_webedit/cached-content/style.css': ['wayback'],
                      'example.co.uk/gallery/': ['wayback'], 'example.co.uk/about-us': ['wayback']})
        f, _ = c2f('oldbuilder', {'discovery.log': self.WEBEDIT_DISC}, crawl_pages=[row('/')],
                   discovery={'domain': 'example.co.uk', 'found': found, 'not_crawled': list(found)},
                   site='example.co.uk')
        self.assertFalse([x for x in f['findings'] if 'dead ends' in x['title']], [x['title'] for x in f['findings']])

    def test_jp05_cdn_cgi_and_assets_are_not_dead_pages(self):
        found = {f'example.net/assets/images/image0{i}.jpg?v=52a0252a': ['wayback'] for i in range(6)}
        found.update({'example.net/cdn-cgi/l/email-protection': ['wayback'],
                      'example.net/cdn-cgi/scripts/5c5dd728/cloudflare-static/email-decode.min.js': ['wayback'],
                      'example.net': ['ct', 'wayback']})
        f, _ = c2f('cdnassets', {'discovery.log': self.CDN_DISC}, crawl_pages=[row('/')],
                   discovery={'domain': 'example.net', 'found': found, 'not_crawled': list(found)},
                   site='example.net')
        self.assertFalse([x for x in f['findings'] if 'dead ends' in x['title']], [x['title'] for x in f['findings']])

    def test_jp05_archived_dead_pages_still_reported(self):
        """Positive control: dead old PAGES (not assets) must still produce the finding."""
        disc = self.CDN_DISC.replace('/assets/ (6), /cdn-cgi/ (2)', '/services/ (7), /blog/ (1)')
        found = {f'acme.com/services/old-{i}': ['wayback'] for i in range(8)}
        found['acme.com/blog/post'] = ['wayback']
        f, _ = c2f('acmearch', {'discovery.log': disc}, crawl_pages=[row('/')],
                   discovery={'domain': 'acme.com', 'found': found, 'not_crawled': list(found)}, site='acme.com')
        t = [x for x in f['findings'] if 'dead ends' in x['title']]
        self.assertEqual(len(t), 1, [x['title'] for x in f['findings']])
        self.assertTrue(all('/services/' in u or '/blog/' in u for u in t[0]['urls']), t[0]['urls'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
