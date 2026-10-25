#!/usr/bin/env python3
"""Hostile pages for onpage/measure.py: it must never raise, never hang, and say 'not measured'
only when there is truly no HTML. Run: python3 tests/test_onpage_hostile.py (no network).

Cases: empty, whitespace, binary garbage, an XHTML page with an XML encoding declaration, no <body>,
unclosed tags, 3,000-deep nesting, a 6 MB page (time limit), malformed link addresses, a page with
only a <frameset>, script-only page, and every factor returning a number."""
import os, sys, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'onpage'))
from measure import measure_page, load_registry, add_tf_idf  # noqa: E402
import codecs  # noqa: E402
import textutil as T  # noqa: E402
from textutil import tokens, words, sentences  # noqa: E402

REG = load_registry()
KW, VAR = 'stump removal', ['stump removal', 'stump', 'removal']
URL = 'https://stumps.example/page'


def m(html):
    return measure_page(html, URL, KW, VAR, registry=REG)


class Hostile(unittest.TestCase):
    def assert_measured(self, r):
        self.assertEqual(set(r['not_measured']), {'tf_idf'}, 'only tf_idf waits for the compared set')
        add_tf_idf([r], registry=REG)
        self.assertEqual(len(r['values']), 50, r['not_measured'])
        for k, v in r['values'].items():
            self.assertIsInstance(v, (int, float), k)
            self.assertFalse(isinstance(v, bool), k)

    def test_empty_is_not_measured(self):
        for h in ('', '   \n\t ', None):
            r = m(h)
            self.assertEqual(r['values'], {})
            self.assertEqual(len(r['not_measured']), 50)

    def test_binary_garbage_does_not_raise(self):
        r = m(os.urandom(5000).decode('latin-1'))
        self.assertIsInstance(r['values'], dict)

    def test_xml_declaration_xhtml(self):
        h = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
             '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd"><html xmlns="http://www.w3.org/1999/xhtml">'
             '<head><title>Stump Removal</title></head><body><h1>Stump removal</h1><p>stump removal</p></body></html>')
        r = m(h)
        self.assert_measured(r)
        self.assertEqual(r['values']['h1_count'], 1)
        self.assertEqual(r['values']['keyword_body'], 2)

    def test_no_body_tag(self):
        r = m('<title>x</title><h1>Stump removal</h1><p>stump removal here</p>')
        self.assert_measured(r)
        self.assertEqual(r['values']['h1_count'], 1)
        self.assertGreaterEqual(r['values']['word_count'], 5)

    def test_unclosed_tags(self):
        r = m('<html><body><h1>Stump removal<p>one<p>two <a href="/x">stump<li>removal')
        self.assert_measured(r)

    def test_deep_nesting(self):
        h = '<html><body>' + '<div>' * 3000 + 'stump removal' + '</div>' * 3000 + '</body></html>'
        t = time.time()
        r = m(h)
        self.assert_measured(r)
        self.assertLess(time.time() - t, 10)

    def test_big_page_time(self):
        para = '<p>Stump removal and stump grinding in town. ' + 'word ' * 60 + '</p><a href="/a">stump</a><img src="x.jpg" alt="stump">\n'
        h = '<html><head><title>Stump removal</title></head><body>' + para * 13500 + '</body></html>'
        self.assertGreater(len(h), 5_000_000)
        t = time.time()
        r = m(h)
        dt = time.time() - t
        self.assert_measured(r)
        self.assertLess(dt, 60, f'6 MB page took {dt:.1f} s')

    def test_malformed_links(self):
        h = ('<html><body><a href="http://[::1">a</a><a href="https://wpforms id=3600/">b</a><a href="http://a.example:99999/">c</a>'
             '<a href="javascript:void(0)">d</a><a href="mailto:x@y.z">e</a><a href="#top">f</a><a href="//cdn.example/x">g</a>'
             '<a href="https://www.linkedin.com/company/stumpco">li</a></body></html>')
        r = m(h)
        self.assert_measured(r)
        self.assertEqual(r['values']['linkedin_links'], 1)

    def test_malformed_links_in_link_scopes(self):
        h = '<html><body><a href="https://wpforms id=3600/">b</a><a href="http://[::1">a</a><a href="https://linkedin.com/in/jo">j</a></body></html>'
        got = mrow(h, row("li", "social", networks=["linkedin"], distinct=True, where="links"),
                   row("d", "matches", target="variations", scope="distinct_href"))
        self.assertEqual(got["li"], 1)

    def test_frameset_only(self):
        r = m('<html><frameset><frame src="a.html"></frameset></html>')
        self.assertIsInstance(r['values'], dict)

    def test_script_only_page_has_no_words(self):
        r = m('<html><body><script>var stump_removal = "stump removal stump removal";</script></body></html>')
        self.assert_measured(r)
        self.assertEqual(r['values']['word_count'], 0, 'script code is not visible text')
        self.assertEqual(r['values']['keyword_body'], 0)


