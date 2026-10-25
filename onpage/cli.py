#!/usr/bin/env python3
"""On-page comparison: your page vs your top competitors on 50 on-page factors.

Usage (run through ./onpage.sh from the repo root):
  onpage.sh --keyword "stump removal" --mine https://you.example/page \
            --competitor https://a.example/ --competitor https://b.example/x --competitor https://c.example/y \
            [--variations "stump grinding|tree stump removal"] [--raw] [--out DIR] [--name "Your Co"]
            [--as-browser <competitor url>]... [--html <url>=<saved file>]...

Bot name refused: your own page is retried once as a normal Chrome browser.
A competitor page is retried that way only when named with --as-browser (the bot asks the user first).
--html URL=FILE measures a page the user saved in their own browser instead of visiting it.
Bot-check / captcha pages are never bypassed. The report says which pages were read which way.

Writes <out>/onpage.json (schema: onpage/schema/result.schema.json), onpage.md, onpage.html, run.log, and
<out>/pages/<n>-<role>/ (n = 0 for your page, 1.. for competitors) with the HTML that was measured
(server.html and/or browser.html, written by fetch when it supports save_to).
Summary on stdout: the top 10 actions (keyword-placement factors where most competitors have more).
Default out: reports/onpage/<keyword-slug>_<your-host>/; when that folder already holds a report, the next free
<folder>-2, <folder>-3 ... (a re-run never overwrites a report; stdout says which folder). --out DIR is used as given.
Re-run options for blocked pages (--as-browser, --html) are printed on stdout as "TO RE-RUN:" lines (and in run.log);
the report itself says them in plain words.
Exit: 0 complete (every page measured, and measured the same way: all in the browser, or all without it)
      6 PARTIAL (a competitor page could not be measured, or some pages were measured without a browser
        because the browser failed for them while others were rendered; the report says which and why)
      4 NOTHING TO COMPARE (your page, or every competitor page, could not be measured; report written)
      2 bad arguments · 3 did not start (a Python package is missing: run ./install.sh)
"""
import argparse
import datetime as dt
import inspect
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_HOME = os.path.dirname(HERE)
sys.path.insert(0, HERE)

VERSION = "1.1.1"


def _die(code, msg):
    print(msg, file=sys.stderr)
    sys.exit(code)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "page"


# Areas whose texts are quoted under an action line (cli.collect_evidence -> pages[].evidence -> report).
SINGLE_SCOPES = ("title", "meta:description", "meta:og:title", "meta:og:description", "canonical", "tags:h1")


REPORT_FILES = ("onpage.json", "onpage.md", "onpage.html")


def holds_report(d):
    return any(os.path.exists(os.path.join(d, f)) for f in REPORT_FILES)


def free_folder(base):
    """base, or base-2, base-3 ... : the first one that holds no earlier report (a re-run never overwrites one)."""
    out, n = base, 1
    while holds_report(out):
        n += 1
        out = f"{base}-{n}"
    return out


def duplicate_tags(m):
    """measure_page's notes.duplicate_tags (tag -> how many), only tags found more than once."""
    d = ((m or {}).get("notes") or {}).get("duplicate_tags") or {}
    return {k: v for k, v in d.items() if isinstance(v, int) and not isinstance(v, bool) and v > 1}


def _scope_of(f):
    p = f.get("params") or {}
    if p.get("scope"):
        return p["scope"]
    if p.get("tags"):
        return "title" if p["tags"] == ["title"] else "tags:" + ",".join(p["tags"])
    return None


def _item(t, phr, max_chars):
    """{'text': the text (whitespace collapsed, cut at max_chars), 'matched': the variations the counter found in it}."""
    from textutil import count_phrases, line_tokens
    tally = {}
    for toks in line_tokens(t):
        count_phrases(toks, phr, tally)
    t = " ".join(t.split())
    return {"text": t if len(t) <= max_chars else t[:max_chars - 1] + "…", "matched": sorted(" ".join(k) for k in tally)}


