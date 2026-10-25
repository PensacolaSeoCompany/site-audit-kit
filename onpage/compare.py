"""Compare your page with the competitor pages, factor by factor (pure functions, no I/O).

For each factor: your value, each competitor's value (None = not measured), and over the MEASURED
competitors (n_measured of them): min, median, max, and n_more / n_less = how many have more / less
than you. The mean (competitor_avg) is kept in the JSON only: with 3 pages one long page sets it.

position (from n of n):
  below all      every measured competitor has more        ("below all 3")
  below most     more than half have more                  ("below 2 of 3")
  middle         neither more than half above nor below you
  above most     more than half have less                  ("above 2 of 3")
  above all      every measured competitor has less        ("above all 3")
  same as all    every measured competitor has the same value
  not measured   your page, or every competitor page, could not be measured
gap_to_median = competitor median - your value (positive: competitors have more).
Values are compared at the precision the report prints them (shown()): two values that print the same
are the same, so a row never says "above" or "below" between equal printed numbers.

Bands (from factors.json "band"; a row without one is treated as "context"):
  target   where the keyword sits and what a writer edits. The ACTION LIST is made of these rows only:
           most measured competitors have more (n_more > n_measured / 2), ordered by n_more, then by
           the gap relative to the median. No action list below MIN_FOR_ACTIONS measured competitors
           (one page is one page's habit, not "most competitors"). The page address (family "url") is
           never an action: renaming a page that ranks needs a redirect, so it is a note (address_notes).
  length   counts that rise with page length. Summarised in one line (word counts) and shown with a
           per-1,000-words rate, never as actions.
  context  source and markup (page size, scripts, link counts...). Shown for reference, never as actions.
over(): a one-per-page field (title, meta description, H1, Open Graph title/description, canonical URL)
where yours is far above EVERY measured competitor gets a read-aloud note. Never an action, never a penalty claim.
With 3 competitors there is no statistics here: no correlation, no significance. The table says what
those pages have; it does not say what Google rewards.
"""

import math

BANDS = ("target", "length", "context")
MIN_FOR_ACTIONS = 2
NOTE_ONLY_FAMILIES = ("url",)
# One instance per page, so a count far above every competitor is not explained by page length.
SINGLE_FIELD_KEYS = ("variations_title", "variations_meta_description", "variations_h1",
                     "variations_og_title", "variations_og_description", "variations_canonical")
OVER_MIN, OVER_RATIO, OVER_PLUS = 3, 2, 3  # yours >= 3 and > max(2 x competitor max, competitor max + 3)
WORD_KEY = "word_count"


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def band_of(f):
    b = f.get("band")
    return b if b in BANDS else "context"


def median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return None
    m = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return round(m, 2) if isinstance(m, float) else m


def shown(v):
    """The value as the report prints it (fmt): whole numbers stay whole; a decimal under 1 keeps 2 places,
    others 1 place. Every comparison uses this, so what the reader sees and what the row says agree."""
    if isinstance(v, float) and not v.is_integer():
        return round(v, 2 if abs(v) < 1 else 1)
    return v


def counts(mine, comps):
    """(n_measured, n_more, n_less) over the measured competitors, compared as printed (shown)."""
    vals = [shown(c) for c in comps if c is not None]
    if mine is None:
        return len(vals), None, None
    mine = shown(mine)
    return len(vals), sum(1 for c in vals if c > mine), sum(1 for c in vals if c < mine)


def position(mine, comps):
    n, more, less = counts(mine, comps)
    if mine is None or not n:
        return "not measured"
    if more == n:
        return "below all"
    if less == n:
        return "above all"
    if more == 0 and less == 0:
        return "same as all"
    if more > n / 2:
        return "below most"
    if less > n / 2:
        return "above most"
    return "middle"


def _all(n):
    """'both' for 2, 'all 3' for 3 or more."""
    return "both" if n == 2 else f"all {n}"


def more_needed(row):
    """How many more to reach the competitors' middle value, as a whole number (rounded up: half a keyword
    is not a target); None for a yes/no row or when yours is not below the middle value."""
    g = row.get("gap_to_median")
    if row.get("unit") != "count" or not isinstance(g, (int, float)) or isinstance(g, bool) or g <= 0:
        return None
    return math.ceil(round(g, 6))


