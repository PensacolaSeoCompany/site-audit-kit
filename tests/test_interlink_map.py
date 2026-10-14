#!/usr/bin/env python3
"""Tests for interlink-map.py (the public link-graph page). Run: python3 tests/test_interlink_map.py

Guards objections/SYNTHESIS.md A2 / B4 (RU-02, UA-04): crawled URLs and anchor text were written
raw into a <script> block (json.dumps) and into tip.innerHTML, so a link such as
/a</script><img src=x onerror=alert(1)> ran script on the published report host.
Outputs only under tests/_out/linkmap/ (rebuilt on every run, a few KB)."""
import json, os, re, shutil, subprocess, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', 'interlink-map.py')
OUT = os.path.join(HERE, '_out', 'linkmap')
BASE = 'https://example.com'
EVIL = '/a</script><img src=x onerror=alert(1)>'
EVIL_ANCHOR = 'click</script><img src=y onerror=alert(2)>& x'

def crawl():
    pages = ['', '/about', '/contact', EVIL]
    def links(src):
        return [{'href': BASE + t, 'anchor': EVIL_ANCHOR if t == EVIL else 'go ' + (t or 'home')}
                for t in pages if t != src]
    return {'pages': [{'url': BASE + p, 'internal_links': links(p)} for p in pages]}

class LinkMap(unittest.TestCase):
    longMessage = False  # keep failure output short (the page is ~6 KB)
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
        src = os.path.join(OUT, 'crawl.json'); cls.html_path = os.path.join(OUT, 'linkmap.html')
        with open(src, 'w') as f: json.dump(crawl(), f)
        cls.proc = subprocess.run(['python3', TOOL, src, cls.html_path, 'Acme <b>&</b> Co'],
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL)
        cls.page = ''
        if os.path.exists(cls.html_path):
            with open(cls.html_path, encoding='utf-8') as f: cls.page = f.read()

    def test_tool_ran(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertIn('</html>', self.page)

    def test_no_script_breakout(self):
        # exactly two real closing tags: the d3 loader and the inline graph script
        self.assertEqual(self.page.lower().count('</script'), 2)

    def test_no_raw_img_anywhere(self):
        self.assertNotIn('<img', self.page.lower(), 'raw <img in page')

    def test_embedded_json_has_no_angle_brackets_or_separators(self):
        i, j = self.page.find('const nodes='), self.page.find('const W=')
        self.assertTrue(0 <= i < j, 'embedded data block not found')
        for bad in ('<', '>', '\u2028', '\u2029'):
            self.assertNotIn(bad, self.page[i:j], f'raw {bad!r} in embedded JSON')

    def test_data_still_round_trips(self):
        m = re.search(r'const nodes=(\[.*?\]), links=', self.page, re.S)
        self.assertIsNotNone(m)
        labels = {n['label'] for n in json.loads(m.group(1))}
        self.assertIn(EVIL, labels)  # escaped in transport, intact after JSON.parse

    def test_tooltip_does_not_use_innerHTML(self):
        self.assertNotIn('innerHTML', self.page, 'innerHTML used')

    def test_lists_escape_crawled_text(self):
        self.assertIn('&lt;/script&gt;&lt;img', self.page, 'crawled text not escaped in lists')

if __name__ == '__main__':
    unittest.main(verbosity=2)
