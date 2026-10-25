#!/usr/bin/env python3
"""Tests for onpage/fetch.py and the onpage.sh exit codes, against a local server (no real site).
Run: python3 tests/test_onpage_fetch.py   (needs the kit's Chrome for the rendered cases)

Cases: static page, client-rendered page (JavaScript writes the text), 403, a bot-check page with
status 200, robots.txt refusal, a PDF, a redirect, a 500. Then the CLI end to end: all pages fine
(exit 0, files written, result matches its schema), one competitor blocked (exit 6, PARTIAL named),
your page blocked (exit 4), bad arguments (exit 2).
Control: the client-rendered page measured with --raw must show (almost) no words, proving the
rendered case really depends on the browser."""
import http.server, json, os, socketserver, subprocess, sys, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(HERE, '_out', 'onpage')
VPY = os.path.join(ROOT, '.venv', 'bin', 'python')
PY = VPY if os.path.exists(VPY) else sys.executable
os.environ['CRAWL_DELAY'] = '0.05'
sys.path.insert(0, os.path.join(ROOT, 'onpage'))
sys.path.insert(0, ROOT)
import fetch as F  # noqa: E402
import schemacheck  # noqa: E402

WORDS = ' '.join(['Stump removal is quick work for a crew with a grinder.'] * 20)
STATIC = f"""<!doctype html><html><head><meta charset=utf-8><title>Stump Removal Experts</title>
<meta name=description content="Fast stump removal."></head><body><h1>Stump Removal</h1><h2>Stump grinding</h2>
<p>{WORDS}</p><a href="/about">About</a><a href="https://facebook.com/stumpco">Facebook</a></body></html>"""
CSR = """<!doctype html><html><head><title>App</title></head><body><div id=root></div>
<script>document.getElementById('root').innerHTML='<h1>Stump Removal</h1><p>' + 'Stump removal by the hour. '.repeat(80) + '</p>';</script></body></html>"""
CHALLENGE = "<!doctype html><html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"
ROBOTS = "User-agent: *\nDisallow: /private\n"
# fixtures for the fetch and render rules
import collections, time, glob, signal  # noqa: E402
HITS = collections.Counter()
REAL = f"<!doctype html><html><head><title>Real page</title></head><body><h1>Real</h1><p>{WORDS}</p></body></html>"
SELF_SOLVE = ("<!doctype html><html><head><title>Just a moment...</title></head><body>Checking your browser"
              "<script>setTimeout(()=>{document.cookie='passed=1';location.reload()},800)</script></body></html>")
SG_CHECK = ("<!doctype html><html><head><title>Loading</title></head><body>one moment"
            "<script>setTimeout(()=>{document.cookie='passed=1';location.href='/sgc'},500)</script></body></html>")
CHTITLE = f"<!doctype html><html><head><title>How to Challenge a DUI Breath Test | Smith Law</title></head><body><h1>Challenge a breath test</h1><p>{WORDS}</p></body></html>"
BUSY = f"""<!doctype html><html><head><title>Busy</title></head><body><p>{WORDS}</p>
<script>function lp(){{fetch('/slow?'+Math.random()).finally(lp)}};lp();lp();lp();</script></body></html>"""
LAZY = """<!doctype html><html><head><title>Lazy</title></head><body><p>top words</p><div style="height:5000px"></div><div id="s"></div>
<script>new IntersectionObserver((e,o)=>{if(e[0].isIntersecting){o.disconnect();fetch('/slowfrag').then(r=>r.text()).then(t=>document.getElementById('s').innerHTML=t)}}).observe(document.getElementById('s'))</script></body></html>"""
SHADOW = """<!doctype html><html><head><title>Shadow</title></head><body><p>light words here</p><x-box><span>slotted lightword</span></x-box>
<script>customElements.define('x-in',class extends HTMLElement{constructor(){super();this.attachShadow({mode:'open'}).innerHTML='<p>nestedshadowword</p>'}});
customElements.define('x-box',class extends HTMLElement{constructor(){super();this.attachShadow({mode:'open'}).innerHTML='<p>'+Array.from({length:80},(_, i)=>'shadowword'+i).join(' ')+'</p><slot></slot><x-in></x-in>'}})</script></body></html>"""
HANG = "<!doctype html><html><head><title>Hang</title></head><body><p>words</p><script>window.addEventListener('load',()=>setTimeout(()=>{while(true){}},100))</script></body></html>"
HEAVY = f"""<!doctype html><html><head><meta name=viewport content="width=device-width"><title>Heavy</title><link rel=preload as=font href=/f.woff2 crossorigin>
<style>@font-face{{font-family:X;src:url(/f2.woff2)}} body{{font-family:X}}</style>
<script src="http://www.google-analytics.com/analytics.js"></script></head><body><p>{WORDS}</p>
<img src=/i.png><video src=/v.mp4 autoplay muted></video>
<script>document.body.insertAdjacentHTML('beforeend','<p id=vw>viewport'+innerWidth+'</p>')</script></body></html>"""
SECTITLE = f"<!doctype html><html><head><title>Security Check Services for Small Offices | Acme</title></head><body><h1>Security checks</h1><p>{WORDS}</p></body></html>"
SOFT404 = "<!doctype html><html><head><title>404 Page not found</title></head><body><h1>Oops</h1><p>We could not find that page. Try the home page.</p></body></html>"
FIX404 = f"<!doctype html><html><head><title>How to Fix a 404 Not Found Error</title></head><body><h1>Fixing 404 not found errors</h1><p>{WORDS} {WORDS} {WORDS}</p></body></html>"
# The firewall's plain block page: it refuses the bot name and offers nothing to solve (a refusal, not a check).
CFBLOCK = ("<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title></head><body><div id=cf-wrapper>"
           "<div id=cf-error-details><h1>Sorry, you have been blocked</h1><h2>You are unable to access example.com</h2>"
           "<h2>Why have I been blocked?</h2><p>This website is using a security service to protect itself from online attacks."
           "</p></div></div></body></html>")
UAS = collections.defaultdict(list)   # path -> user agents seen
UAV = ("<!doctype html><html><head><title>Variant</title></head><body><p>{v}</p><p id=js></p><p>" + WORDS + "</p>"
       "<script>document.getElementById('js').textContent=/Mobi|Android/.test(navigator.userAgent)?'JSMOBILE':'JSDESKTOP'</script></body></html>")
SLOT = """<!doctype html><html><head><title>Slot</title></head><body><x-card><span>LIGHTTEXT stump removal</span></x-card>
<script>customElements.define('x-card',class extends HTMLElement{constructor(){super();this.attachShadow({mode:'open'}).innerHTML='<h2>SHADOWHEAD stump removal</h2><slot>FALLBACK stump removal</slot>'}})</script></body></html>"""
SLOWIMG = f"<!doctype html><html><head><title>Slow image</title></head><body><p>slow image page words here</p><img src=/slowimg.png><p>{WORDS}</p></body></html>"
BIGHEAD = ("<!doctype html><html><head><title>Phone repair</title><script>var msg='Please verify you are human';</script><style>"
           + ".c{color:red}\n" * 3000 + "</style></head><body><h1>Phone repair</h1><p>"
           + " ".join(["Screens and batteries for every phone model."] * 200) + "</p></body></html>")
