#!/usr/bin/env python3
"""Tests for build-crawl-report.js. Run: python3 tests/test_builder.py

Each test names the published defect it guards (2026-10-14 six-reviewer round 2). Fixture pages
live on 127.0.0.1:9 (discard port, nothing listens), so the builder's live re-checks fail fast
and closed. Output goes to tests/_out/builder/ (rebuilt every run)."""
import json, os, re, shutil, subprocess, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.join(HERE, '..', 'build-crawl-report.js')
OUT = os.path.join(HERE, '_out', 'builder')
H = 'http://127.0.0.1:9'

def page(path, status=200, links=(), og=None, tw=None, title='Home', h1=1, md='A page.'):
    return {'url': H + path, 'status': status, 'content_type': 'text/html', 'title': title,
            'meta_description': md, 'heading_counts': {'h1': h1}, 'headings': {'h1': [title]},
            'word_count': 300, 'images_missing_alt': 0, 'open_graph': og or {}, 'twitter': tw or {},
            'internal_links': [{'href': H + l, 'raw_href': l, 'anchor': l} for l in links]}

def build(pages, slug='acme', sidecar=None, lh=None, env=None, out_name='site-audit.html', crawl_extra=None, lh_prefix=None):
    shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
    with open(os.path.join(OUT, f'{slug}-full.json'), 'w') as f:
        d = {'site': '127.0.0.1', 'summary': {'pages': len(pages), 'avg_word_count': 300}, 'pages': pages}
        for k, v in (crawl_extra or {}).items():
            if k == 'summary':
                d['summary'].update(v)
            else:
                d[k] = v
        json.dump(d, f)
    if sidecar is not None:
        with open(os.path.join(OUT, f'{slug}-findings.json'), 'w') as f: json.dump(sidecar, f)
    for tier, d in (lh or {}).items():
        with open(os.path.join(OUT, f'{lh_prefix or slug}-{tier}.json'), 'w') as f: json.dump(d, f)
    out = os.path.join(OUT, out_name)
    e = {k: v for k, v in os.environ.items() if k not in ('CTA_URL', 'CTA_TEL', 'CTA_TEL_TXT', 'REPORT_BRAND')}
    e.update(env or {})
    r = subprocess.run(['node', BUILDER, os.path.join(OUT, f'{slug}-full.json'), out, 'Acme Plumbing'],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300, env=e)
    assert r.returncode == 0, r.stdout + r.stderr
    with open(out) as f: return f.read(), r.stdout

def audit_tab(html):  # the client-facing pane, without the raw-data tab
    return html.split('<div class="pane" id=raw>')[0]

