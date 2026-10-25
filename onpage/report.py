"""Write onpage.md and onpage.html from a result dict (onpage.json). No network, no external assets."""
import html as H
import re

from compare import actions, address_notes, fmt, length_line, more_needed, over, say, span, why_no_actions
from textutil import _TOKEN, _tok, norm, phrase_tokens

SECTIONS = [  # (band, title, intro); the order is the same in onpage.md and onpage.html
    ("target", "Full table: keyword placement (target factors)",
     "Where the keyword sits and what a writer edits. The action list above comes from these rows only."),
    ("length", "Full table: page length and the counts that grow with it",
     "These counts rise and fall with how long a page is, so they are not separate to-dos. "
     "The per 1,000 words columns take length out."),
    ("context", "For reference: source and markup (not to-dos)",
     "Page source, scripts, links and markup. They describe the kind of site more than the copy: shown for reference, never as changes to make."),
]
ACTIONS_TITLE = "Where most competitors have more (ideas to test)"
TEST_LINE = ("These are ideas to test, not proven fixes. Change one thing at a time and leave the rest of the site alone. "
             "Pick a similar page on your site that you do not change, and compare both pages' Search Console average position "
             "for this keyword in the 28 days before the edit and the 28 days after Google next crawls the page "
             "(your web person can help; ask whoever ran this report for the full test recipe).")
# What the 50 factors are. ONE constant: README.md and SKILL.md quote it word for word (a test checks).
FACTOR_NOTE = "These are 50 common on-page factors; none is proven to move rankings."
SUMMARY_MAX = 10
EVIDENCE_PAGES = 3   # competitors quoted under an action line (the ones with the most, listed in table order)
EVIDENCE_TEXTS = 5   # texts quoted per page for areas with many elements (H2s, link text)
OVER_TITLE = "Much more than every competitor (read it aloud)"
OVER_INTRO = ("One-per-page text where your count is far above every competitor measured. Not a to-do and not proof of a problem: "
              "read it aloud, and if it sounds like the same words repeated, rewrite it so it reads naturally.")
ADDRESS_LEAD = "Not a to-do: the page address."
ADDRESS_LINE = ("For a page that already ranks, only change its address with a 301 redirect from the old one, "
                "or it loses its history; for a new page, use the keyword in the address.")
TAG_LABELS = {"title": "title tags", "meta:description": "meta description tags", "og:title": "social-share (Open Graph) title tags",
              "og:description": "social-share (Open Graph) description tags", "canonical": "canonical links", "link:canonical": "canonical links",
              "h1": "H1 tags"}
COLUMNS_LINE = ("How to read the tables: \"You\" is your page; \"Competitor range\" is the lowest to the highest competitor; "
                "\"Middle value\" is the one in the middle (half the competitors have more, half less); "
                "\"Where you stand\" counts the competitors above or below you.")
NO_BROWSER_ALL = "Measured without a browser (Chrome did not start); counts may be lower than what visitors see."
NO_BROWSER_SOME = ("{who} measured without a browser (the browser did not work for {them}), so {its} counts may be lower than "
                   "what visitors see, and comparing {it_them} with the other pages is less reliable. The run is reported as partial.")
NOTHING_TITLE = "Nothing to compare"
SAVE_STEP = ("To compare it anyway: open your page in your own browser, save it (File, Save Page As, \"Webpage, HTML only\") "
             "and send the file to whoever runs this report; it can be measured from that copy.")
SINGLE_TAGS = {"title", "meta:description", "og:title", "og:description", "canonical", "link:canonical"}


def _n_measured(res):
    return sum(1 for p in res.get("pages", []) if p.get("role") == "competitor" and p.get("measured"))


def about(res):
    n = _n_measured(res)
    return [
        FACTOR_NOTE,
        f"With {n} competitor page{'s' if n != 1 else ''} no statistics are possible: this report shows what these pages have, not what Google rewards.",
        "Top pages also rank for reasons a page count cannot see (links to the site, brand, age). Matching their counts does not give you those.",
    ]
DISCLAIMERS = [
    "This is an automated count. It can miss things or count things that don't matter, so have your web person check before making changes.",
    "It doesn't guarantee rankings or traffic. These are what the competitor pages have, not proof of what Google rewards.",
    "I didn't change anything on any site.",
    "Some advice may not apply to your business, site, or situation. Use your own judgment and check with your web person.",
    "You choose which changes to make. The template creator and this bot are not responsible for any loss of traffic, rankings, leads, or revenue from following or not following this report.",
]