def collect_evidence(html, url, variations, registry, per_area=5, max_chars=200):
    """For each target factor counted over a text area: the texts it read, with the variations found in each.
    One-per-page areas (title, meta description, H1...): the first text, even with no match (that is what yours says).
    Other areas (H2s, link text): up to per_area texts that hold a variation. Never raises: {} when nothing can be read."""
    try:
        from measure import Page
        from textutil import phrase_tokens
        pg = Page(html, url)
        if getattr(pg, "doc", None) is None:
            return {}
        phr = phrase_tokens(variations)
        out, memo = {}, {}
        for f in registry["factors"]:
            sc = _scope_of(f)
            once = bool((f.get("params") or {}).get("once"))
            if f.get("band") != "target" or not sc:
                continue
            if sc == "headings" and f.get("measure") != "questions":
                continue
            mk = (sc, once)
            if sc == "first100":  # the matched words with a few words around them, from the first 100 words
                if mk not in memo:
                    memo[mk] = first100_items(pg.page_text, phr, per_area)
                if memo[mk]:
                    out[f["key"]] = memo[mk]
                continue
            if sc == "headings":  # 'Questions in headings': the headings that end with a question mark
                if mk not in memo:
                    try:
                        qs = [t for t in pg.scope_texts(HEADING_TAGS, once) if t.rstrip().endswith(("?", "？"))]
                    except (ValueError, AttributeError, TypeError):
                        qs = []
                    memo[mk] = [_item(t, phr, max_chars) for t in qs][:per_area]
                if memo[mk]:
                    out[f["key"]] = memo[mk]
                continue
            sc_mk = mk
            if sc_mk not in memo:
                try:
                    texts = pg.scope_texts(sc, once)
                except (ValueError, AttributeError, TypeError):
                    texts = []
                items = [_item(t, phr, max_chars) for t in texts]
                if sc in SINGLE_SCOPES:
                    items = items[:1]
                else:
                    items = [x for x in items if x["matched"]][:per_area]
                memo[sc_mk] = items
            if memo[sc_mk]:
                out[f["key"]] = memo[sc_mk]
        return out
    except Exception:  # evidence is a reading aid; a page that breaks it still gets its numbers
        return {}


HEADING_TAGS = "tags:h1,h2,h3,h4,h5,h6"
CONTEXT_WORDS = 4  # words quoted on each side of a match in the first 100 words


def first100_items(page_text, phr, limit=5, around=CONTEXT_WORDS):
    """The keyword variations the 'first 100 words' factor counts, each quoted with up to `around` words on each
    side (from the same block, inside the window): [{'text': '... words **match** words ...', 'matched': [...]}].
    Matches close together share one quote. Uses the counter's window (textutil.window_text) and matching rule."""
    from textutil import count_phrases, match_spans, window_text
    out = []
    for line in window_text(page_text, 100):
        spans = match_spans(line, phr)  # [(start, end, phrase)] in the original text, as the counter counts
        if not spans:
            continue
        wd = [m for m in WORD_RE.finditer(line)]
        groups = []
        for a, b, p in spans:
            i = next((k for k, m in enumerate(wd) if m.end() > a), len(wd) - 1)
            j = next((k for k in range(len(wd) - 1, -1, -1) if wd[k].start() < b), i)
            lo, hi = max(0, i - around), min(len(wd) - 1, j + around)
            if groups and lo <= groups[-1][1] + 1:
                groups[-1] = [groups[-1][0], max(hi, groups[-1][1]), groups[-1][2] + [p]]
            else:
                groups.append([lo, hi, [p]])
        for lo, hi, ps in groups:
            a, b = wd[lo].start(), wd[hi].end()
            txt = ("… " if lo > 0 else "") + line[a:b] + (" …" if hi < len(wd) - 1 else "")
            out.append({"text": txt, "matched": sorted(set(" ".join(p) for p in ps))})
            if len(out) >= limit:
                return out
    return out