class Builder(unittest.TestCase):
    def test_failed_speed_test_is_not_zero(self):  # two client sites: NO_FCP shown as 0
        lh = {'mobile-4g': {'categories': {'performance': {'score': None}}, 'runtimeError': {'code': 'NO_FCP'}, 'audits': {}},
              'desktop': {'categories': {'performance': {'score': 0.91}}, 'audits': {}}}
        html, _ = build([page('/')], lh=lh)
        perf = html[html.find('<table class="perf">'):]
        perf = perf[:perf.find('</table>')]
        self.assertNotRegex(perf, r'>0<', 'a failed test rendered as score 0')
        self.assertIn('Not measured', perf)
        self.assertRegex(perf, r'>91<')

    def test_blocked_403_is_not_a_broken_link(self):  # a client site: 46 Cloudflare 403s shown as broken
        pages = [page('/', links=['/gone', '/walled']), page('/gone', 404), page('/walled', 403)]
        side = {'findings': [{'severity': 'high', 'title': "1 link leads to a page that isn't there", 'detail': 'x', 'urls': []}]}
        html, _ = build(pages, sidecar=side)
        a = audit_tab(html)
        self.assertNotIn('Broken address: /walled', a)
        self.assertIn('Broken address: /gone', a)
        self.assertRegex(a, r'class=pill>1 error<')
        self.assertIn('What we could not check', a)
        self.assertIn('/walled', a[a.find('What we could not check'):])

    def test_share_preview_copy_matches_the_site(self):  # a client site: LARGE preview claimed, 0 tags
        html, _ = build([page('/')])
        a = audit_tab(html)
        self.assertNotIn('LARGE picture-style preview', a)
        self.assertNotIn('all 1 pages', a)
        self.assertNotIn('1 page have', a)
        self.assertIn('1 page has no share', a)
        self.assertNotIn('after a flood', a)  # trade-specific copy in every report

    def test_share_preview_large_sentence_when_true(self):
        tw = {'twitter:card': 'summary_large_image'}
        html, _ = build([page('/', tw=tw), page('/b', tw=tw, title='B')])
        self.assertIn('LARGE picture-style preview', audit_tab(html))

    def test_not_measured_block(self):  # research lesson: say what we could not check
        side = {'findings': [], 'not_measured': [{'step': 'perf', 'what': 'Page speed', 'why': 'The speed test could not load your home page.'}]}
        a = audit_tab(build([page('/')], sidecar=side)[0])
        self.assertIn('What we could not check', a)
        self.assertIn('Page speed', a)
        self.assertIn('The speed test could not load your home page.', a)

    def test_book_a_call_box(self):  # CTA_URL set: the call box sits above the findings
        env = {'CTA_URL': 'https://example.com/contact', 'CTA_TEL': '+15555550100', 'REPORT_BRAND': 'Example Agency'}
        html = build([page('/')], env=env)[0]
        a = audit_tab(html)
        self.assertIn('https://example.com/contact', a)
        self.assertIn('tel:+15555550100', a)
        top = a[:a.find('What needs fixing')]
        self.assertIn('Book a call', top, 'call box must sit above the findings')
        self.assertIn('<footer>Example Agency</footer>', html)

    def test_no_branding_by_default(self):  # public kit: no call box, generic footer
        html = build([page('/')])[0]
        self.assertNotIn('Book a call', html)
        self.assertNotIn('tel:', html)
        self.assertIn('<footer>Site audit</footer>', html)

    def test_markdown_output(self):  # report.md: same findings, not-measured block, perf table
        side = {'findings': [{'severity': 'HIGH', 'title': 'Planted <b>title</b>', 'whats_happening': 'A thing happens.',
                              'fix': 'Fix it.', 'urls': [H + '/a']}],
                'not_measured': [{'step': 'perf', 'what': 'Page speed', 'why': 'Could not load.'}]}
        lh = {'desktop': {'categories': {'performance': {'score': 0.91}}, 'audits': {}},
              'mobile-4g': {'categories': {'performance': {'score': None}}, 'runtimeError': {'code': 'NO_FCP'}, 'audits': {}}}
        md, out = build([page('/', links=['/gone']), page('/gone', 404)], sidecar=side, lh=lh, out_name='report.md')
        self.assertIn('(Markdown)', out)
        self.assertIn('## What needs fixing', md)
        self.assertIn('[HIGH] Planted \\<b\\>title\\</b\\>', md)
        self.assertNotIn('<b>', md)
        self.assertIn("**What's happening:** A thing happens.", md)
        self.assertIn('## What we could not check', md)
        self.assertIn('**Page speed**: Could not load.', md)
        self.assertIn('### 404', md)
        self.assertRegex(md, r'\| Desktop · WiFi \| 91 \|')
        self.assertIn('Not measured (NO\\_FCP)', md)
        self.assertNotIn('Book a call', md)
        self.assertFalse(os.path.exists(os.path.join(OUT, 'site-audit.html')))

    def test_unknown_severity_refused(self):  # research: "Critical " sorted below LOW
        side = {'findings': [{'severity': 'Critical ', 'title': 't', 'detail': 'd'}]}
        with self.assertRaises(AssertionError): build([page('/')], sidecar=side)

    def test_noindex_definition_matches_the_crawler(self):  # DE-04: three definitions disagreed
        # A NOINDEX page must not be asked for a meta description; max-image-preview:none is indexable.
        a = audit_tab(build([page('/', md=''), page('/x', md='', title='X')], sidecar={'findings': []})[0])
        self.assertIn('2 pages don', a)
        pages = [page('/', md=''), page('/x', md='', title='X')]
        pages[0]['meta_robots'] = 'NOINDEX, follow'
        pages[1]['meta_robots'] = 'index, max-image-preview:none'
        a = audit_tab(build(pages, sidecar={'findings': []})[0])
        self.assertIn('1 page doesn', a)

DISCLAIMERS = [
    "This is an automated audit. It can miss things or flag things that aren't real problems, so have your web person check before making changes.",
    "It doesn't guarantee rankings or traffic.",
    "I didn't change anything on your site.",
    "Your site data stays on this bot's computer and isn't sent to the template creator.",
    "Some advice may not apply to your business, site, or situation. Use your own judgment and check with your web person.",
    "You choose which changes to make. The template creator and this bot are not responsible for any loss of traffic, rankings, leads, or revenue from following or not following this report.",
]


def plain(html):  # tags out, entities decoded: what a reader sees
    import html as _h
    return _h.unescape(re.sub(r'<[^>]+>', ' ', html))