def _banner(res):
    comps = [p for p in res["pages"] if p["role"] == "competitor"]
    mine_ok = any(p["measured"] for p in res["pages"] if p["role"] == "mine")
    return (f"{_n_measured(res)} of {len(comps)} competitor pages measured" + ("" if mine_ok else "; your page was not measured")
            + ". Ranges and counts use only the pages measured; the Pages list says why each other page is missing.")


def nothing_to_compare(res):
    """Exit 4: your page, or every competitor page, was not measured."""
    mine_ok = any(p["measured"] for p in res["pages"] if p["role"] == "mine")
    return not mine_ok or _n_measured(res) == 0


def _nothing_text(res):
    mine = next((p for p in res["pages"] if p["role"] == "mine"), {})
    if not mine.get("measured"):
        f = mine.get("fetch") or {}
        check = f.get("block_kind") == "check" or "bot check" in (f.get("error") or "")
        out = "your page was not measured" + (" (it showed a bot check, which this tool never gets past)." if check else
                                              "; the Pages list says why.")
        return out + (" " + SAVE_STEP if f.get("blocked") or check else "")
    return "no competitor page could be measured. The Pages list says why; re-run with other pages that rank for the keyword."


def _headline(res):
    """(title, text) of the top card, or None for a complete run."""
    if nothing_to_compare(res):
        return NOTHING_TITLE, _nothing_text(res)
    if not res["complete"]:
        return "Partial report", _banner(res)
    return None


def _is_no_browser(p):
    f = p.get("fetch") or {}
    if not p.get("measured") or f.get("source") == "saved-file":
        return False
    return "without a browser" in (f.get("note") or "") or (f.get("error") or "").startswith("browser failed")


def no_browser_pages(pages, mode=None):
    """Measured pages read from the server HTML because the browser failed (not because --raw asked for it).
    Known from the fetch record's note ("measured without a browser: ..."), or an older record's "browser failed" error,
    or (with mode "rendered") a live page whose mode_used is raw."""
    return [p for p in pages if _is_no_browser(p) or (mode == "rendered" and p.get("measured") and (p.get("fetch") or {}).get("mode_used") == "raw"
                                                      and (p.get("fetch") or {}).get("source") != "saved-file")]


def no_browser_line(res):
    """One plain line for the top of the report, or None when every page was measured as asked."""
    pages = res["pages"]
    nb = no_browser_pages(pages, res["job"].get("mode"))
    if not nb:
        return None
    measured = [p for p in pages if p["measured"]]
    if len(nb) == len(measured):
        return NO_BROWSER_ALL
    names = [lab for p, lab in zip(pages, _labels(pages)) if any(p is q for q in nb)]
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    one = len(names) == 1
    return NO_BROWSER_SOME.format(who=who + (" was" if one else " were"), them="it" if one else "them",
                                  its="its" if one else "their", it_them="it" if one else "them")


def tag_problems(res):
    """'Your page has 2 meta description tags; ...' for each tag your page has more than once (from measure's notes)."""
    mine = next((p for p in res["pages"] if p["role"] == "mine"), {})
    out = []
    for tag, n in sorted((mine.get("duplicate_tags") or {}).items()):
        if not isinstance(n, int) or n < 2:
            continue
        line = f"Your page has {n} {TAG_LABELS.get(tag, tag + ' tags')}"
        out.append(line + ("; search engines use one. Keep one." if tag in SINGLE_TAGS else "."))
    return out


def _spans(text):
    """[(token, start, end)] over the original text, using the counter's token pattern and case folding (textutil),
    so the words marked are the words counted. A piece that folds into several tokens gives each the piece's span."""
    out = []
    for m in _TOKEN.finditer(text):
        for tok in _tok(norm(m.group())):
            out.append((tok, m.start(), m.end()))
    return out


def mark(text, variations, html=False):
    """Mark the keyword variations in a quoted text the way the counter counts them: the counter's tokens (any case,
    any punctuation between words), longest phrase first, each word used once. **x** in markdown, <b>x</b> in HTML."""
    esc = H.escape if html else (lambda x: x)
    phr = phrase_tokens(variations or [])  # drops empty phrases
    if not phr or not text:
        return esc(text or "")
    sp = _spans(text)
    toks = [t for t, _, _ in sp]
    hits, i = [], 0
    first = {}
    for p in phr:
        first.setdefault(p[0], []).append(p)
    while i < len(toks):
        found = None
        for p in sorted(first.get(toks[i], ()), key=len, reverse=True):
            if tuple(toks[i:i + len(p)]) == p:
                found = p
                break
        if found:
            hits.append((sp[i][1], sp[i + len(found) - 1][2]))
            i += len(found)
        else:
            i += 1
    out, pos = [], 0
    for a, b in hits:  # in order, never overlapping (each word is used once)
        out.append(esc(text[pos:a]))
        out.append(f"<b>{esc(text[a:b])}</b>" if html else f"**{text[a:b]}**")
        pos = b
    out.append(esc(text[pos:]))
    return "".join(out)


