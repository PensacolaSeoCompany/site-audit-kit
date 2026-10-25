"""Measure the 50 on-page factors (onpage/factors.json) on one page's HTML.

measure_page(html, url, keyword, variations) -> {"values": {key: number}, "not_measured": {key: why},
                                                 "tally": {variation: matches in the HTML source},
                                                 "notes": {} or {"duplicate_tags": {tag: count > 1}}}
Single-value tags (title, meta description, og:title, og:description, canonical) are read from the
FIRST tag only; notes.duplicate_tags names each of those (and h1) the page has more than once, with
keys "title", "meta:description", "og:title", "og:description", "canonical", "h1".
Everything is computed from the HTML given (str, or bytes decoded by textutil.decode_html); no
network. The registry decides what each factor is; this file only implements the measure kinds the
registry may name. tf_idf needs the whole compared set: call add_tf_idf(results) on the list of
measure_page results of the pages compared together (a page measured alone has it 'not measured').
"""
import json
import math
import os
import re
from urllib.parse import urljoin, urlsplit, unquote

import lxml.html
from lxml import etree

from textutil import (tokens, line_tokens, window_lines, phrase_tokens, count_phrases, starts_with_any,
                      sentences, words, decode_html)

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE_KINDS = {"matches", "sentences_with", "starts_with", "is_exactly", "tag_count", "word_count", "sentence_count",
                 "text_kb", "css_kb", "url_count", "questions", "term_frequency", "tf_idf", "phones", "social", "has"}

# Elements whose content is never visible text.
_INVISIBLE = {"script", "style", "noscript", "template", "svg", "math", "head", "title", "iframe", "object", "canvas"}
# Containers whose elements are not part of the shown page (tag counts, tag scopes skip them).
_NOT_SHOWN = {"template", "noscript", "svg", "math", "script", "style", "head"}
# Elements that break words (anything not inline); inline ones join with a space too, which is safe
# because a space never joins two words into one.
_BLOCK = {"p", "div", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "table",
          "section", "article", "header", "footer", "nav", "aside", "main", "blockquote", "pre", "dd", "dt",
          "dl", "form", "fieldset", "figure", "figcaption", "hr", "address", "option", "label", "button",
          "title", "head", "body", "html", "script", "style", "noscript", "template", "select", "textarea",
          "caption", "thead", "tbody", "tfoot", "summary", "details", "dialog", "menu", "legend", "meta", "link"}
_HEADING = {"h1", "h2", "h3", "h4", "h5", "h6"}
_ABS_URL = re.compile(r"""https?://[^\s"'<>()\\]+""", re.I)
_ANY_URL = re.compile(r"""(?:https?:)?//[^\s"'<>()\\]+""", re.I)  # also protocol-relative //host/...
_WS = re.compile(r"\s+")
_XML_DECL = re.compile(r"^﻿?\s*<\?xml[^>]*\?>", re.I)
_PARSER = lxml.html.HTMLParser(huge_tree=True, remove_blank_text=False)
# US phone: optional +1, area code (2-9xx), exchange (2-9xx), 4 digits; separators ( ) - . space.
_PHONE = re.compile(r"(?<![\w+])(?:\+?1[\s.\-]?)?(?:\(\s*([2-9]\d\d)\s*\)|([2-9]\d\d))[\s.\-]?([2-9]\d\d)[\s.\-]?(\d{4})(?!\w)")

# Social profile addresses: per network, the first path segments that ARE an account page.
# Anything else on the host (posts, articles, share, login, jobs...) is not a profile.
SOCIAL = {
    "facebook": (("facebook.com", "fb.com", "fb.me"), None,
                 {"sharer", "sharer.php", "share.php", "share", "plugins", "dialog", "tr", "login", "login.php", "events",
                  "groups", "watch", "hashtag", "help", "policies", "privacy", "permalink.php", "photo.php", "story.php",
                  "photo", "photos", "posts", "videos", "reel", "l.php", "business", "ads", "marketplace", "gaming"}),
    "instagram": (("instagram.com",), None, {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "about", "legal", "embed.js"}),
    "twitter": (("twitter.com", "x.com"), None, {"intent", "share", "home", "search", "hashtag", "i", "login", "privacy", "tos", "widgets.js", "status"}),
    "linkedin": (("linkedin.com",), {"company", "in", "school", "showcase", "pub"}, None),
    "youtube": (("youtube.com", "youtu.be"), {"channel", "c", "user"}, None),
}


