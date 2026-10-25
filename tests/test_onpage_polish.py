#!/usr/bin/env python3
"""What a stranger reads, after a fresh end-to-end user test: repeated page parts count once, every action line
quotes its text, targets are whole numbers, plain wording (no "All 2", no command-line options in the report),
a re-run never overwrites an earlier report, and SKILL.md covers a machine with no git and a bot with no search.
Run: python3 tests/test_onpage_polish.py (a local server, a few seconds, no Chrome: --raw)."""
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(ROOT, 'onpage'))
import cli  # noqa: E402
import compare as C  # noqa: E402
import measure as M  # noqa: E402
import report as R  # noqa: E402

VPY = os.path.join(ROOT, '.venv', 'bin', 'python')
PY = VPY if os.path.exists(VPY) else sys.executable
REG = M.load_registry()
ROW = {f['key']: f for f in REG['factors']}
VARS = ['plumber raleigh', 'plumber', 'raleigh']


def measure(html, key):
    return M.measure_page(html, 'https://me.example/', 'plumber raleigh', VARS)['values'][key]


def page(body):
    return f'<html><head><title>Home</title></head><body>{body}</body></html>'


MENU = '<nav><a href="/loc">Plumber of Raleigh Change location</a><a href="/svc">Services</a></nav>'


class RepeatsCountOnce(unittest.TestCase):
    """A phone menu and a desktop menu holding the same links are one set of links, not two."""

    def test_same_text_same_address_counts_once(self):
        one = measure(page(MENU), 'variations_a')
        self.assertEqual(one, 2, 'plumber + raleigh in one link')
        self.assertEqual(measure(page(MENU * 5), 'variations_a'), one, 'the same link 5 times counts once')

    def test_relative_and_absolute_address_are_the_same_link(self):
        h = page('<a href="/loc">Raleigh plumber</a><a href="https://me.example/loc">Raleigh plumber</a>')
        self.assertEqual(measure(h, 'variations_a'), 2, 'raleigh + plumber, once')

    def test_different_address_or_text_is_a_different_link(self):
        h = page('<a href="/a">Raleigh plumber</a><a href="/b">Raleigh plumber</a><a href="/a">Raleigh plumber now</a>')
        self.assertEqual(measure(h, 'variations_a'), 6, '3 different links, 2 matches each')

    def test_spaces_do_not_make_a_link_different(self):
        h = page('<a href="/a">Raleigh  plumber</a><a href="/a"> Raleigh\nplumber </a>')
        self.assertEqual(measure(h, 'variations_a'), 2)

    def test_heading_questions_count_once(self):
        h = page('<h2>Ready for Comfort?</h2><h2>How can we help?</h2><h2>Ready for Comfort?</h2>')
        self.assertEqual(measure(h, 'heading_questions'), 2)
        self.assertEqual(measure(page('<h2>Ready?</h2><h3>Ready?</h3>'), 'heading_questions'), 2,
                         'a different tag is a different heading')
        self.assertEqual(measure(page('<h2>Ready?</h2><h2>Ready?</h2>'), 'has_heading_question'), 1)

    def test_other_counts_unchanged(self):
        h = page('<h2>Raleigh plumber</h2><h2>Raleigh plumber</h2>' + MENU * 2)
        self.assertEqual(measure(h, 'variations_h2'), 4, 'only the factors marked "once" leave repeats out')
        self.assertEqual(measure(h, 'variations_body'), 8)
        pg = M.Page(page('<h2>Ready?</h2><h2>Ready?</h2>' + MENU * 2), 'https://me.example/')
        kw, vs = (), [tuple(v.split()) for v in VARS]
        self.assertEqual(M.measure_factor(pg, {'measure': 'questions', 'params': {'scope': 'headings'}}, kw, vs), 2,
                         'without "once" a repeated heading counts each time')
        self.assertEqual(M.measure_factor(pg, {'measure': 'matches', 'params': {'scope': 'tags:a', 'target': 'variations'}}, kw, vs), 4)

    def test_rule_is_written_in_the_definitions(self):
        for k in ('variations_a', 'heading_questions', 'has_heading_question'):
            self.assertTrue(ROW[k]['params'].get('once'), k)
            self.assertIn('counts once', ROW[k]['definition'], k)
        self.assertIn('same address', ROW['variations_a']['definition'])

    def test_quotes_are_not_repeated(self):
        ev = cli.collect_evidence(page(MENU * 5 + '<h2>Ready?</h2><h2>Ready?</h2>'), 'https://me.example/', VARS, REG)
        self.assertEqual([x['text'] for x in ev['variations_a']], ['Plumber of Raleigh Change location'])
        self.assertEqual([x['text'] for x in ev['heading_questions']], ['Ready?'])