AJAX = ['/wp-admin/admin-ajax.php?action=process_simple_like&post_id=%d&nonce=x' % i for i in (1, 2, 3)]


class PerfFileWithPort(unittest.TestCase):
    def test_host_with_port_finds_perf_files(self):  # smoke case A: perf-audit.sh writes 127.0.0.1:9-<tier>.json
        lh = {'desktop': {'categories': {'performance': {'score': 0.77}}, 'audits': {}}}
        html, out = build([page('/')], lh=lh, lh_prefix='127.0.0.1:9')
        self.assertIn('1 perf tiers', out)
        self.assertIn('<table class="perf">', html)
        self.assertRegex(html, r'>77<')


class NotRealSources(unittest.TestCase):
    """A live report (2026-10-14) counted WordPress like-button requests (admin-ajax.php?action=...)
    as pages that link to a broken address. Only real pages may count as sources."""
    def test_admin_ajax_is_not_a_linking_page(self):
        pages = [page('/', links=['/about', '/gone']), page('/about', links=['/gone'], title='About'), page('/gone', 404)]
        pages += [page(u, links=['/gone'], title='') for u in AJAX]
        side = {'findings': [{'severity': 'high', 'title': "1 link leads to a page that isn't there", 'detail': 'x', 'urls': []}]}
        for out_name in ('site-audit.html', 'report.md'):
            doc, _ = build(pages, sidecar=side, out_name=out_name)
            top = doc[:doc.find('All pages')] if out_name.endswith('html') else doc
            self.assertNotIn('admin-ajax', top, out_name)
            self.assertRegex(top, r'[Ll]inked from 2 pages', out_name)
            self.assertNotRegex(top, r'[Ll]inked from 5 pages', out_name)
            if out_name.endswith('html'):
                self.assertIn("isn't there</span><span class=cnt>2 URLs<", top)


class SafetyCopy(unittest.TestCase):
    def test_disclaimers_in_html_and_md(self):  # owner safety rule 2026-10-14: verbatim, both outputs
        html, _ = build([page('/')])
        md, _ = build([page('/')], out_name='report.md')
        for line in DISCLAIMERS:
            self.assertIn(line, plain(html), 'html: ' + line)
            self.assertIn(line, md, 'md: ' + line)
        self.assertGreater(plain(html).rfind(DISCLAIMERS[0]), plain(html).find('What needs fixing'), 'disclaimers belong at the bottom')

    def test_complete_run_is_not_partial(self):
        html, _ = build([page('/')], sidecar={'findings': []})
        self.assertNotIn('PARTIAL', html)
        md, _ = build([page('/')], sidecar={'findings': []}, out_name='report.md')
        self.assertNotIn('PARTIAL', md)

    def _partial(self, reason_part, **kw):
        html, _ = build([page('/')], **kw)
        title = re.search(r'<title>(.*?)</title>', html).group(1)
        self.assertIn('PARTIAL', title)
        banner = re.search(r'PARTIAL REPORT: ([^<]+)', html)
        self.assertTrue(banner, 'no PARTIAL REPORT banner')
        self.assertIn(reason_part, banner.group(1))
        self.assertLess(html.find('PARTIAL REPORT:'), html.find('What needs fixing'), 'banner must be at the top')
        md, _ = build([page('/')], out_name='report.md', **kw)
        first = md.splitlines()[0]
        self.assertIn('PARTIAL', first)
        self.assertIn('PARTIAL REPORT: ', md)
        self.assertIn(reason_part, md[md.find('PARTIAL REPORT: '):].splitlines()[0])

    def test_partial_from_not_measured(self):
        self._partial('Page speed', sidecar={'findings': [], 'not_measured': [{'step': 'perf', 'what': 'Page speed', 'why': 'x'}]})

    def test_partial_from_sidecar_key(self):
        self._partial('the run stopped early', sidecar={'findings': [], 'partial': {'reason': 'the run stopped early'}})

    def test_partial_from_could_not_check(self):
        self._partial('Contact form', sidecar={'findings': [], 'could_not_check': [{'what': 'Contact form', 'why': 'x'}]})

    def test_partial_from_robots(self):
        self._partial('robots.txt', crawl_extra={'summary': {'robots_disallowed': 3},
                                                  'robots_disallowed': [{'url': H + '/p', 'status': None, 'note': 'robots-disallowed'}]})

    def test_partial_from_page_cap(self):
        self._partial('500-page limit', crawl_extra={'summary': {'page_cap_hit': True, 'max_pages': 500}})

    def test_partial_from_incomplete_crawl(self):
        self._partial('crawl did not finish', crawl_extra={'partial': True})


if __name__ == '__main__':
    unittest.main(verbosity=1)
