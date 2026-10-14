# 02 - The findings sidecar and the report builder

How audit findings become the HTML report, and how to hand-edit them without getting bitten.

Two programs, both at the repository root:

- `checks-to-findings.py` (here `C2F`). Reads `checks.log`, `trace.log`, `hack.log`, `render.log`,
  `pixel.log`, `discovery.log`, `filterscan.log` and `cms-deepdive.log` from the case folder, plus the
  crawl JSON, and writes the "sidecar" `$CRAWL_DIR/<slug>-findings.json`.
- `build-crawl-report.js` (here `BCR`). Reads one crawl JSON (`<slug>-full.json`), finds a sidecar
  next to it, adds its own "auto" findings, re-probes some URLs live, and writes one HTML file (or one
  Markdown file when the output name ends in `.md`).

Citations name a function, a variable or a quoted marker string; grep for it. Line numbers move
while the kit is being edited, so the few given here carry the date they were checked. Statements
marked OBSERVED come from builds run on 2026-10-13 against a five-row crawl copy (all status 200, so
no live probe fired) with hand-written test sidecars; no published report was touched. Where the code
has changed since, the text says so and describes the current code.

---

## 0. Paths in this kit

| Name | Value |
|---|---|
| `KIT_HOME` | the repository root; every script finds it from its own location |
| `KIT_WORK` | `${KIT_WORK:-$KIT_HOME/work}` |
| `CRAWL_DIR` | `$KIT_WORK/crawl/`: `<slug>-full.json`, `<slug>-findings.json`, `<slug>-operator-findings.json`, perf JSONs |
| `CASES_DIR` | `$KIT_WORK/cases/<slug>/`: stage logs, `site-audit.html`, `findings-verified.json`, `evidence/` |
| `REPORTS_DIR` | `$KIT_HOME/reports/<domain>/`: the published `site-audit.html`, `report.md`, `report.pdf`, `findings.json`, `linkmap.html` (domain = host without `www.`) |
| publish backups | `$KIT_WORK/publish-backups/<domain>/` |

The orchestrator (`run-full-audit.sh`, see `docs/01-PIPELINE-ANATOMY.md`) runs C2F as stage 6b and
BCR as stage 7, building into `$CASES_DIR/<slug>/site-audit.html`. `publish-run.sh` copies a
verified page into `$REPORTS_DIR/<domain>/`. `audit.sh` builds and copies without a publish gate.

Optional report branding, all read by BCR from the environment and all off by default:
`REPORT_BRAND` (footer text; default "Site audit"), `CTA_URL` (shows a "Book a call" box above the
findings and again at the bottom), `CTA_TEL` (adds a phone button), `CTA_TEL_TXT` (its label).

---

## 1. Sidecar file resolution

### 1.1 What the writer (C2F) writes

- C2F takes `slug` from `argv[1]`, the case folder from `argv[2]` (default `$KIT_WORK/cases/<slug>`)
  and the crawl folder from `argv[3]` (default `$KIT_WORK/crawl`). `KIT_HOME` is the script's own
  folder; `KIT_WORK` comes from the environment.
- It writes ONE client sidecar: `<crawl-dir>/<slug>-findings.json`, named by the RUN slug, with
  - `findings`: the list, sorted by severity (`critical`, `high`, `medium`, `low`; anything else
    ranks 9);
  - `perf_note`: "See the Performance accordion; reference only, not a priority fix.";
  - `could_not_check`: groups of linked addresses the site refused to show the checker (401, 403,
    406, 429, no answer, bot challenge), each `{what, why, urls}`. These are not findings and not
    passes. BCR does not read this key (it lists the same crawl rows itself, section 4); `audit.sh`
    and the verifier do.
- It also writes `<crawl-dir>/<slug>-operator-findings.json` with `operator_only` (every finding whose
  title matches `consent` or `tracker`; these never go into the client sidecar) and
  `not_measured_csr_checks` (checks that need rendered pages on a client-rendered site).
- The host-named copy (`<host>-findings.json`) that older versions wrote next to the run-slug file is
  gone. It was a decoy: hand edits to it were ignored because BCR reads the run-slug file first (a
  client run, 2026-10).
- C2F REGENERATES; it never merges. Running it again overwrites the sidecar with fresh output and
  destroys every hand edit. A hand-edited sidecar was lost this way on a client audit (2026-08).

### 1.2 What the reader (BCR) reads

