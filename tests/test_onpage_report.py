#!/usr/bin/env python3
"""Tests for what the user reads: onpage/report.py (onpage.md, onpage.html, the stdout summary) and cli.py wiring.
Run: python3 tests/test_onpage_report.py (no network except a local server, a few seconds, no Chrome: --raw).

The action list must hold TARGET rows where most measured competitors have more, and nothing else:
a CONTEXT row below all competitors is not an action, a row above all is not an action, length rows are
summarised in one line. onpage.md and onpage.html must list the same actions in the same order, actions first."""
import html as H
import inspect
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(ROOT, 'onpage'))
sys.path.insert(0, ROOT)
import compare as C  # noqa: E402
import report as R  # noqa: E402
import schemacheck as S  # noqa: E402

VPY = os.path.join(ROOT, '.venv', 'bin', 'python')
PY = VPY if os.path.exists(VPY) else sys.executable
OUT = os.path.join(HERE, '_out', 'onpage-report')

REG = {'factors': [
    {'key': 'word_count', 'name': 'Word count', 'group': 'size', 'unit': 'words', 'band': 'length'},
    {'key': 'variations_p', 'name': 'Keyword variations in paragraphs', 'group': 'content', 'unit': 'count', 'band': 'length'},
    {'key': 'h2_count', 'name': 'Number of H2 tags', 'group': 'headings', 'unit': 'count', 'band': 'target'},
    {'key': 'variations_title', 'name': 'Keyword variations in the title', 'group': 'head', 'unit': 'count', 'band': 'target'},
    {'key': 'variations_h1', 'name': 'Keyword variations in H1', 'group': 'headings', 'unit': 'count', 'band': 'target'},
    {'key': 'has_og_title', 'name': 'Has an OpenGraph title', 'group': 'head', 'unit': 'yes/no', 'band': 'target'},
    {'key': 'page_kb', 'name': 'Page size (KB)', 'group': 'size', 'unit': 'KB', 'band': 'context'},
    {'key': 'image_count', 'name': 'Number of images', 'group': 'images', 'unit': 'count', 'band': 'context'},
]}
MINE = {'word_count': 3300, 'variations_p': 20, 'h2_count': 1, 'variations_title': 9, 'variations_h1': 1, 'has_og_title': 0,
        'page_kb': 10, 'image_count': 1}
COMPS = [
    {'word_count': 1200, 'variations_p': 9, 'h2_count': 6, 'variations_title': 2, 'variations_h1': 2, 'has_og_title': 1, 'page_kb': 90, 'image_count': 14},
    {'word_count': 5900, 'variations_p': 40, 'h2_count': 4, 'variations_title': 3, 'variations_h1': 0, 'has_og_title': 1, 'page_kb': 80, 'image_count': 17},
    {'word_count': 1311, 'variations_p': 5, 'h2_count': 2, 'variations_title': 0, 'variations_h1': 3, 'has_og_title': 0, 'page_kb': 70, 'image_count': 3},
]
# Expected actions: h2_count (3 of 3 more), then the 2-of-3 rows by gap relative to the median:
# has_og_title (median 1, you 0: gap 1.0), variations_h1 (median 2, you 1: gap 0.5).
# Not actions: variations_title (above 2 of 3... you 9, above all), page_kb/image_count (context, below all),
# word_count/variations_p (length band).
WANT = ['Number of H2 tags', 'Has an OpenGraph title', 'Keyword variations in H1']


def fetchrec(url, final=None):
    return {'final_url': final or url, 'status': 200, 'mode_used': 'raw', 'raw_words': 100, 'rendered_words': None,
            'js_dependent': False, 'blocked': False, 'error': None, 'ms': 5, 'user_agent': 'bot', 'source': 'live'}