def row(key, measure, **params):
    return {"key": key, "measure": measure, "params": params}


def mrow(html, *rows, kw=KW, var=VAR, url=URL):
    return measure_page(html, url, kw, var, registry={"factors": list(rows)})["values"]


KWB = row("kw_body", "matches", target="keyword", scope="body_text")


class Fixes(unittest.TestCase):
    """Measurer rules a careless change could break."""

    def test_phrase_does_not_join_two_blocks(self):
        self.assertEqual(mrow("<body><p>we do stump</p><p>removal is next</p></body>", KWB)["kw_body"], 0)
        self.assertEqual(mrow("<body><li>stump</li><li>removal</li></body>", row("li", "matches", target="keyword", scope="tag:ul"))["li"], 0)
        self.assertEqual(mrow("<body><p>stump <b>removal</b></p></body>", KWB)["kw_body"], 1, "inline tags still join")

    def test_title_and_h1_do_not_join(self):
        h = "<html><head><title>Best stump</title></head><body><h1>Removal today</h1></body></html>"
        r = row("f", "matches", target="keyword", scope="first100")
        self.assertEqual(mrow(h, r)["f"], 0)

    def test_source_scope_breaks_at_blocks(self):
        r = row("src", "matches", target="keyword", scope="html_source")
        self.assertEqual(mrow("<body><div>stump</div><div>removal</div></body>", r)["src"], 0)
        self.assertEqual(mrow("<body><div>stump <span>removal</span></div></body>", r)["src"], 1)

    def test_lxml_depth_400(self):
        h = "<html><body>" + "<div>" * 400 + '<h2>stump removal</h2><a href="/x">stump</a>' + "</div>" * 400 + "</body></html>"
        got = mrow(h, row("h2", "tag_count", tags=["h2"]), row("v", "matches", target="variations", scope="tag:h2"), KWB)
        self.assertEqual((got["h2"], got["v"], got["kw_body"]), (1, 1, 1))

    def test_deep_text_walk_2000(self):
        # libxml2 with huge_tree keeps 2,048 levels (the default keeps 255); the text walk is iterative
        h = "<html><body>" + "<span>" * 2000 + "stump removal" + "</span>" * 2000 + "</body></html>"
        self.assertEqual(mrow(h, KWB)["kw_body"], 1)

    def test_svg_title_is_not_the_page_title(self):
        h = ('<html><head><title>Stump removal</title></head><body><svg><title>stump removal icon</title></svg>'
             '<h1>x</h1></body></html>')
        self.assertEqual(mrow(h, row("t", "matches", target="keyword", scope="title"))["t"], 1)

    def test_tag_counts_skip_template_noscript_hidden(self):
        h = ('<body><template><h1>a</h1><img src="t.jpg"></template><noscript><img src="n.jpg"></noscript>'
             '<div hidden><h1>b</h1></div><h1>c</h1><img src="v.jpg"></body>')
        got = mrow(h, row("h1", "tag_count", tags=["h1"]), row("img", "tag_count", tags=["img"]))
        self.assertEqual((got["h1"], got["img"]), (1, 1))

    def test_sentence_abbreviations(self):
        h = "<body><p>We serve St. Louis and Ft. Myers. Ask Dr. Smith about the U.S. rules today. Mt. Hood too!</p></body>"
        self.assertEqual(mrow(h, row("s", "sentence_count"))["s"], 3)

    def test_first100_counts_words_not_tokens(self):
        h = "<body><p>" + "line-up " * 98 + "stump removal</p></body>"
        self.assertEqual(mrow(h, row("f", "matches", target="keyword", scope="first100"))["f"], 1)
        h = "<body><p>stump removal " + "line-up " * 98 + "</p></body>"
        self.assertEqual(mrow(h, row("l", "matches", target="keyword", scope="last100"))["l"], 1)
        h = "<body><p>" + "a & " * 50 + "stump removal</p></body>"  # 100 words before the phrase: outside
        self.assertEqual(mrow(h, row("f", "matches", target="keyword", scope="first100"))["f"], 0)

    def test_linkedin_non_account_urls(self):
        h = ('<body><a href="https://www.linkedin.com/company/acme">a</a><a href="https://www.linkedin.com/posts/acme_x">b</a>'
             '<a href="https://www.linkedin.com/pulse/how-to">c</a><a href="https://www.linkedin.com/in/jane/">d</a>'
             '<a href="https://linkedin.com/shareArticle?url=x">e</a></body>')
        r = row("li", "social", networks=["linkedin"], distinct=False, where="links")
        self.assertEqual(mrow(h, r)["li"], 2)

    def test_cjk_and_thai_substring_match(self):
        h = "<body><p>東京の歯医者さんです。大阪の歯医者</p></body>"
        self.assertEqual(mrow(h, KWB, kw="歯医者")["kw_body"], 2)
        h = "<body><p>คลินิกทันตแพทย์กรุงเทพ</p></body>"
        self.assertEqual(mrow(h, KWB, kw="ทันตแพทย์")["kw_body"], 1)

    def test_bytes_are_decoded_by_charset(self):
        raw = '<html><head><meta charset="windows-1252"></head><body><p>caf\u00e9 stump removal</p></body></html>'.encode("cp1252")
        r = measure_page(raw, URL, "café", ["café"], registry={"factors": [KWB]})
        self.assertEqual(r["values"]["kw_body"], 1)