def _family_keys(res, r):
    fam = r.get("family") or r["key"]
    return [r["key"]] + [x["key"] for x in res["factors"] if (x.get("family") or x["key"]) == fam and x["key"] != r["key"]]


def evidence_lines(res, r, html=False):
    """'Yours: "..."', 'Competitor 2: "..."' under an action line: the counted text with the matched words marked.
    Only what the run stored (pages[].evidence, from cli.collect_evidence); nothing when it has none."""
    keys = _family_keys(res, r)
    labels = _labels(res)
    q = ("“", "”") if html else ('"', '"')

    def ev(p):
        e = p.get("evidence") or {}
        return next((e[k] for k in keys if e.get(k)), None)

    def line(label, items):
        txt = ", ".join(q[0] + mark(x["text"], x.get("matched") or [], html) + q[1] for x in items[:EVIDENCE_TEXTS])
        return f"{label}: {txt}"

    out = []
    mine = res["pages"][0]
    if mine.get("evidence") is not None and mine["measured"]:
        items = ev(mine)
        out.append(line("Yours", items) if items else "Yours: none on your page")
    comps = [(p, lab, v) for p, lab, v in zip(res["pages"][1:], labels[1:], r["competitors"]) if p["measured"]]
    picked = [t for t in comps if ev(t[0]) and (r["mine"] is None or t[2] is None or t[2] > r["mine"])]
    picked = sorted(picked, key=lambda t: -(t[2] or 0))[:EVIDENCE_PAGES]  # the pages with the most ...
    shown = [line(lab, ev(p)) for p, lab, v in comps if any(p is t[0] for t in picked)]  # ... in table order (1, 2, 3)
    return out + shown if shown else []  # your text alone compares with nothing


def _gap(r):
    k = more_needed(r)  # a whole number, rounded up: never half a keyword
    return f" (to reach the middle value: {k} more)" if k else ""


def _label(p, i):
    return "Your page" if p["role"] == "mine" else f"Competitor {i}"


def _labels(res):
    out, n = [], 0
    for p in (res["pages"] if isinstance(res, dict) else res):
        if p["role"] == "competitor":
            n += 1
        out.append(_label(p, n))
    return out


def _final(p):
    f = p["fetch"].get("final_url")
    return f if f and f != p["url"] else None


def _page_line(p, label):
    f = p["fetch"]
    where = p["url"] + (f" (ended at {_final(p)})" if _final(p) else "")
    if p["measured"]:
        extra = []
        nb = _is_no_browser(p)
        if nb:  # the browser's own error text stays in run.log and onpage.json, never in the report
            note = f.get("note") or ""
            extra.append(note if "without a browser" in note else "measured without a browser (the browser could not start)")
        else:
            if f.get("mode_used"):
                extra.append(f"measured: {f['mode_used']} HTML")
            if f.get("note"):
                extra.append(f["note"])
        if f.get("js_dependent"):
            extra.append("most of its text appears only after JavaScript runs")
        if f.get("error") and not nb:
            extra.append(f["error"])
        return f"{label}: {where}" + (f" ({'; '.join(extra)})" if extra else "")
    return f"{label}: {where} NOT MEASURED ({f.get('error') or 'unknown reason'})"


def _actions(res):
    return actions(res["factors"])


def _action_text(r):
    return f"{say(r)}{_gap(r)}"


def summary(res, limit=SUMMARY_MAX):
    """The top action lines printed to stdout (same rows, same order as the report's action list)."""
    return [f"{r['name']}: {_action_text(r)}" for r in _actions(res)[:limit]]


def _per1k_cells(r):
    p = r.get("per_1k")
    if not p:
        return ["", ""]
    vals = [x for x in p["competitors"] if x is not None]
    return [fmt(p["mine"]), span(min(vals), max(vals)) if vals else "not measured"]


def _table_rows(res, band):
    return [r for r in res["factors"] if r.get("band", "context") == band]


def _subtitle(res):
    name = res["job"].get("name")
    n = len(res["factors"])
    return (f"For {name} · " if name else "") + f"{res['created']} · {n} factors · site-audit-kit {res['version']}"


def over_line(r):
    return f"yours has {fmt(r['mine'])}; the most any measured competitor has is {fmt(r['competitor_max'])}."


