"""Keyword variations: the phrases counted by every 'variations' factor.

Rule (deterministic, written down so anyone can check a count by hand):
1. The keyword itself.
2. Every run of consecutive words from the keyword that neither starts nor ends with a filler word
   ("in", "near", "me", "the", "for", "best", ...). "stump removal near me" gives "stump removal",
   "stump" and "removal" ("removal near" and "near me" are dropped).
   A multi-word place name counts as ONE word here, so it is never split: "lawyer los angeles" gives
   "lawyer" and "los angeles", never "los", "angeles" or "lawyer los". A place is (a) a word from
   PLACE_PREFIX ("los", "san", "fort", "st", ...) joined to the word after it, or (b) a pair in
   PLACE_PAIRS ("new york", "kansas city"); joins chain ("salt lake" + "lake city" = "salt lake city").
   "new" alone is not a prefix ("new roof" is not a place). Known limit: a place outside both lists
   is split like any other words.
   A single word of 1 or 2 letters or digits ("tx", "ac", "ny", "24") is not a variation on its own
   (every state code or "AC" on a page would count), unless it is the whole keyword. It still counts
   inside a longer phrase ("ac repair", "austin tx").
3. Singular/plural forms (s <-> no s, es, y <-> ies) of each non-filler, non-place word, one word at a
   time, in every phrase from 1-2. Words that only look plural ("news", "campus", "analysis", "glass")
   are never cut, and "new" never gains an "s". A form is KEPT ONLY IF it occurs on at least one of
   the pages being compared ("lawyers" is kept when a page says it). Every page is then counted with
   the same list.
4. Phrases the caller adds (--variations "a|b|c"), for example the related phrases Google bolds or
   suggests for the keyword. They are added as given (same matching rule).
The kit builds the list itself (rules 1-3); the bot may add phrases with rule 4. Inflection is
English only.
"""
from textutil import _NS_ANY, tokens

SHORT_MAX = 2  # a lone word this short is not a variation (rule 2)

FILLER = {"a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "with", "near", "me", "my", "by",
          "from", "or", "vs", "is", "are", "best", "top", "how", "what", "where", "who", "why", "do", "does",
          "can", "i", "you", "your", "our", "we", "it", "its", "this", "that"}

PLACE_PREFIX = {"los", "las", "san", "santa", "santo", "saint", "st", "fort", "ft", "el", "mount", "mt", "port",
                "des", "baton", "corpus", "sioux", "palo", "rio", "del", "costa"}
PLACE_PAIRS = {("new", w) for w in ("york", "orleans", "jersey", "haven", "mexico", "hampshire", "braunfels",
                                    "bedford", "rochelle", "britain", "brunswick", "london", "rochelle", "zealand")}
PLACE_PAIRS |= {("salt", "lake"), ("lake", "city"), ("ann", "arbor"), ("kansas", "city"), ("oklahoma", "city"), ("jersey", "city"), ("carson", "city"), ("virginia", "beach"),
                ("long", "beach"), ("west", "palm"), ("palm", "beach"), ("palm", "springs"), ("colorado", "springs"),
                ("grand", "rapids"), ("cedar", "rapids"), ("little", "rock"), ("round", "rock"), ("sugar", "land"),
                ("high", "point"), ("lake", "charles"), ("north", "carolina"), ("south", "carolina"),
                ("north", "dakota"), ("south", "dakota"), ("west", "virginia"), ("rhode", "island"),
                ("hong", "kong"), ("united", "states"), ("united", "kingdom"), ("tel", "aviv"), ("buenos", "aires")}

# Words that end like a plural but are not one; and words that must not gain an "s".
NOT_PLURAL = {"news", "series", "species", "always", "perhaps", "plus", "yes", "has", "was", "his", "this", "thus",
              "gas", "bus", "lens", "chaos", "canvas", "atlas", "alias", "bias", "physics", "economics",
              "mathematics", "politics", "ethics", "athletics", "electronics", "logistics", "less", "unless"}
NO_PLURAL = {"new", "old", "free", "local", "near", "best", "cheap", "fast", "same", "next", "open", "full"}
_NOT_S_ENDINGS = ("ss", "us", "is", "ous", "ics")


def _inflect(word):
    out = {word}
    if len(word) < 3 or not word.isalpha() or word in NOT_PLURAL:
        return out
    if word.endswith("ies") and len(word) > 4:
        out.add(word[:-3] + "y")
    elif word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        out.add(word[:-2])   # boxes -> box, glasses -> glass
        out.add(word[:-1])   # houses -> house, courses -> course (a form is kept only if a page has it)
    elif word.endswith("s"):
        if not word.endswith(_NOT_S_ENDINGS):
            out.add(word[:-1])
    elif word not in NO_PLURAL:
        if word.endswith("y") and word[-2] not in "aeiou":
            out.add(word[:-1] + "ies")
        elif word.endswith(("x", "z", "ch", "sh")):
            out.add(word + "es")
        else:
            out.add(word + "s")
    return out


def _short(word):
    """1-2 letters or digits in a script written with spaces (one Chinese/Japanese/Thai character is a token, not a short word)."""
    return len(word) <= SHORT_MAX and all(c.isascii() or c.isalpha() and not _NS_ANY.search(c) for c in word)


def units(toks):
    """Group keyword tokens into units: a multi-word place name is one unit, every other word its own."""
    out, i, n = [], 0, len(toks)
    while i < n:
        j = i
        while j + 1 < n and (toks[j] in PLACE_PREFIX or (toks[j], toks[j + 1]) in PLACE_PAIRS):
            j += 1
        out.append(tuple(toks[i:j + 1]))
        i = j + 1
    return out


def build(keyword, extra=None, page_tokens=None):
    """-> ordered list of variation phrases (strings), keyword first, duplicates removed.
    page_tokens: token lists of the compared pages; inflected forms must occur in one of them
    (None = keep no inflected forms)."""
    toks = tokens(keyword)
    seen, out = set(), []

    def add(words):
        p = " ".join(words)
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    add(toks)
    us = units(toks)
    n = len(us)
    places = {" ".join(u) for u in us if len(u) > 1}
    for size in range(n, 0, -1):
        for i in range(0, n - size + 1):
            run = us[i:i + size]
            if (len(run[0]) == 1 and run[0][0] in FILLER) or (len(run[-1]) == 1 and run[-1][0] in FILLER):
                continue
            if len(run) == 1 and len(run[0]) == 1 and _short(run[0][0]):
                continue  # "tx", "ac": the whole keyword was added above when it is that word
            add([w for u in run for w in u])
    if page_tokens:
        from textutil import count_phrase
        base = list(out)
        for p in base:
            w = p.split(" ")
            # positions inside a place name are never inflected
            fixed = set()
            for pl in places:
                k = len(pl.split(" "))
                for s in range(len(w) - k + 1):
                    if " ".join(w[s:s + k]) == pl:
                        fixed.update(range(s, s + k))
            for i, word in enumerate(w):
                if word in FILLER or i in fixed:
                    continue
                for alt in sorted(_inflect(word) - {word}):
                    cand = w[:i] + [alt] + w[i + 1:]
                    t = tuple(cand)
                    if " ".join(cand) not in seen and any(count_phrase(pt, t) for pt in page_tokens):
                        add(cand)
    for p in extra or []:
        t = tokens(p)
        if t:
            add(t)
    return out