def load_registry(path=None):
    with open(path or os.path.join(HERE, "factors.json"), encoding="utf-8") as f:
        reg = json.load(f)
    for row in reg["factors"]:
        if row["measure"] not in MEASURE_KINDS:
            raise ValueError(f"factor {row['key']}: unknown measure kind {row['measure']!r}")
    return reg


def _host(u):
    try:
        h = (urlsplit(u).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _join(base, href):
    """urljoin that never raises (real pages carry malformed hrefs: bad brackets, spaces in the host)."""
    try:
        return urljoin(base, href.strip())
    except ValueError:
        return href.strip()


def _tag(e):
    return e.tag.lower() if isinstance(e.tag, str) else ""


def _walk(root, skip_invisible):
    """Text of root with a newline at every block-element edge and a space at inline edges.
    Iterative (no recursion limit on deeply nested pages). skip_invisible: leave out _INVISIBLE
    elements and elements marked hidden. Comments carry no text (blanked in Page); their tails do."""
    out = []
    if root is None:
        return ""
    if root.text and isinstance(root.tag, str):
        out.append(_WS.sub(" ", root.text))
    stack = [(c, False) for c in reversed(list(root))]
    in_heading = 1 if _tag(root) in _HEADING else 0
    while stack:
        e, closing = stack.pop()
        tag = _tag(e)
        # a <br> inside a heading is a line wrap in one heading, not a new block
        sep = " " if tag == "br" and in_heading else ("\n" if tag in _BLOCK else " ")
        if closing:
            if tag in _HEADING:
                in_heading -= 1
            out.append(sep)
            if e.tail:
                out.append(_WS.sub(" ", e.tail))
            continue
        if skip_invisible and (tag in _INVISIBLE or (isinstance(e.tag, str) and e.get("hidden") is not None)):
            out.append(sep)
            if e.tail:
                out.append(_WS.sub(" ", e.tail))
            continue
        out.append(sep)
        if tag in _HEADING:
            in_heading += 1
        if e.text and isinstance(e.tag, str):
            out.append(_WS.sub(" ", e.text))
        stack.append((e, True))
        stack.extend((c, False) for c in reversed(list(e)))
    # source line breaks were made plain spaces above; only block edges are "\n"
    parts = "".join(out).split("\n")
    return "\n".join(p.strip() for p in (re.sub(" +", " ", q) for q in parts) if p.strip())


def _text_of(el):
    return _walk(el, True)


def _shown(e):
    """False for an element inside template/noscript/svg/script/style/head or an element marked hidden."""
    for a in e.iterancestors():
        if _tag(a) in _NOT_SHOWN or a.get("hidden") is not None:
            return False
    return e.get("hidden") is None


class Page:
    def __init__(self, html, url):
        html = decode_html(html) if isinstance(html, (bytes, bytearray)) else html
        self.html = html or ""
        self.url = url
        # lxml refuses a str that carries an XML encoding declaration (XHTML pages): drop it first.
        src = _XML_DECL.sub("", self.html, count=1)
        try:
            self.doc = lxml.html.document_fromstring(src, parser=_PARSER) if src.strip() else None
        except (etree.ParserError, ValueError):
            self.doc = None
        self.comments = []
        if self.doc is not None:
            for c in self.doc.iter(etree.Comment, etree.ProcessingInstruction):
                self.comments.append(c.text or "")
                c.text = ""  # comments are not text; tails are kept by _walk
        self._cache = {}

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def els(self, tag):
        """Elements of a tag that are part of the shown page (not in template/noscript/svg/hidden)."""
        if self.doc is None:
            return []
        return self._memo("els:" + tag, lambda: [e for e in self.doc.iter(tag) if _shown(e)])

    def els_any(self, tags):
        """Outermost shown elements whose tag is one of tags, in document order."""
        ts = set(tags)
        if self.doc is None:
            return []
        return [e for e in self.doc.iter(*ts) if _shown(e) and not any(_tag(a) in ts for a in e.iterancestors())]

    @property
    def body(self):
        if self.doc is None:
            return None
        b = self.doc.find(".//body")
        return b if b is not None else self.doc

    @property
    def body_text(self):
        return self._memo("body_text", lambda: _text_of(self.body))

    @property
    def page_text(self):
        """Title plus visible body text, one line per block: the text a reader of the page gets."""
        return self._memo("page_text", lambda: "\n".join(self.title()[:1] + [self.body_text]).strip())

    @property
    def page_tokens(self):
        return self._memo("page_tokens", lambda: tokens(self.page_text))

    def source_texts(self):
        """The HTML source as text: every text node (scripts, styles, head and comments included),
        inline markup joined by a space and block edges by a line break (a phrase may not cross a
        block), plus every attribute value on its own. Tag and attribute names are markup, not text."""
        def build():
            attrs = []
            for e in self.doc.iter():
                if isinstance(e.tag, str):
                    attrs.extend(v for v in e.attrib.values() if v)
            return [_walk(self.doc, False)] + [c for c in self.comments if c.strip()] + attrs
        return self._memo("source_texts", build)

    def meta(self, name):
        if self.doc is None:
            return []
        out = []
        for m in self.doc.iter("meta"):
            n = (m.get("name") or m.get("property") or "").strip().lower()
            if n == name:
                out.append(m.get("content") or "")
        return out

    def titles(self):
        """Every document <title> (not inside an SVG or MathML image)."""
        if self.doc is None:
            return []
        return [t for t in self.doc.iter("title") if not any(_tag(a) in ("svg", "math") for a in t.iterancestors())]

    def title(self):
        """The document title: the first <title> that is not inside an SVG or MathML image."""
        ts = self.titles()
        return [_WS.sub(" ", ts[0].text_content()).strip()] if ts else []

    def canonicals(self):
        if self.doc is None:
            return []
        return [l.get("href") for l in self.doc.iter("link")
                if "canonical" in (l.get("rel") or "").lower().split() and l.get("href")]

    def duplicate_tags(self):
        """Single-value tags the page has more than once: {"title": 2, "meta:description": 2, ...}.
        Search engines use one of each; the counted value reads the first (h1 is reported too)."""
        n = {"title": len(self.titles()), "meta:description": len(self.meta("description")),
             "og:title": len(self.meta("og:title")), "og:description": len(self.meta("og:description")),
             "canonical": len(self.canonicals()), "h1": len(self.els("h1"))}
        return {k: v for k, v in n.items() if v > 1}

    def base(self):
        base = self.url
        if self.doc is not None:
            b = self.doc.find(".//base[@href]")
            if b is not None:
                try:
                    base = urljoin(self.url, b.get("href").strip())
                except ValueError:
                    pass
        return base

    def hrefs(self):
        return [a.get("href") for a in self.els("a") if a.get("href") is not None]

    def social_urls(self):
        """Every absolute address written in the HTML source (links, JSON-LD sameAs, scripts)."""
        return self._memo("social_urls", lambda: ["https:" + u if u.startswith("//") else u
                                                   for u in _ANY_URL.findall(self.html)])

    def once(self, els):
        """The elements with each repeat left out: an element counts once when another element before it has the
        same tag, the same visible text (spaces collapsed) and, for a link, the same address (a phone menu and
        a desktop menu holding the same links). Different text or a different address is a different element."""
        seen, out = set(), []
        base = self.base()
        for e in els:
            key = (_tag(e), " ".join(_text_of(e).split()), _join(base, e.get("href") or "") if _tag(e) == "a" else None)
            if key not in seen:
                seen.add(key)
                out.append(e)
        return out

    def scope_texts(self, scope, once=False):
        """Texts that make up a scope; matches are counted per text (and per line) and added.
        once: for a tags scope, leave out repeated elements (see once())."""
        if scope == "body_text":
            return [self.body_text]
        if scope == "html_source":
            return self.source_texts()
        if scope == "title":
            return self.title()
        if scope.startswith("meta:"):
            # a second <meta name=description> (or og:title, og:description) is a duplicate search engines
            # do not add up: count the first; measure_page reports the duplicate in notes
            return self.meta(scope[5:])[:1]
        if scope.startswith("tag:") or scope.startswith("tags:"):
            # outermost elements only, so text inside a nested <li> (or <div>...) counts once
            els = self.els_any(scope.split(":", 1)[1].split(","))
            return [_text_of(e) for e in (self.once(els) if once else els)]
        if scope.startswith("attr:"):
            tag, attr = scope[5:].split("@")
            if self.doc is None:
                return []
            it = self.doc.iter() if tag == "*" else self.doc.iter(tag)
            return [unquote(e.get(attr)) for e in it if isinstance(e.tag, str) and e.get(attr)]
        if scope == "distinct_href":
            seen, out = set(), []
            base = self.base()
            for h in self.hrefs():
                u = _join(base, h)
                if u not in seen:
                    seen.add(u)
                    out.append(unquote(h))
            return out
        if scope == "canonical":
            return [unquote(h) for h in self.canonicals()[:1]]  # the first canonical, as for meta tags
        if scope == "script":
            return [e.text or "" for e in self.doc.iter("script")] if self.doc is not None else []
        if scope == "absolute_urls":
            return [unquote(m) for m in _ABS_URL.findall(self.html)]
        raise ValueError(f"unknown scope {scope!r}")

    def scope_lines(self, scope, once=False):
        """Token lists the counter reads; a phrase never runs from one list into the next."""
        if scope == "first100":
            return window_lines(self.page_text, 100, True)
        if scope == "last100":
            return window_lines(self.page_text, 100, False)
        return [t for txt in self.scope_texts(scope, once) for t in line_tokens(txt)]

    def scope_tokens(self, scope):  # kept for callers that want one token list per text
        return self.scope_lines(scope)


def _social_key(u, networks):
    p = urlsplit(u)
    host = (p.hostname or "").lower()
    for net in networks:
        hosts, allow, deny = SOCIAL[net]
        if any(host == h or host.endswith("." + h) for h in hosts):
            segs = [s for s in p.path.split("/") if s]
            if not segs:
                return None
            first = segs[0].lower()
            if allow is not None:
                if first not in allow or len(segs) < 2:
                    return None
                return (net, first + "/" + segs[1].lower())
            if first in deny or first.startswith(("@", "?")) and net != "youtube":
                return None
            return (net, first)
    return None


def _phones(page):
    found = []
    for line in page.body_text.split("\n"):
        for m in _PHONE.finditer(line):
            found.append((m.group(1) or m.group(2)) + m.group(3) + m.group(4))
    seen = set(found)
    for h in page.hrefs():
        h = h.strip()
        if h.lower().startswith("tel:"):
            d = re.sub(r"\D", "", unquote(h[4:]))
            if len(d) == 11 and d.startswith("1"):
                d = d[1:]
            if len(d) == 10 and d[0] in "23456789" and d[3] in "23456789" and d not in seen:
                seen.add(d)
                found.append(d)
    return len(found)


def _source_tally(page, var_phr):
    def build():
        tally = {}
        for t in page.scope_lines("html_source"):
            count_phrases(t, var_phr, tally)
        return tally
    return page._memo("tally", build)


def measure_factor(page, row, kw_phr, var_phr):
    m, p = row["measure"], row.get("params", {})
    target = var_phr if p.get("target") == "variations" else kw_phr
    if m == "matches":
        return sum(count_phrases(t, target) for t in page.scope_lines(p["scope"], p.get("once", False)))
    if m == "sentences_with":
        return sum(1 for s in sentences(page.body_text) if count_phrases(tokens(s), target) > 0)
    if m == "starts_with":
        texts = page.title() if p["tags"] == ["title"] else [_text_of(e) for e in page.els_any(p["tags"])]
        firsts = [ln[0] for ln in (line_tokens(t) for t in texts) if ln]
        return sum(1 for t in firsts if starts_with_any(t, target))
    if m == "is_exactly":
        ts = set(target)
        return sum(1 for e in page.els_any(p["tags"]) if tuple(tokens(_text_of(e))) in ts)
    if m == "tag_count":
        return sum(len(page.els(t)) for t in p["tags"])
    if m == "word_count":
        n = len(words(page.page_text))
        if p.get("scope") == "with_hidden" and page.doc is not None:
            extra = []
            for e in page.doc.iter("noscript"):
                extra.append(e.text_content() if not len(e) else _walk(e, False))
            for e in page.doc.iter():
                if isinstance(e.tag, str) and e.get("hidden") is not None and _tag(e) not in _INVISIBLE \
                        and not any(a.get("hidden") is not None for a in e.iterancestors()):
                    extra.append(" ".join(e.itertext()))
            extra.extend(i.get("alt") for i in page.els("img") if i.get("alt"))
            n += sum(len(words(x)) for x in extra)
        return n
    if m == "sentence_count":
        return len(sentences(page.body_text))
    if m == "text_kb":
        return round(len(_WS.sub(" ", page.page_text).encode("utf-8")) / 1024, 2)
    if m == "css_kb":
        if page.doc is None:
            return 0
        return round(sum(len((e.text or "").encode("utf-8")) for e in page.doc.iter("style")) / 1024, 2)
    if m == "url_count":
        return len(_ABS_URL.findall(page.html))
    if m == "questions":
        if p.get("scope") == "headings":
            hs = page.els_any(["h1", "h2", "h3", "h4", "h5", "h6"])
            n = sum(1 for e in (page.once(hs) if p.get("once") else hs) if _text_of(e).rstrip().endswith(("?", "？")))
        else:
            n = sum(1 for s in sentences(page.body_text) if s.rstrip().endswith(("?", "？")))
        return int(n > 0) if p.get("as") == "yes/no" else n
    if m == "term_frequency":
        n = sum(_source_tally(page, target).values())
        return round(1 + math.log10(n), 4) if n > 0 else 0
    if m == "phones":
        return _phones(page)
    if m == "social":
        keys = []
        src = page.social_urls() if p.get("where") == "source" else [_join(page.base(), h) for h in page.hrefs()]
        for u in src:
            try:
                keys.append(_social_key(u, p["networks"]))
            except ValueError:
                pass
        keys = [k for k in keys if k]
        return len(set(keys)) if p.get("distinct") else len(keys)
    if m == "has":
        if page.doc is None:
            return 0
        if p["what"] == "og_type_article":
            return int(any(c.strip().lower() == "article" for c in page.meta("og:type")))
        if p["what"] == "apple_touch_icon":
            return int(any(any(r.startswith("apple-touch-icon") for r in (l.get("rel") or "").lower().split())
                           for l in page.doc.iter("link")))
        if p["what"] == "meta_charset":
            for mt in page.doc.iter("meta"):
                if mt.get("charset") or ((mt.get("http-equiv") or "").lower() == "content-type" and "charset" in (mt.get("content") or "").lower()):
                    return 1
            return 0
        raise ValueError(f"unknown has {p['what']!r}")
    raise ValueError(f"unknown measure {m!r}")


TF_IDF_WHY = "needs the compared pages (document frequency); measured by add_tf_idf on the whole set"


def measure_page(html, url, keyword, variations, registry=None):
    reg = registry or load_registry()
    kw_phr = phrase_tokens([keyword])
    var_phr = phrase_tokens(variations)
    page = Page(html, url)
    values, not_measured = {}, {}
    if page.doc is None:
        for row in reg["factors"]:
            not_measured[row["key"]] = "no HTML to measure"
        return {"values": values, "not_measured": not_measured, "tally": {}, "notes": {}}
    for row in reg["factors"]:
        if row["measure"] == "tf_idf":
            not_measured[row["key"]] = TF_IDF_WHY
            continue
        values[row["key"]] = measure_factor(page, row, kw_phr, var_phr)
    tally = {" ".join(k): v for k, v in _source_tally(page, var_phr).items()}
    dup = page.duplicate_tags()
    return {"values": values, "not_measured": not_measured, "tally": tally,
            "notes": {"duplicate_tags": dup} if dup else {}}


def add_tf_idf(results, registry=None):
    """Fill every tf_idf factor in place on a list of measure_page results (the pages compared
    together, one list). N = pages in the list that were measured; d = those whose source contains
    the variation. Value = mean over the page's variations of (1 + log10 tf) * log10(N / d); 0 when
    the page has none. Returns results."""
    reg = registry or load_registry()
    keys = [r["key"] for r in reg["factors"] if r["measure"] == "tf_idf"]
    measured = [r for r in results if r.get("values")]
    N = len(measured)
    df = {}
    for r in measured:
        for v, c in (r.get("tally") or {}).items():
            if c > 0:
                df[v] = df.get(v, 0) + 1
    for r in measured:
        t = {v: c for v, c in (r.get("tally") or {}).items() if c > 0}
        val = round(sum((1 + math.log10(c)) * math.log10(N / df[v]) for v, c in t.items()) / len(t), 4) if t else 0
        for k in keys:
            r["values"][k] = val
            r["not_measured"].pop(k, None)
    return results