- The sidecar is looked up in the directory of the INPUT JSON, not the working folder and not the
  output folder: `dir = path.dirname(inPath)`.
- `runSlug` = input file name with `-full.json` removed.
- Precedence: if `<dir>/<runSlug>-findings.json` EXISTS, use `runSlug`; otherwise use `D.site` (the
  host from the crawl JSON, no `www.`). The test is `fs.existsSync` only.
- The chosen file is parsed inside a `try`. A parse error now stops the build:
  `FATAL: <file> is not valid JSON (...). Fix it; nothing was built.` and exit 3. (Before 2026-10-13
  the `catch` was empty, the build continued with zero sidecar findings and exited 0; OBSERVED.)
- The file may be an array of findings or an object `{findings:[...], perf_note:"...", ...}`. Only the
  object form can carry `perf_note`, `suppress_builder` and `not_measured`.

### 1.3 Precedence table

| Files present in the input JSON's folder | File BCR uses |
|---|---|
| `<runSlug>-findings.json` (valid) | run-slug file. A host file is ignored. |
| `<runSlug>-findings.json` (invalid JSON) | run-slug file; FATAL exit 3, no HTML written. A host file is still ignored. |
| only `<host>-findings.json` | host file (for example one left by an older tool version or by another slug of the same site) |
| neither | none (auto findings only) |

### 1.4 Failure modes

1. **A stale host file.** When C2F did not run (or crashed) for this slug, BCR falls back to
   `<host>-findings.json` if one exists. That file can belong to another run of the same site. The
   build exits 0 and looks normal. Check which file was used (section 7, step 1).
2. **Broke the JSON.** Now loud: exit 3, nothing written. Fix the file and rebuild.
3. **Re-ran C2F after editing.** All hand edits are gone.
4. **Built from a copy elsewhere.** If you run BCR on a crawl JSON copied into another folder (for
   example the case folder), it reads the sidecar from THAT folder, not from `$CRAWL_DIR`.
5. **Perf data from another run.** Perf JSONs are looked up under four spellings including the bare
   host (`<slug>-<tier>.json`, `<site>-<tier>.json`, `<runSlug>-<site>-<tier>.json`,
   `<runSlug>-<tier>.json`), so a run with no perf stage can show perf tiers from an older run of
   the same host. See `docs/KNOWN-PITFALLS.md` (concurrent audits sharing state).

---

## 2. The finding schema (what BCR reads)

### 2.1 Top level of the sidecar file

| Key | Effect |
|---|---|
| `findings` (array) | the sidecar findings. A bare top-level array is also accepted. |
| `perf_note` (string) | printed under the perf table. Shows only if at least one perf JSON was found. |
| `suppress_builder` (array of strings) | each entry is a case-insensitive substring of an AUTO finding title (section 4). Matching auto findings are dropped, and BCR prints `suppressed N builder finding(s) by sidecar suppress_builder: ...` to stderr. Use it when a verifier proves an auto finding false for this site. |
| `not_measured` (array of `{step, what, why}`) | steps the run skipped. BCR shows them first under "What we could not check" (`what` in bold, then `why`). `publish-run.sh` refuses an INCOMPLETE run unless every SKIPPED gate key is listed here by `step`. |
| `could_not_check` | written by C2F; not read by BCR. |

### 2.2 Per finding