class MutantKillers(unittest.TestCase):
    """Rules a mutation run could change with no test failing."""

    def test_hidden_element_itself_not_counted(self):
        self.assertEqual(mrow("<body><h1 hidden>a</h1><h1>b</h1></body>", row("h", "tag_count", tags=["h1"]))["h"], 1)

    def test_element_edges_separate_words(self):
        self.assertEqual(mrow("<body><p>stump<template>x</template>removal</p></body>", row("w", "word_count"))["w"], 2)

    def test_svg_title_alone_is_no_title(self):
        h = "<html><body><svg><title>stump removal</title></svg><p>x</p></body></html>"
        self.assertEqual(mrow(h, row("t", "matches", target="keyword", scope="title"))["t"], 0)

    def test_canonical_is_decoded(self):
        h = '<html><head><link rel="canonical" href="https://x.example/stump%2Dremoval"></head><body>x</body></html>'
        self.assertEqual(mrow(h, row("c", "matches", target="keyword", scope="canonical"))["c"], 1)

    def test_css_kb_is_bytes(self):
        h = "<html><head><style>" + "\u00e9" * 20 + "</style></head><body>x</body></html>"
        self.assertEqual(mrow(h, row("c", "css_kb"))["c"], 0.04)

    def test_heading_questions(self):
        h = "<body><h2>Why us? Because</h2><template><h2>Q?</h2></template><h2>A?</h2><h3>B?</h3></body>"
        got = mrow(h, row("n", "questions", scope="headings"), row("y", "questions", scope="headings", **{"as": "yes/no"}))
        self.assertEqual((got["n"], got["y"]), (2, 1))

    def test_og_type_must_be_article(self):
        h = '<html><head><meta property="og:type" content="website"></head><body>x</body></html>'
        self.assertEqual(mrow(h, row("o", "has", what="og_type_article"))["o"], 0)

    def test_window_edges(self):
        f = row("f", "matches", target="keyword", scope="first100")
        l = row("l", "matches", target="keyword", scope="last100")
        self.assertEqual(mrow("<body><p>" + "w " * 99 + "stump removal</p></body>", f)["f"], 0, "word 100-101 straddles the edge")
        self.assertEqual(mrow("<body><p>x stump removal " + "w " * 98 + "</p></body>", l)["l"], 1, "last 100 starts at stump")

    def test_nfkc_fullwidth(self):
        self.assertEqual(mrow("<body><p>\uff53\uff54\uff55\uff4d\uff50 \uff52\uff45\uff4d\uff4f\uff56\uff41\uff4c</p></body>", KWB)["kw_body"], 1)

    def test_meta_charset_koi8r(self):
        raw = '<html><head><meta charset="koi8-r"></head><body><p>\u043f\u0440\u0438\u0432\u0435\u0442</p></body></html>'.encode("koi8-r")
        r = measure_page(raw, URL, "\u043f\u0440\u0438\u0432\u0435\u0442", [], registry={"factors": [KWB]})
        self.assertEqual(r["values"]["kw_body"], 1)

    def test_cjk_stops_and_bullets(self):
        s = row("s", "sentence_count")
        self.assertEqual(mrow("<body><p>\u6771\u4eac\u3067\u3059\u3002\u5927\u962a\u3067\u3059\u3002</p></body>", s)["s"], 2)
        self.assertEqual(mrow("<body><p>stump \u2022 removal</p></body>", s)["s"], 2)

    def test_comments_are_source(self):
        self.assertEqual(mrow("<body><!-- stump removal --><p>x</p></body>", row("h", "matches", target="keyword", scope="html_source"))["h"], 1)

    def test_base_href_resolves_distinct_links(self):
        h = '<html><head><base href="https://a.example/b/"></head><body><a href="stump-removal">1</a><a href="https://a.example/b/stump-removal">2</a></body></html>'
        got = mrow(h, row("d", "matches", target="keyword", scope="distinct_href"), url="https://a.example/p/")
        self.assertEqual(got["d"], 1)


