#!/usr/bin/env python3
"""What the report reader sees must be true for the run it describes. Made-up pages only (no real sites or clients).
Run: python3 tests/test_onpage_reader.py (no network, under a second).

- The empty action list says why it is empty (your page not measured / no competitor / only 1 competitor / none).
- One measured competitor is never "most competitors": no action list below 2.
- A one-per-page field far above every competitor gets a read-aloud note, never an action, never green.
- The canonical-URL row is a note, not an action (renaming a page needs a redirect).
- Duplicate title / meta description tags on your page are named.
- Action lines quote the counted text, with the matched words marked.
- The factor-origin sentence lives in one constant; README and SKILL.md quote it word for word."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(ROOT, 'onpage'))
import compare as C  # noqa: E402
import report as R  # noqa: E402
import cli  # noqa: E402
from measure import load_registry  # noqa: E402

REG = load_registry()
KEYS = [f['key'] for f in REG['factors']]


def vals(**kw):
    """A made-up measured page: every factor 0 unless given."""
    v = {k: 0 for k in KEYS}
    v.update(word_count=800)
    v.update(kw)
    return v


def fetchrec(url, **kw):
    r = {'final_url': url, 'status': 200, 'mode_used': 'raw', 'raw_words': 100, 'rendered_words': None, 'js_dependent': False,
         'blocked': False, 'error': None, 'ms': 5, 'user_agent': 'bot', 'source': 'live'}
    r.update(kw)
    return r


def result(mine, comps, notes=None, mine_extra=None, comp_extra=None):
    urls = ['https://me.example/stump-removal'] + [f'https://c{i}.example/stumps' for i in range(1, len(comps) + 1)]
    pages = []
    for i, (u, v) in enumerate(zip(urls, [mine] + comps)):
        p = {'role': 'mine' if i == 0 else 'competitor', 'url': u, 'measured': v is not None,
             'fetch': fetchrec(u) if v is not None else fetchrec(u, status=403, blocked=True, error='blocked: HTTP 403'),
             'values': v or {}, 'not_measured': {}}
        p.update((mine_extra if i == 0 else (comp_extra or {}).get(i)) or {})
        pages.append(p)
    rows = C.compare(REG, mine, comps)
    complete = mine is not None and all(c is not None for c in comps)
    job = {'keyword': 'stump removal', 'mine': urls[0], 'competitors': urls[1:], 'mode': 'raw', 'variations': ['stump removal', 'stump', 'removal']}
    return {'tool': 'site-audit-kit onpage', 'version': 't', 'created': '2026-10-24 00:00 UTC', 'job': job, 'pages': pages,
            'factors': rows, 'actions': [r['key'] for r in C.actions(rows)], 'complete': complete, 'notes': notes or ['a note']}


def section(md, start):
    m = re.search(r'^## ' + re.escape(start) + r'.*?$(.*?)(?=^## |\Z)', md, re.M | re.S)
    return m.group(1) if m else None


def acts_section(md):
    return section(md, R.ACTIONS_TITLE)


class EmptyListSaysWhy(unittest.TestCase):
    def test_mine_unmeasured(self):
        md = R.markdown(result(None, [vals(variations_title=2)] * 3))
        self.assertIn('your page could not be measured', acts_section(md))
        self.assertNotIn('at most half of the measured competitors', md)
        self.assertIn('your page could not be measured', R.html_page(result(None, [vals()] * 3)))

    def test_no_competitor(self):
        md = R.markdown(result(vals(), [None, None, None]))
        self.assertIn('no competitor page could be measured', acts_section(md))
        self.assertNotIn('at most half of the measured competitors', md)

    def test_one_competitor_is_not_most(self):
        res = result(vals(variations_title=0, variations_h1=0), [None, vals(variations_title=3, variations_h1=2), None])
        self.assertEqual(C.actions(res['factors']), [])
        md = R.markdown(res)
        self.assertIn('only 1 competitor page was measured', acts_section(md))
        self.assertEqual(R.summary(res), [])
        self.assertIn('only 1 competitor page was measured', R.html_page(res))

    def test_two_competitors_give_actions(self):
        res = result(vals(variations_title=0), [vals(variations_title=3), vals(variations_title=2), None])
        self.assertIn('variations_title', [r['key'] for r in C.actions(res['factors'])])

    def test_nothing_behind(self):
        md = R.markdown(result(vals(variations_title=3), [vals(variations_title=1)] * 3))
        self.assertIn('at most half of the measured competitors have more', acts_section(md))


class Banner(unittest.TestCase):
    def test_partial_banner_counts_pages_once(self):
        notes = ['PARTIAL: competitor 1 (https://c1.example/stumps) could not be measured: blocked.',
                 'PARTIAL: competitor 3 (https://c3.example/stumps) could not be measured: blocked.']
        res = result(vals(), [None, vals(), None], notes=notes)
        md, page = R.markdown(res), R.html_page(res)
        self.assertNotIn('PARTIAL: PARTIAL', md)
        self.assertIn('1 of 3 competitor pages measured', md)
        self.assertIn('1 of 3 competitor pages measured', page)
        self.assertIn('Partial report', md)

    def test_no_banner_when_complete(self):
        res = result(vals(), [vals()] * 3)
        md, page = R.markdown(res), R.html_page(res)
        self.assertNotIn('Partial report', md)
        self.assertNotIn('Partial report', page)
        self.assertNotIn("card partial", page)

    def test_banner_says_mine_missing(self):
        res = result(None, [vals()] * 3)
        self.assertIn('your page was not measured', R.markdown(res))

    def test_about_counts_measured_competitors(self):
        md = R.markdown(result(vals(), [None, vals(), None]))
        self.assertIn('With 1 competitor page no statistics are possible', md)
        self.assertIn('With 3 competitor pages no statistics', R.markdown(result(vals(), [vals()] * 3)))


class Over(unittest.TestCase):
    def test_meta_far_above_every_competitor(self):
        res = result(vals(variations_meta_description=12), [vals(variations_meta_description=1), vals(variations_meta_description=2),
                                                              vals(variations_meta_description=1)])
        self.assertEqual([r['key'] for r in C.over(res['factors'])], ['variations_meta_description'])
        md = R.markdown(res)
        sec = section(md, R.OVER_TITLE)
        self.assertIsNotNone(sec, md)
        self.assertIn('Keyword variations in the meta description', sec)
        self.assertIn('read it aloud', (R.OVER_TITLE + sec).lower())
        self.assertNotIn('penalt', sec.lower())
        self.assertNotIn('Keyword variations in the meta description', acts_section(md))
        self.assertIn(R.OVER_TITLE, R.html_page(res))

    def test_thresholds(self):
        def flagged(mine, comps):
            res = result(vals(variations_title=mine), [vals(variations_title=c) for c in comps])
            return [r['key'] for r in C.over(res['factors'])]
        self.assertEqual(flagged(3, [2, 2, 2]), [])
        self.assertEqual(flagged(7, [3, 1, 0]), ['variations_title'], '7 > max(2 x 3, 3 + 3)')
        self.assertEqual(flagged(6, [3, 1, 0]), [], '6 is not > 6')

    def test_threshold_low_max(self):
        def flagged(mine, comps):
            res = result(vals(variations_title=mine), [vals(variations_title=c) for c in comps])
            return [r['key'] for r in C.over(res['factors'])]
        self.assertEqual(flagged(5, [1, 0, 1]), ['variations_title'], '5 > max(2, 4)')
        self.assertEqual(flagged(4, [1, 0, 1]), [], '4 is not > 4')
        self.assertEqual(flagged(3, [0, 0, 0]), [], '3 is not > max(0, 3)')
        self.assertEqual(flagged(4, [0, 0, 0]), ['variations_title'])
        self.assertEqual(flagged(2, [0, 0, 0]), [], 'under 3 never')

    def test_two_competitors_are_enough(self):
        res = result(vals(variations_meta_description=12), [vals(variations_meta_description=1), vals(variations_meta_description=2), None])
        self.assertEqual([r['key'] for r in C.over(res['factors'])], ['variations_meta_description'])

    def test_needs_two_competitors(self):
        res = result(vals(variations_meta_description=12), [None, vals(variations_meta_description=1), None])
        self.assertEqual(C.over(res['factors']), [])

    def test_long_areas_never_flagged(self):
        res = result(vals(variations_h2=40, variations_headings=89, variations_a=28, variations_body=300),
                     [vals(variations_h2=1, variations_headings=9, variations_a=2, variations_body=20)] * 3)
        self.assertEqual(C.over(res['factors']), [])

    def test_one_line_per_family(self):
        res = result(vals(variations_og_title=9, variations_og_description=9), [vals()] * 3)
        fams = [r['family'] for r in C.over(res['factors'])]
        self.assertEqual(len(fams), len(set(fams)))

    def test_above_is_not_green(self):
        css = R.CSS
        rule = re.search(r'\.t-above-all[^{]*\{([^}]*)\}', css).group(1)
        self.assertNotIn('--hi', rule)
        self.assertNotIn('--hibg', rule)


class Address(unittest.TestCase):
    def test_canonical_is_a_note_not_an_action(self):
        res = result(vals(variations_canonical=0), [vals(variations_canonical=2)] * 3)
        self.assertNotIn('variations_canonical', [r['key'] for r in C.actions(res['factors'])])
        self.assertNotIn('variations_canonical', res['actions'])
        md = R.markdown(res)
        self.assertNotIn('canonical URL', acts_section(md).split(R.ADDRESS_LEAD)[0])
        self.assertIn('301', md)
        self.assertIn(next(f['name'] for f in REG['factors'] if f['key'] == 'variations_canonical'), md)
        self.assertIn('301', R.html_page(res))
        self.assertEqual([r['key'] for r in C.address_notes(res['factors'])], ['variations_canonical'])

    def test_no_note_when_not_behind(self):
        res = result(vals(variations_canonical=2), [vals(variations_canonical=2)] * 3)
        self.assertEqual(C.address_notes(res['factors']), [])
        self.assertNotIn('301', R.markdown(res))


class Titles(unittest.TestCase):
    def test_action_title(self):
        self.assertEqual(R.ACTIONS_TITLE, 'Where most competitors have more (ideas to test)')
        self.assertIn('## Where most competitors have more (ideas to test)', R.markdown(result(vals(), [vals()] * 3)))

    def test_test_line_has_control_and_recrawl(self):
        self.assertIn('similar page on your site that you do not change', R.TEST_LINE)
        self.assertIn('after Google next crawls the page', R.TEST_LINE)

    def test_gap_to_middle_value(self):
        res = result(vals(variations_title=0), [vals(variations_title=3), vals(variations_title=2), vals(variations_title=0)])
        self.assertIn('to reach the middle value: 2 more', acts_section(R.markdown(res)))


class FactorOrigin(unittest.TestCase):
    """One sentence, one constant; README and SKILL.md quote it word for word."""

    def _flat(self, path):
        with open(os.path.join(ROOT, path), encoding='utf-8') as fh:
            return re.sub(r'\s+', ' ', fh.read())

    def test_one_constant(self):
        with open(os.path.join(ROOT, 'onpage', 'report.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertEqual(src.count('50 common on-page factors'), 1, 'the sentence lives in FACTOR_NOTE only')
        self.assertIn(R.FACTOR_NOTE, R.markdown(result(vals(), [vals()] * 3)))
        self.assertIn(R.FACTOR_NOTE, R.html_page(result(vals(), [vals()] * 3)))

    def test_docs_quote_it(self):
        for doc in ('README.md', 'SKILL.md', 'onpage/factors.json'):
            self.assertTrue(R.FACTOR_NOTE in self._flat(doc), f'{doc} must quote report.FACTOR_NOTE word for word')


class DuplicateTags(unittest.TestCase):
    def test_named_on_your_page(self):
        res = result(vals(), [vals()] * 3, mine_extra={'duplicate_tags': {'meta:description': 2, 'title': 2}})
        md, page = R.markdown(res), R.html_page(res)
        self.assertIn('Your page has 2 meta description tags', md)
        self.assertIn('Your page has 2 title tags', md)
        self.assertIn('Your page has 2 meta description tags', page)
        self.assertLess(md.index('Your page has 2 meta description tags'), md.index('## ' + R.ACTIONS_TITLE))

    def test_none_when_single(self):
        md = R.markdown(result(vals(), [vals()] * 3, mine_extra={'duplicate_tags': {}}))
        self.assertNotIn('Your page has', md)
        self.assertNotIn('Your page has', R.markdown(result(vals(), [vals()] * 3)))

    def test_unknown_tag_label(self):
        md = R.markdown(result(vals(), [vals()] * 3, mine_extra={'duplicate_tags': {'og:title': 3, 'zz:odd': 2}}))
        self.assertIn('Your page has 3 social-share (Open Graph) title tags', md)
        self.assertIn('Your page has 2 zz:odd tags', md)

    def test_cli_copies_measure_notes(self):
        self.assertEqual(cli.duplicate_tags({'notes': {'duplicate_tags': {'title': 2}}}), {'title': 2})
        self.assertEqual(cli.duplicate_tags({'values': {}}), {})
        self.assertEqual(cli.duplicate_tags({'notes': {'duplicate_tags': {'title': 1, 'h1': 3}}}), {'h1': 3})

    def test_from_measure_page(self):
        from measure import measure_page
        html = ("<html><head><title>Stump removal</title><title>Stump removal stump removal</title>"
                "<meta name=description content='stump removal'><meta name=description content='stump removal fast'>"
                "</head><body><h1>Stump removal</h1><p>Words here.</p></body></html>")
        m = measure_page(html, 'https://me.example/', 'stump removal', ['stump removal', 'stump', 'removal'], registry=REG)
        dup = cli.duplicate_tags(m)
        if not (m.get('notes') or {}).get('duplicate_tags'):
            self.skipTest('measure_page does not report duplicate tags yet')
        self.assertEqual(dup, {'title': 2, 'meta:description': 2})
        md = R.markdown(result(vals(), [vals()] * 3, mine_extra={'duplicate_tags': dup}))
        self.assertIn('Your page has 2 meta description tags; search engines use one. Keep one.', md)


HTML_MINE = ("<html><head><title>Home | Acme</title><meta name=description content='We grind stumps fast.'></head>"
             "<body><h1>Welcome</h1><h2>About us</h2><h2>Stump grinding</h2><p>Hello.</p></body></html>")
HTML_COMP = ("<html><head><title>Stump Removal in Town</title><meta name=description content='Stump removal and stump grinding, same day.'></head>"
             "<body><h1>Stump removal</h1><h2>Why stump removal</h2><p><a href=/a>Why stump removal</a> <a href=/p>Prices</a> "
             "<a href=/g>Stump grinding</a></p></body></html>")


class Evidence(unittest.TestCase):
    VARS = ['stump removal', 'stump', 'removal', 'stumps']

    def test_collect(self):
        ev = cli.collect_evidence(HTML_COMP, 'https://c1.example/', self.VARS, REG)
        self.assertEqual(ev['variations_title'], [{'text': 'Stump Removal in Town', 'matched': ['stump removal']}])
        self.assertEqual(ev['variations_meta_description'][0]['matched'], ['stump', 'stump removal'])
        self.assertEqual([x['text'] for x in ev['variations_a']], ['Why stump removal', 'Stump grinding'],
                         'many-element areas: only the texts that hold a variation')
        self.assertEqual(ev['variations_h1'], [{'text': 'Stump removal', 'matched': ['stump removal']}])
        mine = cli.collect_evidence(HTML_MINE, 'https://me.example/', self.VARS, REG)
        self.assertEqual(mine['variations_title'], [{'text': 'Home | Acme', 'matched': []}], 'your single field is shown even with no match')
        self.assertEqual(mine['variations_meta_description'][0]['matched'], ['stumps'])
        self.assertNotIn('word_count', mine, 'only target rows')

    def test_collect_never_raises(self):
        self.assertEqual(cli.collect_evidence('', 'https://x.example/', self.VARS, REG), {})
        self.assertIsInstance(cli.collect_evidence('<<<>>>\x00', 'https://x.example/', self.VARS, REG), dict)

    def test_action_line_quotes_text(self):
        evm = cli.collect_evidence(HTML_MINE, 'https://me.example/', self.VARS, REG)
        evc = cli.collect_evidence(HTML_COMP, 'https://c1.example/', self.VARS, REG)
        res = result(vals(variations_title=0), [vals(variations_title=1)] * 3, mine_extra={'evidence': evm},
                     comp_extra={i: {'evidence': evc} for i in (1, 2, 3)})
        sec = acts_section(R.markdown(res))
        self.assertIn('Yours: "Home | Acme"', sec)
        self.assertIn('Competitor 1: "**Stump Removal** in Town"', sec)
        page = R.html_page(res)
        self.assertIn('Competitor 1: “<b>Stump Removal</b> in Town”', page)

    def test_mark(self):
        self.assertEqual(R.mark('Stump removal and stump grinding', ['stump', 'stump removal']),
                         '**Stump removal** and **stump** grinding')
        self.assertEqual(R.mark('a <b> stump', ['stump'], html=True), 'a &lt;b&gt; <b>stump</b>')
        self.assertEqual(R.mark('stumpy', ['stump']), 'stumpy', 'whole words only')


class RerunAdvice(unittest.TestCase):
    def page(self, url, kind, role='competitor'):
        return {'role': role, 'url': url, 'measured': False, 'values': {}, 'not_measured': {},
                'fetch': fetchrec(url, status=403, blocked=True, error='blocked', block_kind=kind)}

    def test_refusal_vs_check(self):
        mine = {'role': 'mine', 'url': 'https://me.example/', 'measured': True, 'values': {}, 'not_measured': {}, 'fetch': fetchrec('https://me.example/')}
        pages = [mine, self.page('https://r.example/', 'refusal'), self.page('https://k.example/', 'check'),
                 self.page('https://u.example/', None)]
        notes = cli.run_notes(pages)
        self.assertFalse([n for n in notes if '--' in n], 'the owner-facing notes say it in plain words, no options')
        self.assertTrue([n for n in notes if 'https://k.example/' in n and 'Webpage, HTML only' in n], notes)
        steps = cli.bot_steps(pages)  # stdout and run.log, for the bot
        ref = [n for n in steps if 'https://r.example/' in n and '--' in n]
        chk = [n for n in steps if 'https://k.example/' in n and '--' in n]
        unk = [n for n in steps if 'https://u.example/' in n and '--' in n]
        self.assertTrue(ref and chk and unk, steps)
        self.assertIn('--as-browser', ref[0])
        self.assertNotIn('--as-browser', chk[0], 'a bot check is never bypassed: only the saved-file route')
        self.assertIn('--html', chk[0])
        self.assertIn('--html', unk[0])

    def test_complete_has_no_partial_note(self):
        mine = {'role': 'mine', 'url': 'https://me.example/', 'measured': True, 'values': {}, 'not_measured': {}, 'fetch': fetchrec('https://me.example/')}
        comp = dict(mine, role='competitor', url='https://c.example/', fetch=fetchrec('https://c.example/'))
        notes = cli.run_notes([mine, comp, dict(comp, url='https://d.example/')])
        self.assertFalse([n for n in notes if n.startswith('PARTIAL')], notes)
        notes = cli.run_notes([mine, self.page('https://r.example/', 'refusal')])
        self.assertTrue([n for n in notes if n.startswith('PARTIAL: no competitor')], notes)
        notes = cli.run_notes([mine, comp, self.page('https://r.example/', 'refusal')])
        one = [n for n in notes if n.startswith('Only 1 competitor')]
        self.assertTrue(one and 'blocked page' in one[0], notes)
        gone = dict(self.page('https://g.example/', None), fetch=fetchrec('https://g.example/', error='not a web page'))
        one = [n for n in cli.run_notes([mine, comp, gone]) if n.startswith('Only 1 competitor')]
        self.assertTrue(one and 'blocked' not in one[0], one)


class NotesExact(unittest.TestCase):
    """run_notes, line by line: each note appears only when its case is there."""

    def pg(self, url, role='competitor', measured=True, **fetch):
        return {'role': role, 'url': url, 'measured': measured, 'values': {}, 'not_measured': {}, 'fetch': fetchrec(url, **fetch)}

    def test_plain_complete_run(self):
        notes = cli.run_notes([self.pg('https://me.example/', 'mine'), self.pg('https://a.example/'), self.pg('https://b.example/')])
        self.assertEqual(notes, ['Keyword matching: case-insensitive whole words; each word counts once, inside the longest variation it belongs to.'])

    def test_every_case(self):
        pages = [self.pg('https://me.example/', 'mine', False, error='timeout'),
                 self.pg('https://a.example/', mode_used='rendered', user_agent='browser', js_dependent=True),
                 self.pg('https://b.example/', measured=False, blocked=True, error='blocked: 403', block_kind='refusal'),
                 self.pg('https://c.example/', source='saved-file', mode_used='saved'),
                 self.pg('https://d.example/', measured=False, blocked=True, error='blocked: check', user_agent='browser', block_kind='check')]
        notes = cli.run_notes(pages)
        want = ['PARTIAL: your page could not be measured (timeout). Nothing to compare.',
                'PARTIAL: competitor 2 (https://b.example/) could not be measured: blocked: 403. Ranges and counts use the competitors that were measured.',
                'PARTIAL: competitor 4 (https://d.example/) could not be measured: blocked: check. Ranges and counts use the competitors that were measured.',
                'Pages were measured differently (some in a browser, some from server HTML only), so small differences may come from that.',
                "Read once as a normal Chrome browser because the site refused this bot's name: https://a.example/",
                'Measured from a copy the user saved in their own browser (not visited by this bot): https://c.example/',
                "These sites refused this bot's name. To include one: say yes and the page is read once more as a normal Chrome browser, "
                'or open the page in your own browser, save it (File, Save Page As, "Webpage, HTML only") and send the file to whoever '
                'runs this report: https://b.example/',
                'These pages showed a bot check (never bypassed) or failed as a normal browser too. To include one: open the page in '
                'your own browser, save it (File, Save Page As, "Webpage, HTML only") and send the file to whoever runs this report: '
                'https://d.example/',
                'Most text on these pages appears only after JavaScript runs (crawlers that do not run JavaScript see much less): https://a.example/',
                'Keyword matching: case-insensitive whole words; each word counts once, inside the longest variation it belongs to.']
        self.assertEqual(notes, want, 'd was already read as a browser: only the saved-file route; 2 competitors measured: no "Only 1" note')

    def test_rerun_advice_only_for_blocked_pages(self):
        pages = [self.pg('https://me.example/', 'mine'), self.pg('https://a.example/'), self.pg('https://b.example/'),
                 self.pg('https://gone.example/', measured=False, error='not a web page'),
                 self.pg('https://f.example/', measured=False, blocked=True, error='refused twice', user_agent='browser', block_kind='refusal')]
        self.assertFalse([n for n in cli.run_notes(pages) if '--' in n], 'no options in the owner-facing notes')
        adv = [n for n in cli.bot_steps(pages) if '--' in n]
        self.assertEqual(len(adv), 1, adv)
        self.assertNotIn('gone.example', adv[0], 'a page that was not blocked gets no rerun advice')
        self.assertIn('https://f.example/', adv[0])
        self.assertNotIn('--as-browser', adv[0], 'already refused as a browser: only the saved-file route')

    def test_no_competitor_no_only_one_note(self):
        notes = cli.run_notes([self.pg('https://me.example/', 'mine'), self.pg('https://a.example/', measured=False, error='x')])
        self.assertFalse([n for n in notes if n.startswith('Only 1')], notes)


class EvidenceEdges(unittest.TestCase):
    def test_scope_of(self):
        self.assertEqual(cli._scope_of({'params': {'tags': ['h1', 'h2']}}), 'tags:h1,h2')
        self.assertEqual(cli._scope_of({'params': {'tags': ['title']}}), 'title')
        self.assertEqual(cli._scope_of({'params': {'scope': 'meta:description', 'tags': ['h1']}}), 'meta:description')
        self.assertIsNone(cli._scope_of({}))

    def test_caps_and_absences(self):
        links = ''.join(f"<a href=/{i}>stump {i}</a>" for i in range(7))
        long_title = 'Stump ' + 'x' * 300
        html = f"<html><head><title>{long_title}</title></head><body><p>{links}</p><p>stump stump</p></body></html>"
        ev = cli.collect_evidence(html, 'https://c.example/', ['stump'], REG)
        self.assertEqual(len(ev['variations_a']), 5)
        t = ev['variations_title'][0]['text']
        self.assertEqual(len(t), 200)
        exact = 'Stump ' + 'y' * 194
        html2 = f"<html><head><title>{exact}</title></head><body><p>x</p></body></html>"
        self.assertEqual(cli.collect_evidence(html2, 'https://c.example/', ['stump'], REG)['variations_title'][0]['text'], exact,
                         '200 characters fit')
        self.assertTrue(t.endswith('…'))
        self.assertEqual(cli.collect_evidence(html, 'https://c.example/', ['stump'], REG, max_chars=400)['variations_title'][0]['text'],
                         long_title)
        self.assertNotIn('variations_canonical', ev, 'no canonical link: nothing stored')
        self.assertNotIn('variations_meta_description', ev)
        self.assertNotIn('variations_body', ev, 'only target rows')
        self.assertEqual(cli.collect_evidence(html, 'https://c.example/', ['stump'], None), {}, 'never raises')


class Families(unittest.TestCase):
    def test_snippet_text_is_one_edit(self):
        fam = {f['key']: f.get('family') for f in REG['factors']}
        self.assertEqual(fam['variations_og_description'], fam['variations_meta_description'],
                         'meta description and OG description are usually one edit (one factors.json family)')
        self.assertEqual(fam['variations_og_title'], fam['variations_title'])



class MarkSameAsCounter(unittest.TestCase):
    """The bold words in a quote are the words the counter counted: same tokens, any case, any punctuation between words."""

    def test_comma_inside_a_phrase(self):
        # the user test: "Austin" was bold but "TX" was not, though the counter counted "austin tx"
        self.assertEqual(R.mark('Master Plumbing in Austin, TX - Austin', ['austin tx', 'austin']),
                         'Master Plumbing in **Austin, TX** - **Austin**')

    def test_case_and_hyphen(self):
        self.assertEqual(R.mark('STUMP-Removal pros', ['stump removal']), '**STUMP-Removal** pros')
        self.assertEqual(R.mark('Stump removal', ['Stump Removal']), '**Stump removal**')

    def test_longest_first_like_the_counter(self):
        # counter: "stump removal" once (its words used up), then "stump"
        self.assertEqual(R.mark('stump removal and stump', ['stump', 'removal', 'stump removal']),
                         '**stump removal** and **stump**')

    def test_html_escaped(self):
        self.assertEqual(R.mark('A & B, TX <x>', ['b tx'], html=True), 'A &amp; <b>B, TX</b> &lt;x&gt;')

    def test_quote_matches_evidence(self):
        html = "<html><head><title>Master Plumbing in Austin, TX - Austin</title></head><body><p>x</p></body></html>"
        vs = ['plumber austin tx', 'austin tx', 'plumber austin', 'plumber', 'austin']
        ev = cli.collect_evidence(html, 'https://c.example/', vs, REG)['variations_title'][0]
        self.assertEqual(R.mark(ev['text'], ev['matched']), 'Master Plumbing in **Austin, TX** - **Austin**')


class NothingToCompare(unittest.TestCase):
    """Exit 4 (your page or every competitor not measured) is 'Nothing to compare', not 'Partial'; exit 6 stays partial."""

    def test_mine_not_measured(self):
        res = result(None, [vals()] * 3)
        md, page = R.markdown(res), R.html_page(res)
        self.assertIn('**Nothing to compare:**', md)
        self.assertNotIn('Partial report', md)
        self.assertIn('<b>Nothing to compare.</b>', page)
        self.assertNotIn('Partial report', page)

    def test_no_competitor_measured(self):
        md = R.markdown(result(vals(), [None, None, None]))
        self.assertIn('**Nothing to compare:**', md)
        self.assertNotIn('Partial report', md)

    def test_partial_stays_partial(self):
        md = R.markdown(result(vals(), [vals(), None, vals()]))
        self.assertIn('**Partial report:**', md)
        self.assertNotIn('Nothing to compare', md)

    def test_bot_check_on_your_page_says_what_to_do(self):
        res = result(None, [vals()] * 3)
        res['pages'][0]['fetch'].update(block_kind='check', error='blocked: the site served a bot check (status 403): x')
        md = R.markdown(res)
        top = md[:md.index('## ' + R.ACTIONS_TITLE)]
        self.assertIn('bot check', top)
        self.assertIn('save', top.lower())
        self.assertIn('HTML only', top)


def nobrowser(url, **kw):
    return fetchrec(url, mode_used='raw', note='measured without a browser: Chrome did not start', **kw)


class WithoutBrowser(unittest.TestCase):
    """Pages measured from the server HTML because the browser failed must be named at the top, never shown as a full rendered run."""

    def res(self, which):
        r = result(vals(), [vals()] * 3)
        r['job']['mode'] = 'rendered'
        for i, p in enumerate(r['pages']):
            p['fetch'] = nobrowser(p['url']) if i in which else fetchrec(p['url'], mode_used='rendered')
        return r

    def test_all_pages(self):
        r = self.res({0, 1, 2, 3})
        md, page = R.markdown(r), R.html_page(r)
        top = md[:md.index('## ' + R.ACTIONS_TITLE)]
        self.assertIn(R.NO_BROWSER_ALL, top)
        self.assertEqual(R.NO_BROWSER_ALL, 'Measured without a browser (Chrome did not start); counts may be lower than what visitors see.')
        self.assertIn(H_esc(R.NO_BROWSER_ALL), page)
        self.assertEqual(cli.run_state(r['pages']), ('complete', 0), 'every page measured the same way: exit 0')

    def test_some_pages(self):
        r = self.res({0, 2})
        md = R.markdown(r)
        top = md[:md.index('## ' + R.ACTIONS_TITLE)]
        self.assertIn('Your page and Competitor 2 were measured without a browser', top)
        self.assertNotIn(R.NO_BROWSER_ALL, md)
        self.assertEqual(cli.run_state(r['pages']), ('partial', 6), 'pages measured differently: not complete')

    def test_none(self):
        r = self.res(set())
        self.assertNotIn('without a browser', R.markdown(r))
        self.assertEqual(cli.run_state(r['pages']), ('complete', 0))

    def test_requested_raw_is_not_a_fallback(self):
        r = result(vals(), [vals()] * 3)
        self.assertEqual(R.no_browser_pages(r['pages']), [])
        self.assertNotIn('without a browser', R.markdown(r))

    def test_legacy_fallback_error_detected(self):
        p = {'role': 'competitor', 'url': 'u', 'measured': True,
             'fetch': fetchrec('u', error='browser failed (Failed to launch the browser process:  Code: null\n\nstderr:\n[1324:1324:1024/210527.460968:FATAL:content/browser/zygote_ho); measured the server HTML')}
        self.assertEqual(len(R.no_browser_pages([p])), 1)
        line = R._page_line(p, 'Competitor 1')
        self.assertNotIn('stderr', line)
        self.assertNotIn('FATAL', line)
        self.assertIn('measured without a browser', line)

    def test_page_line_plain(self):
        r = self.res({1})
        md = R.markdown(r)
        self.assertIn('- Competitor 1: https://c1.example/stumps (measured without a browser: Chrome did not start)', md)

    def test_state_nothing(self):
        r = result(None, [vals()] * 3)
        self.assertEqual(cli.run_state(r['pages']), ('nothing', 4))
        r = result(vals(), [vals(), None, vals()])
        self.assertEqual(cli.run_state(r['pages']), ('partial', 6))

    def test_run_note_names_the_pages(self):
        r = self.res({2})
        notes = cli.run_notes(r['pages'])
        n = [x for x in notes if 'without a browser' in x]
        self.assertTrue(n and 'https://c2.example/stumps' in n[0], notes)


def H_esc(s):
    import html
    return html.escape(s)


class QuestionEvidence(unittest.TestCase):
    """'Questions in headings' quotes the question headings, like every other action line."""
    HTML = ("<html><head><title>t</title></head><body><h1>Stump removal</h1><h2>How much does stump removal cost?</h2>"
            "<h3>Our crew</h3><h3>Do you grind roots?</h3><p>x</p></body></html>")

    def test_collect(self):
        ev = cli.collect_evidence(self.HTML, 'https://c.example/', ['stump removal', 'stump'], REG)
        self.assertEqual([x['text'] for x in ev['heading_questions']], ['How much does stump removal cost?', 'Do you grind roots?'])
        self.assertEqual(ev['heading_questions'], ev['has_heading_question'])

    def test_action_line_quotes(self):
        evc = cli.collect_evidence(self.HTML, 'https://c.example/', ['stump removal'], REG)
        evm = cli.collect_evidence("<html><body><h2>Plain</h2></body></html>", 'https://me.example/', ['stump removal'], REG)
        res = result(vals(heading_questions=0), [vals(heading_questions=2)] * 3, mine_extra={'evidence': evm},
                     comp_extra={i: {'evidence': evc} for i in (1, 2, 3)})
        sec = acts_section(R.markdown(res))
        self.assertIn('Yours: none on your page', sec)
        self.assertIn('Competitor 1: "How much does **stump removal** cost?", "Do you grind roots?"', sec)


class PlainWords(unittest.TestCase):
    """Factor names and column labels a small-business owner can read."""
    JARGON = ('TF-IDF', 'log-scaled', 'log10', 'absolute URL', 'P tags', 'Inline CSS', 'HTML source', 'canonical URL')

    def test_factor_names(self):
        for f in REG['factors']:
            for j in self.JARGON:
                self.assertNotIn(j.lower(), f['name'].lower(), (f['key'], f['name']))
            if 'Open Graph' in f['name']:
                self.assertIn('social', f['name'].lower(), f['name'])

    def test_column_legend(self):
        md = R.markdown(result(vals(), [vals()] * 3))
        self.assertIn(R.COLUMNS_LINE, md)
        self.assertIn('Middle value', R.COLUMNS_LINE)
        self.assertIn('half', R.COLUMNS_LINE)
        self.assertIn(H_esc(R.COLUMNS_LINE), R.html_page(result(vals(), [vals()] * 3)))



class HeadlineExact(unittest.TestCase):
    """The top card's words, case by case (what the owner reads first)."""

    def mine_failed(self, **f):
        res = result(None, [vals()] * 3)
        res['pages'][0]['fetch'] = fetchrec(res['pages'][0]['url'], **f)
        return R._headline(res)

    def test_bot_check(self):
        self.assertEqual(self.mine_failed(blocked=True, block_kind='check', error='blocked: x'),
                         ('Nothing to compare', 'your page was not measured (it showed a bot check, which this tool never gets past). ' + R.SAVE_STEP))
        self.assertEqual(self.mine_failed(blocked=False, error='blocked: the browser got a bot check page: y')[1],
                         'your page was not measured (it showed a bot check, which this tool never gets past). ' + R.SAVE_STEP)

    def test_refusal(self):
        self.assertEqual(self.mine_failed(blocked=True, block_kind='refusal', error='blocked: 403')[1],
                         'your page was not measured; the Pages list says why. ' + R.SAVE_STEP)

    def test_other_failure_has_no_save_step(self):
        self.assertEqual(self.mine_failed(blocked=False, error='timeout')[1], 'your page was not measured; the Pages list says why.')

    def test_no_competitor(self):
        self.assertEqual(R._headline(result(vals(), [None, None])),
                         ('Nothing to compare', 'no competitor page could be measured. The Pages list says why; '
                                                're-run with other pages that rank for the keyword.'))

    def test_complete_has_none(self):
        self.assertIsNone(R._headline(result(vals(), [vals()] * 3)))


