#!/usr/bin/env python3
"""READ-ONLY guarantee (owner safety rule 2026-10-14). Run: python3 tests/test_read_only.py

1. A source scan: no tool in the kit may send a POST/PUT/PATCH/DELETE, a request body, or call
   form.submit()/requestSubmit(). The scanner is first run on planted samples (the control): it
   must catch each one, so a pass means something.
2. filter-url-scan.py clicks filter controls on a rendered page. On a local fixture page with a
   form, a submit button and "Delete"/"Subscribe" controls it must trigger no side-effect request.
3. Every tool's header says it only reads.

The fixture page is served by an in-process http.server on 127.0.0.1 (stopped in tearDown)."""
import glob, http.server, os, re, subprocess, sys, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
VPY = os.path.join(ROOT, '.venv', 'bin', 'python')
PY = VPY if os.path.exists(VPY) else sys.executable

WRITE_PATTERNS = [
    re.compile(r"""method\s*[=:]\s*['"](POST|PUT|PATCH|DELETE)['"]""", re.I),
    re.compile(r"""['"]-X['"]\s*,\s*['"](POST|PUT|PATCH|DELETE)""", re.I),
    re.compile(r"""-X\s*(POST|PUT|PATCH|DELETE)\b"""),
    re.compile(r"""['"]--data(-binary|-raw|-urlencode)?['"]|['"]-d['"]\s*,"""),
    re.compile(r"""\bRequest\([^)]*\bdata\s*="""),
    re.compile(r"""\bdata\s*=\s*b['"]"""),
    re.compile(r"""\.(post|put|patch|delete)\(\s*['"f]"""),
    re.compile(r"""\.submit\(\)|requestSubmit\("""),
]

PLANTED = [
    "req = urllib.request.Request(u, data=b'x=1')",
    "_rq = _ur.Request(url, data=b'<?xml?>', headers=h)",
    "r = requests.post('https://x/login', data={'u': 1})",
    "async with session.post(f'{base}/form') as r:",
    "cmd = ['curl', '-X', 'POST', url]",
    "fetch(u, {method: 'POST'})",
    "document.querySelector('form').submit()",
    "el.form.requestSubmit(btn)",
]


def tool_files():
    pats = ['*.py', '*.js', 'cms/*.py', 'crawler/*.py', 'crawler/checks/*.py']
    return sorted(f for p in pats for f in glob.glob(os.path.join(ROOT, p)))


def read(f):
    with open(f, encoding='utf-8', errors='replace') as fh:
        return fh.read()


def scan(text):
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split('#', 1)[0] if not line.lstrip().startswith(('//', '*')) else ''
        if any(p.search(code) for p in WRITE_PATTERNS):
            hits.append((n, line.strip()))
    return hits


class SourceScan(unittest.TestCase):
    def test_control_catches_planted_writes(self):
        for s in PLANTED:
            self.assertTrue(scan(s), f'scanner missed a planted write: {s}')

    def test_no_tool_writes(self):
        bad = {}
        for f in tool_files():
            hits = scan(read(f))
            if hits:
                bad[os.path.relpath(f, ROOT)] = hits
        self.assertEqual(bad, {}, 'write requests found in tools')

    def test_headers_say_read_only(self):
        tools = ['full-extract.py', 'crawl_transport.py', 'audit-checks.py', 'checks-to-findings.py',
                 'build-crawl-report.js', 'interlink-map.py', 'verify-audit.py', 'click-test.js',
                 'render-check.py', 'trace-errors.py', 'url-discovery.py', 'filter-url-scan.py',
                 'hack-scan.py', 'detect-cms.py', 'pixel-consent-check.py', 'content-attribution.py',
                 'cms/wordpress.py', 'crawler/__init__.py', 'crawler/cli.py']
        for t in tools:
            head = read(os.path.join(ROOT, t))[:6000]
            self.assertIn('READ-ONLY', head, t)


PAGE = """<!doctype html><html><head><title>Shop</title></head><body>
<a href="/?color=red">Red</a>
<button type="button" onclick="history.pushState({}, '', '/?size=s')">Small</button>
<form action="/side-effect" method="post"><button type="button" onclick="fetch('/side-effect?inform')">Blue</button>
<button type="submit">Filter</button></form>
<button type="submit" onclick="fetch('/side-effect?lonesubmit')">Go</button>
<button type="button" onclick="fetch('/side-effect?delete')">Delete list</button>
<span role="button" onclick="fetch('/side-effect?subscribe')">Subscribe</span>
</body></html>"""
SIDE = []


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        SIDE.append(('POST', self.path))
        self.send_response(405)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/side-effect'):
            SIDE.append(('GET', self.path))
        ok = self.path.split('?')[0] == '/'
        b = (PAGE if ok else 'not found').encode()
        self.send_response(200 if ok else 404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class FilterScanClicks(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_filter_scan_triggers_no_side_effect(self):
        SIDE.clear()
        url = f'http://127.0.0.1:{self.srv.server_address[1]}/'
        env = {k: v for k, v in os.environ.items() if k not in ('SERPAPI_KEY', 'SERP_API_KEY', 'CRAWL_PROXY')}
        env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
        r = subprocess.run([PY, os.path.join(ROOT, os.environ.get('FILTER_SCAN_UNDER_TEST', 'filter-url-scan.py')), url],
                           capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=240, cwd=ROOT)
        self.assertNotIn('Traceback', r.stderr, r.stderr[-2000:])
        self.assertEqual(SIDE, [], 'filter-url-scan clicked a control that changes something')


class Pins(unittest.TestCase):  # owner rule 2026-10-14: every dependency pinned exactly
    def test_requirements_exact(self):
        import json
        for line in read(os.path.join(ROOT, 'requirements.txt')).splitlines():
            spec = line.split('#', 1)[0].strip()
            if spec:
                self.assertRegex(spec, r'^[A-Za-z0-9_.\-]+==[0-9][0-9A-Za-z.\-]*$', spec)
        deps = json.loads(read(os.path.join(ROOT, 'package.json')))['dependencies']
        for k, v in deps.items():
            self.assertRegex(v, r'^\d+\.\d+\.\d+$', f'{k}: {v}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