def result(mine=MINE, comps=COMPS, name='Acme Stumps', finals=None):
    finals = finals or {}
    urls = ['https://me.example/page'] + [f'https://c{i}.example/x' for i in range(1, len(comps) + 1)]
    pages = []
    for i, (u, v) in enumerate(zip(urls, [mine] + comps)):
        pages.append({'role': 'mine' if i == 0 else 'competitor', 'url': u, 'measured': v is not None,
                      'fetch': fetchrec(u, finals.get(u)), 'values': v or {}, 'not_measured': {}})
    rows = C.compare(REG, mine, comps)
    job = {'keyword': 'stump removal', 'mine': urls[0], 'competitors': urls[1:], 'mode': 'raw', 'variations': ['stump removal', 'stump']}
    if name:
        job['name'] = name
    return {'tool': 'site-audit-kit onpage', 'version': 't', 'created': '2026-10-24 00:00 UTC', 'job': job, 'pages': pages,
            'factors': rows, 'actions': [r['key'] for r in C.actions(rows)], 'complete': True, 'notes': ['a note']}


def md_section(md, title_start):
    m = re.search(r'^## ' + re.escape(title_start) + r'.*?$(.*?)(?=^## |\Z)', md, re.M | re.S)
    return m.group(1) if m else None


def md_actions(md):
    sec = md_section(md, R.ACTIONS_TITLE)
    return re.findall(r'^\d+\. \*\*(.+?)\*\*', sec, re.M)


def html_actions(page):
    m = re.search(r'<ol id=actions>(.*?)</ol>', page, re.S)
    return [H.unescape(x) for x in re.findall(r'<li><b>(.*?)</b>', m.group(1))] if m else None


class ActionList(unittest.TestCase):
    def setUp(self):
        self.res = result()
        self.md = R.markdown(self.res)
        self.html = R.html_page(self.res)

    def test_md_actions_exact(self):
        self.assertEqual(md_actions(self.md), WANT)

    def test_html_actions_exact_and_same_as_md(self):
        self.assertEqual(html_actions(self.html), WANT)
        self.assertEqual(html_actions(self.html), md_actions(self.md))

    def test_context_below_all_is_not_an_action(self):
        sec = md_section(self.md, R.ACTIONS_TITLE)
        self.assertNotIn('Page size', sec)
        self.assertNotIn('Number of images', sec)
        self.assertNotIn('Page size', re.search(r'<ol id=actions>(.*?)</ol>', self.html, re.S).group(1))
        ref = md_section(self.md, 'For reference')
        self.assertIsNotNone(ref, 'context rows get their own section')
        self.assertIn('Page size (KB)', ref)
        self.assertIn('Number of images', ref)
        self.assertNotIn('Number of H2 tags', ref, 'a target row is not listed under reference')
        self.assertNotIn('Number of H2 tags', md_section(self.md, 'Full table: page length'))

    def test_above_all_is_not_an_action(self):
        self.assertNotIn('Keyword variations in the title', md_section(self.md, R.ACTIONS_TITLE))

    def test_length_rows_not_actions_but_summarised(self):
        sec = md_section(self.md, R.ACTIONS_TITLE)
        self.assertNotIn('Word count', sec)
        self.assertNotIn('paragraphs', sec)
        line = 'Competitor pages are 1,200-5,900 words; yours is 3,300.'
        self.assertIn(line, self.md)
        self.assertIn(line, self.html)
        self.assertLess(self.md.index(line), self.md.index('## ' + R.ACTIONS_TITLE), 'the length line is at the top')
        ln = md_section(self.md, 'Full table: page length')
        self.assertIn('per 1,000 words', ln)
        self.assertIn('6.1', ln, 'your variations_p rate: 20 / 3,300 words = 6.1 per 1,000')

    def test_actions_first_in_both(self):
        self.assertLess(self.md.index('## ' + R.ACTIONS_TITLE), self.md.index('## Pages'))
        self.assertLess(self.md.index('## ' + R.ACTIONS_TITLE), self.md.index('## Full table'))
        self.assertLess(self.html.index('<ol id=actions>'), self.html.index('<h2>Pages</h2>'))
        self.assertLess(self.html.index('<ol id=actions>'), self.html.index('Full table'))
        self.assertLess(self.md.index('## For reference'), self.md.index('## Please read'))

    def test_action_sentence_quotes_values(self):
        lines = [x for x in md_section(self.md, R.ACTIONS_TITLE).splitlines() if re.match(r'\d+\. ', x)]
        self.assertIn('1. **Number of H2 tags**: Competitors have 2 to 6 (middle value 4); you have 1. 3 of 3 have more. '
                      '(to reach the middle value: 3 more)', lines)
        self.assertFalse([x for x in lines if 'average' in x], 'quote ranges, not 3-page averages')

    def test_none_message(self):
        mine = dict(MINE, h2_count=50, variations_h1=9, has_og_title=1)
        md = R.markdown(result(mine=mine))
        self.assertEqual(md_actions(md), [])
        self.assertIn('None:', md_section(md, R.ACTIONS_TITLE))
        self.assertEqual(html_actions(R.html_page(result(mine=mine))), [])

    def test_summary_is_the_action_list(self):
        lines = R.summary(self.res)
        self.assertEqual([x.split(':')[0] for x in lines], WANT)
        many = {'factors': [{'key': f'k{i}', 'name': f'F{i}', 'group': 'x', 'unit': 'count', 'band': 'target'} for i in range(14)]}
        rows = C.compare(many, {f'k{i}': 0 for i in range(14)}, [{f'k{i}': 1 for i in range(14)}] * 3)
        self.assertEqual(len(R.summary({'factors': rows})), 10, 'stdout shows the top 10')