B3 = "<!doctype html><html><head><title>t</title>%s</head><body>%s</body></html>"


def v3(body, kw="stump removal", head="", var=None):
    return measure_page(B3 % (head, body), "https://ex.com/", kw, var or [kw], registry=REG)


class Extraction(unittest.TestCase):
    """Text extraction cases from real pages (<br> in headings, scripts with combining marks, invisible
    characters inside words, duplicate single-value tags)."""

    def test_br_in_heading_is_one_heading(self):
        x = v3("<h2>Los Angeles<br>DUI Lawyer</h2>", "los angeles dui lawyer")["values"]
        self.assertEqual((x["variations_h2"], x["starts_with_variation_h2"], x["exact_variation_headings"], x["variations_body"]), (1, 1, 1, 1))

    def test_br_in_nested_heading_markup(self):
        x = v3("<h1><span>Stump<br/></span>removal</h1><p>stump<br>removal</p>")["values"]
        self.assertEqual((x["variations_h1"], x["variations_p"]), (1, 0))

    def test_br_in_paragraph_still_breaks(self):
        self.assertEqual(v3("<p>stump<br>removal</p>")["values"]["variations_p"], 0)

    def test_br_after_heading_closes_breaks_again(self):
        self.assertEqual(v3("<h2>stump</h2><div>x<br>removal stump<br>removal</div>")["values"]["variations_body"], 0)

    def test_soft_hyphen_and_zero_width_inside_a_word(self):
        self.assertEqual(v3("<p>stump re&shy;moval</p><p>stump re\u200bmoval</p><p>stump\u2060 removal</p>")["values"]["variations_body"], 3)

    def test_hindi_words_are_whole(self):
        self.assertEqual(tokens("\u0926\u0902\u0924 \u091a\u093f\u0915\u093f\u0924\u094d\u0938\u0915 \u0926\u093f\u0932\u094d\u0932\u0940"),
                         ["\u0926\u0902\u0924", "\u091a\u093f\u0915\u093f\u0924\u094d\u0938\u0915", "\u0926\u093f\u0932\u094d\u0932\u0940"])
        self.assertEqual(len(words("\u0938\u092c\u0938\u0947 \u0905\u091a\u094d\u091b\u093e \u0926\u0902\u0924 \u091a\u093f\u0915\u093f\u0924\u094d\u0938\u0915")), 4)

    def test_turkish_capital_dotted_i(self):
        self.assertEqual(v3("<h1>\u0130stanbul Di\u015f Klini\u011fi</h1>", "istanbul di\u015f")["values"]["variations_h1"], 1)

    def test_arabic_vowel_marks_ignored(self):
        self.assertEqual(v3("<h1>\u0637\u064e\u0628\u0650\u064a\u0628 \u0623\u0633\u0646\u0627\u0646</h1>", "\u0637\u0628\u064a\u0628 \u0623\u0633\u0646\u0627\u0646")["values"]["variations_h1"], 1)

    def test_ascii_text_unchanged(self):
        self.assertEqual(tokens("Don't stump_removal 5-star"), ["don't", "stump_removal", "5", "star"])
        self.assertEqual(words("Don't line-up & go"), ["don't", "line-up", "&", "go"])


