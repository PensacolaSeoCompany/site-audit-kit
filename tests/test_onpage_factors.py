#!/usr/bin/env python3
"""Hand-counted expected values for ALL 50 factors (onpage/factors.json) on two fixture pages.
Run: python3 tests/test_onpage_factors.py (no network).

Every number below was counted by hand from the factor definitions, not produced by running the code
(the working is in the comments). Keyword "stump removal"; variations given explicitly: stump removal,
stump, removal, stump grinding. The two pages are also the compared set for tf_idf (N = 2).
Fixture 1 is small (< 100 words). Fixture 2 is built to break rules a small page cannot: > 100 words with
hyphenated words, a www page with a <base href>, tel:/mailto: links, nested p/span/li, an SVG icon with a
<title>, a <template>, a <noscript> image, abbreviations, a heading that contains but does not start
with a variation. Control: a planted wrong expectation must be reported."""
import math, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'onpage'))
from measure import measure_page, load_registry, add_tf_idf  # noqa: E402

KW = 'stump removal'
VAR = ['stump removal', 'stump', 'removal', 'stump grinding']

URL1 = 'https://stumps.example/services/stump-removal'
HTML1 = """<!doctype html><html><head>
<meta charset="utf-8">
<title>Stump Removal | Stump Grinding Co</title>
<meta name="description" content="Fast stump removal and stump grinding.">
<meta property="og:title" content="Stump Removal Pros">
<meta property="og:description" content="We grind every stump.">
<meta property="og:type" content="article">
<link rel="canonical" href="https://stumps.example/services/stump-removal">
<link rel="apple-touch-icon" href="/icon.png">
<script type="application/ld+json">{"name":"Stump removal co","sameAs":["https://www.linkedin.com/company/stumpco"]}</script>
<style>.stump{color:red}</style>
</head><body>
<h1>Stump Removal</h1>
<p>We offer stump removal in town. Stump grinding is fast!</p>
<h2>Stump grinding</h2>
<h2>Why hire us?</h2>
<h3>Removal process</h3>
<section><ul><li>Stump removal quote</li><li>Free visit</li></ul></section>
<p><span>Stump</span> work, <i>removal</i> work. Call (312) 555-0123 today.</p>
<img src="/img/stump.jpg" alt="Stump removal truck"><img src="/img/b.jpg" alt="">
<a href="/contact">Contact us</a>
<a href="https://stumps.example/stump-removal/">Stump removal</a>
<a href="/stump-removal/">Again</a>
<a href="https://www.linkedin.com/company/stumpco">LinkedIn</a>
<a href="https://www.linkedin.com/posts/stumpco_x">Post</a>
<noscript>stump removal hidden</noscript>
<div hidden>stump removal hidden too</div>
</body></html>"""

# Page text: the title, then one line per block of visible body text (inline links and images join one line).
PAGE_TEXT1 = ("Stump Removal | Stump Grinding Co\nStump Removal\nWe offer stump removal in town. Stump grinding is fast!\n"
              "Stump grinding\nWhy hire us?\nRemoval process\nStump removal quote\nFree visit\n"
              "Stump work, removal work. Call (312) 555-0123 today.\nContact us Stump removal Again LinkedIn Post")

