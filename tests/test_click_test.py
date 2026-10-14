#!/usr/bin/env python3
"""Tests for click-test.js (JP-02: "links go nowhere" shipped for 3 h 39 min on a makeup-artist site; the
buttons were a working Thrive button group). Run: python3 tests/test_click_test.py

Fixture page served by an in-process ThreadingHTTPServer on 127.0.0.1 (stopped in tearDown).
Output goes to tests/_out/verify/click/. click-test.js closes its own Chrome; the subprocess has a
hard timeout so a hung run is killed by its own PID (subprocess.run), never by pattern."""
import http.server, json, os, subprocess, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SC = os.path.abspath(os.path.join(HERE, '..'))
TOOL = os.path.join(SC, 'click-test.js')
OUT = os.path.join(HERE, '_out', 'verify', 'click')

PAGE = """<!doctype html><html><head><title>Fixture</title>
<style>#panel{padding:20px;background:#eee} .spacer{height:2400px}</style></head><body>
<nav><a href="#" id="tab" class="tab" aria-expanded="false">Prices tab</a></nav>
<div id="panel" hidden>Our prices: wash 3 dollars</div>
<div class="spacer">long page</div>
<a href="#" id="dead">Read more dead link</a>
<form action="/side-effect" method="post"><a href="#" id="inform" onclick="fetch('/side-effect?inform')">Form helper link</a>
<button type="button" id="formbtn" onclick="fetch('/side-effect?formbtn')">Pick a color</button></form>
<button type="submit" id="lonesubmit" onclick="fetch('/side-effect?lonesubmit')">Next</button>
<a href="#" id="del" onclick="fetch('/side-effect?delete');return false">Delete my account</a>
<span role="button" id="sub" onclick="fetch('/side-effect?subscribe')">Subscribe</span>
<a href="#" id="signin" onclick="fetch('/side-effect?signin');return false">Sign in</a>
<script>
document.getElementById('tab').addEventListener('click', function (e) {
  e.preventDefault();
  var p = document.getElementById('panel');
  p.hidden = !p.hidden;
  this.setAttribute('aria-expanded', String(!p.hidden));
});
</script></body></html>"""


SIDE_EFFECTS = []   # any request to /side-effect means the tool clicked something it must not


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        SIDE_EFFECTS.append(('POST', self.path))
        self.send_response(405)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/side-effect'):
            SIDE_EFFECTS.append(('GET', self.path))
        b = PAGE.encode()
        self.send_response(200 if self.path.split('?')[0] in ('/', '/index.html') else 404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


class ClickTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(OUT, exist_ok=True)   # nothing deleted; click-test.json is overwritten each run
        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.srv.server_address[1]}/'

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def run_tool(self, out):
        self.assertTrue(os.path.exists(TOOL), f'{TOOL} does not exist')
        return subprocess.run(['node', TOOL, self.url, out], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=150, cwd=SC)

    def test_tab_works_dead_link_does_nothing(self):
        out = os.path.join(OUT, 'click-test.json')
        if os.path.exists(out):
            open(out, 'w').close()   # stale from an earlier run
        r = self.run_tool(out)
        self.assertEqual(r.returncode, 0, r.stdout[-1000:] + r.stderr[-2000:])
        with open(out) as f:
            res = json.load(f)
        by = {x['text']: x for x in res['results']}
        self.assertIn('Prices tab', by, res)
        self.assertIn('Read more dead link', by, res)
        self.assertEqual(by['Prices tab']['verdict'], 'works', by['Prices tab'])
        self.assertEqual(by['Read more dead link']['verdict'], 'does nothing', by['Read more dead link'])

    def test_read_only_skips_risky_elements(self):  # owner safety rule 2026-10-14: never submit or change a site
        out = os.path.join(OUT, 'click-test-safety.json')
        if os.path.exists(out):
            open(out, 'w').close()
        SIDE_EFFECTS.clear()
        r = self.run_tool(out)
        self.assertEqual(r.returncode, 0, r.stdout[-1000:] + r.stderr[-2000:])
        with open(out) as f:
            res = json.load(f)
        tested = {x['text'] for x in res['results']}
        skipped = {x['text']: x.get('why', '') for x in res['not_tested']}
        for t in ('Form helper link', 'Pick a color', 'Next', 'Delete my account', 'Subscribe', 'Sign in'):
            self.assertNotIn(t, tested, f'{t} was clicked')
            self.assertIn(t, skipped, res['not_tested'])
            self.assertIn('read-only', skipped[t])
        self.assertEqual(SIDE_EFFECTS, [], 'the tool triggered a side effect')
        self.assertIn('Prices tab', tested)

    def test_refuses_tmp_output(self):  # brief: evidence lives in the case folder, never /tmp
        self.assertTrue(os.path.exists(TOOL), f'{TOOL} does not exist')
        r = subprocess.run(['node', TOOL, self.url, '/tmp/click-test-should-not-exist.json'], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=30, cwd=SC)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists('/tmp/click-test-should-not-exist.json'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