SPA = f"<!doctype html><html><head><title>Home</title></head><body><h1>Home</h1><p>{WORDS}</p><script>history.replaceState(null,'','/other-path')</script></body></html>"
JSREDIR = "<!doctype html><html><head><title>Go</title></head><body><p>moving</p><script>location.href='/static'</script></body></html>"
JSOFF = "<!doctype html><html><head><title>Go</title></head><body><p>moving</p><script>location.href='TARGET'</script></body></html>"
# Chrome's raw words when it cannot start as a non-root user in a container (seen in a user test)
ZYGOTE = ("Failed to launch the browser process!  Code: null\n\nstderr:\n[1324:1324:1024/210527.460968:FATAL:"
          "content/browser/zygote_host/zygote_host_impl_linux.cc(132)] No usable sandbox!")
CAFE = ("<!doctype html><html><head><meta charset=windows-1252><title>Café crème</title></head><body><h1>Café crème</h1><p>"
        + WORDS + "</p></body></html>").encode('cp1252')
SJIS = ("<!doctype html><html><head><title>歯科 東京</title></head><body><h1>歯科 東京</h1><p>"
        + WORDS + "</p></body></html>").encode('shift_jis')


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='text/html; charset=utf-8', extra=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path
        HITS[p.split('?')[0]] += 1
        bot = 'GrokBot' in (self.headers.get('User-Agent') or '')
        chrome = bool(self.headers.get('Sec-Fetch-Mode'))   # urllib never sends it
        passed = 'passed=1' in (self.headers.get('Cookie') or '')
        if p == '/reset':            # Chrome's connection is dropped, urllib gets the page (WAF style)
            if chrome:
                self.close_connection = True
                self.connection.shutdown(2)
                return
            return self._send(200, STATIC)
        if p == '/jsc2':             # a self-solving check served 200 to everyone
            return self._send(200, REAL if passed else SELF_SOLVE)
        if p == '/jsc3':             # the bot's urllib read gets the page; Chrome gets a self-solving check
            return self._send(200, REAL if (passed or not chrome) else SELF_SOLVE)
        if p == '/sgc':              # Chrome is sent through a check URL that solves itself and comes back
            if chrome and not passed:
                return self._send(302, '', extra={'Location': '/.well-known/sgcaptcha/?r=%2Fsgc'})
            return self._send(200, REAL)
        if p.startswith('/.well-known/sgcaptcha'):
            return self._send(200, SG_CHECK)
        if p == '/chtitle':
            return self._send(200, CHTITLE)
        if p == '/busy':
            return self._send(200, BUSY)
        if p.startswith('/slow?') or p == '/slow':
            time.sleep(4)
            return self._send(200, 'ok', 'text/plain')
        if p == '/lazy':
            return self._send(200, LAZY)
        if p == '/slowfrag':
            time.sleep(3)
            return self._send(200, '<p>' + ' '.join(f'lazyword{i}' for i in range(60)) + '</p>')
        if p == '/shadow':
            return self._send(200, SHADOW)
        if p == '/hang':
            return self._send(200, HANG)
        if p == '/heavy':
            return self._send(200, HEAVY)
        if p in ('/f.woff2', '/f2.woff2'):
            return self._send(200, b'wOF2', 'font/woff2')
        if p == '/v.mp4':
            return self._send(200, b'\x00' * 64, 'video/mp4')
        if p == '/i.png':
            return self._send(200, b'\x89PNG\r\n', 'image/png')
        if p == '/gone':             # a removed page sent to the home page
            return self._send(301, '', extra={'Location': '/'})
        if p == '/Trail':            # same page: case + trailing slash only
            return self._send(301, '', extra={'Location': '/trail/?utm=x'})
        if p.startswith('/trail/'):
            return self._send(200, STATIC)
        if p == '/sectitle':
            return self._send(200, SECTITLE)
        if p == '/soft404':
            return self._send(200, SOFT404)
        if p == '/fix404':
            return self._send(200, FIX404)
        if p == '/botwall':          # refuses the bot name, serves a normal browser
            return self._send(403, '<html><body>no bots</body></html>') if bot else self._send(200, STATIC)
        if p == '/botwall-challenge':  # refuses the bot, gives a browser a challenge page: never bypassed
            return self._send(403, '<html><body>no bots</body></html>') if bot else self._send(200, CHALLENGE)
        if p == '/denied':  # an "Access Denied" block page for the bot name (WAF style), the page for a browser
            return self._send(403, '<html><head><title>Access Denied</title></head><body>denied</body></html>') if bot else self._send(200, STATIC)
        UAS[p.split('?')[0]].append(self.headers.get('User-Agent') or '')
        if p == '/cfblock':          # the firewall's block page for the bot name, the page for a browser
            return self._send(403, CFBLOCK) if bot else self._send(200, STATIC)
        if p == '/chrome403':        # the plain request gets the page; the browser visit is refused
            return self._send(403, '<html><head><title>Access Denied</title></head><body>denied</body></html>') if chrome else self._send(200, STATIC)
        if p == '/uav':              # picks phone or desktop HTML by user agent (dynamic serving)
            ua = self.headers.get('User-Agent') or ''
            return self._send(200, UAV.format(v='MOBILEVARIANT' if 'Mobile' in ua else 'DESKTOPVARIANT'))
        if p == '/slot':
            return self._send(200, SLOT)
        if p == '/slowimg':
            return self._send(200, SLOWIMG)
        if p == '/slowimg.png':
            time.sleep(20)
            return self._send(200, b'\x89PNG\r\n', 'image/png')
        if p == '/bighead':
            return self._send(200, BIGHEAD)
        if p == '/spa':
            return self._send(200, SPA)
        if p == '/jsredir':
            return self._send(200, JSREDIR)
        if p == '/cp1252':           # no charset in the header: the <meta charset> decides
            return self._send(200, CAFE, 'text/html')
        if p == '/cp1252gz':         # the same bytes, gzip-compressed
            import gzip
            return self._send(200, gzip.compress(CAFE), 'text/html', {'Content-Encoding': 'gzip'})
        if p == '/sjis':             # the charset only in the header
            return self._send(200, SJIS, 'text/html; charset=Shift_JIS')
        if p == '/robots.txt':
            return self._send(200, ROBOTS, 'text/plain')
        if p in ('/', '/static', '/about'):
            return self._send(200, STATIC)
        if p == '/csr':
            return self._send(200, CSR)
        if p == '/forbidden':
            return self._send(403, '<html><body>Forbidden</body></html>')
        if p == '/challenge':
            return self._send(200, CHALLENGE)
        if p == '/private':
            return self._send(200, STATIC)
        if p == '/file.pdf':
            return self._send(200, b'%PDF-1.4 not a page', 'application/pdf')
        home = self.server.server_address[0] == '127.0.0.1'   # 127.0.0.2 stands for ANOTHER website
        if p == '/offsite':          # a 307 to the same path on another website (one.com/ -> other.com/)
            return self._send(307, '', extra={'Location': url(1, '/offsite')}) if home else self._send(200, STATIC)
        if p == '/offwall':          # to another website that refuses the bot: not this page's block, no retry
            return self._send(302, '', extra={'Location': url(1, '/offwall')}) if home else self._send(403, '<html><body>Forbidden</body></html>')
        if p == '/jsoff':            # a script sends the browser to the same path on another website
            return self._send(200, JSOFF.replace('TARGET', url(1, '/jsoff'))) if home else self._send(200, STATIC)
        if p == '/empty200':         # 200 with no body at all
            return self._send(200, b'')
        if p == '/moved':
            return self._send(301, '', extra={'Location': '/static'})
        if p == '/boom':
            return self._send(500, '<html><body>error</body></html>')
        return self._send(404, '<html><body>no</body></html>')