| Key | Required | Renders where / does what |
|---|---|---|
| `severity` | yes | Must be one of `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `NOTE` in any case. Anything else (a missing value, `info`, `"Critical "` with a space) stops the build: `FATAL: unknown severity ...`, exit 3. Uppercased for the sort and the chip. See section 6. |
| `title` | yes | Accordion summary text, HTML-escaped. ALSO read by the title matchers in section 3. |
| `whats_happening` | no | "What's happening" step. |
| `see_it` | no | "See it yourself" step. |
| `youll_see` | no | "What you'll see" step. |
| `why` | no | "Why this matters" step. |
| `fix` | no | "How to fix it" step. |
| `detail` | no | Rendered ONLY when all five walk-through keys are empty or absent. |
| `urls` | no | List under the body. Each item is a string or an object `{url, note, sources[]}`. |
| `groups` | no | Each group `{label, code[], urls[]}`. `label` is a bold line, `code[]` lines render under "For your web person ...", `urls` is a list. `urls` is REQUIRED inside a group in HTML mode (2.4). |
| `expand_inlinks` | no | A path or URL. BCR appends a group listing every crawled page that links to it, with the href as written. The group label starts "Broken address:", so use it for broken targets. |

Any other key (for example `id`, `confidence`, notes to self) is ignored by BCR. C2F writes `detail`
and `urls` on most findings and the five walk-through keys on some (the broken-link finding, the meta
description finding and others).

### 2.3 Override rules

- **Walk-through beats detail.** `hasWalk` (in `findingCard()`) is true if ANY of the five walk keys
  is truthy. Then `detail` is not rendered at all. OBSERVED: a finding with
  `detail:"SANITIZED-DETAIL"` and `whats_happening:"UNSANITIZED-WALK"` produced HTML containing the
  walk text and not the detail text.
- **Consequence for sanitizing:** if you scrub a name, a wrong number or a claim from `detail` only,
  the `whats_happening`, `see_it`, `youll_see`, `why` and `fix` text still ships. Also scrub `urls[]`
  strings and `groups[].label`; both render verbatim.
- **Only steps with text render.** Empty walk keys are skipped, so a finding with only `fix` shows one
  step.
- **URL count chip.** It counts `urls` items that are objects or that begin with `http(s)://` or `/`.
  Lines like "↳ linked from: ..." do not count. If no item counts, it falls back to `urls.length`
  plus every group's `urls.length`. Prose lines in `urls` therefore do not move the chip, and a
  finding whose `urls` are all prose shows the fallback total.
- **Object URL items:** `sources` with at least max(20, 80% of pages) entries render as "found in the
  site-wide template" instead of a list.
- **Linkify:** every `http(s)://...` substring in a string item becomes a link; a leading `/path` item
  is linked to the crawl origin.

### 2.4 Crash and silent-skip cases

- A group without `urls` throws `TypeError` (`g.urls.length` in the chip count, `ulist(g.urls)` in
  the group render). OBSERVED: exit 1, no HTML written. Loud, which is good. The Markdown path uses
  `(g.urls||[])` and does not crash.
- Invalid JSON: exit 3 (section 1.2).
- Unknown or missing severity: exit 3.
- A finding with no `title` renders an empty summary; no error.

---

## 3. Title-text matchers in BCR (every one)

BCR finds sidecar findings by regex on `f.title`. These are the only finding-title reads; other
`.title` reads in BCR are PAGE titles from the crawl.

### 3.1 Broken-link merge (`sidecarBroken`)

- Regex, case-insensitive:
  `/do(es)? not exist|doesn'?t exist|aren'?t there|isn'?t there|broken (internal )?link/i`
- `manual.find(...)` returns the FIRST matching finding in file order. C2F sorts by severity before
  writing, so a higher-severity finding with a matching phrase wins over the real broken-link finding.
- On a match: every broken target BCR still believes broken (after the filters and the live
  re-probe, section 5.2) is appended as a group to that sidecar finding, and BCR's own HIGH
  broken-link finding is NOT created. The sidecar finding keeps its own severity, title and body.
- On no match: BCR adds its own HIGH finding "N broken internal link target(s) across M page(s)".
- What C2F emits today: "N of M checked links on your site lead to pages that aren't there" (or
  "leads to a page that isn't there", "... aren't there or fail to load", "... fail to load (broken
  links)"). Every variant matches; the C2F source carries a comment saying the words must stay.
- Traps:
  - Retitle without one of those phrases and the same defect ships twice (two client sites,
    2026-09).
  - Any other finding whose title happens to contain "do not exist", "doesn't exist", "isn't there",
    "aren't there" or "broken link" captures the merge. The broken targets then render inside an
    unrelated accordion and the real broken-link HIGH disappears. C2F's soft-404 canned title says
    "Addresses that don't exist on your site answer as if they do": "don't exist" does NOT match
    today; rewording it to "do not exist" would.
  - Deleting the sidecar broken-link finding (to withdraw a false positive) does not remove it: BCR
    re-creates its own HIGH version from the crawl unless the live re-probe drops the target. Use
    `suppress_builder` with a fragment of BCR's title ("broken internal link target") to drop it.
- `sidecarText` is computed and never used. Dead code.

### 3.2 CSR shell suppression (`shellFinding`, `csrShell`, `isShell`)