WANT1 = {
    # body lines: h1 1 | p 2 (stump removal, stump grinding) | h2 1 | h2 0 | h3 1 | li 1 | li 0 | p 2 | links 1 = 9
    'variations_body': 9, 'keyword_body': 4,
    'h1_count': 1, 'p_count': 2, 'image_count': 2,
    'variations_title': 2, 'starts_with_variation_title': 1,
    'variations_h1': 1, 'variations_h2': 1, 'variations_h3': 1,
    'variations_h1_h2': 2, 'variations_h1_h3': 3, 'variations_headings': 3,
    'starts_with_variation_h1': 1, 'starts_with_variation_h2': 1, 'starts_with_variation_h1_h3': 3, 'starts_with_variation_headings': 3,
    # whole: "Stump Removal" and "Stump grinding" are exactly a variation; "Removal process" is not
    'exact_variation_h1_h3': 2, 'exact_variation_headings': 2,
    'heading_questions': 1, 'has_heading_question': 1, 'question_count': 1,
    # sentences: h1 | 2 in p1 | h2 | h2 | h3 | li | li | 2 in p2 | link line = 11; with a variation: 8
    'sentence_count': 11, 'variations_sentences': 8,
    # words: title 5 + body 2+10+2+3+2+3+2+8+7 = 39 -> 44 ("(312)" is "312", "555-0123" is one word)
    'word_count': 44,
    # + noscript 3 + hidden div 4 + alt "Stump removal truck" 3 (the empty alt adds nothing)
    'word_count_with_hidden': 54,
    # under 100 words: the window is the whole page text: title 2 + body 9
    'variations_first100': 11,
    'variations_p': 4, 'variations_li': 1, 'variations_a': 1, 'variations_span': 1, 'variations_section': 1,
    # source text: title 2, JSON-LD 1, style 1, body 9, noscript 1, hidden 1 = 15; attributes: description 2,
    # og:title 1, og:description 1, canonical 1, img src 1, alt 1, two stump-removal hrefs 2 = 9
    'variations_html': 24,
    'term_frequency': round(1 + math.log10(24), 4),
    'variations_canonical': 1,
    # absolute URLs: canonical (1), JSON-LD linkedin, 3 http(s) hrefs (stump-removal 1) = 5 URLs, 2 matches
    'variations_urls': 2, 'absolute_url_count': 5,
    'variations_script': 1, 'variations_href': 2,
    # distinct resolved hrefs: /contact, /stump-removal/ (twice -> once), 2 linkedin = 1 match
    'variations_distinct_href': 1,
    'variations_meta_description': 2, 'variations_og_title': 1, 'variations_og_description': 1,
    'inline_css_kb': round(len('.stump{color:red}') / 1024, 2),
    'text_kb': round(len(PAGE_TEXT1.encode('utf-8')) / 1024, 2),
    'og_type_article': 1, 'has_apple_touch_icon': 1,
    'us_phone_count': 1,
    # JSON-LD sameAs + the /company/ link; the /posts/ link is not a profile
    'linkedin_links': 2,
    # tally p1: stump removal 14, stump grinding 4, stump 4, removal 2; p2 has all but "removal" (N = 2):
    # only removal has idf > 0: (1 + log10 2) * log10(2 / 1) / 4 variations found
    'tf_idf': round((1 + math.log10(2)) * math.log10(2) / 4, 4),
}

URL2 = 'https://www.trees.example/a/page.html'
INTRO = ' '.join(['line-up'] * 50)
MORE = ' '.join(['word'] * 60)
HTML2 = f"""<html><head><base href="https://www.trees.example/base/">
<title>Tree Service</title>
<link rel="canonical" href="https://www.trees.example/stump%20removal">
</head><body>
<header><svg class="icon"><title>stump removal icon</title><path d="M0"/></svg><a href="tel:+1-312-555-0199">Call</a> <a href="mailto:a@trees.example">Mail</a></header>
<p>{INTRO}</p>
<template><h2>Stump removal</h2><p>stump removal template</p><img src="t.jpg"></template>
<h1>Stump-Removal: <em>fast</em> and <strong>safe</strong></h1>
<div><div><p>Outer <span>stump <span>removal</span></span> text</p></div></div>
<h2>Do you need stump removal?</h2>
<p>{MORE} stump removal</p>
<ul><li>Stump grinding<ul><li>stump removal nested</li></ul></li></ul>
<a href="page2.html">Page two</a>
<a href="https://trees.example/x">Bare host</a>
<img src="a.jpg" alt="x"><noscript><img src="a.jpg"></noscript>
<p>Visit St. Louis or Ft. Myers. Ask Dr. Smith in the U.S. today. Call 312.555.0199 now.</p>
</body></html>"""
PAGE_TEXT2 = (f"Tree Service\nCall Mail\n{INTRO}\nStump-Removal: fast and safe\nOuter stump removal text\n"
              f"Do you need stump removal?\n{MORE} stump removal\nStump grinding\nstump removal nested\nPage two Bare host\n"
              "Visit St. Louis or Ft. Myers. Ask Dr. Smith in the U.S. today. Call 312.555.0199 now.")