class SingleValueTags(unittest.TestCase):
    """A browser and a search engine use ONE title, description, og:title, og:description and canonical:
    a second tag is a page defect (reported in notes), not more keyword use."""
    HEAD = ("<title>stump removal</title>"
            "<meta name=description content='stump removal'><meta name=description content='stump removal stump removal'>"
            "<meta property='og:title' content='stump'><meta property='og:title' content='stump removal stump'>"
            "<meta property='og:description' content='removal'><meta property='og:description' content='stump stump'>"
            "<link rel=canonical href='https://ex.com/stump-removal'><link rel=canonical href='https://ex.com/stump/removal'>")

    @classmethod
    def setUpClass(cls):
        cls.r = v3("<h1>stump removal</h1><h1>other</h1><p>x</p>", head=cls.HEAD, var=VAR)

    def test_first_tag_only(self):
        x = self.r["values"]
        self.assertEqual((x["variations_meta_description"], x["variations_og_title"], x["variations_og_description"],
                          x["variations_canonical"]), (1, 1, 1, 1))

    def test_duplicate_tags_note(self):
        # B3 has its own <title>t</title> before the head: 2 titles
        self.assertEqual(self.r["notes"], {"duplicate_tags": {"title": 2, "meta:description": 2, "og:title": 2,
                                                              "og:description": 2, "canonical": 2, "h1": 2}})

    def test_no_note_when_single(self):
        r = v3("<h1>stump</h1>", head="<meta name=description content='stump'>")
        self.assertEqual(r["notes"], {})

    def test_empty_page_has_notes(self):
        self.assertEqual(measure_page("", URL, KW, VAR, registry=REG)["notes"], {})

    def test_hidden_h1_not_a_duplicate(self):
        self.assertEqual(v3("<h1>a</h1><template><h1>b</h1></template><h1 hidden>c</h1>")["notes"], {})

    def test_svg_title_not_a_duplicate(self):
        self.assertEqual(v3("<svg><title>icon</title></svg><p>x</p>")["notes"], {})