- Regex, case-SENSITIVE: `/empty shells until JavaScript/`.
- A second regex `/empty shells until JavaScript|invisible until JavaScript/` (`csrShell`) only runs
  on a finding that already matched the first, so `csrShell` is always true when `shellFinding`
  exists. The "invisible until JavaScript" branch is unreachable.
- On a match, `isShell(p)` is true for a 200 HTML row that either (a) has no title, no H1 in
  `headings.h1`, and under 400 words, or (b) has under 200 words. Such rows are removed from:
  - missing-H1
  - missing meta description
  - share image / OG
- They are NOT removed from alt text, canonical, duplicate-address, or the page list.
- The test is by CRITERIA, not by membership: BCR never reads the shell finding's `urls`. Any real
  thin page under 200 words (a thank-you page, a short policy page) is also dropped from the H1, meta
  and OG findings even if the shell finding does not list it.
- What C2F emits: "N pages are empty shells until JavaScript runs" (severity `critical`). Matches.
- OBSERVED: with the title "1 pages are empty shells until JavaScript runs" an 87-word `/thank-you`
  row was dropped from the OG finding. Retitled "1 page is blank until JavaScript runs", the OG
  finding came back. "Empty shells until javascript" (capital E or lowercase j) would also fail,
  because there is no `/i`.

### 3.3 Matchers inside C2F (affect hand edits only if you re-run C2F)

C2F also looks up its own findings by title while building, for dedupe: `startswith("Links and
images use the wrong version")`, `"email address is hidden"`, `"sitemap but nothing"`,
`"before visitors can consent"`, `"filter"` / `"only when clicked"`. These read the in-memory list
during generation, not the sidecar file, so a hand edit cannot break them. Changing the canned copy
in C2F can. C2F also moves every finding whose title matches `consent|tracker` to the operator-only
file, so a client finding that happens to use the word "tracker" disappears from the client sidecar.

---

## 4. BCR's own auto findings

All are computed from the crawl JSON. The base set is `p200`: rows with status 200, not
`redirected`, content type HTML. `noindexed(p)` uses the crawler's stored `noindex` verdict when
present, else the shared `NOINDEX_RE` on `meta_robots` (the same regex as
`crawl_transport.NOINDEX_RE`). The sidecar controls them only through the two matchers in section 3
and `suppress_builder`.

| # | Title (pattern) | Severity | Trigger | Sidecar interaction |
|---|---|---|---|---|
| 1 | "N broken internal link target(s) across M page(s)" | HIGH | a row in `errs` (non-200 and not "could not check", see below) with at least one linking page; drops `/cdn-cgi/l/email-protection`, status 406, and status 0/null with a `?`; then dropped if a live re-probe answers 200-399 and not 202 | merged into the sidecar finding matched by 3.1; else its own finding |
| 2 | "N page(s) tell Google the "real" page lives on another website" | CRITICAL | canonical host is not the site host or a subdomain of it; not noindexed | none. C2F check 1 can ship the same fact again: two CRITICALs for one defect |
| 3 | "N page(s) declare a BROKEN official-version address" | CRITICAL | canonical has whitespace, a double slash after the host, or `&amp;`/`&#38;` | none |
| 4 | "N page(s) point their canonical at a different page on this site" | MEDIUM | same-site canonical that differs from the URL after removing: identical-title targets, redirect-to-canonical, Shopify collection products, case/encoding/`+` vs `%20` | none. C2F check 2 ("Some pages name a different page as their official version") can duplicate it |
| 5 | "N of your pages are reachable at more than one address" | MEDIUM | `?` URL whose clean form was crawled, no canonical, not an auth route | none. C2F check 21 may overlap |
| 6a | "Most pages record no main headline (H1) - verify this against the live pages first" | MEDIUM | missing-H1 rate above 70%, at least 10 pages, and some pages DO have an H1 (a page builder may emit the H1 in markup the raw crawl misses) | shell rows removed (3.2) |
| 6b | "N page(s) missing a main headline (H1)" | MEDIUM | `heading_counts.h1` falsy, not noindexed, not shell | shell rows removed (3.2) |
| 7 | "N page(s) don't/doesn't tell Google what to write underneath them/it in search results" | MEDIUM | no meta description, not noindexed, not shell, self-canonical | shell rows removed (3.2) |
| 8 | "N image(s) missing alt text on M page(s)" | LOW | `images_missing_alt > 0` | none |
| 9 | "N page(s) has/have no share image / no share title / no share image or title set for social media" | LOW | non-asset page lacking `og:title`, or lacking both `og:image` and `twitter:image` | shell rows removed (3.2) |

