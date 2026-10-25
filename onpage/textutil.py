"""Text helpers for the on-page factor counter: tokens, words, sentences, phrase matching, decoding.

Matching rule (one rule for every factor that counts a phrase):
- Text is lower-cased and split into word tokens (letters, digits and underscore, so a code name
  like stump_removal stays one token; an apostrophe inside a word stays in it, so "don't" is one
  token). Everything else is a separator.
- Scripts written without spaces (Chinese, Japanese, Thai, Lao, Khmer, Myanmar): every character is
  its own token, so a keyword in those scripts matches as a substring ("歯医者" inside "東京の歯医者さん").
- A phrase matches where its tokens appear as consecutive tokens. "stump removal" matches
  "Stump-Removal" and "stump removal," but not "stumpremoval" or "stumps removal".
- A phrase never runs across two blocks: the measurer hands each block line (a paragraph, a list
  item, a heading, the title) to the counter separately, so "stump</p><p>removal" is no match.
- A list of phrases is counted longest-first without overlap: reading left to right, at each word
  the longest phrase that starts there is counted and its words are used up. In the text
  "stump removal", the list ["stump", "removal", "stump removal"] gives 1, not 3. Each word on the
  page counts once, inside the longest variation it belongs to.
"""
import codecs
import re
import unicodedata

# Scripts written without spaces between words: one character = one token.
_NOSPACE = "฀-๿຀-໿က-႟ក-៿぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿"
_NS_ANY = re.compile(f"[{_NOSPACE}]")
# Combining marks (Devanagari/Bengali/Tamil vowel signs, Hebrew points...) and ZWNJ/ZWJ belong to the
# word they sit in; Python's \\w does not match them, which cut Hindi words into one-letter tokens.
def _ranges(pred):
    out, c = [], 0x300
    while c < 0x10000:
        if pred(chr(c)):
            s = c
            while c + 1 < 0x10000 and pred(chr(c + 1)):
                c += 1
            out.append(re.escape(chr(s)) + ("-" + re.escape(chr(c)) if c > s else ""))
        c += 1
    return "".join(out)


_MARKS = _ranges(lambda ch: unicodedata.category(ch) in ("Mn", "Mc", "Me")) + "\u200c\u200d"
_WC = f"(?:(?![{_NOSPACE}])[\\w{_MARKS}])"
_TOKEN = re.compile(f"[{_NOSPACE}]|(?![{_NOSPACE}])\\w{_WC}*(?:['’]{_WC}+)*", re.UNICODE)
_TOKEN_FAST = re.compile(f"\\w[\\w{_MARKS}]*(?:['’][\\w{_MARKS}]+)*", re.UNICODE)
_TOKEN_ASCII = re.compile(r"\w+(?:'\w+)*")
# Invisible format characters that sit INSIDE a word (soft hyphen, zero-width space, word joiner, BOM)
# and Arabic short-vowel marks / tatweel (optional in writing; a keyword is typed without them).
_DROP = re.compile("[\u00ad\u200b\u2060\ufeff\u0640\u064b-\u065f\u0670]")