class Header(unittest.TestCase):
    def test_name_and_final_url(self):
        res = result(finals={'https://c2.example/x': 'https://c2.example/moved'})
        md, page = R.markdown(res), R.html_page(res)
        self.assertIn('Acme Stumps', md)
        self.assertIn('Acme Stumps', page)
        self.assertIn('https://c2.example/moved', md)
        self.assertIn('https://c2.example/moved', page)
        self.assertEqual(md.count('ended at'), 1, 'final URL shown only where it differs')

    def test_no_name_no_crash(self):
        self.assertNotIn('Acme', R.markdown(result(name='')))

    def test_partial_competitor_shown_not_measured(self):
        res = result(comps=[COMPS[0], None, COMPS[2]])
        res['complete'] = False
        res['notes'] = ['PARTIAL: competitor 2 could not be measured.']
        md = R.markdown(res)
        self.assertIn('PARTIAL', md)
        self.assertIn('not measured', md)
        self.assertIn('**Partial report:** 2 of 3 competitor pages measured', md)
        self.assertNotIn('your page was not measured', md)
        self.assertIn("<div class='card partial'><b>Partial report.</b> 2 of 3 competitor pages measured", R.html_page(res))

    def test_banner_only_when_partial(self):
        res = result()
        self.assertNotIn('Partial report', R.markdown(res))
        self.assertNotIn('Partial report', R.html_page(res))

    def test_page_lines(self):
        res = result(comps=[COMPS[0], None, COMPS[2]])
        res['pages'][0]['fetch'].update(mode_used='rendered', note='read in a browser', js_dependent=True)
        res['pages'][2]['fetch'].update(error='blocked: HTTP 403')
        res['pages'][3]['fetch'].update(error='slow but read')
        md, page = R.markdown(res), H.unescape(R.html_page(res))
        for want in ('Your page: https://me.example/page (measured: rendered HTML; read in a browser; '
                     'most of its text appears only after JavaScript runs)',
                     'Competitor 1: https://c1.example/x (measured: raw HTML)',
                     'Competitor 2: https://c2.example/x NOT MEASURED (blocked: HTTP 403)',
                     'Competitor 3: https://c3.example/x (measured: raw HTML; slow but read)'):
            self.assertIn('- ' + want, md)
            self.assertIn('<div class=card>' + want + '</div>', page)
        res['pages'][2]['fetch']['error'] = None
        self.assertIn('NOT MEASURED (unknown reason)', R.markdown(res))

    def test_yes_no_cells(self):
        self.assertEqual(C.fmt(1, 'yes/no'), 'yes')
        self.assertEqual(C.fmt(0, 'yes/no'), 'no')
        self.assertIn('| Has an OpenGraph title | no | yes | yes | no |', R.markdown(result()))

    def test_html_escapes_page_text(self):
        res = result()
        res['job']['keyword'] = '<script>x</script>'
        self.assertNotIn('<script>x', R.html_page(res))