WANT2 = {
    # body lines with a match: h1 1, nested p 1, h2 1, long p 1, li "Stump grinding" 1, nested li 1 (svg, template skipped)
    'variations_body': 6, 'keyword_body': 5,
    # the template's h2/p/img and the noscript img are not counted
    'h1_count': 1, 'p_count': 4, 'image_count': 1,
    'variations_title': 0, 'starts_with_variation_title': 0,
    'variations_h1': 1, 'variations_h2': 1, 'variations_h3': 0,
    'variations_h1_h2': 2, 'variations_h1_h3': 2, 'variations_headings': 2,
    # h1 starts with "Stump-Removal"; the h2 contains a variation but does not start with one
    'starts_with_variation_h1': 1, 'starts_with_variation_h2': 0, 'starts_with_variation_h1_h3': 1, 'starts_with_variation_headings': 1,
    'exact_variation_h1_h3': 0, 'exact_variation_headings': 0,
    'heading_questions': 1, 'has_heading_question': 1, 'question_count': 1,
    # 9 one-sentence lines + 3 in the last p (St. Ft. Dr. U.S. do not end a sentence)
    'sentence_count': 12, 'variations_sentences': 6,
    # title 2 + Call Mail 2 + 50 + 4 + 4 + 5 + 62 + 2 + 3 + 4 + last p 19 ("U.S." = u, s; 312.555.0199 = 3) = 157
    'word_count': 157, 'word_count_with_hidden': 158,
    # window = 100 words: title 2, Call Mail 2, intro 50, h1 4, p 4, h2 5 (= 67) then 33 words of the long p
    'variations_first100': 3,
    # outermost p: intro 0, nested 1, long 1, last 0; outer li holds the nested li line: 2; outer span 1
    'variations_p': 2, 'variations_li': 2, 'variations_a': 0, 'variations_span': 1, 'variations_section': 0,
    # source text: svg title 1, template h2 1 + p 1, h1 1, nested p 1, h2 1, long p 1, li 1, nested li 1 = 9;
    # attributes: canonical href "stump%20removal" (not decoded in the source: "stump" 1) = 1
    'variations_html': 10, 'term_frequency': 2.0,
    'variations_canonical': 1,
    # base href, canonical (decoded: 1 match), https://trees.example/x
    'variations_urls': 1, 'absolute_url_count': 3,
    'variations_script': 0, 'variations_href': 0, 'variations_distinct_href': 0,
    'variations_meta_description': 0, 'variations_og_title': 0, 'variations_og_description': 0,
    'inline_css_kb': 0, 'text_kb': round(len(PAGE_TEXT2.encode('utf-8')) / 1024, 2),
    'og_type_article': 0, 'has_apple_touch_icon': 0,
    # 312.555.0199 in the text; the tel: link is the same number
    'us_phone_count': 1, 'linkedin_links': 0,
    # every variation it has is on page 1 too: idf 0
    'tf_idf': 0,
}


def mismatches(got, want):
    return sorted(k for k, v in want.items() if got.get(k) != v)


class AllFifty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()
        r1 = measure_page(HTML1, URL1, KW, VAR, registry=cls.reg)
        r2 = measure_page(HTML2, URL2, KW, VAR, registry=cls.reg)
        cls.alone = dict(r1['not_measured'])
        add_tf_idf([r1, r2], registry=cls.reg)
        cls.got1, cls.got2 = r1['values'], r2['values']

    def test_every_factor_has_a_hand_count(self):
        keys = {f['key'] for f in self.reg['factors']}
        self.assertEqual(set(WANT1), keys)
        self.assertEqual(set(WANT2), keys)

    def test_values_fixture1(self):
        for k, v in WANT1.items():
            with self.subTest(k):
                self.assertEqual(self.got1[k], v, f'{k}: got {self.got1[k]}, hand count {v}')

    def test_values_fixture2(self):
        for k, v in WANT2.items():
            with self.subTest(k):
                self.assertEqual(self.got2[k], v, f'{k}: got {self.got2[k]}, hand count {v}')

    def test_tf_idf_waits_for_the_set(self):
        self.assertEqual(set(self.alone), {'tf_idf'})

    def test_every_row_has_a_band(self):
        bands = {f['key']: f['band'] for f in self.reg['factors']}
        self.assertEqual(set(bands.values()), {'target', 'length', 'context'})
        self.assertEqual(bands['variations_title'], 'target')
        self.assertEqual(bands['word_count'], 'length')
        self.assertEqual(bands['inline_css_kb'], 'context')

    def test_control_planted_wrong_value_fails(self):
        planted = dict(WANT1, h1_count=WANT1['h1_count'] + 1)
        self.assertEqual(mismatches(self.got1, planted), ['h1_count'], 'control: a wrong hand count must be reported')
        self.assertNotEqual(self.got1['variations_body'], 9 * 3,
                            'control: the overlap-sum rule (each phrase counted separately) must not match')

    def test_count_rows_that_grow_with_length_are_length_band(self):
        # heading COUNT rows add up over every heading, so a longer page has more: band "length", never an action.
        # The H1 and the single fields stay "target"; tf_idf needs a collection, so it is "context".
        bands = {f['key']: f['band'] for f in self.reg['factors']}
        for k in ('variations_h2', 'variations_h3', 'variations_h1_h2', 'variations_h1_h3', 'variations_headings',
                  'starts_with_variation_h2', 'starts_with_variation_h1_h3', 'starts_with_variation_headings',
                  'exact_variation_h1_h3', 'exact_variation_headings'):
            self.assertEqual(bands[k], 'length', k)
        for k in ('variations_h1', 'starts_with_variation_h1', 'h1_count', 'variations_title', 'starts_with_variation_title',
                  'variations_meta_description', 'variations_og_title', 'variations_og_description', 'variations_canonical',
                  'variations_first100'):
            self.assertEqual(bands[k], 'target', k)
        self.assertEqual(bands['tf_idf'], 'context')

    def test_term_frequency_definition_names_its_scope(self):
        f = {r['key']: r for r in self.reg['factors']}['term_frequency']
        self.assertIn('HTML source', f['definition'])
        self.assertIn('scripts', f['definition'])
        self.assertNotIn('usual', f['definition'])
        self.assertIn('not meaningful', {r['key']: r for r in self.reg['factors']}['tf_idf']['definition'])