WORD_RE = re.compile(r"\S+")


def run_state(pages, mode=None):
    """(state, exit code): ("nothing", 4) your page or every competitor not measured; ("partial", 6) a competitor
    not measured, or some pages measured without a browser while others were rendered; else ("complete", 0)."""
    from report import no_browser_pages
    mine, comps = pages[0], pages[1:]
    n_ok = sum(1 for c in comps if c["measured"])
    if not mine["measured"] or n_ok == 0:
        return "nothing", 4
    nb = no_browser_pages(pages, mode)
    if n_ok < len(comps) or 0 < len(nb) < sum(1 for p in pages if p["measured"]):
        return "partial", 6
    return "complete", 0


def run_notes(pages, mode=None):
    """Notes for the report about how each page was read, and what to do about the ones that were not."""
    mine, comps = pages[0], pages[1:]
    n_ok = sum(1 for c in comps if c["measured"])
    notes = []
    if not mine["measured"]:
        notes.append(f"PARTIAL: your page could not be measured ({mine['fetch'].get('error')}). Nothing to compare.")
    if n_ok == 0:
        notes.append("PARTIAL: no competitor page could be measured. Nothing to compare.")
    for i, c in enumerate(comps, 1):
        if not c["measured"]:
            notes.append(f"PARTIAL: competitor {i} ({c['url']}) could not be measured: {c['fetch'].get('error')}. "
                         "Ranges and counts use the competitors that were measured.")
    if 0 < n_ok < 2:
        blk = any(c["fetch"].get("blocked") for c in comps if not c["measured"])
        notes.append("Only 1 competitor page was measured, so there is no action list. To get one, re-run with another page "
                     "that ranks for the keyword" + (", or include a blocked page as the next notes say." if blk else "."))
    from report import no_browser_pages
    nb = [p["url"] for p in no_browser_pages(pages, mode)]
    if nb:
        notes.append("Measured without a browser because the browser did not work for these pages (text that JavaScript adds "
                     "is missing, so counts may be lower than what visitors see). Ask whoever runs this report to check the browser "
                     "and run it again: "
                     + ", ".join(nb))
    modes = {p["fetch"].get("mode_used") for p in pages if p["measured"]}
    if len(modes) > 1:
        notes.append("Pages were measured differently (some in a browser, some from server HTML only), so small differences may come from that.")
    asb = [p["url"] for p in pages if p["measured"] and p["fetch"].get("user_agent") == "browser"]
    if asb:
        notes.append("Read once as a normal Chrome browser because the site refused this bot's name: " + ", ".join(asb))
    sv = [p["url"] for p in pages if p["fetch"].get("source") == "saved-file"]
    if sv:
        notes.append("Measured from a copy the user saved in their own browser (not visited by this bot): " + ", ".join(sv))
    # In the report the way to include a blocked page is said in plain words; the options the bot re-runs with
    # are in bot_steps() (stdout and run.log only).
    refused, checked, unknown = _blocked_kinds(comps)
    if refused:
        notes.append("These sites refused this bot's name. To include one: say yes and the page is read once more as a normal "
                     "Chrome browser, or " + SAVE_WORDS + ": " + ", ".join(refused))
    if checked:
        notes.append("These pages showed a bot check (never bypassed) or failed as a normal browser too. To include one: "
                     + SAVE_WORDS + ": " + ", ".join(checked))
    if unknown:
        notes.append("These competitor pages were blocked. To include one: say yes and the page is read once more as a normal "
                     "Chrome browser (this helps only when the site refused the bot's name), or " + SAVE_WORDS + ": " + ", ".join(unknown))
    js = [p["url"] for p in pages if p["fetch"].get("js_dependent")]
    if js:
        notes.append("Most text on these pages appears only after JavaScript runs (crawlers that do not run JavaScript see much less): " + ", ".join(js))
    notes.append("Keyword matching: case-insensitive whole words; each word counts once, inside the longest variation it belongs to.")
    return notes