class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


SERVERS = []


def setUpModule():
    os.makedirs(OUT, exist_ok=True)
    for host in ('127.0.0.1', '127.0.0.2', '127.0.0.3', '127.0.0.4'):
        s = TS((host, 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        SERVERS.append(s)


def tearDownModule():
    for s in SERVERS:
        s.shutdown()
        s.server_close()


def url(i, path):
    h, p = SERVERS[i].server_address
    return f'http://{h}:{p}{path}'


class Fetch(unittest.TestCase):
    def test_static_rendered(self):
        r = F.fetch(url(0, '/static'))
        self.assertEqual(r['mode_used'], 'rendered', r['error'])
        self.assertEqual(r['status'], 200)
        self.assertGreater(r['rendered_words'], 150)
        self.assertFalse(r['js_dependent'])

    def test_csr_rendered_has_words(self):
        r = F.fetch(url(0, '/csr'))
        self.assertEqual(r['mode_used'], 'rendered', r['error'])
        self.assertGreater(r['rendered_words'], 200)
        self.assertTrue(r['js_dependent'])

    def test_control_csr_raw_is_empty(self):
        r = F.fetch(url(0, '/csr'), mode='raw')
        self.assertEqual(r['mode_used'], 'raw')
        self.assertLess(r['raw_words'], 5, 'control: the CSR fixture has text in its server HTML; it no longer tests rendering')

    def test_403_blocked(self):
        r = F.fetch(url(1, '/forbidden'))
        self.assertTrue(r['blocked'])
        self.assertIsNone(r['html'])

    def test_challenge_blocked(self):
        r = F.fetch(url(1, '/challenge'))
        self.assertTrue(r['blocked'], r)
        self.assertIsNone(r['html'])

    def test_robots_refusal_not_fetched(self):
        r = F.fetch(url(1, '/private'))
        self.assertIsNone(r['html'])
        self.assertIn('robots', r['error'])

    def test_pdf_not_a_page(self):
        r = F.fetch(url(2, '/file.pdf'))
        self.assertIsNone(r['html'])
        self.assertIn('not a web page', r['error'])

    def test_redirect_followed(self):
        r = F.fetch(url(2, '/moved'), mode='raw')
        self.assertEqual(r['status'], 200)
        self.assertTrue(r['final_url'].endswith('/static'))

    def test_server_error_not_measured(self):
        r = F.fetch(url(3, '/boom'))
        self.assertIsNone(r['html'])


class BrowserRetry(unittest.TestCase):
    """Own page retried as a browser; competitors only on the user's yes;
    saved files accepted; challenge pages never bypassed."""

    def test_control_wall_refuses_bot(self):
        r = F.fetch(url(0, '/botwall'))
        self.assertTrue(r['blocked'], 'control: the bot wall no longer refuses the bot name')
        self.assertIsNone(r['html'])

    def test_retry_reads_as_browser(self):
        r = F.fetch(url(0, '/botwall'), browser_retry=True)
        self.assertEqual(r['user_agent'], 'browser', r)
        self.assertGreater(r['rendered_words'], 150)
        self.assertIn('normal Chrome browser', r['note'])

    def test_access_denied_page_is_a_refusal(self):
        self.assertTrue(F.fetch(url(0, '/denied'))['blocked'])
        r = F.fetch(url(0, '/denied'), browser_retry=True)
        self.assertEqual(r['user_agent'], 'browser', r)
        self.assertTrue(r['html'])

    def test_challenge_never_bypassed(self):
        r = F.fetch(url(0, '/botwall-challenge'), browser_retry=True)
        self.assertTrue(r['blocked'], r)
        self.assertIsNone(r['html'])

    def test_saved_file(self):
        f = os.path.join(OUT, 'saved.html')
        with open(f, 'w') as fh:
            fh.write(STATIC)
        r = F.read_saved('https://blocked.example/page', f)
        self.assertEqual((r['source'], r['mode_used']), ('saved-file', 'saved'))
        self.assertTrue(r['html'])
        g = os.path.join(OUT, 'not-a-page.txt')
        with open(g, 'w') as fh:
            fh.write('just some notes')
        self.assertIn('not a web page', F.read_saved('https://x.example/', g)['error'])
        c = os.path.join(OUT, 'challenge.html')
        with open(c, 'w') as fh:
            fh.write(CHALLENGE)
        self.assertTrue(F.read_saved('https://x.example/', c)['blocked'])


class FetchRules(unittest.TestCase):
    """Error pages, bot checks, load waits, saved HTML, shadow DOM, sandbox, cleanup, blocking, viewport,
    messages, redirects to another page and soft 404s."""

    def test_chrome_error_page_never_measured(self):
        r = F.fetch(url(0, '/reset'))
        self.assertNotIn('chrome-error', str(r['final_url']), r)
        self.assertEqual(r['mode_used'], 'raw', 'Chrome failed: the server HTML is measured and the note says so')
        self.assertEqual(r['note'], 'measured without a browser: the browser could not load the page', r)
        self.assertIsNone(r['error'], 'the page WAS measured: no error')
        self.assertIn('net::ERR_', ' '.join(r['browser_log'] or []), 'the raw words stay for run.log')
        self.assertEqual(r['status'], 200)

    def test_self_solving_check_blocked(self):
        r = F.fetch(url(1, '/jsc2'))
        self.assertTrue(r['blocked'], r)
        self.assertIsNone(r['html'])
        self.assertIsNone(r['js_dependent'])

    def test_self_solving_check_blocked_raw_mode(self):
        r = F.fetch(url(1, '/jsc2'), mode='raw')
        self.assertTrue(r['blocked'], r)
        self.assertIsNone(r['html'])

    def test_check_seen_only_by_chrome_blocked(self):
        r = F.fetch(url(1, '/jsc3'))
        self.assertTrue(r['blocked'], r)
        self.assertIsNone(r['html'])
        self.assertTrue(any(st.get('check') for st in r.get('chain') or []), r.get('chain'))

    def test_check_url_in_redirect_chain_blocked(self):
        r = F.fetch(url(1, '/sgc'))
        self.assertTrue(r['blocked'], r)
        self.assertIsNone(r['html'])

    def test_never_bypassed_with_browser_retry(self):
        r = F.fetch(url(1, '/jsc3'), browser_retry=True)
        self.assertTrue(r['blocked'], r)
        self.assertIsNone(r['html'])

    def test_title_word_challenge_is_not_a_check(self):
        r = F.fetch(url(2, '/chtitle'))
        self.assertFalse(r['blocked'], r)
        self.assertGreater(r['rendered_words'] or 0, 150)
        f = os.path.join(OUT, 'chtitle.html')
        with open(f, 'w') as fh:
            fh.write(CHTITLE)
        self.assertFalse(F.read_saved('https://x.example/', f)['blocked'])

    def test_check_title_word_on_a_long_page_is_a_page(self):
        r = F.fetch(url(2, '/sectitle'))
        self.assertFalse(r['blocked'], r)
        self.assertTrue(r['html'])

    def test_render_ok_rejects_no_status_and_chrome_error(self):
        good = {'ok': True, 'html': '<p>x</p>', 'status': 200, 'final_url': 'http://a.example/'}
        self.assertTrue(F._render_ok(good))
        self.assertFalse(F._render_ok(dict(good, status=None)))
        self.assertFalse(F._render_ok(dict(good, final_url='chrome-error://chromewebdata/')))

    def test_one_load_no_30s_wait(self):
        HITS.clear()
        t = time.time()
        r = F.fetch(url(2, '/busy'))
        self.assertEqual(r['mode_used'], 'rendered', r)
        self.assertEqual(HITS['/busy'], 2, 'one urllib read + ONE Chrome load (never a second navigation)')
        self.assertLess(time.time() - t, 25)

    def test_lazy_content_after_scroll(self):
        r = F.fetch(url(2, '/lazy'))
        self.assertIn('lazyword59', r['html'] or '', r['error'])

    def test_save_to(self):
        d = os.path.join(OUT, 'saved-pages', '1-competitor')
        for f in glob.glob(os.path.join(d, '*.html')):
            os.remove(f)
        r = F.fetch(url(2, '/csr'), save_to=d)
        with open(os.path.join(d, 'server.html'), encoding='utf-8') as fh:
            self.assertIn("getElementById('root')", fh.read())
        with open(os.path.join(d, 'browser.html'), encoding='utf-8') as fh:
            self.assertEqual(fh.read(), r['html'])
        self.assertEqual(len(r['saved']['browser.html']['sha256']), 64)

    def test_shadow_dom_text_captured(self):
        r = F.fetch(url(3, '/shadow'))
        self.assertIn('shadowword79', r['html'] or '', r['error'])
        self.assertIn('nestedshadowword', r['html'])
        self.assertEqual(r['html'].count('slotted lightword'), 1, 'slotted light text is counted once')
        self.assertGreater(r['rendered_words'], 80)

    def test_sandbox_on_unless_root(self):
        def args(uid, env=''):
            js = (f"const r=require({json.dumps(os.path.join(ROOT, 'onpage', 'render.js'))});"
                  f"process.stdout.write(JSON.stringify(r.chromeArgs({uid},{{{env}}})))")
            return json.loads(subprocess.run([F._kit_node(), '-e', js], capture_output=True, text=True, timeout=30).stdout)
        self.assertNotIn('--no-sandbox', args(1000))
        self.assertIn('--no-sandbox', args(0))
        self.assertIn('--no-sandbox', args(1000, 'RENDER_NO_SANDBOX:"1"'))
        self.assertNotIn('--no-sandbox', args(0, 'RENDER_NO_SANDBOX:"0"'), '"0" asks for the sandbox even as root')
        r = F.fetch(url(3, '/static'))
        if os.getuid() == 0:
            self.assertIs(r['sandbox'], False)
        else:   # with the sandbox, or (a container without user namespaces) one start without it
            self.assertTrue(r['sandbox'] or r['sandbox_retry'], r)

    def test_profile_removed_after_hard_timeout(self):
        d = os.path.join(OUT, 'hang')
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):
            if f.endswith('.json'):
                os.remove(os.path.join(d, f))
        before = set(glob.glob('/tmp/puppeteer_dev_chrome_profile-*'))
        out = os.path.join(d, 'r.json')
        p = subprocess.run([F._kit_node(), os.path.join(ROOT, 'onpage', 'render.js'), url(3, '/hang'), out],
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, RENDER_HARD_MS='6000'))
        self.assertEqual(p.returncode, 5)
        with open(out) as fh:
            self.assertIn('hard timeout', json.load(fh)['error'])
        self.assertEqual([f for f in os.listdir(d) if f != 'r.json'], [], 'profile dir left behind')
        self.assertEqual(set(glob.glob('/tmp/puppeteer_dev_chrome_profile-*')) - before, set())

    def test_python_timeout_kills_node_and_chrome(self):
        import tempfile
        tag = os.path.join(OUT, f'pykill-{os.getpid()}')   # a folder only this test uses
        os.makedirs(tag, exist_ok=True)
        old, old_tmp = F.RENDER_TIMEOUT_S, tempfile.tempdir
        F.RENDER_TIMEOUT_S, tempfile.tempdir = 8, tag
        os.environ['RENDER_HARD_MS'] = '300000'
        try:
            t = time.time()
            r = F._render(url(3, '/hang'))
        finally:
            F.RENDER_TIMEOUT_S, tempfile.tempdir = old, old_tmp
            del os.environ['RENDER_HARD_MS']
        self.assertFalse(r['ok'])
        self.assertLess(time.time() - t, 20)
        time.sleep(1)
        left = subprocess.run(['pgrep', '-f', tag], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [], 'node or Chrome left running after the Python timeout')
        self.assertEqual(os.listdir(tag), [], 'profile folder left behind')
        os.rmdir(tag)

    def test_block_media_fonts_analytics_keep_images(self):
        HITS.clear()
        r = F.fetch(url(3, '/heavy'))
        self.assertEqual(r['mode_used'], 'rendered', r)
        self.assertEqual(HITS['/f.woff2'] + HITS['/f2.woff2'] + HITS['/v.mp4'], 0, dict(HITS))
        self.assertEqual(HITS['/i.png'], 1)
        self.assertGreaterEqual(r['blocked_requests'], 1)

    def test_mobile_viewport_recorded(self):
        r = F.fetch(url(3, '/heavy'))
        self.assertIn('viewport412', r['html'])
        self.assertEqual(r['viewport'], {'width': 412, 'height': 915, 'mobile': True})
        self.assertTrue(r['final_url'].endswith('/heavy'))

    def test_messages(self):
        r = F.fetch(url(0, '/botwall-challenge'), browser_retry=True)
        self.assertIn('bot check', r['error'])
        self.assertNotIn('answered 200', r['error'])
        r = F.fetch(url(0, '/botwall'), mode='raw', browser_retry=True)
        self.assertIn('Chrome', r['note'])
        self.assertIn('--raw', r['note'])

    def test_redirect_to_another_page_not_measured(self):
        for mode in ('raw', 'rendered'):
            r = F.fetch(url(1, '/gone'), mode=mode)
            self.assertTrue(r['redirected_to_other_page'], r)
            self.assertIsNone(r['html'])
            self.assertIn('redirect', r['error'])
        r = F.fetch(url(1, '/Trail'))
        self.assertFalse(r['redirected_to_other_page'], r)
        self.assertTrue(r['html'])

    def test_soft_404_not_measured(self):
        r = F.fetch(url(1, '/soft404'))
        self.assertTrue(r['soft_404'], r)
        self.assertIsNone(r['html'])
        self.assertIn('not found', r['error'])
        r = F.fetch(url(1, '/fix404'))
        self.assertFalse(r['soft_404'], 'a long article about 404 errors is a real page')
        self.assertTrue(r['html'])