def norm(text):
    """NFKC, lower case, curly apostrophe to straight, non-breaking space to space."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).replace("’", "'").replace("\xa0", " ")
    if not t.isascii() and _DROP.search(t):
        t = _DROP.sub("", t)
    t = t.lower()
    return t.replace("i\u0307", "i") if "\u0307" in t else t  # Turkish capital dotted I lower-cases to i + combining dot


def _tok(t):
    if t.isascii():
        return _TOKEN_ASCII.findall(t)
    return (_TOKEN if _NS_ANY.search(t) else _TOKEN_FAST).findall(t)


def tokens(text):
    return _tok(norm(text))


def line_tokens(text):
    """Token lists, one per line (block) of text: the unit a phrase may not cross."""
    return [t for t in (tokens(ln) for ln in (text or "").split("\n")) if t]


# A counted WORD (word count, first/last 100 words): letters/digits with inner apostrophes or
# hyphens kept ("line-up", "don't" are one word); a lone "&" reads as "and". Every word counts,
# short ones ("a", "to", "5") too (WORD_MIN = 1). A run of Chinese/Japanese/Thai characters with no
# space or punctuation inside is one word (known limit: those scripts have no word separator).
_WORD = re.compile(f"\\w[\\w'\\-{_MARKS}]*|(?<!\\S)&(?!\\S)", re.UNICODE)
WORD_MIN = 1  # every word counts ("a", "to", "5")


_WORD_ASCII = re.compile(r"\w[\w'\-]*|(?<!\S)&(?!\S)")


def words(text):
    t = norm(text)
    return [w for w in (_WORD_ASCII if t.isascii() else _WORD).findall(t) if len(w) >= WORD_MIN]


def window_lines(text, n, first=True):
    """The token lists (one per line) that hold the first (or last) n counted words of text. The
    window is measured in words (word_count's word), then the tokens inside it are matched."""
    lines = [norm(ln) for ln in (text or "").split("\n")]
    out, left = [], n
    seq = lines if first else list(reversed(lines))
    for ln in seq:
        if left <= 0:
            break
        ms = [m for m in _WORD.finditer(ln) if len(m.group()) >= WORD_MIN]
        if not ms:
            continue
        if len(ms) <= left:
            part = ln
            left -= len(ms)
        elif first:
            part, left = ln[:ms[left - 1].end()], 0
        else:
            part, left = ln[ms[-left].start():], 0
        t = _tok(part)
        if t:
            out.append(t)
    return out if first else list(reversed(out))


def window_text(text, n=100):
    """The lines of text (original case and spelling) that hold its first n counted words, the last one cut
    after word n: the text that window_lines(text, n, True) tokenises. A line whose normal form has a
    different length than the original is kept whole (it cannot be cut at the same place)."""
    out, left = [], n
    for ln in (text or "").split("\n"):
        if left <= 0:
            break
        nl = norm(ln)
        ms = [m for m in _WORD.finditer(nl) if len(m.group()) >= WORD_MIN]
        if not ms:
            continue
        if len(ms) <= left:
            out.append(ln)
            left -= len(ms)
        else:
            out.append(ln[:ms[left - 1].end()] if len(nl) == len(ln) else ln)
            left = 0
    return out


def match_spans(text, phrases):
    """[(start, end, phrase)] of the matches count_phrases counts in one line of text (longest first, no
    overlap), as character positions in the ORIGINAL text, so a quote can show the words that were counted."""
    sp = []
    for m in _TOKEN.finditer(text or ""):
        for t in _tok(norm(m.group())):
            sp.append((t, m.start(), m.end()))
    first = {}
    for p in phrases or ():
        first.setdefault(p[0], []).append(p)
    out, i = [], 0
    toks = [t for t, _, _ in sp]
    while i < len(toks):
        found = next((p for p in sorted(first.get(toks[i], ()), key=len, reverse=True)
                      if tuple(toks[i:i + len(p)]) == p), None)
        if found:
            out.append((sp[i][1], sp[i + len(found) - 1][2], found))
            i += len(found)
        else:
            i += 1
    return out


def phrase_tokens(phrases):
    """['Stump Removal', 'stump'] -> [('stump', 'removal'), ('stump',)]; empty phrases dropped, order kept, duplicates dropped."""
    out, seen = [], set()
    for p in phrases:
        t = tuple(tokens(p))
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def count_phrase(toks, phrase):
    n, k = 0, len(phrase)
    if k == 0 or k > len(toks):
        return 0
    first = phrase[0]
    for i in range(len(toks) - k + 1):
        if toks[i] == first and tuple(toks[i:i + k]) == phrase:
            n += 1
    return n


def count_phrases(toks, phrases, tally=None):
    """Longest-first, non-overlapping matches of any phrase (phrases = output of phrase_tokens).
    tally: optional dict, gets +1 per match under the phrase that matched."""
    if not phrases or not toks:
        return 0
    first = {}
    for p in phrases:
        first.setdefault(p[0], set()).add(p)
    maxk = {w: max(len(p) for p in ps) for w, ps in first.items()}
    n = i = 0
    L = len(toks)
    while i < L:
        ps = first.get(toks[i])
        if ps:
            for k in range(min(maxk[toks[i]], L - i), 0, -1):
                t = tuple(toks[i:i + k])
                if t in ps:
                    n += 1
                    i += k
                    if tally is not None:
                        tally[t] = tally.get(t, 0) + 1
                    break
            else:
                i += 1
        else:
            i += 1
    return n


def starts_with_any(toks, phrases):
    return any(len(p) <= len(toks) and tuple(toks[:len(p)]) == p for p in phrases)


# Sentence ends: . ! ? followed by space, CJK full stops, blank lines, bullets.
_SENT_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])|\n{2,}|\s*•\s*")
# A full stop after one of these does not end a sentence (titles and place-name abbreviations).
ABBREV = {"mr", "mrs", "ms", "dr", "st", "ft", "mt", "jr", "sr", "prof", "rev", "gen", "sgt", "capt", "lt",
          "col", "gov", "sen", "rep", "hon", "ave", "blvd", "hwy", "rd", "no", "vs", "approx", "dept", "est",
          "e.g", "i.e", "cf", "pt", "ste", "apt"}