SAVE_WORDS = ("open the page in your own browser, save it (File, Save Page As, \"Webpage, HTML only\") and send the file "
              "to whoever runs this report")


def _blocked_kinds(comps):
    """(refused, checked, unknown) URLs of blocked competitor pages. A bot check, or a page already read as a
    browser, can only come in as a copy the user saves; a refusal of the bot's NAME may pass as a browser."""
    blocked = [p for p in comps if not p["measured"] and p["fetch"].get("blocked")]
    kind = {p["url"]: ("check" if p["fetch"].get("block_kind") == "check" or p["fetch"].get("user_agent") == "browser"
                       else p["fetch"].get("block_kind")) for p in blocked}
    return ([u for u, k in kind.items() if k == "refusal"], [u for u, k in kind.items() if k == "check"],
            [u for u, k in kind.items() if k not in ("refusal", "check")])


def bot_steps(pages, mode=None):
    """The re-run options for blocked competitor pages, for the bot (stdout and run.log; never in the report)."""
    refused, checked, unknown = _blocked_kinds(pages[1:])
    out = []
    if refused:
        out.append("Refused the bot's name: ask the user, then re-run with --as-browser <url>, "
                   "or measure a copy they save with --html <url>=<file>: " + ", ".join(refused))
    if checked:
        out.append("Bot check (never bypassed) or failed as a browser too: ask the user to save the page from their own browser "
                   "and re-run with --html <url>=<file>: " + ", ".join(checked))
    if unknown:
        out.append("Blocked: ask the user, then re-run with --as-browser <url> (only helps when the site refused the bot's name), "
                   "or measure a copy they save with --html <url>=<file>: " + ", ".join(unknown))
    if no_browser_urls(pages, mode):
        out.append("Browser did not work for some pages: run ./doctor.sh, then re-run.")
    return out


def no_browser_urls(pages, mode=None):
    from report import no_browser_pages
    return [p["url"] for p in no_browser_pages(pages, mode)]


def parse(argv):
    ap = argparse.ArgumentParser(prog="onpage.sh", description="Compare your page with competitor pages on 50 on-page factors.")
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--mine", required=True, help="your page URL")
    ap.add_argument("--competitor", action="append", default=[], help="a competitor page URL (give 1 to 5; 3 is the usual)")
    ap.add_argument("--variations", default="", help='extra keyword variations, separated by |')
    ap.add_argument("--raw", action="store_true", help="measure the server HTML (no browser) for every page")
    ap.add_argument("--out", help="output folder")
    ap.add_argument("--name", default="", help="your business name (shown in the report)")
    ap.add_argument("--as-browser", action="append", default=[], metavar="URL",
                    help="a competitor URL the user agreed may be read once as a normal browser if it refuses the bot name")
    ap.add_argument("--html", action="append", default=[], metavar="URL=FILE",
                    help="measure a page from a file the user saved in their own browser")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        sys.exit(2 if e.code else 0)
    urls = [a.mine] + a.competitor
    if not 1 <= len(a.competitor) <= 5:
        _die(2, "BAD ARGUMENTS: give 1 to 5 --competitor URLs (3 is the usual).")
    for u in urls:
        sp = urlsplit(u)
        if sp.scheme not in ("http", "https") or not sp.hostname:
            _die(2, f"BAD ARGUMENTS: not a web address: {u!r} (use https://host/path)")
    if not a.keyword.strip() or len(a.keyword) > 200:
        _die(2, "BAD ARGUMENTS: --keyword is empty or too long.")
    if len(set(urls)) != len(urls):
        _die(2, "BAD ARGUMENTS: the same URL was given twice.")
    for u in a.as_browser:
        if u not in a.competitor:
            _die(2, f"BAD ARGUMENTS: --as-browser {u!r} is not one of the --competitor URLs.")
    a.saved = {}
    for item in a.html:
        u, sep, f = item.rpartition("=")  # last "=": URLs may hold "=" in the query string
        if not sep or u not in urls:
            _die(2, f"BAD ARGUMENTS: --html needs URL=FILE with URL one of the pages given: {item!r}")
        if not os.path.isfile(f):
            _die(2, f"BAD ARGUMENTS: saved file not found: {f!r}")
        a.saved[u] = f
    return a