Every row above can also be dropped with `suppress_builder`.

Not auto findings:

- **www links**: BCR has no www/non-www finding. That comes only from C2F (checks 6 and 12).
- **Missing title**: BCR has no missing-title finding. It only puts a "no title" flag on the page
  card.
- **Errors by status code** is a section, not a finding. It lists every row in `errs` that the
  broken-link re-probe did not clear (`reverifiedOK`), one accordion per status, with linking pages.
  The sidecar has no effect on it.
- **What we could not check** is a section, not a finding. It lists the sidecar's `not_measured`
  entries first, then `blockedRows`: every non-200 row whose status is 0, null, 401, 403, 406 or
  429 (`BLOCKED_ST`). Those answers say the site or the transport refused the checker, not that the
  page is missing (RFC 9110: only 404 and 410 say a page is not there). They are not counted as
  errors and not as passes. This split came from a client site (2026-10) where 46 bot-check 403s
  were published as "Broken address" and "49 errors".

---

## 5. Live re-probes (network calls at build time)

Every rebuild contacts the live site. Both probes run `curl -4 -s -L --compressed -m 25` with the
crawl's UA, discard the body and keep only the status code. `CRAWL_PROXY` and `CRAWL_UA` are honoured
(default UA: Chrome 131). Calls are synchronous (`execFileSync`, 40 s timeout).

### 5.1 Probe 1: transient statuses at the source (`_TRANSIENT_ST`)

- Scope: every crawl row whose status is in `{0, 408, 425, 429, 500, 502, 503, 504, 507, 508, 509,
  522, 524}`. 404, 403 and 410 are not probed here. A `null` status is not in the set.
- Up to 2 attempts; it retries while the answer is null or still transient. No pause between tries.
- If the answer is 200-399 and not 202: the row's `status` is OVERWRITTEN in memory with the live code
  and `_recovered_from` is set. The JSON on disk is not changed.
- A 0 or 429 row that does NOT recover lands in "What we could not check" (it is in `BLOCKED_ST`);
  a 5xx that does not recover stays an error, because visitors get it too.
- Side effects:
  - A recovered row leaves `errs`, the error pill and "Errors by status code".
  - But `p200` was already computed BEFORE this probe. So a recovered row is also absent from every
    on-page finding and from the "All pages" list. It disappears from the report entirely (R7).
  - Log line: "(N transient non-200 row(s) re-verified live as OK ...)".
- Why it exists: on a shared host (a cleaning-company site, 2026-08) the crawl's own concurrency hit
  the host's resource cap and 13 URLs returned 508; all answered 200 on paced live probes.

### 5.2 Probe 2: broken-link targets

- Scope: every candidate for auto finding 1 (404, 410, persistent 5xx and other non-blocked
  non-200 rows that have a linking page).
- Up to 2 attempts, retrying ONLY on transport failure (`live===null`), not on a transient answer.
- Dropped if the answer is 200-399 and not 202. Log line "(re-verified OK, dropped from report:
  [old] -> new URL)". The URL is added to `reverifiedOK`, which also removes it from "Errors by
  status code" and from the error pill.
- Because of `-L`, a target that now redirects to a live page (for example a 404 that the site owner
  has since redirected to the homepage) is dropped. Usually right, but the report can then differ
  from the crawl.

### 5.3 202 handling

- 202 is excluded from "OK" in both probes. Reason: some WAF captcha pages answer 202, and a manual
  rebuild without `CRAWL_PROXY` once dropped a real 404 (a client site, 2026-09).
- C2F's own re-check (`_recheck_full()`, used for the broken-link finding) now excludes 202 too
  (`if _now and 200 <= _now < 400 and _now != 202`). The two producers agree on this rule.

### 5.4 Consequences for hand editing

- A rebuild is not deterministic. The same JSON plus the same sidecar can give a different findings
  count an hour later if the site changed or throttled.
- On a WAF or captcha host you must export the same `CRAWL_PROXY` and `CRAWL_UA` the orchestrator
  used, or the probes see a challenge page. See `docs/KNOWN-PITFALLS.md` (rebuilds and the live
  re-probe).
- A large error list makes the rebuild slow: worst case about 2 x 25 s per URL per probe,
  sequential.

### 5.5 Other build side effects