class Tables(unittest.TestCase):
    """Rows hand-checked: per 1,000 words = 20 / 3,300 = 6.1, 9 / 1,200 = 7.5, 5 / 1,311 = 3.8."""

    def setUp(self):
        self.res = result(comps=[COMPS[0], None, COMPS[2]])
        self.md, self.html = R.markdown(self.res), R.html_page(self.res)

    def test_headers_per_band(self):
        plain = '| Factor | You | Competitor 1 | Competitor 2 | Competitor 3 | Competitor range | Middle value | Where you stand |'
        per1k = ('| Factor | You | Competitor 1 | Competitor 2 | Competitor 3 | Competitor range | Middle value | You per 1,000 words '
                 '| Competitors per 1,000 words | Where you stand |')
        self.assertIn(plain, md_section(self.md, 'Full table: keyword placement'))
        self.assertIn(per1k, md_section(self.md, 'Full table: page length'))
        self.assertIn(plain, md_section(self.md, 'For reference'))
        tgt = self.html[self.html.index('Full table: keyword placement'):self.html.index('Full table: page length')]
        self.assertNotIn('per 1,000 words</th>', tgt)
        ln = self.html[self.html.index('Full table: page length'):self.html.index('For reference')]
        self.assertIn('<th>You per 1,000 words</th>', ln)

    def test_absent_pieces(self):
        reg = {'factors': [f for f in REG['factors'] if f['band'] == 'target' and f['key'] != 'variations_title']}
        mine = dict(MINE, h2_count=0)
        res = result(mine=mine)
        res['factors'] = C.compare(reg, mine, COMPS)
        md, page = R.markdown(res), R.html_page(res)
        for piece in ('Tags on your page', 'Page length', R.OVER_TITLE, 'For reference', 'Full table: page length', 'None:',
                      'No list', 'Not a to-do', '(also: )', 'None'):
            self.assertNotIn(piece, md, piece)
            self.assertNotIn(piece, page, piece)
        self.assertEqual(md_actions(md), ['Number of H2 tags', 'Has an OpenGraph title', 'Keyword variations in H1'])
        self.assertNotIn('your page was not measured', md)
        res = result(name='')
        self.assertIn('\n2026-10-24 00:00 UTC · 8 factors', R.markdown(res))

    def test_page_line_without_extras(self):
        res = result()
        res['pages'][1]['fetch'].update(mode_used=None)
        self.assertIn('- Competitor 1: https://c1.example/x\n', R.markdown(res))

    def test_md_rows(self):
        for want in ('| Factor | You | Competitor 1 | Competitor 2 | Competitor 3 | Competitor range | Middle value | Where you stand |',
                     '| Number of H2 tags | 1 | 6 | not measured | 2 | 2 to 6 | 4 | below both |',
                     '| Factor | You | Competitor 1 | Competitor 2 | Competitor 3 | Competitor range | Middle value | You per 1,000 words '
                     '| Competitors per 1,000 words | Where you stand |',
                     '| Keyword variations in paragraphs | 20 | 9 | not measured | 5 | 5 to 9 | 7 | 6.1 | 3.8 to 7.5 | above both |',
                     '| Page size (KB) | 10 | 90 | not measured | 70 | 70 to 90 | 80 | below both |'):
            self.assertIn(want, self.md.splitlines())

    def test_html_rows(self):
        for want in ("<tr><td title='variations_p'>Keyword variations in paragraphs</td><td class=you>20</td><td>9</td>"
                     "<td>not measured</td><td>5</td><td>5 to 9</td><td>7</td><td>6.1</td><td>3.8 to 7.5</td>"
                     "<td><span class='pos n-above-all'>above both</span></td></tr>",
                     "<tr><td title='h2_count'>Number of H2 tags</td><td class=you>1</td><td>6</td><td>not measured</td><td>2</td>"
                     "<td>2 to 6</td><td>4</td><td><span class='pos t-below-all'>below both</span></td></tr>",
                     "<th>Competitor 3</th><th>Competitor range</th><th>Middle value</th><th>You per 1,000 words</th>"):
            self.assertIn(want, self.html)

    def test_nothing_measured_row(self):
        res = result(comps=[None, None, None])
        self.assertIn('| Number of H2 tags | 1 | not measured | not measured | not measured | not measured | not measured | not measured |',
                      R.markdown(res).splitlines())
        res = result(mine=None)
        res['complete'] = False
        self.assertIn('your page was not measured', R.markdown(res))
        self.assertIn('your page was not measured', R.html_page(res))

    def test_also_and_subtitle(self):
        reg = {'factors': REG['factors'] + [{'key': 'leading_h2', 'name': 'H2 starts with it', 'group': 'h', 'unit': 'count',
                                              'band': 'target', 'family': 'h2'}]}
        res = result()
        rows = C.compare({'factors': [dict(f, family='h2') if f['key'] == 'h2_count' else f for f in reg['factors']]},
                         dict(MINE, leading_h2=0), [dict(c, leading_h2=1) for c in COMPS])
        res['factors'] = rows
        md, page = R.markdown(res), R.html_page(res)
        self.assertIn('1. **H2 starts with it**: All 3 competitors have 1; you have 0. (to reach the middle value: 1 more) '
                      '(also: Number of H2 tags)', md.splitlines())
        self.assertIn('<span class=mut>(also: Number of H2 tags)</span>', page)
        self.assertIn('For Acme Stumps · 2026-10-24 00:00 UTC · 9 factors · site-audit-kit t', md)

    def test_evidence_order_and_cap(self):
        comps = [dict(COMPS[0], h2_count=v) for v in (3, 9, 5, 7)]
        res = result(comps=comps)
        res['pages'][0]['evidence'] = {'h2_count': [{'text': 'Mine', 'matched': []}]}
        for i, p in enumerate(res['pages'][1:], 1):
            p['evidence'] = {'h2_count': [{'text': f'Stump {i}', 'matched': ['stump']}]}
        sec = md_section(R.markdown(res), R.ACTIONS_TITLE)
        ev = [x.strip() for x in sec.splitlines() if x.startswith('   - ')]
        self.assertEqual(ev, ['- Yours: "Mine"', '- Competitor 2: "**Stump** 2"', '- Competitor 3: "**Stump** 3"',
                              '- Competitor 4: "**Stump** 4"'], 'the 3 with the most, listed in table order')
        page = R.html_page(res)
        self.assertIn('<ul class=ev><li>Yours: “Mine”</li><li>Competitor 2: “<b>Stump</b> 2”</li>', page)
        del res['pages'][0]['evidence']
        self.assertNotIn('Yours:', R.markdown(res), 'no evidence stored for your page: no Yours line')
        self.assertIn('- Competitor 2: "**Stump** 2"', R.markdown(res))
        res['pages'][0]['evidence'] = {}
        self.assertIn('- Yours: none on your page', R.markdown(res))
        for p in res['pages'][1:]:
            p['evidence'] = {}
        self.assertNotIn('Yours:', R.markdown(res), 'your text alone compares with nothing')
        self.assertNotIn('<ul class=ev>', R.html_page(res))

    def test_gap_only_for_counts(self):
        sec = md_section(R.markdown(result()), R.ACTIONS_TITLE)
        og = [x for x in sec.splitlines() if 'OpenGraph' in x][0]
        self.assertNotIn('to reach', og, 'a yes/no row has no "more"')
        self.assertTrue(og.endswith('your page does not have it.'), og)

    def test_evidence_only_from_pages_with_more_and_family_keys(self):
        comps = [dict(COMPS[0], h2_count=v) for v in (0, 9, 2, 1, 5)]
        res = result(mine=dict(MINE, h2_count=1), comps=comps)
        res['factors'] = C.compare({'factors': [dict(f, family='h2') if f['key'] in ('h2_count', 'page_kb') else f
                                                for f in REG['factors']]}, res['pages'][0]['values'], comps)
        res['pages'][0]['evidence'] = {'page_kb': [{'text': 'Mine', 'matched': []}]}
        for i, p in enumerate(res['pages'][1:], 1):
            p['evidence'] = {'page_kb': [{'text': f'T{i}', 'matched': []}] + [{'text': f'x{j}', 'matched': []} for j in range(7)]}
        sec = md_section(R.markdown(res), R.ACTIONS_TITLE)
        ev = [x.strip() for x in sec.splitlines() if x.startswith('   - ')]
        self.assertEqual(ev[0], '- Yours: "Mine"', 'falls back to another factor of the same family')
        self.assertEqual([x.split(':')[0] for x in ev], ['- Yours', '- Competitor 2', '- Competitor 3', '- Competitor 5'],
                         'competitor 1 has less than you, competitor 4 the same: not quoted')
        self.assertEqual(ev[1].count('"') // 2, 5, 'at most 5 texts per page')
        comps = [dict(COMPS[0], h2_count=v) for v in (9, 1, 5)]
        res2 = result(mine=dict(MINE, h2_count=1), comps=comps)
        for i, p in enumerate(res2['pages']):
            p['evidence'] = {'h2_count': [{'text': f'T{i}', 'matched': []}]}
        sec = md_section(R.markdown(res2), R.ACTIONS_TITLE)
        self.assertEqual([x.strip().split(':')[0] for x in sec.splitlines() if x.startswith('   - ')],
                         ['- Yours', '- Competitor 1', '- Competitor 3'], 'a competitor with the same count is not quoted')

    def test_mark_edges(self):
        self.assertEqual(R.mark('a stump', ['stump', ' ', '']), 'a **stump**')
        self.assertEqual(R.mark('a  stump', ['stump', ' ']), 'a  **stump**', 'a blank variation never matches')
        self.assertEqual(R.mark('<x> & stump', []), '<x> & stump')
        self.assertEqual(R.mark('<x> & stump', [], html=True), '&lt;x&gt; &amp; stump')
        self.assertEqual(R.mark('a & stump', ['stump']), 'a & **stump**')

    def test_tag_labels(self):
        res = result()
        res['pages'][0]['duplicate_tags'] = {'h1': 2, 'canonical': 2, 'og:description': 1}
        md = R.markdown(res)
        self.assertIn('Your page has 2 H1 tags.', md)
        self.assertNotIn('2 H1 tags; search engines', md)
        self.assertIn('Your page has 2 canonical links; search engines use one. Keep one.', md)
        tags = [x for x in md.splitlines() if x.startswith('**Tags on your page:**')][0]
        self.assertNotIn('Open Graph description', tags)
        self.assertIn('<div class=card><b>Tags on your page:</b> Your page has 2 canonical links', R.html_page(res))
        res['pages'][0]['duplicate_tags'] = {'title': True, 'h1': '3'}
        self.assertNotIn('Tags on your page', R.markdown(res), 'only whole counts of 2 or more')


class Wording(unittest.TestCase):
    def test_report_text(self):
        res = result()
        self.assertIn("50 common on-page factors", R.markdown(res))
        self.assertIn('no statistics are possible', R.markdown(res))

    def test_cli_notes_and_skill(self):
        with open(os.path.join(ROOT, 'SKILL.md')) as fh:
            skill = fh.read()
        sec9 = skill[skill.index('## 9.'):]
        for must in ('control page', 'one change', 'Search Console', 'inconclusive', 'recrawl', 'directories'):
            self.assertIn(must.lower(), sec9.lower(), must)

    def test_skill_install_and_their_page(self):
        with open(os.path.join(ROOT, 'SKILL.md')) as fh:
            skill = fh.read()
        sec2 = skill[skill.index('## 2.'):skill.index('## 3.')]
        self.assertIn('--branch v1.1.1', sec2, 'install pins the release tag')
        self.assertIn('`git`', sec2, 'git is a listed prerequisite')
        self.assertIn('python3-venv', sec2)
        sec9 = skill[skill.index('## 9.'):]
        self.assertIn('targets this keyword', sec9, 'how to choose THEIR page')
        self.assertIn('home page only when', sec9)
        self.assertIn('directories', sec9, 'the competitor rules stay')
        self.assertIn('Measured without a browser', sec9)
        with open(os.path.join(ROOT, 'README.md')) as fh:
            self.assertIn('--branch v1.1.1', fh.read())

    def test_public_docs_name_no_review_round(self):
        with open(os.path.join(ROOT, 'docs', 'KNOWN-PITFALLS.md')) as fh:
            self.assertNotIn('review round', fh.read())
        with open(os.path.join(ROOT, 'url-discovery.py')) as fh:
            self.assertNotIn('§', fh.read(), 'no references to internal numbered notes')

    def test_schema_accepts_rows(self):
        sch = S.load('result.schema.json')
        row_schema = sch['properties']['factors']['items']
        for r in result()['factors']:
            self.assertEqual(S.validate(r, row_schema), [], r['key'])
        bad = dict(result()['factors'][0], band='to-do')
        self.assertTrue(S.validate(bad, row_schema), 'control: an unknown band must fail')
        self.assertIn('actions', sch['required'])


# ---- cli end to end (raw mode, local server) ----
def page(h2, title):
    body = ' '.join(['Stump removal is quick work for a crew with a grinder.'] * 30)
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title></head><body><h1>Stump removal</h1>"
            + ''.join(f'<h2>Stump removal step {i}</h2>' for i in range(h2)) + f"<p>{body}</p></body></html>")