def standing(mine, comps):
    """Plain words for the position: 'below all 3', 'below 2 of 3', 'above 2 of 3', ..."""
    n, more, less = counts(mine, comps)
    if mine is None or not n:
        return "not measured"
    if more == n:
        return f"below {_all(n)}" if n > 1 else "below the 1 competitor"
    if less == n:
        return f"above {_all(n)}" if n > 1 else "above the 1 competitor"
    if more == 0 and less == 0:
        return f"same as {_all(n)}" if n > 1 else "same as the 1 competitor"
    if more > less:
        return f"below {more} of {n}"
    if less > more:
        return f"above {less} of {n}"
    return f"in the middle ({more} of {n} have more)"


def _rate(v, words):
    if v is None or not words:
        return None
    return round(v * 1000 / words, 1)


def compare(registry, mine_values, comp_values):
    """mine_values: dict or None; comp_values: list of dict-or-None (one per competitor, same order)."""
    mv = mine_values or {}
    cvs = [c or {} for c in comp_values]
    my_words = _num(mv.get(WORD_KEY)) if mine_values is not None else None
    comp_words = [_num(c.get(WORD_KEY)) if c_raw is not None else None for c, c_raw in zip(cvs, comp_values)]
    rows = []
    for f in registry["factors"]:
        k = f["key"]
        b = band_of(f)
        mine = _num(mv.get(k)) if mine_values is not None else None
        comps = [_num(c.get(k)) if c_raw is not None else None for c, c_raw in zip(cvs, comp_values)]
        vals = [c for c in comps if c is not None]
        n, more, less = counts(mine, comps)
        med = median(vals)
        per_1k = None
        if b == "length" and f["unit"] == "count" and k != WORD_KEY:
            pc = [_rate(v, w) for v, w in zip(comps, comp_words)]
            per_1k = {"mine": _rate(mine, my_words), "competitors": pc,
                      "competitor_median": median([x for x in pc if x is not None])}
        rows.append({
            "key": k, "name": f["name"], "group": f["group"], "unit": f["unit"], "band": b, "family": f.get("family") or k,
            "mine": mine, "competitors": comps,
            "n_measured": n, "n_more": more, "n_less": less,
            "competitor_min": min(vals) if vals else None,
            "competitor_median": med,
            "competitor_max": max(vals) if vals else None,
            "competitor_avg": round(sum(vals) / len(vals), 2) if vals else None,
            "position": position(mine, comps),
            "standing": standing(mine, comps),
            "gap_to_median": round(med - mine, 2) if (med is not None and mine is not None) else None,
            "per_1k": per_1k,
        })
    return rows


def _rel_gap(r):
    med = r["competitor_median"]
    return (med - r["mine"]) / max(abs(med), 1)


def _behind(r):
    """A target row where most measured competitors (at least MIN_FOR_ACTIONS of them) have more than you."""
    return (r.get("band") == "target" and r.get("mine") is not None and (r.get("n_measured") or 0) >= MIN_FOR_ACTIONS
            and r.get("n_more") is not None and r["n_more"] > r["n_measured"] / 2)


def _fam(r):
    return r.get("family") or r["key"]


def actions(rows):
    """TARGET rows where most measured competitors have more; most competitors first, then biggest relative gap.
    Rows of one family (factors.json "family": the same edit, e.g. the H1) collapse into one line.
    Needs MIN_FOR_ACTIONS measured competitors; the page address (NOTE_ONLY_FAMILIES) is never an action."""
    out = [r for r in rows if _behind(r) and _fam(r) not in NOTE_ONLY_FAMILIES]
    out = sorted(out, key=lambda r: (-r["n_more"], -_rel_gap(r)))
    # One line per family (one edit on the page, e.g. the H1): the strongest row leads, the others are named in "also".
    lead, keep = {}, []
    for r in out:
        fam = _fam(r)
        if fam in lead:
            lead[fam]["also"].append(r["name"])
            continue
        r = dict(r, also=[])
        lead[fam] = r
        keep.append(r)
    return keep