- HTML mode: if the crawl has more than 60 pages or more than 2,000,000 bytes, BCR writes
  `<output basename>-raw.json` next to the output HTML and links it from the Raw tab instead of
  inlining it (inlining a big crawl froze the browser). Rebuilding to a new output name leaves a new
  raw JSON beside it.
- BCR overwrites any existing file at the output path.
- Markdown mode (`.md` output): same findings, walk-through text, "What we could not check", errors
  and the perf table, no Raw tab. Crawled text is escaped or put in backticks so it cannot become
  Markdown structure. `audit.sh` uses this mode for `report.md`.

---

## 6. Severity ordering

- Validation first: any finding whose severity, uppercased, is not one of the five ranks stops the
  build (exit 3). `"high"` and `"High"` pass. `"Critical "` with a trailing space, `"info"`,
  `"warning"` and `"severe"` all fail. (Before 2026-10-14 they ranked 9 and sorted below LOW;
  OBSERVED: a `"Critical "` finding showed a red chip at the very bottom of the list.)
- Rank table: `CRITICAL 0, HIGH 1, MEDIUM 2, LOW 3, NOTE 4` (`sevRank`); the key is
  `String(severity).toUpperCase()` (`sevKey`).
- Ties: sidecar findings sort before auto findings of the same severity. Within each group the
  original order is kept (stable sort in Node), so sidecar file order matters within a severity.
- `allFindings = [...manual, ...findings]` minus suppressed auto findings: the sidecar is not
  deduplicated against auto findings except by the two matchers in section 3 and `suppress_builder`.
- C2F's own severities (for context): the `SEV` map gives each audit-check section a default; an
  unmapped section defaults to `high`; a section with only warnings (no red flag) is softened to
  `medium`; a section whose only warnings say UNKNOWN, could not, blocked, HTTP Error, not measured
  and similar is skipped entirely ("unmeasured, not defective: never invent copy"); check 12 "NOT
  duplication" is softened; the broken-link finding is `high` when every code is 4xx, else
  `critical`; the CSR shell finding is `critical`. C2F has no `note` rank (it would sort as 9 inside
  C2F, but BCR re-sorts anyway).

---

## 7. Safe hand-edit procedure

Use this after C2F has run and before anything is published.

1. **Find the file BCR will read.** Note the input JSON you will build from (normally
   `$CRAWL_DIR/<slug>-full.json`). In the SAME folder, check for `<slug>-findings.json`. If it exists,
   that is the only file to edit. If it does not exist, BCR will read `<host>-findings.json`; find out
   where that file came from before you trust it, or re-run C2F for this slug.
2. **Back it up first.** Copy the file to the case folder under a fixed name, keeping any earlier
   backup, for example
   `cp -n "$CRAWL_DIR/<slug>-findings.json" "$CASES_DIR/<slug>/findings-auto-original.json"`.
   `-n` keeps an older backup if one is already there; that first copy is the true auto version. Do
   not name the copy `*-findings.json` inside a folder you might build from (failure mode 4). Do not
   run C2F again after this point; it overwrites the sidecar.
3. **Edit.**
   - To change body text: edit the five walk keys if any are present. `detail` is invisible when any
     walk key is set. When removing a claim, also search `urls[]` strings and `groups[].label`.
   - To retitle the broken-link finding: keep one of "doesn't exist", "does not exist", "isn't there",
     "aren't there", "broken link" in the title.
   - To retitle the CSR shell finding: keep the exact case-sensitive phrase "empty shells until
     JavaScript".
   - Do not put those broken-link phrases in any OTHER finding's title.
   - Severity: one of `critical`, `high`, `medium`, `low`, `note`, no spaces.
   - Every `groups[]` entry needs a `urls` array, even an empty one.
   - To remove a false BCR auto finding: add a title fragment to `suppress_builder` (object form
     only). Note the reason in the case folder.
   - To record a step the run could not measure: add `{"step": "<gate key>", "what": "...",
     "why": "..."}` to `not_measured`. `publish-run.sh` needs this for every SKIPPED key of an
     INCOMPLETE run.
   - The rules file (`report-rules.json`) lists text that must never be published. `publish-run.sh`
     will refuse a page that matches it, so check new wording against it now.
4. **Validate the JSON** before building: `python3 -m json.tool <file>` must print the JSON and exit
   0. BCR also refuses invalid JSON now (exit 3), but find the error before the rebuild.