class TextUtil(unittest.TestCase):
    """Direct checks of the text helpers (each one a mutant that otherwise survived)."""

    def test_norm_empty(self):
        self.assertEqual((T.norm(None), T.norm("")), ("", ""))

    def test_marks_and_punctuation(self):
        self.assertEqual(tokens("x\u0300y z"), ["x\u0300y", "z"])          # a combining mark stays in its word
        self.assertEqual(tokens("stump\u2014removal \u201cok\u201d"), ["stump", "removal", "ok"])  # dashes, quotes split

    def test_phrase_tokens(self):
        self.assertEqual(T.phrase_tokens(["Stump Removal", "stump removal", "", " ", "stump"]), [("stump", "removal"), ("stump",)])

    def test_count_phrase(self):
        self.assertEqual(T.count_phrase(["a", "b"], ("a", "b")), 1)
        self.assertEqual(T.count_phrase(["a"], ()), 0)
        self.assertEqual(T.count_phrase(["a"], ("a", "b")), 0)
        self.assertEqual(T.count_phrase(["a", "c", "b"], ("a", "b")), 0)

    def test_count_phrases(self):
        self.assertEqual(T.count_phrases([], [("a",)]), 0)
        self.assertEqual(T.count_phrases(["a"], []), 0)
        self.assertEqual(T.count_phrases(tokens("stump removal removal"), T.phrase_tokens(["stump removal", "removal"])), 2)

    def test_window_lines(self):
        self.assertEqual(T.window_lines("a b\nc", 3), [["a", "b"], ["c"]])
        self.assertEqual(T.window_lines("a b\nc d", 3), [["a", "b"], ["c"]])
        self.assertEqual(T.window_lines("a b c d", 2, first=False), [["c", "d"]])
        self.assertEqual(T.window_lines("a b\nc\nd e", 3, first=False), [["c"], ["d", "e"]])
        self.assertEqual(T.window_lines("x y\na b c", 2, first=False), [["b", "c"]])

    def test_am_pm(self):
        self.assertEqual(len(sentences("Open at 9 a.m. every day.")), 1)
        self.assertEqual(len(sentences("Open at 9 a.m. Then close.")), 2)
        self.assertEqual(len(sentences("Plan a.b. Then go.")), 2)
        self.assertEqual(sentences("Hi. Ask Dr. Smith."), ["Hi.", "Ask Dr. Smith."])

    def test_decode_html(self):
        self.assertEqual(T.decode_html("abc"), "abc")
        self.assertIsNone(T.decode_html(None))
        self.assertEqual(T.decode_html("caf\u00e9".encode("utf-8")), "caf\u00e9")             # no meta: UTF-8
        self.assertEqual(T.decode_html(b"caf\xe9"), "caf\u00e9")                             # not UTF-8: windows-1252
        self.assertEqual(T.decode_html(codecs.BOM_UTF8 + "\u00e9".encode()), "\u00e9")
        self.assertEqual(T.decode_html("\ufeff\u00e9".encode("utf-16")), "\u00e9")
        self.assertEqual(T.decode_html("\u00e9".encode("cp1252"), "text/html; charset=windows-1252"), "\u00e9")
        self.assertEqual(T.decode_html("\u00e9".encode("utf-8"), "text/html"), "\u00e9")
        self.assertEqual(T.decode_html("\u043f\u0440\u0438".encode("koi8-r"), "text/html; charset=koi8-r"), "\u043f\u0440\u0438")
        self.assertEqual(T.decode_html(b'<meta charset="iso-8859-1">\x93hi\x94'), '<meta charset="iso-8859-1">\u201chi\u201d')
        self.assertEqual(T.decode_html(b'<meta charset="utf-16">caf\xc3\xa9'), '<meta charset="utf-16">caf\u00e9')


