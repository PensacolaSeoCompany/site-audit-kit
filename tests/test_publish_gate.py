#!/usr/bin/env python3
"""Tests for publish-run.sh (the only publisher). Run: python3 tests/test_publish_gate.py

Each test names the incident it guards. The gate's folders are redirected into tests/_out/ through
REPORTS_DIR (publish target) / CASES_DIR / KIT_WORK (backups) / CRAWL_DIR (findings sidecar), so the
kit's real reports/ and work/ are never touched. tests/_out/ is rebuilt on every run (a few KB)."""
import json, os, shutil, subprocess, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, '..', 'publish-run.sh')
OUT = os.path.join(HERE, '_out', 'gate')

def page(findings_html='<span class=ft>3 links lead to pages that aren\'t there</span>'):
    return f'<!doctype html><html><body>{findings_html}<p>municipal court</p></body></html>'

class Gate(unittest.TestCase):
    def setUp(self):
        # 2026-10-14: the first red run against an OLD gate (no root overrides) published
        # acme-site-audit.html to the live folder. Never run against a gate that cannot be sandboxed.
        if 'kit-paths.sh' not in open(GATE).read():
            self.fail('publish-run.sh does not take its folders from kit-paths.sh (REPORTS_DIR); refusing to run (it would publish for real)')
        shutil.rmtree(OUT, ignore_errors=True)
        self.web, self.case, self.work, self.rep = (os.path.join(OUT, d) for d in ('web', 'case', 'work', 'crawl'))
        self.bak = os.path.join(self.work, 'publish-backups')
        for d in (self.web, self.case, self.work, self.rep): os.makedirs(d)
        self.env = dict(os.environ, REPORTS_DIR=self.web, CASES_DIR=self.case, KIT_WORK=self.work,
                        CRAWL_DIR=self.rep)
        for k in ('PUBLISH_DOMAIN', 'PUBLISH_REASON', 'PUBLISH_BACKUP_DIR', 'KIT_RULES'): self.env.pop(k, None)

    def pub(self, name, domain='acme'):  # the published file for a domain (domain defaults to the slug)
        return os.path.join(self.web, domain, name)

    def make_case(self, slug, html=None, log='ALL STEPS COMPLETE\n', verified=True, sidecar=None):
        d = os.path.join(self.case, slug); os.makedirs(d, exist_ok=True)
        open(os.path.join(d, 'site-audit.html'), 'w').write(html or page())
        open(os.path.join(d, 'run1.log'), 'w').write(log)
        if verified: json.dump({'findings': []}, open(os.path.join(d, 'findings-verified.json'), 'w'))
        json.dump(sidecar or {'findings': []}, open(os.path.join(self.rep, f'{slug}-findings.json'), 'w'))
        return os.path.join(d, 'site-audit.html')

    def run_gate(self, *args):
        return subprocess.run(['bash', GATE, *args], env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def test_ok_publishes_and_stamps(self):
        src = self.make_case('acme')
        r = self.run_gate('acme', 'report', src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('kind=report slug=acme', open(self.pub('site-audit.html')).read())

    def test_municipal_is_not_cipa(self):  # a client page, 2026-10-14
        src = self.make_case('acme', html=page('<span class=ft>municipal</span>'))
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 0)

    def test_refuses_consent_text(self):  # report rule: never the pre-consent finding
        src = self.make_case('acme', html=page('<span class=ft>Trackers fire before visitors can consent</span>'))
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3)

    def test_backups_leave_web_root(self):  # RU-01 / UA-03: backups were public
        src = self.make_case('acme')
        self.run_gate('acme', 'report', src)
        open(src, 'a').write('<!-- v2 -->')
        r = self.run_gate('acme', 'report', src)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(os.path.exists(self.pub('_backup')), 'backup folder created in the reports folder')
        self.assertEqual(sorted(os.listdir(os.path.join(self.web, 'acme'))), ['site-audit.html'])
        self.assertTrue(os.listdir(os.path.join(self.bak, 'acme')), 'no backup written to the backup root')

    def test_refuses_source_of_other_slug(self):  # RU-04: wrong client's page under a slug
        self.make_case('acme'); other = self.make_case('other')
        r = self.run_gate('acme', 'report', other)
        self.assertEqual(r.returncode, 3, r.stdout)

    def test_refuses_unverified(self):  # UA-05: published before any verification
        src = self.make_case('acme', verified=False)
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3)

    def test_refuses_incomplete_run_without_not_measured(self):  # a client run, 2026-10-14
        log = '  ✗ perf  — SKIPPED\nINCOMPLETE — 1 step(s) skipped, DO NOT report as done\n'
        src = self.make_case('acme', log=log)
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3)

    def test_incomplete_run_ok_when_every_skip_is_declared(self):
        log = '  ✗ perf  — SKIPPED\nINCOMPLETE — 1 step(s) skipped, DO NOT report as done\n'
        src = self.make_case('acme', log=log, sidecar={'findings': [], 'not_measured': [
            {'step': 'perf', 'what': 'Page speed', 'why': 'The speed test could not load the page.'}]})
        r = self.run_gate('acme', 'report', src)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_refuses_meta_length_finding(self):  # 2026-10-14: report rule, no length findings
        src = self.make_case('acme', html=page(
            '<span class=ft>12 page titles are longer than 60 characters</span>'))
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3)

    def test_length_words_outside_findings_are_fine(self):
        src = self.make_case('acme', html=page('<p class=muted>Title (64 characters)</p>'))
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 0)

    def test_linkmap_kind(self):  # RU-02 / UA-04: link maps went public without the gate
        self.make_case('acme')
        lm = os.path.join(self.case, 'acme', 'linkmap.html'); open(lm, 'w').write('<html>map</html>')
        r = self.run_gate('acme', 'linkmap', lm)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(os.path.exists(self.pub('linkmap.html')))

    def test_goes_nowhere_needs_click_test(self):  # 2026-10-14: "7 buttons go nowhere" was false (they were tabs)
        src = self.make_case('acme', html=page('<span class=ft>7 service buttons go nowhere</span>'))
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3, 'no click test')
        ev = os.path.join(self.case, 'acme', 'evidence'); os.makedirs(ev)
        with open(os.path.join(ev, 'click-test-home.json'), 'w') as f:
            json.dump({'status': 'measured', 'results': [{'text': 'Repair', 'verdict': 'works'}]}, f)
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3, 'click test says works')
        with open(os.path.join(ev, 'click-test-home.json'), 'w') as f:
            json.dump({'status': 'not_measured', 'results': [{'text': 'Repair', 'verdict': 'does nothing'}]}, f)
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3, 'click test not measured')
        # synthetic click-test.js output in the real format: 3 icons "does nothing"
        with open(os.path.join(ev, 'click-test-home.json'), 'w') as f:
            json.dump({'status': 'measured', 'url': 'https://example.com/', 'results': [
                {'text': 'icon', 'selector': 'a.icon-%d' % i, 'verdict': 'does nothing'} for i in range(3)]}, f)
        r = self.run_gate('acme', 'report', src)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_nowhere_useful_is_not_a_dead_click(self):  # a client page: date link "goes nowhere useful"
        src = self.make_case('acme', html=page('<div class=wbody>Google also follows a link that goes nowhere useful.</div>'))
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 0)

    # 2026-10-14: older audits (crawl data no longer on disk) can only get text corrections.
    def test_correction_needs_existing_page_and_reason(self):
        self.make_case('acme', verified=False)
        fix = os.path.join(self.case, 'acme', 'site-audit-corrected.html'); open(fix, 'w').write(page())
        self.assertEqual(self.run_gate('acme', 'correction', fix).returncode, 3, 'no published page yet')
        os.makedirs(os.path.join(self.web, 'acme'), exist_ok=True); open(self.pub('site-audit.html'), 'w').write(page())
        self.assertEqual(self.run_gate('acme', 'correction', fix).returncode, 3, 'no reason given')
        self.env['PUBLISH_REASON'] = 'removed a false robots.txt clause'
        r = self.run_gate('acme', 'correction', fix)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('removed a false robots.txt clause', open(os.path.join(self.case, 'acme', 'CORRECTIONS.log')).read())
        self.assertTrue(os.listdir(os.path.join(self.bak, 'acme')))

    def test_correction_still_checks_report_rules(self):
        self.make_case('acme', verified=False)
        os.makedirs(os.path.join(self.web, 'acme'), exist_ok=True); open(self.pub('site-audit.html'), 'w').write(page())
        fix = os.path.join(self.case, 'acme', 'c.html'); open(fix, 'w').write(page('<span class=ft>pre-consent</span>'))
        self.env['PUBLISH_REASON'] = 'x'
        self.assertEqual(self.run_gate('acme', 'correction', fix).returncode, 3)

    # portability (2026-10-14): publish = copy into $REPORTS_DIR/<domain>/, domain read from the crawl JSON
    def test_domain_from_crawl_json(self):
        src = self.make_case('acme')
        json.dump({'site': 'www.example.com', 'pages': []}, open(os.path.join(self.rep, 'acme-full.json'), 'w'))
        r = self.run_gate('acme', 'report', src)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(os.path.exists(self.pub('site-audit.html', 'example.com')), r.stdout)
        self.assertIn(self.pub('site-audit.html', 'example.com'), r.stdout)

    def test_domain_argument_wins(self):
        src = self.make_case('acme')
        r = self.run_gate('acme', 'report', src, 'Example.org')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(os.path.exists(self.pub('site-audit.html', 'example.org')))

    def test_refuses_when_rules_file_missing(self):
        src = self.make_case('acme')
        self.env['KIT_RULES'] = os.path.join(OUT, 'no-such-rules.json')
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3)

    def test_refuses_kind_change_over_email(self):  # an approved email page is never overwritten by a report
        src = self.make_case('acme')
        self.assertEqual(self.run_gate('acme', 'email', src).returncode, 0)
        r = self.run_gate('acme', 'report', src)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn('detailed', r.stdout)

    def test_refuses_self_rating_finding(self):  # owner 2026-10-15: aggregateRating is never a finding
        src = self.make_case('acme', html=page('<span class=ft>Your website publishes its own star rating to Google</span>'))
        self.assertEqual(self.run_gate('acme', 'report', src).returncode, 3)

if __name__ == '__main__':
    unittest.main(verbosity=1)