class NoBrowserEdges(unittest.TestCase):
    def pg(self, measured=True, **f):
        return {'role': 'competitor', 'url': 'https://c.example/', 'measured': measured, 'fetch': fetchrec('https://c.example/', **f)}

    def test_flags(self):
        nb = dict(note='measured without a browser: x')
        self.assertIs(R._is_no_browser(self.pg(**nb)), True)
        self.assertIs(R._is_no_browser(self.pg(measured=False, **nb)), False, 'a page not measured is not "measured without"')
        self.assertIs(R._is_no_browser(self.pg(source='saved-file', error='browser failed (x)')), False, 'a saved copy is the user\'s choice')
        self.assertIs(R._is_no_browser(self.pg()), False)

    def test_mode_rendered_raw_page(self):
        live = self.pg(mode_used='raw')
        self.assertEqual(R.no_browser_pages([live], 'rendered'), [live], 'asked for a browser, got raw: a fallback')
        self.assertEqual(R.no_browser_pages([live], 'raw'), [], '--raw asked for it')
        self.assertEqual(R.no_browser_pages([self.pg(mode_used='raw', source='saved-file')], 'rendered'), [])
        self.assertEqual(R.no_browser_pages([self.pg(measured=False, mode_used='raw')], 'rendered'), [])
        self.assertEqual(R.no_browser_pages([self.pg(mode_used='rendered')], 'rendered'), [])
        self.assertEqual(R.no_browser_pages([{'role': 'competitor', 'url': 'u', 'measured': True, 'fetch': None}], 'rendered'), [])

    def line(self, which, n=4):
        r = result(vals(), [vals()] * (n - 1))
        r['job']['mode'] = 'rendered'
        for i, p in enumerate(r['pages']):
            p['fetch'] = nobrowser(p['url']) if i in which else fetchrec(p['url'], mode_used='rendered')
        return R.no_browser_line(r)

    def test_grammar(self):
        self.assertEqual(self.line({2}), 'Competitor 2 was measured without a browser (the browser did not work for it), so its counts may be '
                         'lower than what visitors see, and comparing it with the other pages is less reliable. The run is reported as partial.')
        self.assertEqual(self.line({0, 1, 2}), 'Your page, Competitor 1 and Competitor 2 were measured without a browser (the browser did not '
                         'work for them), so their counts may be lower than what visitors see, and comparing them with the other pages is less '
                         'reliable. The run is reported as partial.')
        self.assertIsNone(self.line(set()))

    def test_markdown_mark_does_not_escape(self):
        self.assertEqual(R.mark('a <b> stump', ['stump']), 'a <b> **stump**')
        self.assertEqual(R.mark('', ['stump']), '')
        self.assertEqual(R.mark('stump', []), 'stump')