5. **Count what you expect.**
   `python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(len(d['findings'] if isinstance(d,dict) else d))" <file>`
   gives the sidecar count S. It works for both shapes BCR accepts.
6. **Rebuild** with the orchestrator's transport environment exported (`CRAWL_PROXY`, `CRAWL_UA`) on
   a WAF host, and the same branding variables:
   `node build-crawl-report.js "$CRAWL_DIR/<slug>-full.json" "$CASES_DIR/<slug>/site-audit.html" "<Business Name>"`.
   This is the same output path the orchestrator writes. The third argument is the name exactly as it
   was passed to `run-full-audit.sh` (its third argument); a different string changes the report
   title. The output must end in `.html` (or `.md`) and the input in `.json`.
7. **Check the last line:** "Wrote <out> — P pages (E errors, B not checked, M steps not measured),
   F findings, T perf tiers." F must be at least S minus the auto findings you suppressed. If F
   equals the auto-only count, the sidecar was not read. Expect F = S + (auto findings) minus one
   if BCR's broken-link finding merged into yours, minus any suppressed ones. Check that T did not
   drop and that stderr shows the `suppressed ...` line you expect.
8. **Grep the HTML** for every new or changed title (expect at least 1 hit each) and for every
   withdrawn title or phrase (expect 0): `grep -c "<new title text>" <out.html>`. Titles are
   HTML-escaped, so a title containing `"` or `&` appears as `&quot;` / `&amp;`; grep a quote-free
   fragment.
9. **Check for doubles.** List the accordion titles: `grep -o '<span class=ft>[^<]*' <out.html>`.
   Look for the same defect twice (broken links, CSR shell plus "missing H1/meta/share" for the same
   pages, an off-site canonical CRITICAL from both producers).
10. **Look at it.** Open or screenshot the findings section once after every rebuild. Confirm the
    severity order, that removed text is gone from the walk-through steps, and that "What we could
    not check" says what you expect.
11. **Publish** only through `publish-run.sh <slug> report "$CASES_DIR/<slug>/site-audit.html"` after
    `findings-verified.json` exists (see `docs/01-PIPELINE-ANATOMY.md`, section 9).

---

## 8. Code review: defects and fragile spots

Ordered by how likely they are to ship a wrong report. Items marked FIXED are kept so the reason for
the current behaviour stays on record.

**R1. Invalid sidecar JSON was swallowed. FIXED 2026-10-13.** The `catch` now prints `FATAL ...` and
exits 3. Still useful to add: print "Using sidecar <path> (N findings)" on every run, so the file
choice is visible.

**R2. The host-named file as a decoy. MOSTLY FIXED.** C2F writes only `<slug>-findings.json` now. BCR
still falls back to `<host>-findings.json` when the run-slug file is missing, so an old host file can
still be read silently. Fix: drop the fallback, or warn when it is used.

**R3. Title regex matchers.** `sidecarBroken` and `shellFinding`. Scenario: a plain-language retitle
ships the defect twice or in fragments (several client sites, 2026-08 to 2026-09). Fix: C2F writes a
stable `id` key (`"id":"broken-links"`, `"id":"csr-shell"`) and BCR matches `f.id` first, with the
title regex only as a fallback that warns. Keep the id out of the rendered HTML.

**R4. The broken-link matcher takes the first loose match.** `manual.find` with phrases like "isn't
there". Scenario: a finding titled "Your contact form's thank-you page isn't there" absorbs every
broken target and the HIGH broken-link finding vanishes. Fix: the `id` key from R3; failing that,
require exactly one match and warn on more than one.

**R5. Shell suppression by criteria, not membership.** `isShell`. Scenario: a real 120-word thank-you
page with no H1 or meta is silently dropped from those findings because a shell finding exists. Fix:
suppress only URLs listed in the shell finding's `urls`, normalised for the trailing slash.

**R6. Dead regex branch.** `csrShell`'s "invisible until JavaScript" can never fire because
`shellFinding` already required "empty shells until JavaScript". Fix: one regex, or the `id` from R3.

**R7. Recovered rows vanish from the whole report.** `p200` is built before probe 1 rewrites
statuses. Scenario: 13 pages answered 508 during the crawl, recover live, and appear in neither
errors nor "All pages" nor any on-page check (their missing H1s are never reported). Fix: compute
`p200` after the recovery block, and mark recovered rows "status from live re-check" (their crawl
fields may be empty because the crawl got an error body).