def address_notes(rows):
    """Rows that would be actions but change the page address: shown as a note, never as a to-do."""
    return [r for r in rows if _behind(r) and _fam(r) in NOTE_ONLY_FAMILIES]


def over(rows):
    """One-per-page fields where yours is far above EVERY measured competitor (at least MIN_FOR_ACTIONS of them):
    yours >= OVER_MIN and > max(OVER_RATIO x competitor max, competitor max + OVER_PLUS). One line per family.
    A prompt to read the text aloud, never a to-do and never a ranking claim."""
    out, seen = [], set()
    for r in rows:
        if r.get("key") not in SINGLE_FIELD_KEYS or r.get("unit") != "count" or _fam(r) in seen:
            continue
        y, mx = r.get("mine"), r.get("competitor_max")
        if y is None or mx is None or (r.get("n_measured") or 0) < MIN_FOR_ACTIONS:
            continue
        if y >= OVER_MIN and y > max(OVER_RATIO * mx, mx + OVER_PLUS):
            seen.add(_fam(r))
            out.append(r)
    return out


def why_no_actions(rows):
    """The sentence shown when the action list is empty; it must be true for THIS run."""
    t = [r for r in rows if r.get("band") == "target"]
    if not t or all(r.get("mine") is None for r in t):
        return "None: your page could not be measured, so there is nothing to compare."
    n = max((r.get("n_measured") or 0) for r in t)
    if n == 0:
        return "None: no competitor page could be measured, so there is nothing to compare."
    if n < MIN_FOR_ACTIONS:
        return (f"No list: only {n} competitor page was measured, and one page is not enough to suggest changes "
                f"(at least {MIN_FOR_ACTIONS} are needed). The tables still show how your page compares with it.")
    return "None: on every keyword-placement factor, at most half of the measured competitors have more than your page."


def fmt(v, unit=None):
    if v is None:
        return "not measured"
    if unit == "yes/no":
        return "yes" if v else "no"
    r = shown(v)
    if isinstance(r, float) and not r.is_integer():
        return f"{r:,.2f}".rstrip("0") if abs(r) < 1 else f"{r:,.1f}"
    return f"{int(r):,}"


def span(lo, hi, unit=None, sep=" to "):
    a, b = fmt(lo, unit), fmt(hi, unit)
    return a if a == b else f"{a}{sep}{b}"


def length_line(rows):
    """'Competitor pages are 1,200-5,900 words; yours is 3,300.' or None when word count is not in the rows."""
    r = next((x for x in rows if x["key"] == WORD_KEY), None)
    if r is None or not r["n_measured"]:
        return None
    yours = fmt(r["mine"]) if r["mine"] is not None else "not measured"
    if r["n_measured"] == 1:
        return f"The competitor page is {fmt(r['competitor_min'])} words; yours is {yours}."
    return f"Competitor pages are {span(r['competitor_min'], r['competitor_max'], sep='-')} words; yours is {yours}."


def say(row):
    """One plain sentence for a factor row: the range and the middle value, never a 3-page average."""
    u, mine, n = row["unit"], row["mine"], row["n_measured"]
    if mine is None:
        return "Not measured on your page."
    if not n:
        return "Not measured on any competitor page."
    vals = [c for c in row["competitors"] if c is not None]
    if u == "yes/no":
        have = sum(1 for c in vals if c)
        who = (("Both competitors have" if have == 2 else "Neither competitor has") if n == 2 and have in (0, 2)
               else f"{have} of {n} competitors {'has' if have == 1 else 'have'}")
        return f"{who} it; your page {'has' if mine else 'does not have'} it."
    if n == 1:
        return f"The competitor has {fmt(vals[0], u)}; you have {fmt(mine, u)}."
    if fmt(row["competitor_min"], u) == fmt(row["competitor_max"], u):
        return f"{_all(n).capitalize()} competitors have {fmt(vals[0], u)}; you have {fmt(mine, u)}."
    more = row["n_more"]
    tail = ("Both have more." if n == 2 and more == 2 else f"{more} of {n} {'has' if more == 1 else 'have'} more.")
    return (f"Competitors have {span(row['competitor_min'], row['competitor_max'], u)} (middle value {fmt(row['competitor_median'], u)}); "
            f"you have {fmt(mine, u)}. {tail}")