FIRST = page('<h1>Welcome</h1><p>We are a family firm. Our Raleigh plumber team fixes leaks, drains and water heaters '
             'for homes across the area, and every plumber is licensed.</p>' + '<p>' + 'filler words here ' * 60 + 'raleigh</p>')


class First100Quoted(unittest.TestCase):
    def test_matches_quoted_with_context(self):
        ev = cli.collect_evidence(FIRST, 'https://me.example/', VARS, REG)['variations_first100']
        self.assertEqual(ev, [{'text': '… a family firm. Our Raleigh plumber team fixes leaks, drains …', 'matched': ['plumber', 'raleigh']},
                              {'text': '… the area, and every plumber is licensed.', 'matched': ['plumber']}])
        texts = ' '.join(x['text'] for x in ev)
        self.assertIn('every plumber is licensed', texts)
        self.assertNotIn('filler words here filler', texts.replace('…', ''), 'only text near a match is quoted')
        n = measure(FIRST, 'variations_first100')
        self.assertEqual(sum(len(R.mark(x['text'], x['matched']).split('**')) // 2 for x in ev), n,
                         'the words marked in the quotes are the words counted')

    def test_outside_the_window_not_quoted(self):
        ev = cli.collect_evidence(FIRST, 'https://me.example/', VARS, REG)['variations_first100']
        self.assertFalse([x for x in ev if x['text'].endswith('raleigh')], 'the "raleigh" after word 100 is not counted')

    def test_report_line_has_quotes(self):
        res = mk_result({'variations_first100': [2, 5, 6, 3]},
                        evidence={'variations_first100': [{'text': 'Our Raleigh plumber team', 'matched': ['raleigh', 'plumber']}]})
        sec = acts(R.markdown(res))
        self.assertIn('Yours: "Our **Raleigh** **plumber** team"', sec)
        self.assertIn('Competitor 1: "Our **Raleigh** **plumber** team"', sec)


class WindowAndSpans(unittest.TestCase):
    """The quote helpers read the same window and the same matches as the counter."""

    def test_window_text_is_the_counted_window(self):
        import random
        import textutil as T
        rnd = random.Random(7)
        vocab = ['Raleigh', 'plumber', 'a', 'line-up', '&', 'don\'t', '', '5', 'Ünïcode', 'drains,']
        for _ in range(300):
            text = '\n'.join(' '.join(rnd.choice(vocab) for _ in range(rnd.randint(0, 9))) for _ in range(rnd.randint(1, 8)))
            n = rnd.randint(1, 30)
            got = [t for t in (T.tokens(x) for x in T.window_text(text, n)) if t]
            self.assertEqual(got, T.window_lines(text, n, True), (text, n))

    def test_window_text_cut(self):
        import textutil as T
        self.assertEqual(T.window_text('one two\n\nthree four five\nsix', 4), ['one two', 'three four'])
        self.assertEqual(T.window_text('one two', 2), ['one two'])
        self.assertEqual(T.window_text('', 5), [])
        self.assertEqual(T.window_text('\ufb01x \ufb01x \ufb01x', 2), ['\ufb01x \ufb01x \ufb01x'],
                         'a line whose normal form is longer (a ligature) is kept whole, never cut mid-word')

    def test_match_spans(self):
        import textutil as T
        ph = T.phrase_tokens(['stump', 'stump removal', 'removal'])
        self.assertEqual(T.match_spans('Stump Removal, then stump.', ph),
                         [(0, 13, ('stump', 'removal')), (20, 25, ('stump',))])
        self.assertEqual(T.match_spans('no match here', ph), [])
        self.assertEqual(T.match_spans('', ph), [])


def mk_result(values, evidence=None, n=3):
    """A result with your page first, then n competitors; values = {key: [mine, c1, c2, ...]}."""
    reg = {'factors': [ROW[k] for k in values]}
    mine = {k: v[0] for k, v in values.items()}
    comps = [{k: v[i] for k, v in values.items()} for i in range(1, n + 1)]
    rows = C.compare(reg, mine, comps)
    pg = lambda role, url, vals: {'role': role, 'url': url, 'measured': True, 'values': vals, 'not_measured': {},
                                  'evidence': evidence or {}, 'fetch': {'final_url': url, 'mode_used': 'raw'}}
    return {'job': {'keyword': 'plumber raleigh', 'variations': VARS, 'mode': 'raw'}, 'created': 'today', 'version': 't',
            'pages': [pg('mine', 'https://me.example/', mine)] + [pg('competitor', f'https://c{i}.example/', c) for i, c in enumerate(comps, 1)],
            'factors': rows, 'complete': True, 'notes': []}


def acts(md):
    return md.split(R.ACTIONS_TITLE, 1)[1].split(R.TEST_LINE, 1)[0]


class WholeNumbers(unittest.TestCase):
    def test_two_competitors_round_up(self):
        res = mk_result({'variations_a': [7, 9, 10]}, n=2)
        line = [x for x in acts(R.markdown(res)).splitlines() if 'link text' in x][0]
        self.assertIn('(to reach the middle value: 3 more)', line, 'middle value 9.5, you 7: 2.5 rounds up to 3')
        self.assertNotIn('2.5 more', line)
        self.assertEqual(R.summary(res), [f"{ROW['variations_a']['name']}: " + line.split('**: ', 1)[1]])

    def test_never_a_fraction_of_more(self):
        import itertools
        for mine, a, b in itertools.product(range(0, 5), range(0, 7), range(0, 7)):
            res = mk_result({'variations_a': [mine, a, b]}, n=2)
            for x in R.summary(res):
                m = re.search(r'middle value: (\S+) more', x)
                if m:
                    self.assertRegex(m.group(1), r'^\d+$', x)
                    self.assertGreaterEqual(mine + int(m.group(1)), (a + b) / 2, x)
                    self.assertLess(mine + int(m.group(1)) - 1, (a + b) / 2, x)

    def test_more_needed(self):
        self.assertEqual(C.more_needed({'unit': 'count', 'gap_to_median': 2.5}), 3)
        self.assertEqual(C.more_needed({'unit': 'count', 'gap_to_median': 3}), 3)
        self.assertEqual(C.more_needed({'unit': 'count', 'gap_to_median': 0.01}), 1)
        self.assertIsNone(C.more_needed({'unit': 'count', 'gap_to_median': 0}))
        self.assertIsNone(C.more_needed({'unit': 'yes/no', 'gap_to_median': 1}))


class Wording(unittest.TestCase):
    def test_both_not_all_2(self):
        res = mk_result({'starts_with_variation_title': [0, 1, 1], 'variations_a': [1, 3, 25], 'heading_questions': [0, 0, 0]}, n=2)
        md = R.markdown(res) + R.html_page(res)
        self.assertNotRegex(md, r'(?i)\ball 2\b')
        self.assertIn('Both competitors have 1; you have 0.', md)
        self.assertIn('Both have more.', md)
        self.assertIn('below both', md)
        self.assertIn('same as both', md)
        res3 = mk_result({'starts_with_variation_title': [0, 1, 1, 1]})
        self.assertIn('All 3 competitors have 1; you have 0.', R.markdown(res3))

    def test_yes_no_with_two(self):
        r = {'unit': 'yes/no', 'mine': 0, 'n_measured': 2, 'competitors': [1, 1]}
        self.assertEqual(C.say(r), 'Both competitors have it; your page does not have it.')
        self.assertEqual(C.say(dict(r, competitors=[0, 0])), 'Neither competitor has it; your page does not have it.')

    def test_no_options_in_the_report(self):
        def pg(url, role='competitor', measured=True, **f):
            fe = {'final_url': url, 'mode_used': 'raw', 'blocked': False}
            fe.update(f)
            return {'role': role, 'url': url, 'measured': measured, 'values': {}, 'not_measured': {}, 'fetch': fe}
        pages = [pg('https://me.example/', 'mine'), pg('https://a.example/'),
                 pg('https://b.example/', measured=False, blocked=True, error='blocked: 403', block_kind='refusal'),
                 pg('https://c.example/', measured=False, blocked=True, error='blocked', block_kind='check'),
                 pg('https://d.example/', measured=False, blocked=True, error='blocked'),
                 pg('https://e.example/', note='measured without a browser: browser failed', mode_used='raw')]
        notes = cli.run_notes(pages, 'rendered')
        res = mk_result({'variations_a': [1, 3]}, n=1)
        res['notes'] = notes
        for text in (R.markdown(res), R.html_page(res).split('</style>', 1)[1]):
            self.assertNotRegex(text, r'--[a-z]|doctor\.sh|kit\'s instructions|kit&#x27;s instructions')
        self.assertIn('Webpage, HTML only', ' '.join(notes))
        self.assertTrue([n for n in notes if n.startswith('These competitor pages were blocked') and 'https://d.example/' in n], notes)
        self.assertEqual(cli.bot_steps(pages[:2]), [], 'nothing blocked, browser fine: no re-run lines')
        only_check = cli.bot_steps(pages[:2] + [pages[3]])
        self.assertEqual(len(only_check), 1, only_check)
        self.assertNotIn('--as-browser', only_check[0], 'a bot check is never bypassed')
        self.assertEqual(cli.bot_steps(pages[:2] + [pages[5]]),
                         ['Browser did not work for some pages: run ./doctor.sh, then re-run.'])
        steps = cli.bot_steps(pages, 'rendered')
        joined = ' '.join(steps)
        for s in ('--as-browser', '--html', 'doctor.sh', 'https://b.example/', 'https://c.example/', 'https://d.example/'):
            self.assertIn(s, joined)


PAGES = {
    '/mine': page('<h1>Plumber</h1><p>We fix pipes.</p><a href="/x">Home</a>'),
    '/a': page('<h1>Raleigh plumber</h1><p>Raleigh plumber help.</p><a href="/r">Raleigh plumber</a><h2>Need a plumber?</h2>'),
    '/b': page('<h1>Raleigh plumber</h1><p>Plumber in Raleigh.</p><a href="/r">Plumber Raleigh</a><h2>Why us?</h2>'),
}


class Hd(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write((body or 'nope').encode())

    def log_message(self, *a):
        pass


class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class RerunKeepsEarlierReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = TS(('127.0.0.1', 0), Hd)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.srv.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def run_cli(self, reports, *extra):
        b = self.base
        return subprocess.run([PY, os.path.join(ROOT, 'onpage', 'cli.py'), '--keyword', 'plumber raleigh', '--raw',
                               '--mine', b + '/mine', '--competitor', b + '/a', '--competitor', b + '/b', *extra],
                              capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, KIT_REPORTS=reports, CRAWL_DELAY='0.05'))

    def test_second_run_goes_to_a_new_folder(self):
        os.makedirs(os.path.join(HERE, '_out'), exist_ok=True)
        with tempfile.TemporaryDirectory(dir=os.path.join(HERE, '_out')) as reports:
            p1 = self.run_cli(reports)
            self.assertEqual(p1.returncode, 0, p1.stdout + p1.stderr)
            first = os.path.join(reports, 'onpage', 'plumber-raleigh_127-0-0-1')
            with open(os.path.join(first, 'onpage.json')) as fh:
                created = json.load(fh)['created']
            with open(os.path.join(first, 'onpage.md')) as fh:
                md1 = fh.read()
            self.assertNotIn('new folder', p1.stdout)
            p2 = self.run_cli(reports)
            self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)
            self.assertIn(f'so this one goes to a new folder: {first}-2', p2.stdout)
            self.assertIn(f'files: {first}-2/onpage.html', p2.stdout)
            self.assertTrue(os.path.exists(os.path.join(first + '-2', 'onpage.md')))
            with open(os.path.join(first, 'onpage.md')) as fh:
                self.assertEqual(fh.read(), md1, 'the first report is untouched')
            with open(os.path.join(first, 'onpage.json')) as fh:
                self.assertEqual(json.load(fh)['created'], created)
            p3 = self.run_cli(reports)
            self.assertIn(f'{first}-3', p3.stdout)
            out = os.path.join(reports, 'given')
            self.run_cli(reports, '--out', out)
            p5 = self.run_cli(reports, '--out', out)
            self.assertIn('replacing the earlier report', p5.stdout, '--out is used as given, and says so')

    def test_free_folder(self):
        os.makedirs(os.path.join(HERE, '_out'), exist_ok=True)
        with tempfile.TemporaryDirectory(dir=os.path.join(HERE, '_out')) as d:
            base = os.path.join(d, 'r')
            self.assertEqual(cli.free_folder(base), base)
            os.makedirs(base)
            self.assertEqual(cli.free_folder(base), base, 'an empty folder holds no report')
            open(os.path.join(base, 'onpage.md'), 'w').close()
            self.assertEqual(cli.free_folder(base), base + '-2')


class SkillDoc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'SKILL.md'), encoding='utf-8') as fh:
            cls.s = fh.read()

    def test_git_missing(self):
        self.assertRegex(self.s, r'(?is)git.{0,200}(not installed|missing|no git)')
        self.assertRegex(self.s, r'(?is)(install git|apt-get install -y git).{0,400}ask')

    def test_no_search_tool(self):
        self.assertRegex(self.s, r'(?is)no (web )?search tool.{0,600}ask the (person|user)')
        self.assertRegex(self.s, r'(?is)skip.{0,40}ads.{0,80}map')

    def test_city_alone_counts(self):
        self.assertRegex(self.s, r'(?is)city name alone.{0,300}variation')

    def test_targets_and_reruns(self):
        self.assertRegex(self.s, r'(?is)rounded up')
        self.assertRegex(self.s, r'(?is)-2.{0,80}-3')


if __name__ == '__main__':
    unittest.main()