_LAST_WORD = re.compile(r"([\w.]+)\.$")
_DOTTED_CAPS = re.compile(r"^(?:[A-Z]\.)+[A-Z]$|^[A-Z]$")  # U.S / D.C / a single initial "J"


def _no_break_after(fragment, nxt):
    m = _LAST_WORD.search(fragment)
    if not m:
        return False
    w = m.group(1)
    if w.lower() in ABBREV or _DOTTED_CAPS.match(w):
        return True
    # a.m. / p.m. and other lower-case dotted forms: only when the next part goes on in lower case
    return bool(re.fullmatch(r"(?:[a-z]\.)+[a-z]", w)) and nxt[:1].islower()


def sentences(text):
    """Split visible text into sentences: end punctuation followed by space (not after an
    abbreviation such as St., Ft., Mt., Dr., U.S., e.g.), CJK full stops, line ends (block edges),
    bullets. A 'sentence' must hold at least one word token."""
    parts = []
    for block in (text or "").split("\n"):
        block = block.strip()
        if not block:
            continue
        frags = [f for f in _SENT_END.split(block) if f is not None]
        merged = []
        for f in frags:
            if merged and _no_break_after(merged[-1], f.strip()):
                merged[-1] = merged[-1] + " " + f
            else:
                merged.append(f)
        for s in merged:
            if _TOKEN.search(norm(s)):
                parts.append(s.strip())
    return parts


# --- decoding raw bytes (saved pages, server bodies) ----------------------------------------------
_META_CS = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.I)
_BOMS = ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))


def _codec(name):
    try:
        c = codecs.lookup(name.strip().strip("\"'").lower()).name
    except (LookupError, AttributeError):
        return None
    return "cp1252" if c in ("latin-1", "iso8859-1", "ascii") else c  # WHATWG: latin-1/ascii labels mean windows-1252


def decode_html(raw, content_type=None):
    """bytes -> str. Order (as browsers do): byte-order mark, the charset in the Content-Type header,
    a <meta charset> in the first 4 KB, else UTF-8 if it decodes cleanly, else windows-1252.
    A str is returned unchanged."""
    if raw is None or isinstance(raw, str):
        return raw
    raw = bytes(raw)
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return raw.decode(enc, errors="replace").lstrip("﻿")
    cands = []
    if content_type and "charset=" in content_type.lower():
        cands.append(_codec(content_type.lower().split("charset=", 1)[1].split(";")[0]))
    m = _META_CS.search(raw[:4096])
    if m:
        cands.append(_codec(m.group(1).decode("ascii", "replace")))
    for enc in cands:
        if enc and not enc.startswith("utf-16"):
            return raw.decode(enc, errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")