# Fixture 3: the negatives fixtures 1 and 2 share (a mutation run showed wrong code passing on them).
# No question heading; og:type article but NO apple-touch-icon; stylesheet + alternate <link href> holding
# a variation (canonical count stays 1); one-word keyword "plumber" with an H1 that is exactly "Plumber";
# "plumbers" occurs exactly once in the source; charset only as http-equiv.
HTML3 = """<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta property="og:type" content="article"><title>Pipe help</title>
<link rel="stylesheet" href="/css/plumber.css"><link rel="alternate" href="https://p.example/plumber/">
<link rel="canonical" href="https://p.example/plumber"></head>
<body><h1>Plumber</h1><h2>Call today.</h2><p>Ask our plumbers.</p></body></html>"""
WANT3 = {'has_heading_question': 0, 'heading_questions': 0, 'og_type_article': 1, 'has_apple_touch_icon': 0,
         'variations_canonical': 1, 'starts_with_variation_h1': 1, 'exact_variation_headings': 1, 'variations_h1': 1,
         # source: h1 "plumber" 1, p "plumbers" 1, stylesheet href 1, alternate href 1, canonical href 1 = 5
         'variations_html': 5, 'term_frequency': round(1 + math.log10(5), 4)}


class Negatives(unittest.TestCase):
    def test_fixture3(self):
        reg = load_registry()
        r = measure_page(HTML3, 'https://p.example/plumber', 'plumber', ['plumber', 'plumbers'], registry=reg)
        self.assertEqual({k: r['values'][k] for k in WANT3}, WANT3)
        self.assertEqual(r['tally'], {'plumber': 4, 'plumbers': 1})
        # charset given only by http-equiv still counts as a declared charset (not a factor; the 'has' rule)
        from measure import Page, measure_factor
        row = {'key': 'c', 'measure': 'has', 'params': {'what': 'meta_charset'}}
        self.assertEqual(measure_factor(Page(HTML3, 'https://p.example/'), row, [], []), 1)
        self.assertEqual(measure_factor(Page(HTML3.replace('Content-Type', 'refresh'), 'https://p.example/'), row, [], []), 0)

    def test_variation_found_once(self):
        reg = load_registry()
        a = measure_page('<html><body><p>plumbers</p></body></html>', 'https://a.example/', 'plumber', ['plumber', 'plumbers'], registry=reg)
        b = measure_page('<html><body><p>plumber</p></body></html>', 'https://b.example/', 'plumber', ['plumber', 'plumbers'], registry=reg)
        add_tf_idf([a, b], registry=reg)
        self.assertEqual(a['values']['term_frequency'], 1.0)
        # one match, found on 1 of 2 pages: (1 + log10 1) * log10(2 / 1) = 0.301
        self.assertEqual(a['values']['tf_idf'], round(math.log10(2), 4))


if __name__ == '__main__':
    unittest.main(verbosity=2)