class SandboxAndOtherSite(unittest.TestCase):
    """Chrome without its sandbox only when the sandbox cannot start; another website is never measured;
    Chrome's raw error words never reach error or note."""

    def node(self, js):
        r = os.path.join(ROOT, 'onpage', 'render.js')
        p = subprocess.run([F._kit_node(), '-e', f"const R=require({json.dumps(r)});(async()=>{{{js}}})()"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_sandbox_failure_words(self):
        out = self.node("process.stdout.write(JSON.stringify([" + ",".join(
            f"R.sandboxFailure({json.dumps(m)})" for m in (
                ZYGOTE, 'Failed to move to new namespace: PID namespaces supported, errno = Operation not permitted',
                'stderr: [1:1:1024/210527.460968:FATAL:content/browser/zygote_host/zygote_host_impl_linux.cc(132)]',
                'Running as root without --no-sandbox is not supported. See https://crbug.com/638180.',
                'net::ERR_NAME_NOT_RESOLVED', 'Timed out after 30000 ms while waiting for the WS endpoint')) + "]))")
        self.assertEqual(out, [True, True, True, True, False, False])

    def test_launch_retries_once_without_sandbox(self):
        js = """
          const calls=[]; const fail=(m)=>async(a)=>{calls.push(a); if(!a.includes('--no-sandbox')) throw new Error(m); return 'B';};
          const res={};
          res.a=await R.launchChrome(fail('No usable sandbox!'), ['--x'], async()=>calls.push('reset'));
          res.calls=calls.slice();
          try { await R.launchChrome(async(a)=>{calls.push(a); throw new Error('net::ERR_X')}, ['--x']); } catch(e){ res.other=String(e.message); }
          let tries=0;
          try { await R.launchChrome(async(a)=>{tries++; throw new Error('No usable sandbox!')}, ['--no-sandbox','--x']); } catch(e){ res.again=String(e.message); }
          res.tries=tries;
          res.n=calls.length;
          res.ok=await R.launchChrome(async(a)=>'B', ['--x']);
          process.stdout.write(JSON.stringify(res));"""
        out = self.node(js)
        self.assertEqual(out['a'], {'browser': 'B', 'sandbox': False, 'retried': True, 'first_error': 'No usable sandbox!'})
        self.assertEqual(out['calls'], [['--x'], 'reset', ['--no-sandbox', '--x']], 'one retry, on a fresh profile')
        self.assertEqual(out['other'], 'net::ERR_X', 'another failure is not retried')
        self.assertEqual(out['n'], 4, 'a non-sandbox failure is tried once')
        self.assertEqual(out['again'], 'No usable sandbox!', 'already without the sandbox: nothing to retry')
        self.assertEqual(out['tries'], 1, 'already without the sandbox: started once')
        self.assertEqual(out['ok'], {'browser': 'B', 'sandbox': True, 'retried': False, 'first_error': None})

    @unittest.skipUnless(os.getuid() == 0, 'as root, RENDER_NO_SANDBOX=0 makes Chrome refuse its first start')
    def test_real_chrome_falls_back_without_sandbox(self):
        os.environ['RENDER_NO_SANDBOX'] = '0'
        try:
            r = F.fetch(url(3, '/static'))
        finally:
            del os.environ['RENDER_NO_SANDBOX']
        self.assertEqual(r['mode_used'], 'rendered', r.get('browser_log'))
        self.assertIs(r['sandbox'], False)
        self.assertIs(r['sandbox_retry'], True)
        self.assertIn('sandbox', ' '.join(r['browser_log']))
        self.assertIsNone(r['note'])
        self.assertIsNone(r['error'])

    def test_sandbox_retry_only_when_needed(self):
        r = F.fetch(url(3, '/static'))
        self.assertEqual(r['mode_used'], 'rendered', r.get('browser_log'))
        # root: no sandbox from the start, so nothing to retry; non-root: a retry only when the sandbox failed
        self.assertIs(r['sandbox_retry'], r['sandbox'] is False and os.getuid() != 0, r)
        if not r['sandbox_retry']:
            self.assertIsNone(r['browser_log'])

    def test_browser_reason_is_plain(self):
        for raw, want in ((ZYGOTE, 'Chrome did not start'), ('net::ERR_BLOCKED_BY_CLIENT at http://a.example/', 'the browser could not load the page'),
                          ('hard timeout 75 s', 'the browser took too long'), ('render did not finish in 110 s (stopped)', 'the browser took too long'),
                          ('no Chrome found', 'Chrome was not found'), ('the browser got HTTP 503', 'the browser got HTTP 503'),
                          ('Chrome error page (chrome-error://chromewebdata/)', 'the browser could not load the page'),
                          ('could not read the page DOM', 'the browser could not read the page'),
                          (None, 'the browser gave no answer'), ('something odd', 'the browser failed')):
            self.assertEqual(F.browser_reason(raw), want, raw)

    def test_chrome_crash_words_never_in_error_or_note(self):
        old = F._render
        F._render = lambda u, as_browser=False: {'ok': False, 'error': ZYGOTE, 'sandbox': False, 'sandbox_retry': True,
                                                 'log': ['Chrome did not start with its sandbox; started again without it. First error: X']}
        try:
            r = F.fetch(url(3, '/static'))
            w = F.fetch(url(3, '/botwall'), browser_retry=True)
            z = F.fetch(url(3, '/forbidden-nope'))
            e = F.fetch(url(3, '/empty200'))
            F._render = lambda u, as_browser=False: {'ok': True, 'status': 200, 'final_url': u, 'html': STATIC,
                                                     'error': 'html truncated at 8 MB'}
            cut = F.fetch(url(3, '/static'))
        finally:
            F._render = old
        self.assertEqual(z['error'], 'HTTP 404')
        self.assertIs(z['redirected_to_other_page'], False, 'not measured for another reason: no redirect claimed')
        self.assertIs(z['redirected_to_other_site'], False)
        self.assertIs(z['soft_404'], False)
        self.assertEqual(e['error'], 'not measured: Chrome did not start, and the server HTML was not usable')
        self.assertIsNone(cut['browser_log'], 'a DOM Chrome did produce is not a browser failure')
        self.assertIn('incomplete', cut['note'])
        self.assertIs(w['blocked'], True)
        self.assertEqual(w['block_kind'], 'refusal')
        self.assertEqual(r['mode_used'], 'raw')
        self.assertEqual(r['note'], 'measured without a browser: Chrome did not start')
        self.assertIsNone(r['error'])
        self.assertIs(r['sandbox_retry'], True)
        self.assertIn('zygote_host', ' '.join(r['browser_log']), 'raw words kept for run.log')
        self.assertIn('First error', ' '.join(r['browser_log']))
        self.assertIn('as a normal browser also failed (Chrome did not start)', w['error'])
        for rec in (r, w, z):
            shown = f"{rec['error']} {rec['note']}"
            for raw in ('zygote', 'stderr', 'Code: null', 'FATAL', 'Failed to launch'):
                self.assertNotIn(raw, shown, rec)

    def test_site_of_and_other_site(self):
        same = (('https://www.sdplumbing.com/', 'http://sdplumbing.com/'), ('http://x.com/a', 'https://www.x.com/a/'),
                ('https://x.co.uk/', 'https://shop.x.co.uk/'), ('https://austin.brand.com/', 'https://brand.com/'),
                ('https://a.example/', None), ('http://127.0.0.1:8/', 'http://127.0.0.1:8/b'))
        for a, b in same:
            self.assertFalse(F.other_site(a, b), (a, b))
        diff = (('https://www.sdplumbing.com/', 'http://sdair.com/'), ('https://a.co.uk/', 'https://b.co.uk/'),
                ('https://x.com/', 'https://x.net/'), ('http://127.0.0.1:8/', 'http://127.0.0.2:8/'),
                ('https://plumber.com/', 'https://notplumber.com/'))
        for a, b in diff:
            self.assertTrue(F.other_site(a, b), (a, b))
        self.assertEqual(F.site_of('WWW.Shop.Example.COM.'), 'example.com')
        self.assertEqual(F.site_of('127.0.0.9'), '127.0.0.9', 'an IP address stays whole')
        self.assertEqual(F.site_of('::1'), '::1')
        self.assertEqual(F.site_of(''), '')
        self.assertIs(F.other_site('https://a.example/', None), False)
        self.assertIs(F.other_site(None, 'https://a.example/'), False)
        self.assertEqual(F.site_of('beef.cafe.example.com.au'), 'example.com.au')

    def test_redirect_to_another_site_not_measured(self):
        for mode in ('raw', 'rendered'):
            HITS.clear()
            r = F.fetch(url(0, '/offsite'), mode=mode)
            self.assertIsNone(r['html'], 'another website is never measured as this competitor')
            self.assertTrue(r['redirected_to_other_site'], r)
            self.assertIsNone(r['mode_used'])
            self.assertIn('another website', r['error'])
            self.assertIn(url(1, '/offsite'), r['error'], 'the error says where it went')
            self.assertEqual(HITS['/offsite'], 2, 'one plain read (two hops); no browser visit of another site')

    def test_block_on_the_other_site_not_retried(self):
        HITS.clear()
        r = F.fetch(url(0, '/offwall'), browser_retry=True)
        self.assertEqual(HITS['/offwall'], 2, 'one plain read (two hops); never read again as a browser')
        self.assertTrue(r['redirected_to_other_site'], r)
        self.assertFalse(r['blocked'])
        self.assertIsNone(r['block_kind'])
        self.assertIn(url(1, '/offwall'), r['error'])

    def test_script_redirect_to_another_site_not_measured(self):
        r = F.fetch(url(0, '/jsoff'))
        self.assertIsNone(r['html'], 'a script that sends the browser to another website: not measured')
        self.assertTrue(r['redirected_to_other_site'], r)
        self.assertIn(url(1, '/jsoff'), r['error'])
        self.assertEqual(r['final_url'], url(1, '/jsoff'))
        self.assertIsNone(r['rendered_words'], 'nothing measured on the other website is kept')
        self.assertIsNone(r['js_dependent'])

    def test_same_site_moves_still_measured(self):
        r = F.fetch(url(1, '/Trail'))   # case + trailing slash + query on the same site
        self.assertIs(r['redirected_to_other_site'], False, r)
        self.assertIs(r['redirected_to_other_page'], False, r)
        self.assertTrue(r['html'])


class BlockKinds(unittest.TestCase):
    """A refusal (the bot name is refused; the one-time browser read applies) versus a bot check (never
    retried), recorded as block_kind; and ordinary page wording that must not read as a block."""

    def test_firewall_block_page_is_a_refusal(self):
        self.assertIsNone(F._check_reason(CFBLOCK))
        self.assertTrue(F._refusal(CFBLOCK))
        r = F.fetch(url(0, '/cfblock'))
        self.assertTrue(r['blocked'], r)
        self.assertEqual(r.get('block_kind'), 'refusal', r)
        r = F.fetch(url(0, '/cfblock'), browser_retry=True)
        self.assertEqual(r['user_agent'], 'browser', r)
        self.assertTrue(r['html'], r)
        self.assertIsNone(r.get('block_kind'))

    def test_block_kind_recorded(self):
        self.assertEqual(F.fetch(url(1, '/forbidden')).get('block_kind'), 'refusal')
        self.assertEqual(F.fetch(url(1, '/challenge')).get('block_kind'), 'check')
        self.assertEqual(F.fetch(url(1, '/challenge'), mode='raw').get('block_kind'), 'check')
        self.assertEqual(F.fetch(url(1, '/jsc3')).get('block_kind'), 'check', 'a check only Chrome saw')
        self.assertEqual(F.fetch(url(0, '/botwall-challenge'), browser_retry=True).get('block_kind'), 'check')
        self.assertIsNone(F.fetch(url(1, '/static')).get('block_kind'))
        r = F.fetch(url(1, '/chrome403'))
        self.assertTrue(r['blocked'], r)
        self.assertIsNone(r['html'])
        self.assertEqual(r.get('block_kind'), 'refusal', 'refused only in the browser visit')
        c = os.path.join(OUT, 'challenge-kind.html')
        with open(c, 'w') as fh:
            fh.write(CHALLENGE)
        self.assertEqual(F.read_saved('https://x.example/', c).get('block_kind'), 'check')
        d = os.path.join(OUT, 'denied-kind.html')
        with open(d, 'w') as fh:
            fh.write('<html><head><title>Access Denied</title></head><body>denied</body></html>')
        self.assertEqual(F.read_saved('https://x.example/', d).get('block_kind'), 'refusal')

    def test_real_check_still_a_check(self):
        self.assertTrue(F._check_reason(CHALLENGE))
        cf = ('<html><head><title>Just a moment...</title></head><body><form id="challenge-form"></form>'
              '<script>window._cf_chl_opt={}</script>Sorry, you have been blocked? no: checking</body></html>')
        self.assertTrue(F._check_reason(cf), 'a check marker wins over the block wording')
        self.assertFalse(F._refusal(cf))

    def test_thin_page_wording_is_not_a_block(self):
        filler = ' '.join(['We serve the whole county with fast friendly work.'] * 9)
        contact = (f'<html><head><title>Contact Us</title></head><body><nav><a href=/>Home</a></nav><h1>Contact</h1><p>{filler}</p>'
                   '<form><label>Please verify you are human</label><div class=g-recaptcha></div><button>Send</button></form></body></html>')
        self.assertIsNone(F._check_reason(contact), 'a reCAPTCHA label on a thin contact page is a page')
        drains = f'<html><head><title>Blocked Drains Springfield | Joe Plumbing</title></head><body><h1>Blocked drains</h1><p>{filler}</p></body></html>'
        self.assertFalse(F._refusal(drains), "'Blocked Drains' is a service, not a refusal")
        fruit = f'<html><head><title>Forbidden Fruit Bakery</title></head><body><p>{filler}</p></body></html>'
        self.assertFalse(F._refusal(fruit))
        capt = f'<html><head><title>Captcha Solutions for Web Forms</title></head><body><nav><a href=/>Home</a></nav><p>{filler}</p><form><input></form></body></html>'
        self.assertIsNone(F._check_reason(capt))
        # controls: the real block pages still read as blocks
        for t in ('Access Denied', 'Access denied | example.com used Cloudflare to restrict access', '403 Forbidden',
                  'Forbidden', 'Blocked', 'Request blocked', 'Error 1020'):
            self.assertTrue(F._refusal(f'<html><head><title>{t}</title></head><body>no</body></html>'), t)
        self.assertTrue(F._check_reason('<html><head><title>Contact</title></head><body>Please verify you are human</body></html>'),
                        'check wording on a bare page with no form and no menu is still a check')
        self.assertTrue(F._check_reason(f'<html><head><title>Contact</title></head><body><form><label>Please verify you are human</label></form></body></html>', status=403),
                        'check wording with a refusing status is a check')

    def test_cut_step_body_is_not_judged_by_its_size(self):
        cut = BIGHEAD[:30000]
        self.assertIsNone(F._check_reason(cut, truncated=True))
        steps, why = F._chain_check({'chain': [{'url': 'http://a.example/', 'status': 200, 'body': cut, 'truncated': True}],
                                      'html': BIGHEAD, 'final_url': 'http://a.example/'})
        self.assertIsNone(why)
        steps, why = F._chain_check({'chain': [{'url': 'http://a.example/', 'status': 200, 'body': cut, 'truncated': True,
                                                'mitigated': 'challenge'}], 'html': BIGHEAD, 'final_url': 'http://a.example/'})
        self.assertTrue(why, 'size-free evidence still counts on a cut body')
        r = F.fetch(url(2, '/bighead'))
        self.assertFalse(r['blocked'], r)
        self.assertEqual(r['mode_used'], 'rendered', r)


class RuleEdges(unittest.TestCase):
    """Offline edges of the block rules (no server, no Chrome)."""

    def test_header_and_address_evidence(self):
        self.assertTrue(F._check_reason(None, mitigated='challenge'))
        self.assertIsNone(F._check_reason(REAL, mitigated='bypass'), 'only the value "challenge" is evidence')
        self.assertTrue(F._check_reason(None, url='http://a.example/.well-known/sgcaptcha/?r=%2F'))
        self.assertIsNone(F._check_reason(None, url='http://a.example/contact'))

    def test_block_wording_with_check_wording_is_a_check(self):
        page = ('<html><head><title>Attention Required! | Cloudflare</title></head><body>Sorry, you have been blocked.'
                ' Please complete the security check to access the site.</body></html>')
        self.assertTrue(F._check_reason(page))
        self.assertFalse(F._refusal(page))

    def test_status_as_second_evidence(self):
        form = '<html><head><title>Contact</title></head><body><form><label>Please verify you are human</label></form></body></html>'
        self.assertIsNone(F._check_reason(form, status=200))
        self.assertIsNone(F._check_reason(form, status=299))
        self.assertTrue(F._check_reason(form, status=300))
        self.assertTrue(F._check_reason(form, status=199))

    def test_size_limits_are_strict(self):
        b150 = ' '.join(['word'] * 150)
        self.assertIsNone(F._check_reason(f'<html><head><title>Just a moment...</title></head><body><p>{b150}</p></body></html>'))
        self.assertFalse(F._refusal(f'<html><head><title>Access Denied</title></head><body><p>{b150}</p></body></html>'))
        b149 = ' '.join(['word'] * 149)
        self.assertTrue(F._check_reason(f'<html><head><title>Just a moment...</title></head><body><p>{b149}</p></body></html>'))
        self.assertTrue(F._refusal(f'<html><head><title>Access Denied</title></head><body><p>{b149}</p></body></html>'))
        b1000 = ' '.join(['word'] * 1000)
        self.assertIsNone(F._check_reason(f'<html><body><p>{b1000}</p><script>_cf_chl_opt={{}}</script></body></html>'))
        self.assertTrue(F._check_reason(f'<html><body><p>{b1000[5:]}</p><script>_cf_chl_opt={{}}</script></body></html>'))

    def test_chain_step_without_cut_flag_is_judged_in_full(self):
        _, why = F._chain_check({'chain': [{'url': 'http://a.example/', 'status': 200, 'body': CHALLENGE}],
                                 'html': REAL, 'final_url': 'http://a.example/'})
        self.assertTrue(why)

    def test_doc_url(self):
        self.assertEqual(F._doc_url({'chain': [{'url': 'http://a.example/x'}, {'url': 'http://a.example/y'}],
                                     'final_url': 'http://a.example/z'}), 'http://a.example/y')
        self.assertEqual(F._doc_url({'chain': [], 'final_url': 'http://a.example/z'}), 'http://a.example/z')
        self.assertEqual(F._doc_url({'chain': [{'url': None}], 'final_url': 'http://a.example/z'}), 'http://a.example/z')

    def test_bot_user_agent(self):
        old = os.environ.pop('RENDER_VIEWPORT', None)
        try:
            ua = F.bot_user_agent()
            self.assertTrue(ua.endswith(f'(compatible; {F.ct.UA})'), ua)
            self.assertIn('Mobile', ua)
            os.environ['RENDER_VIEWPORT'] = 'desktop'
            self.assertEqual(F.bot_user_agent(), F.ct.UA)
        finally:
            os.environ.pop('RENDER_VIEWPORT', None)
            if old is not None:
                os.environ['RENDER_VIEWPORT'] = old

    def test_cut_note(self):
        self.assertIsNone(F._cut({'ok': True, 'error': None}))
        self.assertEqual(F._cut({'ok': True, 'error': None}, 'n.'), 'n.')
        self.assertIsNone(F._cut({'ok': False, 'error': 'boom'}))
        self.assertEqual(F._cut({'ok': True, 'error': 'html truncated at 8 MB'}),
                         'The browser copy is incomplete: html truncated at 8 MB.')
        self.assertEqual(F._cut({'ok': True, 'error': 'x'}, 'n.'), 'n. The browser copy is incomplete: x.')


class RenderRules(unittest.TestCase):
    """Phone user agent that keeps the bot name, load wait, slot fallback, size cut note, same-document
    address changes, and the server HTML's own character set."""

    def test_phone_user_agent_keeps_bot_name(self):
        UAS.pop('/uav', None)
        r = F.fetch(url(2, '/uav'))
        self.assertIn('MOBILEVARIANT', r['html'] or '', r)
        self.assertIn('JSMOBILE', r['html'])
        seen = UAS['/uav']
        self.assertEqual(len(seen), 2, seen)   # the plain request and the browser load
        for ua in seen:
            self.assertIn('Mobile', ua)
            self.assertIn('GrokBot-SiteAudit', ua)
            self.assertNotIn('Googlebot', ua)
        self.assertTrue(r['raw_words'] and abs(r['raw_words'] - r['rendered_words']) < 5, r)

    def test_slow_image_does_not_hold_the_read(self):
        t = time.time()
        r = F.fetch(url(3, '/slowimg'))
        self.assertEqual(r['mode_used'], 'rendered', r)
        self.assertIn('slow image page words here', r['html'])
        self.assertLess(time.time() - t, 16, 'the read waited for a 20 s image')

    def test_slot_fallback_not_counted(self):
        r = F.fetch(url(3, '/slot'))
        self.assertIn('SHADOWHEAD', r['html'] or '', r)
        self.assertEqual(r['html'].count('LIGHTTEXT'), 1)
        import re
        shown = re.sub(r'<script.*?</script>', '', r['html'], flags=re.S)
        self.assertNotIn('FALLBACK', shown, 'a slot with assigned nodes does not show its fallback')

    def test_size_cut_is_reported(self):
        real = F._render
        F._render = lambda u, as_browser=False: {'ok': True, 'status': 200, 'final_url': u, 'html': STATIC, 'chain': [],
                                                 'error': 'html truncated at 8 MB'}
        try:
            r = F.fetch(url(3, '/static'))
        finally:
            F._render = real
        self.assertTrue(r['html'])
        self.assertIn('8 MB', (r.get('note') or '') + (r.get('error') or ''), r)

    def test_same_document_address_change_is_not_a_redirect(self):
        r = F.fetch(url(1, '/spa'))
        self.assertFalse(r['redirected_to_other_page'], r)
        self.assertTrue(r['html'], r)
        r = F.fetch(url(1, '/jsredir'))
        self.assertTrue(r['redirected_to_other_page'], 'control: a script that loads another page is still a redirect')
        self.assertIsNone(r['html'])

    def test_server_html_charset(self):
        for path in ('/cp1252', '/cp1252gz'):
            r = F.fetch(url(2, path), mode='raw')
            self.assertIn('Café crème', r['html'] or '', (path, r['error']))
            self.assertNotIn('�', r['html'])
        r = F.fetch(url(2, '/sjis'), mode='raw')
        self.assertIn('歯科', r['html'] or '', r['error'])
        f = os.path.join(OUT, 'cafe-saved.html')
        with open(f, 'wb') as fh:
            fh.write(CAFE)
        self.assertIn('Café crème', F.read_saved('https://x.example/', f)['html'] or '')


def run_cli(*args):
    return subprocess.run([os.path.join(ROOT, 'onpage.sh'), *args], capture_output=True, text=True, timeout=300,
                          env=dict(os.environ, CRAWL_DELAY='0.05'))


class Cli(unittest.TestCase):
    def test_complete(self):
        out = os.path.join(OUT, 'complete')
        p = run_cli('--keyword', 'stump removal', '--mine', url(0, '/static'), '--competitor', url(1, '/static'),
                    '--competitor', url(2, '/csr'), '--competitor', url(3, '/static'), '--out', out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for f in ('onpage.json', 'onpage.md', 'onpage.html', 'run.log'):
            self.assertTrue(os.path.getsize(os.path.join(out, f)) > 100, f)
        res = json.load(open(os.path.join(out, 'onpage.json')))
        self.assertEqual(schemacheck.validate(res, schemacheck.load('result.schema.json')), [])
        self.assertTrue(res['complete'])
        self.assertEqual(len(res['factors']), 50)
        h1 = [r for r in res['factors'] if r['key'] == 'h1_count'][0]
        self.assertEqual(h1['mine'], 1)
        self.assertEqual(h1['competitors'], [1, 1, 1])
        md = open(os.path.join(out, 'onpage.md')).read()
        self.assertIn('Please read', md)

    def test_partial_competitor_blocked(self):
        out = os.path.join(OUT, 'partial')
        p = run_cli('--keyword', 'stump removal', '--mine', url(0, '/static'), '--competitor', url(1, '/forbidden'),
                    '--competitor', url(2, '/static'), '--out', out)
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        res = json.load(open(os.path.join(out, 'onpage.json')))
        self.assertFalse(res['complete'])
        self.assertTrue(any(n.startswith('PARTIAL') and '/forbidden' in n for n in res['notes']))
        row = [r for r in res['factors'] if r['key'] == 'word_count'][0]
        self.assertIsNone(row['competitors'][0], 'a blocked page must be None (not measured), never 0')
        self.assertIn('not measured', open(os.path.join(out, 'onpage.md')).read())

    def test_mine_blocked_exit_4(self):
        out = os.path.join(OUT, 'mine-blocked')
        p = run_cli('--keyword', 'stump removal', '--mine', url(0, '/forbidden'), '--competitor', url(1, '/static'), '--out', out)
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)

    def test_mine_behind_bot_wall_is_read_as_browser(self):
        out = os.path.join(OUT, 'mine-wall')
        p = run_cli('--keyword', 'stump removal', '--mine', url(0, '/botwall'), '--competitor', url(1, '/static'), '--out', out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(out, 'onpage.json')) as fh:
            res = json.load(fh)
        self.assertEqual(res['pages'][0]['fetch']['user_agent'], 'browser')
        self.assertTrue(any('normal Chrome browser' in n for n in res['notes']))

    def test_competitor_wall_needs_yes(self):
        out = os.path.join(OUT, 'comp-wall')
        p = run_cli('--keyword', 'stump removal', '--mine', url(0, '/static'), '--competitor', url(1, '/botwall'), '--competitor', url(2, '/static'), '--out', out)
        self.assertEqual(p.returncode, 6, 'without --as-browser a competitor behind a bot wall stays blocked')
        with open(os.path.join(out, 'onpage.json')) as fh:
            res = json.load(fh)
        self.assertTrue(any('normal Chrome browser' in n for n in res['notes']), 'the report says how to include it, in plain words')
        self.assertFalse(any('--' in n for n in res['notes']), 'no command-line options in the owner-facing report')
        self.assertIn('TO RE-RUN: ', p.stdout)
        self.assertIn('--as-browser', p.stdout, 'stdout tells the bot how to re-run')
        out2 = os.path.join(OUT, 'comp-wall-yes')
        p = run_cli('--keyword', 'stump removal', '--mine', url(0, '/static'), '--competitor', url(1, '/botwall'), '--competitor', url(2, '/static'),
                    '--as-browser', url(1, '/botwall'), '--out', out2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_saved_file_cli(self):
        f = os.path.join(OUT, 'saved-cli.html')
        with open(f, 'w') as fh:
            fh.write(STATIC)
        out = os.path.join(OUT, 'saved')
        p = run_cli('--keyword', 'stump removal', '--mine', url(0, '/static'), '--competitor', url(1, '/forbidden'),
                    '--html', f'{url(1, "/forbidden")}={f}', '--out', out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(out, 'onpage.json')) as fh:
            res = json.load(fh)
        self.assertEqual(res['pages'][1]['fetch']['source'], 'saved-file')

    def test_bad_args(self):
        self.assertEqual(run_cli('--keyword', 'x', '--mine', url(0, '/'), '--competitor', url(1, '/'), '--as-browser', url(2, '/')).returncode, 2)
        self.assertEqual(run_cli('--keyword', 'x', '--mine', url(0, '/'), '--competitor', url(1, '/'), '--html', f'{url(1, "/")}=/nope.html').returncode, 2)
        self.assertEqual(run_cli('--keyword', 'x', '--mine', 'nope', '--competitor', url(0, '/')).returncode, 2)
        self.assertEqual(run_cli('--keyword', 'x', '--mine', url(0, '/')).returncode, 2)
        self.assertEqual(run_cli('--keyword', 'x', '--mine', url(0, '/'), '--competitor', url(0, '/')).returncode, 2)
        many = []
        for i in range(6):
            many += ['--competitor', url(1, f'/static?{i}')]
        self.assertEqual(run_cli('--keyword', 'x', '--mine', url(0, '/'), *many).returncode, 2)


class Unreachable(unittest.TestCase):
    """A site that does not answer at all is "unreachable", never "robots-disallowed"."""

    def test_no_answer_is_unreachable(self):
        r = F.fetch('http://127.0.0.9:9/page')  # nothing listens on port 9 here
        self.assertIsNone(r['html'])
        self.assertTrue(r['error'].startswith('unreachable'), r['error'])
        self.assertNotIn('robots-disallowed', r['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