def markdown(res):
    job, labels = res["job"], _labels(res)
    L = [f"# On-page comparison: \"{job['keyword']}\"", "", _subtitle(res), ""]
    hl = _headline(res)
    if hl:
        L += [f"**{hl[0]}:** {hl[1]}", ""]
    nb = no_browser_line(res)
    if nb:
        L += [f"**{nb}**", ""]
    tp = tag_problems(res)
    if tp:
        L += ["**Tags on your page:** " + " ".join(tp), ""]
    ll = length_line(res["factors"])
    if ll:
        L += [f"**Page length:** {ll} Most counts in the page-length table rise with length; compare their per 1,000 words rates.", ""]
    L += [f"## {ACTIONS_TITLE}", ""]
    acts = _actions(res)
    for i, r in enumerate(acts, 1):
        L.append(f"{i}. **{r['name']}**: {_action_text(r)}" + (f" (also: {', '.join(r['also'])})" if r.get("also") else ""))
        L += [f"   - {x}" for x in evidence_lines(res, r)]
    if not acts:
        L.append(f"- {why_no_actions(res['factors'])}")
    for r in address_notes(res["factors"]):
        L += ["", f"{ADDRESS_LEAD} **{r['name']}**: {say(r)} {ADDRESS_LINE}"]
    L += ["", TEST_LINE, ""]
    ov = over(res["factors"])
    if ov:
        L += [f"## {OVER_TITLE}", "", OVER_INTRO, ""] + [f"- **{r['name']}**: {over_line(r)}" for r in ov] + [""]
    L += ["## Pages", ""] + [f"- {_page_line(p, lab)}" for p, lab in zip(res["pages"], labels)] + [""]
    comp_labels = [lab for p, lab in zip(res["pages"], labels) if p["role"] == "competitor"]
    L += [COLUMNS_LINE, ""]
    for band, title, intro in SECTIONS:
        rows = _table_rows(res, band)
        if not rows:
            continue
        extra = ["You per 1,000 words", "Competitors per 1,000 words"] if band == "length" else []
        hdr = ["Factor", "You"] + comp_labels + ["Competitor range", "Middle value"] + extra + ["Where you stand"]
        L += [f"## {title}", "", intro, "", "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
        for r in rows:
            u = r["unit"]
            rng = span(r["competitor_min"], r["competitor_max"], u) if r["n_measured"] else "not measured"
            cells = [r["name"], fmt(r["mine"], u)] + [fmt(c, u) for c in r["competitors"]] + [rng, fmt(r["competitor_median"], u)]
            cells += _per1k_cells(r) if band == "length" else []
            cells.append(r.get("standing") or r["position"])
            L.append("| " + " | ".join(cells) + " |")
        L.append("")
    L += ["## Keyword variations counted", "", ", ".join(job["variations"]), ""]
    L += ["## About these factors", ""] + [f"- {a}" for a in about(res)] + [""]
    L += ["## Notes", ""] + [f"- {n}" for n in res["notes"]] + ["", "## Please read", ""] + [f"- {d}" for d in DISCLAIMERS]
    return "\n".join(L) + "\n"


CSS = """
:root{--bg:#fbfbfa;--fg:#1d1f21;--mut:#5f6368;--line:#dcdcd8;--card:#fff;--low:#b3261e;--lowbg:#fbe9e7;--hi:#1e6b35;--hibg:#e6f4ea;--mid:#6b5d00;--midbg:#fff8d6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#17181a;--fg:#e8eaed;--mut:#9aa0a6;--line:#34363a;--card:#1f2023;--low:#f28b82;--lowbg:#3a1f1d;--hi:#81c995;--hibg:#1d3324;--mid:#fdd663;--midbg:#3a3418}}
:root[data-theme=dark]{--bg:#17181a;--fg:#e8eaed;--mut:#9aa0a6;--line:#34363a;--card:#1f2023;--low:#f28b82;--lowbg:#3a1f1d;--hi:#81c995;--hibg:#1d3324;--mid:#fdd663;--midbg:#3a3418}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:28px 0 8px}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}
.partial{border-color:var(--low);background:var(--lowbg)}.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--card);font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left;white-space:normal;min-width:220px}
th{font-weight:600;font-size:.85rem;color:var(--mut)}td.you{font-weight:600}
.pos{font-size:.8rem;padding:2px 7px;border-radius:999px;color:var(--mut)}.t-below-all{background:var(--lowbg);color:var(--low)}.t-below-most{background:var(--midbg);color:var(--mid)}
.t-above-all,.t-above-most{background:transparent;color:var(--mut)}.t-not-measured,.n-not-measured{font-style:italic}
ol#actions li{margin:4px 0}
ul{padding-left:20px}code{font-size:.85em}
"""


def html_page(res):
    job, labels = res["job"], _labels(res)
    e = H.escape
    comp_labels = [lab for p, lab in zip(res["pages"], labels) if p["role"] == "competitor"]
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>On-page comparison</title><style>{CSS}</style></head><body><main>",
           f"<h1>On-page comparison: “{e(job['keyword'])}”</h1><p class=mut>{e(_subtitle(res))}</p>"]
    hl = _headline(res)
    if hl:
        out.append(f"<div class='card partial'><b>{e(hl[0])}.</b> " + e(hl[1]) + "</div>")
    nb = no_browser_line(res)
    if nb:
        out.append(f"<div class='card partial'><b>{e(nb)}</b></div>")
    tp = tag_problems(res)
    if tp:
        out.append("<div class=card><b>Tags on your page:</b> " + e(" ".join(tp)) + "</div>")
    ll = length_line(res["factors"])
    if ll:
        out.append(f"<div class=card><b>Page length:</b> {e(ll)} Most counts in the page-length table rise with length; compare their per 1,000 words rates.</div>")
    out.append(f"<h2>{e(ACTIONS_TITLE)}</h2>")
    acts = _actions(res)
    items = []
    for r in acts:
        ev = evidence_lines(res, r, html=True)
        items.append(f"<li><b>{e(r['name'])}</b>: {e(_action_text(r))}"
                     + (f" <span class=mut>(also: {e(', '.join(r['also']))})</span>" if r.get("also") else "")
                     + ("<ul class=ev>" + "".join(f"<li>{x}</li>" for x in ev) + "</ul>" if ev else "") + "</li>")
    out.append("<ol id=actions>" + "".join(items) + "</ol>")
    if not acts:
        out.append(f"<p>{e(why_no_actions(res['factors']))}</p>")
    for r in address_notes(res["factors"]):
        out.append(f"<p>{e(ADDRESS_LEAD)} <b>{e(r['name'])}</b>: {e(say(r))} {e(ADDRESS_LINE)}</p>")
    out.append(f"<p class=mut>{e(TEST_LINE)}</p>")
    ov = over(res["factors"])
    if ov:
        out.append(f"<h2>{e(OVER_TITLE)}</h2><p class=mut>{e(OVER_INTRO)}</p><ul>"
                   + "".join(f"<li><b>{e(r['name'])}</b>: {e(over_line(r))}</li>" for r in ov) + "</ul>")
    out.append("<h2>Pages</h2>")
    for p, lab in zip(res["pages"], labels):
        out.append(f"<div class=card>{e(_page_line(p, lab))}</div>")
    out.append(f"<p class=mut>{e(COLUMNS_LINE)}</p>")
    for band, title, intro in SECTIONS:
        rows = _table_rows(res, band)
        if not rows:
            continue
        extra = ["You per 1,000 words", "Competitors per 1,000 words"] if band == "length" else []
        hdr = ["Factor", "You"] + comp_labels + ["Competitor range", "Middle value"] + extra + ["Where you stand"]
        out.append(f"<h2>{e(title)}</h2><p class=mut>{e(intro)}</p><div class=wrap><table><thead><tr>"
                   + "".join(f"<th>{e(h)}</th>" for h in hdr) + "</tr></thead><tbody>")
        for r in rows:
            u = r["unit"]
            rng = span(r["competitor_min"], r["competitor_max"], u) if r["n_measured"] else "not measured"
            cls = ("t-" if band == "target" else "n-") + r["position"].replace(" ", "-")
            cells = [fmt(c, u) for c in r["competitors"]] + [rng, fmt(r["competitor_median"], u)]
            cells += _per1k_cells(r) if band == "length" else []
            out.append(f"<tr><td title='{e(r['key'])}'>{e(r['name'])}</td><td class=you>{e(fmt(r['mine'], u))}</td>"
                       + "".join(f"<td>{e(c)}</td>" for c in cells)
                       + f"<td><span class='pos {cls}'>{e(r.get('standing') or r['position'])}</span></td></tr>")
        out.append("</tbody></table></div>")
    out.append("<h2>Keyword variations counted</h2><p class=mut>" + e(", ".join(job["variations"])) + "</p>")
    out.append("<h2>About these factors</h2><ul>" + "".join(f"<li>{e(a)}</li>" for a in about(res)) + "</ul>")
    out.append("<h2>Notes</h2><ul>" + "".join(f"<li>{e(n)}</li>" for n in res["notes"]) + "</ul>")
    out.append("<h2>Please read</h2><ul>" + "".join(f"<li>{e(d)}</li>" for d in DISCLAIMERS) + "</ul></main></body></html>")
    return "\n".join(out)