PAGES = {'/mine': page(0, 'Home').replace('</title>', "</title><meta name=description content='a'><meta name=description content='b'>"), '/a': page(5, 'Stump removal'), '/b': page(6, 'Stump removal'), '/c': page(7, 'Stump removal')}


class Hd(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = PAGES.get(self.path)
        code = 200 if b else 404
        b = (b or 'not found').encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if code == 200 else 'text/plain')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = TS(('127.0.0.1', 0), Hd)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.srv.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_summary_and_pages_folder(self):
        out = os.path.join(OUT, 'cli')
        b = self.base
        p = subprocess.run([PY, os.path.join(ROOT, 'onpage', 'cli.py'), '--keyword', 'stump removal', '--raw', '--name', 'Acme',
                            '--mine', b + '/mine', '--competitor', b + '/a', '--competitor', b + '/b', '--competitor', b + '/c', '--out', out],
                           capture_output=True, text=True, timeout=180, env=dict(os.environ, CRAWL_DELAY='0.05'))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(out, 'onpage.json')) as fh:
            res = json.load(fh)
        self.assertEqual(S.validate(res, S.load('result.schema.json')), [])
        self.assertFalse([n for n in res['notes'] if n.startswith('PARTIAL')], 'a complete run has no PARTIAL note')
        ev = res['pages'][1].get('evidence') or {}
        self.assertEqual(ev.get('variations_title'), [{'text': 'Stump removal', 'matched': ['stump removal']}])
        self.assertIn('Competitor 1: "**Stump removal**"', R.markdown(res))
        self.assertEqual(res['pages'][0].get('duplicate_tags'), {'meta:description': 2})
        self.assertIn('Your page has 2 meta description tags', R.markdown(res))
        self.assertEqual(res['actions'], [r['key'] for r in C.actions(res['factors'])])
        summ = p.stdout.split('===== ONPAGE SUMMARY =====', 1)[1]
        for line in R.summary(res):
            self.assertIn(line, summ)
        self.assertNotIn('below all competitors on', summ, 'the summary has no headline counting factors below every competitor')
        self.assertIn('RESULT: COMPLETE (exit 0)\n', p.stdout, '--raw asked for no browser: no "measured without a browser" suffix')
        self.assertNotIn('without a browser', p.stdout)
        self.assertNotIn('\nNone\n', p.stdout)
        if any(r['band'] == 'target' and r['key'] == 'h2_count' for r in res['factors']):
            self.assertIn('h2_count', res['actions'])
        import fetch as F
        saves = 'save_to' in inspect.signature(F.fetch).parameters
        for i, (pg, role) in enumerate(zip(res['pages'], ['mine', 'competitor', 'competitor', 'competitor'])):
            folder = os.path.join(out, 'pages', f'{i}-{role}')
            if saves:  # fetch writes the measured HTML there
                self.assertTrue(os.path.isdir(folder), folder)
                self.assertEqual(pg['fetch'].get('saved_to'), os.path.join('pages', f'{i}-{role}'))
                self.assertTrue([f for f in os.listdir(folder) if f.endswith('.html')], folder)
        with open(os.path.join(out, 'onpage.md')) as fh:
            self.assertIn('Acme', fh.read())

    def test_competitor_count_1_to_5(self):
        b = self.base
        for n in (0, 6):
            args = [PY, os.path.join(ROOT, 'onpage', 'cli.py'), '--keyword', 'stump removal', '--raw', '--mine', b + '/mine',
                    '--out', os.path.join(OUT, 'cli-bad')]
            for i in range(n):
                args += ['--competitor', f'{b}/c{i}']
            p = subprocess.run(args, capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 2, (n, p.stdout + p.stderr))
            self.assertIn('1 to 5', p.stderr)

    def test_nothing_to_compare_exit_4(self):
        out = os.path.join(OUT, 'cli-nothing')
        b = self.base
        p = subprocess.run([PY, os.path.join(ROOT, 'onpage', 'cli.py'), '--keyword', 'stump removal', '--raw',
                            '--mine', b + '/gone', '--competitor', b + '/a', '--competitor', b + '/b', '--out', out],
                           capture_output=True, text=True, timeout=180, env=dict(os.environ, CRAWL_DELAY='0.05'))
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertIn('RESULT: NOTHING TO COMPARE (exit 4)', p.stdout)
        with open(os.path.join(out, 'onpage.md')) as fh:
            md = fh.read()
        self.assertIn('**Nothing to compare:**', md)
        self.assertNotIn('Partial report', md)

    def test_browser_that_cannot_start(self):
        """Chrome cannot start for any page: every page measured from the server HTML, said at the top, exit 0 (all the same way)."""
        out = os.path.join(OUT, 'cli-nobrowser')
        b = self.base
        p = subprocess.run([PY, os.path.join(ROOT, 'onpage', 'cli.py'), '--keyword', 'stump removal',
                            '--mine', b + '/mine', '--competitor', b + '/a', '--competitor', b + '/b', '--out', out],
                           capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, CRAWL_DELAY='0.05', CHROME_BIN=os.path.join(OUT, 'no-such-chrome')))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(R.NO_BROWSER_ALL, p.stdout)
        self.assertIn('RESULT: COMPLETE (exit 0) - measured without a browser', p.stdout)
        with open(os.path.join(out, 'onpage.md')) as fh:
            md = fh.read()
        self.assertIn('**' + R.NO_BROWSER_ALL + '**', md[:md.index('## ' + R.ACTIONS_TITLE)])
        for bad in ('stderr', 'FATAL', 'Failed to launch'):
            self.assertNotIn(bad, md[md.index('## Pages'):md.index(R.COLUMNS_LINE)], 'browser error text stays out of the Pages list')

    def test_partial_exit_6(self):
        out = os.path.join(OUT, 'cli-partial')
        b = self.base
        p = subprocess.run([PY, os.path.join(ROOT, 'onpage', 'cli.py'), '--keyword', 'stump removal', '--raw',
                            '--mine', b + '/mine', '--competitor', b + '/a', '--competitor', b + '/gone', '--out', out],
                           capture_output=True, text=True, timeout=180, env=dict(os.environ, CRAWL_DELAY='0.05'))
        self.assertEqual(p.returncode, 6, p.stdout + p.stderr)
        with open(os.path.join(out, 'onpage.json')) as fh:
            res = json.load(fh)
        self.assertFalse(res['complete'])
        self.assertIn('RESULT: PARTIAL', p.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