def main(argv=None):
    a = parse(argv if argv is not None else sys.argv[1:])
    try:
        import lxml.html  # noqa: F401
        from measure import load_registry, measure_page, add_tf_idf
        from fetch import fetch, read_saved
        import variations as V
        from compare import actions, compare
        import report
        import schemacheck
    except ImportError as e:
        _die(3, f"DID NOT START: {e}. Run ./install.sh, then ./doctor.sh.")

    reg = load_registry()
    extra = [v.strip() for v in a.variations.split("|") if v.strip()]
    mode = "raw" if a.raw else "rendered"
    job = {"keyword": a.keyword.strip(), "mine": a.mine, "competitors": a.competitor, "mode": mode,
           "variations": V.build(a.keyword, extra), "extra_variations": extra}
    if a.name:
        job["name"] = a.name
    errs = schemacheck.validate(job, schemacheck.load("job.schema.json"))
    if errs:
        _die(2, "BAD ARGUMENTS: " + "; ".join(errs))

    moved = None
    if a.out:
        out = a.out
    else:
        base = os.path.join(os.environ.get("KIT_REPORTS") or os.path.join(KIT_HOME, "reports"), "onpage",
                            f"{_slug(a.keyword)}_{_slug(urlsplit(a.mine).hostname or '')}")
        out = free_folder(base)
        moved = base if out != base else None
    replacing = bool(a.out) and holds_report(out)
    try:
        os.makedirs(out, exist_ok=True)
        log = open(os.path.join(out, "run.log"), "w", encoding="utf-8")
    except OSError as e:
        _die(3, f"DID NOT START: cannot write the folder {out}: {e}")

    def say(msg):
        line = f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    if moved:
        say(f"an earlier report is in {moved}, so this one goes to a new folder: {out} (nothing was overwritten)")
    if replacing:
        say(f"--out names the folder exactly: replacing the earlier report in {out}")
    urls = [("mine", a.mine)] + [("competitor", u) for u in a.competitor]
    say(f"onpage {VERSION}: keyword={job['keyword']!r} pages={len(urls)} mode={mode}")
    try:
        can_save = "save_to" in inspect.signature(fetch).parameters
    except (TypeError, ValueError):
        can_save = False

    def get(item):
        n, (role, url) = item
        if url in a.saved:
            rec = read_saved(url, a.saved[url])
        else:
            kw = {}
            if can_save:  # fetch writes server.html / browser.html into this folder
                kw["save_to"] = os.path.join(out, "pages", f"{n}-{role}")
            rec = fetch(url, mode=mode, browser_retry=(role == "mine" or url in a.as_browser), **kw)
            if kw:
                rec = dict(rec, saved_to=os.path.relpath(kw["save_to"], out))
        say(f"{role:10} {url} -> {'fetched' if rec.get('html') else 'NOT FETCHED'}"
            f" status={rec.get('status')} mode={rec.get('mode_used')} {rec.get('ms')} ms"
            + (f" ({rec['error']})" if rec.get("error") else "") + (f" [{rec['note']}]" if rec.get("note") else ""))
        for line in rec.get("browser_log") or []:   # Chrome's raw words: run.log only (not the screen, report or onpage.json)
            log.write(f"{dt.datetime.now().strftime('%H:%M:%S')} {role:10} {url} browser detail: {' '.join(str(line).split())[:600]}\n")
        log.flush()
        rec = {k: v for k, v in rec.items() if k != "browser_log"}
        return role, url, rec

    with ThreadPoolExecutor(max_workers=2) as ex:
        fetched = list(ex.map(get, enumerate(urls)))

    # One variation list for every page: keyword phrases + singular/plural forms seen on these pages.
    from measure import Page
    page_tokens = []
    for _, _, rec in fetched:
        if rec.get("html"):
            pg = Page(rec["html"], rec.get("final_url") or "https://x.invalid/")
            if hasattr(pg, "page_tokens"):  # title + visible body text (measure.py v2)
                page_tokens.append(pg.page_tokens)
            else:
                page_tokens.append(pg.body_tokens)
                page_tokens += [V.tokens(t) for t in pg.title()]
    variations = V.build(job["keyword"], extra, page_tokens=page_tokens)
    job["variations"] = variations
    say(f"variations ({len(variations)}): {', '.join(variations)}")

    pages, results = [], []
    for role, url, rec in fetched:
        page = {"role": role, "url": url, "measured": False, "fetch": {k: v for k, v in rec.items() if k not in ("html", "url")},
                "values": {}, "not_measured": {}}
        if rec.get("html"):
            m = measure_page(rec["html"], rec.get("final_url") or url, job["keyword"], variations, registry=reg)
            page["values"], page["not_measured"] = m["values"], m["not_measured"]
            page["measured"] = bool(m["values"])
            if page["measured"]:
                page["duplicate_tags"] = duplicate_tags(m)
                page["evidence"] = collect_evidence(rec["html"], rec.get("final_url") or url, variations, reg)
            if page["measured"]:
                results.append(m)  # m["values"] is page["values"]: add_tf_idf fills it in place
        if not page["measured"]:
            why = rec.get("error") or "no HTML"
            page["not_measured"] = {f["key"]: why for f in reg["factors"]}
        pages.append(page)
    add_tf_idf(results, registry=reg)  # tf_idf needs the compared set (document frequency)

    mine, comps = pages[0], pages[1:]
    rows = compare(reg, mine["values"] if mine["measured"] else None, [c["values"] if c["measured"] else None for c in comps])
    notes = run_notes(pages, mode)
    state, _ = run_state(pages, mode)
    complete = state == "complete"
    res = {"tool": "site-audit-kit onpage", "version": VERSION, "created": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "job": job, "pages": pages, "factors": rows, "actions": [r["key"] for r in actions(rows)],
           "complete": complete, "notes": notes}
    errs = schemacheck.validate(res, schemacheck.load("result.schema.json"))
    if errs:
        say("REPORT FAILED: result does not match its schema: " + "; ".join(errs[:5]))
        sys.exit(5)
    with open(os.path.join(out, "onpage.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    with open(os.path.join(out, "onpage.md"), "w", encoding="utf-8") as f:
        f.write(report.markdown(res))
    with open(os.path.join(out, "onpage.html"), "w", encoding="utf-8") as f:
        f.write(report.html_page(res))
    print("===== ONPAGE SUMMARY =====")
    print(f"keyword: {job['keyword']}")
    for p in pages:
        print(f"{p['role']:10} {'measured' if p['measured'] else 'NOT MEASURED'}  {p['url']}")
    top = report.summary(res)
    print(f"actions: {len(res['actions'])} keyword-placement factors where most competitors have more"
          + (f" (top {len(top)}):" if top else ""))
    for line in top:
        print(f"  - {line}")
    print(f"files: {out}/onpage.html, onpage.md, onpage.json, run.log")
    nb = report.no_browser_line(res)
    if nb:
        print(nb)
    for step in bot_steps(pages, mode):
        say("TO RE-RUN: " + step)
    if state == "nothing":
        print("RESULT: NOTHING TO COMPARE (exit 4)")
        sys.exit(4)
    if state == "partial":
        print("RESULT: PARTIAL (exit 6)")
        sys.exit(6)
    print("RESULT: COMPLETE (exit 0)" + (" - measured without a browser" if nb else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