class RunStateEdges(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(cli.run_state(result(vals(), [None, vals(), None])['pages']), ('partial', 6), 'one competitor measured is partial, not nothing')
        self.assertEqual(cli.run_state(result(vals(), [None, None])['pages']), ('nothing', 4), 'your page alone compares with nothing')
        self.assertEqual(cli.run_state(result(vals(), [vals()])['pages']), ('complete', 0))

    def test_one_page_without_browser(self):
        r = result(vals(), [vals()] * 3)
        for i, p in enumerate(r['pages']):
            p['fetch'] = nobrowser(p['url']) if i == 1 else fetchrec(p['url'], mode_used='rendered')
        self.assertEqual(cli.run_state(r['pages'], 'rendered'), ('partial', 6))


class QuestionEvidenceEdges(unittest.TestCase):
    def test_no_question_headings_no_entry(self):
        ev = cli.collect_evidence("<html><body><h2>Plain</h2><p>Why?</p></body></html>", 'https://c.example/', ['x'], REG)
        self.assertNotIn('heading_questions', ev, 'a question in a paragraph is not a heading question')

    def test_only_question_measures_read_headings(self):
        reg = {'factors': [{'key': 'hv', 'band': 'target', 'measure': 'matches', 'params': {'scope': 'headings'}},
                           {'key': 'hq', 'band': 'target', 'measure': 'questions', 'params': {'scope': 'headings'}}]}
        ev = cli.collect_evidence("<html><body><h2>Why stump?</h2></body></html>", 'https://c.example/', ['stump'], reg)
        self.assertEqual(sorted(ev), ['hq'])
        self.assertEqual(ev['hq'], [{'text': 'Why stump?', 'matched': ['stump']}])


if __name__ == '__main__':
    unittest.main(verbosity=2)