**R8. Errors section and the broken-link finding can disagree. MOSTLY FIXED.** Probe 2 drops now go
to `reverifiedOK` and leave the errors section and the pill, and 0/401/403/406/429 rows moved to
"What we could not check". Left over: the `/cdn-cgi/l/email-protection` filter applies only to the
finding, so such a row can still appear under "Errors by status code". Fix: one `brokenSet` used by
the finding, the pill and the section.

**R9. Different "OK" rules in the two producers. FIXED.** C2F's re-check now excludes 202 like BCR.
Better still: one shared helper both import.

**R10. Probe 2 retries only on transport failure.** It loops while `live===null`; a 429 or 503 answer
ends the loop at once. Scenario: a throttled host answers 503 at build time and a healthy target
stays "broken". Fix: retry on transient codes as probe 1 does, with a short pause.

**R11. Rebuilds are not reproducible.** Both live probes. Scenario: someone asks for one word to
change; the rebuild drops or adds a finding because the site changed; nobody notices because the
count check expected the old number. Fix: cache probe results to `<runSlug>-reprobe.json` with
timestamps and reuse them unless `--reprobe` is given; print the cache age.

**R12. Severity key not validated. FIXED 2026-10-14.** Unknown values now stop the build. The check
uppercases but does not trim, so `"Critical "` is refused rather than accepted; that is the intended
behaviour.

**R13. A group without `urls` crashes the HTML build.** `g.urls.length` in the chip count and
`ulist(g.urls)`. Loud, but it blocks the build over a cosmetic omission. Fix: `(g.urls||[])` in both
places, as the Markdown path already does.

**R14. C2F regeneration destroys hand edits.** Scenario: a client audit, 2026-08. Fix: C2F refuses to
overwrite a sidecar that changed since its own last write (store a hash in a `_generated_sha` key)
unless `--force`; or write `<slug>-findings.generated.json` and let a hand-owned
`<slug>-findings.overrides.json` patch it by `id`.

**R15. Latent NameError in C2F. FIXED.** `_srcpages` is now assigned before its first use in the
broken-link block.

**R16. Silent truncation in C2F.** The generic section finding keeps at most 25 URLs (60 for check
14), the broken-link finding keeps the first 40 lines and 3 sources per target, the CSR shell finding
lists 40 plus "… and N more", and `_client_copy()` cuts detail to 1500 characters. Scenario: a title
says "58 pages" and the list shows 25; a hand editor "fixes" the title count to 25. Fix: write the
full list and let BCR's scroll box handle length; say "first N of M" when a cap is kept.

**R17. Unused code.** `sidecarText` in BCR. Harmless, but it suggests behaviour that does not exist.
Remove.

**R18. Grammar in auto copy. FIXED.** The share-image finding now agrees its verb ("1 page has").

**R19. Inconsistent H1 fields.** `isShell` reads `headings.h1`; missing-H1 reads `heading_counts.h1`.
Scenario: a crawl that fills one and not the other changes which rows are shells. Fix: one helper
`h1Count(p)` used in both.

**R20. The header pill reads `D.summary.pages` and `avg_word_count` directly.** A crawl JSON without
`summary` prints "undefined URLs crawled". Fix: fall back to `pages.length`.

**R21. `suppress_builder` is a loose substring.** A short fragment such as "page" would suppress most
auto findings. Fix: require a fragment of at least a few words, or match whole titles, and print the
suppressed titles to stdout (today they go to stderr only).

Tests for the builder and the sidecar live in `tests/test_builder.py` and
`tests/test_extract_and_findings.py`; add one with every fix, on synthetic fixtures only.

---

## 9. The five most dangerous traps

1. Scrubbing only `detail`: when any walk key exists, `detail` is hidden and the walk text still
   ships.
2. Retitling the broken-link or CSR shell finding without the matcher phrase ships the defect twice
   or in fragments; putting a matcher phrase in another title swallows the broken-link finding.
3. Re-running `checks-to-findings.py` after hand edits overwrites the sidecar.
4. Building when `<slug>-findings.json` is missing: BCR silently reads a `<host>-findings.json` that
   may belong to another run.
5. Rebuilding on a WAF host without the run's `CRAWL_PROXY` and `CRAWL_UA`: the live re-probes see a
   challenge page and the findings change.