class MeasureKillers(unittest.TestCase):
    def test_two_brs_in_one_heading(self):
        self.assertEqual(v3("<h2>x<br>stump<br>removal</h2>")["values"]["variations_h2"], 1)

    def test_br_in_paragraph_after_a_heading(self):
        self.assertEqual(v3("<h2>a</h2><p>stump<br>removal</p>")["values"]["variations_p"], 0)

    def test_only_rel_canonical_links_are_canonicals(self):
        r = v3("<p>x</p>", head="<link rel=stylesheet href='/x.css'><link rel=canonical>"
                                "<link rel=canonical href='/stump-removal'>", var=VAR)
        self.assertEqual((r["values"]["variations_canonical"], r["notes"]), (1, {}))

    def test_page_tokens_are_title_plus_body(self):
        from measure import Page
        self.assertEqual(Page(B3 % ("", "<h1>Stump</h1>"), URL).page_tokens, ["t", "stump"])

    def test_unknown_measure_kind_is_refused(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.json")
            with open(p, "w") as f:
                json.dump({"factors": [{"key": "x", "measure": "nope"}]}, f)
            with self.assertRaises(ValueError):
                load_registry(p)


SOC = """<html><head><script type="application/ld+json">{"sameAs":["https://facebook.com/onlyld"]}</script></head><body>
<a href="https://facebook.com/stumpco">a</a><a href="https://www.facebook.com/stumpco/">b</a><a href="https://facebook.com/sharer.php?u=x">c</a>
<a href="https://facebook.com/">d</a><a href="https://instagram.com/@x">e</a><a href="https://youtube.com/@chan">f</a><a href="https://youtube.com/channel/UC1">g</a>
<a href="https://linkedin.com/company">h</a><a href="https://notfacebook.com/x">i</a><a href="https://m.facebook.com/other">j</a><a name="top">k</a></body></html>"""


class SocialPhonesMisc(unittest.TestCase):
    """Hand-checked against the rules in measure.SOCIAL and _phones (old code a mutation run left unguarded)."""

    def test_social_profiles(self):
        def soc(**p):
            return mrow(SOC, row("x", "social", **p))["x"]
        # facebook links: stumpco, www stumpco/ (same account), m.facebook.com/other; sharer, bare host, other host are not
        self.assertEqual(soc(networks=["facebook"], distinct=False, where="links"), 3)
        self.assertEqual(soc(networks=["facebook"], distinct=True, where="links"), 2)
        self.assertEqual(soc(networks=["facebook"], distinct=True, where="source"), 3)   # + the JSON-LD sameAs
        # instagram @x is not an account path; youtube needs channel/c/user; linkedin needs company/<name>
        self.assertEqual(soc(networks=["instagram", "youtube", "linkedin"], distinct=False, where="links"), 1)
        two = '<body><a href="https://youtube.com/channel/A">1</a><a href="https://youtube.com/channel/B">2</a></body>'
        self.assertEqual(mrow(two, row("y", "social", networks=["youtube"], distinct=True, where="links"))["y"], 2)

    def test_tel_links(self):
        ph = row("p", "phones")
        self.assertEqual(mrow('<body><a href="tel:+1 (312) 555-0100">Call</a></body>', ph)["p"], 1)
        self.assertEqual(mrow('<body><a href="TEL:3125550100">Call</a></body>', ph)["p"], 1)
        self.assertEqual(mrow('<body><p>312-555-0100</p><a href="tel:13125550100">Call</a></body>', ph)["p"], 1)
        self.assertEqual(mrow('<body><a href="tel:3125550100">a</a><a href="tel:3125550100">b</a></body>', ph)["p"], 1)
        for bad in ("tel:123", "tel:3121550100", "tel:1125550100", "tel:23125550100", "sms:3125550100"):
            self.assertEqual(mrow(f'<body><a href="{bad}">x</a></body>', ph)["p"], 0, bad)

    def test_noscript_with_elements_keeps_word_edges(self):
        w = row("w", "word_count", scope="with_hidden")
        self.assertEqual(mrow("<body><noscript><p>alpha</p><p>beta</p></noscript></body>", w)["w"], 2)

    def test_zero_matches(self):
        r = m("<html><body><p>nothing here</p></body></html>")
        self.assertEqual(add_tf_idf([r], registry=REG), [r])
        self.assertEqual((r["values"]["term_frequency"], r["values"]["tf_idf"]), (0, 0))

    def test_css_kb_divides_by_1024(self):
        self.assertEqual(mrow("<html><head><style>" + "a" * 10240 + "</style></head><body>x</body></html>", row("c", "css_kb"))["c"], 10.0)

    def test_body_questions_differ_from_heading_questions(self):
        got = mrow("<body><h2>Why?</h2><p>Is it? Yes. Really?</p></body>", row("q", "questions", scope="body"),
                   row("h", "questions", scope="headings"))
        self.assertEqual((got["q"], got["h"]), (3, 1))

    def test_href_scope_skips_links_without_href(self):
        self.assertEqual(mrow(SOC, row("h", "matches", target="keyword", scope="attr:a@href"))["h"], 0)

    def test_custom_registry_is_used(self):
        self.assertEqual(list(mrow("<body>x</body>", KWB)), ["kw_body"])

    def test_unknown_kinds_raise(self):
        from measure import Page, measure_factor
        pg = Page("<body>x</body>", URL)
        with self.assertRaises(ValueError):
            measure_factor(pg, {"measure": "has", "params": {"what": "nope"}}, [], [])
        with self.assertRaises(ValueError):
            measure_factor(pg, {"measure": "nope", "params": {}}, [], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
