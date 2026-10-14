# 06. Engineering review: architecture, defects, fragile spots, and what to fix first

Audience: an engineer (human or bot) who must decide what to change first in this kit's orchestrator, checks, findings converter and report builder, and who wants to know which outputs can be trusted today.

Method. This review started from seven real audits run with the private tool this kit was forked from (2026-09-29 to 2026-10-13, written up in `docs/05-CASE-STUDIES.md`). Every defect below was then re-checked on 2026-10-14 against the code at the repo root. Defects that the kit (or the last weeks of hardening) already fixed are listed as fixed in section 3, not in the open register. No crawl and no network call was made for this document.

Line numbers drift: other changes land in the same files every week. This review cites code by **file plus function or marker string** (a `grep -n` target that survives edits). A `file:line` is given only where it was read on 2026-10-14; treat it as a hint, and grep the marker.

---

## 0. Citation keys

| Key | Meaning |
|---|---|
| `RFA` | `run-full-audit.sh` (the per-site orchestrator) |
| `AC` | `audit-checks.py` (the 28 standing checks; prints `checks.log`) |
| `C2F` | `checks-to-findings.py` (turns logs into the findings sidecar) |
| `BCR` | `build-crawl-report.js` (builds `site-audit.html` / `report.md`) |
| `FX` | `full-extract.py` (the BFS crawler that writes `<slug>-full.json`) |
| `01:§3`, `02:§1.3`, `03:Card 6` | section of `docs/01-PIPELINE-ANATOMY.md`, `docs/02-BUILDER-AND-SIDECAR.md`, `docs/03-CHECKS-AND-FALSE-POSITIVES.md` |
| `case N` | `docs/05-CASE-STUDIES.md`, case N (cases are numbered in date order, 1 to 7) |
| `PITFALLS (<topic>)` | `docs/KNOWN-PITFALLS.md`, the entry on that topic |
| `NEW` | found while re-checking the kit code; not recorded in docs 01 to 05 |

Paths use the kit's variables: `KIT_HOME` = repo root, `KIT_WORK` = `${KIT_WORK:-$KIT_HOME/work}`, `CRAWL_DIR` = `$KIT_WORK/crawl`, `CASES_DIR` = `$KIT_WORK/cases`, `REPORTS_DIR` = `$KIT_HOME/reports`. All are defined once in `kit-paths.sh`.

The seven cases, by number, as this review refers to them:

| Case | Site type | What made it hard |
|---|---|---|
| 1 | WordPress property-management site on a host with an IP-reputation captcha wall | First crawl was 1 blocked row; second ran through a proxy |
| 2 | Vite + React single-page app on a hosted app builder | Client-rendered (CSR) shell, 1 crawl row |
| 3 | WordPress pest-control site, 888 pages | Real hidden-link hack found by hand; tool's hack alarm was false |
| 4 | App-builder SPA for a tree service | CSR shell; pages rendered by hand |
| 5 | Astro SSR site, 547 pages | Archived URLs redirected to wrong pages; false LOCAL_LEAK critical |
| 6 | Wix site | 21 blocked rows, curl fallback; wrong counts from log parsing |
| 7 | Astro on nginx, 112 pages | Broken lead-magnet PDF; phone false positive from a video ID |

---

## 1. Architecture as built

### 1.1 Entry points

| Command | What it does | Gate |
|---|---|---|
| `./install.sh` | Python venv under `$KIT_HOME/.venv`, `npm install` (puppeteer-core, lighthouse), finds Chrome | `./install.sh --check` |
| `./audit.sh <url>` | One site, unattended. Calls `run-full-audit.sh`, then copies `site-audit.html`, `report.md`, `report.pdf`, `findings.json`, `linkmap.html` into `$REPORTS_DIR/<domain>/` | **None.** Banned-text rules are printed as warnings only. Says so in its header. |
| `run-full-audit.sh <url> <slug> "<name>" [svc] [deep] [max]` | The stage pipeline (below). Builds into `$CASES_DIR/<slug>/` only | The completion gate (`gate()`), printed at exit |
| `run-queue.sh add/work/status/stop` | Spool queue: atomic rename `pending/ -> running/`, per-slug `flock`, heartbeat files | Disk floor per job |
| `publish-run.sh <slug> <kind> <html>` | The only writer to the published folder for verified reports | Refuses (exit 3) without `findings-verified.json`, on an undeclared skipped step, on banned text (`owner-rules.json`), on a kind change |

Two publishing paths therefore exist: `audit.sh` (fast, unverified, for bots that want a result) and `publish-run.sh` (gated, for reports a person will act on). Section 5.1 discusses the risk.

### 1.2 Stage pipeline (`run-full-audit.sh`)

| Stage | Tool | Writes | Gate key | Gate test today |
|---|---|---|---|---|
| 0 preflight | inline | `.run-start` | (fatal) | slug charset, disk floor (`AUDIT_MIN_FREE_GB`, default 2), no generic `HTTP(S)_PROXY`, Chrome, Python deps, Node |
| 1 crawl | `full-extract.py` | `$CRAWL_DIR/<slug>-full.json`, `crawl.log` | `crawl` | page count > 0 |
| 1 quality guard | `crawl_quality()` | (curl fallback pass) `crawl-curl.log` | (fatal) | under 3 real pages after fallback: exit 4 unless `CSR=1` or "tiny site" |
| 2 checks | `audit-checks.py` | `checks.log` | `checks` | `grep "CANONICAL TARGET DOMAIN"` (check 1's header) |
| 2b CMS | `detect-cms.py` | `<slug>-cms.json`, `cms.log` | `cms` | `grep "PLATFORM:"` |
| 3 trace | `trace-errors.py` | `trace.log` | `trace` | exit 0 |
| 4 render | `render-check.py` | `render.log`, PNGs | `render` | `grep "CONTENT:"` |
| 5 perf | `perf-audit.sh` (Lighthouse) | `<host>-<tier>.json`, `perf.log` | `perf` | a score in a host-named JSON newer than `.run-start`, **or** `grep "Performance score" perf.log` |
| 5b discovery | `url-discovery.py` | `discovery.log`, `<slug>-discovery.json` | `discovery` | `grep "DISCOVERY"` |
| 6 social | `social-scan.py` | `social.log` | `social` | log phrases |
| 6d filters | `filter-url-scan.py` | `filterscan.log` | `filterscan` | **always set** |
| 6c pixels | `pixel-consent-check.py` | `pixel.log` | (none) | not gated |
| 6a hack | `hack-scan.py` | `hack.log` | `hackscan` | exit 0 |
| 6e CMS deep-dive | `cms/wordpress.py` or nothing | `cms-deepdive.log` | `cmsdeep` | `grep "WORDPRESS DEEP-DIVE"`, or **always set** when not WordPress |
| 6b converter | `checks-to-findings.py` | `<slug>-findings.json`, `<slug>-operator-findings.json`, `findings.log` | `findings` | `grep "finding(s)"` |
| 7 report | `build-crawl-report.js` | `$CASES_DIR/<slug>/site-audit.html` | `report` | exit 0 |
| 8 link map | `interlink-map.py` | `linkmap.html` (and published) | `linkmap` | exit 0 |
| 9 attribution | `content-attribution.py` | `attribution.log` | `attribution` | exit 0 |

Exit code: a fatal stage keeps its own code (1, 2 or 4); otherwise the number of skipped gate keys. The trap that runs the gate preserves a fatal code (this was fixed; see section 3).

### 1.3 Data contracts between stages

The pipeline has four hand-offs, and their strength decides most of the defect classes below.

1. **Crawl JSON** (`<slug>-full.json`). Structured. One row per URL: `url`, `final_url`, `status`, `redirected`, `redirect_hops`, `word_count`, `title`, `headings`, `canonical`, `internal_links[]` and `external_links[]` as `{href, anchor, rel}`, robots fields, `partial` on a timed-out crawl. This is the strongest contract in the kit.
2. **Stage logs** (`checks.log`, `trace.log`, `hack.log`, `render.log`, `discovery.log`). Human prose with emoji flags. **C2F parses these with regex** to build findings and counts. This is the weakest contract: any change to a `print()` in AC changes a client-facing number.
3. **Findings sidecar** (`<slug>-findings.json`). JSON, but with no schema and no stable id per finding. BCR finds specific findings in it **by regex on titles**.
4. **Report HTML / Markdown**. BCR merges the sidecar with its own "auto findings" computed from the crawl, and **re-probes the live site** at build time.

### 1.4 Where state lives

| State | Location | Shared between runs? |
|---|---|---|
| Crawl, Lighthouse JSON, sidecars | `$CRAWL_DIR/` (flat, keyed by slug or host) | Yes. Lighthouse files are keyed by host, so two slugs of one site share them. |
| Stage logs, evidence, built HTML | `$CASES_DIR/<slug>/` (flat) | Yes, for re-runs of one slug: each run overwrites, except `perf.log` under `SKIP_PERF=1` (append) |
| Published reports | `$REPORTS_DIR/<domain>/` | Overwritten by `audit.sh` (which removes its own five outputs first) or by `publish-run.sh` (which backs up to `$KIT_WORK/publish-backups/` first) |
| Render screenshots | the current directory (`KIT_HOME`) | Yes |
| Queue | `queue/pending`, `running`, `done`, `failed`, `queue/locks`, `queue/heartbeat` | By design |

There is no per-run directory. Freshness is decided by file mtime against `.run-start`, or not at all. This is root cause 4 below.

---

## 2. Five root causes

Most of the 72 tool findings in the seven audits were rewritten, merged or dropped before they shipped (docs/05-CASE-STUDIES.md, cross-audit section A). Nearly every defect in section 4 comes from one of five causes.

1. **Findings are identified by free text, not by data.** BCR finds the sidecar's broken-link and CSR-shell findings by regex on titles. C2F gets its numbers by counting printed lines in logs. Evidence: 6 of 7 audits had a doubled, fragmented or silently ignored finding (03:Card 17). Four wrong numbers came from log parsing: a noindex count of 6 that was really 16 and a www-link count of 2 that was really 3 (case 6), and a hack URL list that was really the orphan list (cases 2 and 3) (03:Cards 6, 8, 9).
2. **Checks read raw HTML and redirect-followed rows with no render or redirect context.** Evidence: 8 false findings in 2 audits from client-rendered shells (cases 2 and 4; 03:Card 1; this part is now fixed). Redirect-blind results in 4 of 7 audits: "92 pages carry a spam link" (most were redirect rows that inherited the destination's links), "68 old addresses" (case 3), soft-404 wording (cases 3 and 4), archived URLs that redirect to the wrong page (case 5) (03:Cards 7, 10, 11).
3. **A loose pattern stands in for identity.** Evidence: 7 false findings in 4 audits. They came from a host substring (case 5, now fixed), 10 digits inside a URL (case 7), `inurl:` search hits on other domains (cases 2 and 3, both CRITICAL), a schema-type allowlist (case 3), equal word counts read as identical pages (case 3), and a silent title-token filter (case 7) (03:Cards 2 to 6, 12).
4. **Runs share mutable state, and gates check that a stage ran, not what it produced.** Evidence: the completion gate was green on a 1-row crawl in 3 audits (cases 1, 2, 4; now a fatal exit). A decoy second sidecar file swallowed every hand edit (case 3; now one file). The perf gate still has a log-grep fallback that an older run's log can satisfy.
5. **"Unknown" is stored as "zero", "complete" or "skipped".** Evidence: an invalid sidecar used to give exit 0 and drop every finding (now exit 3). Lighthouse `score:null` used to render as 0 (now "Not measured"). Still open: a missing search-API key silently skips the index checks (section 4.4, P-NEW-2); several gate keys are set unconditionally (O7).

Causes 1 and 4 are structural. Patches inside the current shape (more title regexes, more mtime checks) have been tried repeatedly; section 7 replaces the shape.

---

## 3. What is already fixed (re-checked 2026-10-14)

Do not re-open these. Each row names the marker to grep if you doubt it.

| ID | Defect (first review) | Fixed how | Where to look |
|---|---|---|---|
| O1 (part) | 1-row blocked or CSR crawl ran every stage and the gate was green | After the curl fallback, under 3 real pages is `exit 4` unless `CSR=1`, or the "tiny site" rule (no blocked status, no challenge title, at most 5 pages) | RFA, `STILL THIN after fallback`, `_TINY` |
| O3 (part) | Perf gate globbed every site's Lighthouse files | Gate reads only this host's files and only those newer than `.run-start` | RFA, `_PSCORE` heredoc |
| O16 (part) | A FATAL exited 15 or 16 (the skip count) | Trap keeps a non-zero fatal code: `trap '_rc=$?; gate; [ "$_rc" -ne 0 ] && exit "$_rc"; ...'` | RFA, `trap ` |
| O-paths | Hard-coded home-directory, web-root and skill paths; one fixed Chrome path | `kit-paths.sh` defines `KIT_HOME`, `KIT_WORK`, `CRAWL_DIR`, `CASES_DIR`, `REPORTS_DIR`, `PY`, `NODE`, `CHROME_BIN`; RFA sources it | `kit-paths.sh`; RFA `. "$(cd ...)/kit-paths.sh"` |
| O-stdin | A child that read stdin could hang an unattended run | `exec < "$EMPTY_STDIN"` at RFA start; `audit.sh` passes the same file | RFA, `EMPTY_STDIN` |
| O-publish | The orchestrator published unverified reports | RFA builds into the case folder only; `publish-run.sh` is the only gated publisher | RFA stage 7 comment; `publish-run.sh` header |
| C1 | Under CSR, checks 3, 4, 23, 25 counted the raw shell ("too small", "no photos", "no form", "no schema") | C2F skips them under `_csr_blind` and records them in `NOT_MEASURED_CSR`, written to the operator file and shown by `audit.sh` as not measured | C2F, `if num in ("3", "4", "23", "25") and _csr_blind` |
| C6 | `[^/]*(\.local)` matched inside real hosts and raised a false CRITICAL | `_local_leak(href, site_host)` parses the host: loopback, `.local/.test/.localhost/.invalid`, or a staging/dev label under the audited domain | AC, `def _local_leak` |
| C16 | The pre-consent tracker finding went to the client sidecar | C2F moves titles matching `consent\|tracker` to `<slug>-operator-findings.json`; `publish-run.sh` also refuses the text (`owner-rules.json`, `page_forbidden`) | C2F, `_OPERATOR = re.compile`; but see F3 in section 5 |
| C19 | C2F counted 202 (captcha wall) as OK on re-check | `_recheck_full` returns a challenge flag; `200 <= _now < 400 and _now != 202` | C2F, `def _recheck_full` |
| C20 | `_srcpages` used before assignment on an empty crawl | Assignment now precedes use inside `if _broken:` | C2F, `_srcpages = {` |
| C-DE05 | Bot-check 403s were reported as broken links | 401/403/406/429, no answer, and challenge pages go to "What we could not check" in C2F and BCR | C2F `_REFUSED`; BCR `BLOCKED_ST` |
| B1 | Invalid sidecar JSON was swallowed by an empty `catch`, exit 0 | `FATAL: ... is not valid JSON`, exit 3 | BCR, sidecar `catch(e)` block |
| B2 (part) | C2F wrote two sidecar files; edits to the host-named one were ignored | C2F writes one file named by the run slug | C2F, comment `ONE sidecar, named by the RUN slug` |
| B6 (part) | Error pill and error table kept 406, status 0 | Shared `BLOCKED_ST` / `isBlocked()`; rows leave the error list | BCR, `BLOCKED_ST` |
| B9 | `"Critical "` (trailing space) sorted below LOW | Unknown severity (after upper-casing) is `exit 3` | BCR, `unknown severity in` |
| B-perf-null | Lighthouse `score:null` rendered as 0 | `lhscore()` returns null; cell says "Not measured" with the runtime error code | BCR, `const lhscore` |
| C-JP05 | Archived assets (images, fonts, CSS) reported as dead pages | C2F filters asset extensions before the archived-URL finding | C2F, `ARCHIVED ASSETS ARE NOT DEAD PAGES` |

The defects about business-listing and rank tools in the private tool's review are not in this kit (those tools do not ship), so their IDs are absent.

---

## 4. Open defect register

Severity: **wrong-fact** = can ship a false statement to the reader of the report. **silent-skip** = a measurement is missing and nothing says so. **cosmetic** = wrong look, wasted cost, or misleading operator text, with no false client-facing fact.

Effort: S = under 2 hours, M = half a day to 2 days, L = more than 2 days.

"Control" = an input that gives the wrong result on today's code, so a test written from it fails before the fix. All controls are synthetic unless stated; real client data never goes into `tests/`.

### 4.1 Orchestrator and gate (`run-full-audit.sh`)

| ID | Where | Failure scenario | Observed | Sev | Eff | Fix | Control that must fail before the fix |
|---|---|---|---|---|---|---|---|
| O1b | RFA `crawl_quality()`: `blocked=[p for p in pg if p.get('status') in (429,403,503)]` | 202 (a common captcha-wall status) is not in the blocked set. A crawl of 202 pages with challenge bodies counts 0 blocked; only the "under 3 real pages" rule catches it, and only when the challenge bodies are under 50 words. | case 1 | wrong-fact | S | Add 202 to the set. Also count rows whose title matches the challenge regex the function already computes (`chal`) as blocked. | Synthetic crawl: 40 rows, status 202, 120-word challenge bodies. Today: no fallback, stages run. |
| O2 | RFA, `PARTIAL CRAWL` is only inside the fallback branch | A first-pass crawl that hits `T_CRAWL` writes `partial:true` and nobody says so. | not observed | silent-skip | S | Check `partial` after pass 1 too, and add it to the gate as `crawl_quality: partial`. `audit.sh` already lists `partial` as not measured; RFA does not. | Synthetic crawl JSON with `partial:true` and 10 real rows: no `PARTIAL` line in the log. |
| O3b | RFA perf gate: `[ "$_PSCORE" = "1" ] \|\| grep -q "Performance score" "$OUT/perf.log"` | The log-grep fallback ignores the mtime rule. See O5. | not observed | silent-skip | S | Drop the fallback. Gate only on `_PSCORE`. | Same as O5. |
| O4 | RFA snapshot loop (`_keep="$CRAWL_DIR/$SLUG-$(basename "$_old")"`) and BCR perf lookup (four file-name spellings) | Lighthouse files are keyed by host. A run of one site under a new slug copies the previous run's perf JSON under the new slug, and BCR tries four spellings in order. A run whose Lighthouse failed can show an earlier run's scores. | not observed in the seven cases | wrong-fact | S | Per-run perf dir (section 7.3). Until then: BCR reads only files newer than the crawl JSON, and prints which file it used. | Run twice with `SKIP_PERF=1` on the second under a new slug: the second report shows scores. |
| O5 | RFA `SKIP_PERF=1` branch: `... \| tee -a "$OUT/perf.log"` | Append keeps an older run's "Performance score" line, so the gate passes on a run that measured nothing. | not observed | silent-skip | S | `tee` without `-a`, plus O3b. | Slug with an old `perf.log` containing a score line; run with `SKIP_PERF=1` and no fresh JSON. Today `DONE[perf]` is set. |
| O6 | RFA: `grep -q "CANONICAL TARGET DOMAIN" "$OUT/checks.log" && DONE[checks]=1` | The gate marker is check 1's header. A crash in checks 2 to 28 still passes. AC already prints a closing line (`=== CRITICAL flags present ... ===` or `=== no critical canonical flags ===`) at the end of `main()`. | not observed | silent-skip | S | Gate on the closing `===` line, or add `AUDIT-CHECKS COMPLETE` and gate on that. | Make check 5 raise on a synthetic crawl: today the gate is green. |
| O7 | RFA stage 6e and 6d: `DONE[cmsdeep]=1` for every non-WordPress platform; `DONE[filterscan]=1` unconditionally; WordPress gate greps `WORDPRESS DEEP-DIVE`, which `cms/wordpress.py` prints as its **first** line (`=== WORDPRESS DEEP-DIVE ===`) | A crash right after the header passes. Filter scan is never gated. "Not applicable" and "done" look the same. | not observed | silent-skip | S | Gate WordPress on `WORDPRESS DEEP-DIVE COMPLETE` (printed at the end of `cms/wordpress.py`). Gate filter scan on `FILTER-VERDICT`. Write stage status files (section 7.4) with `status: not-applicable` in place of a fake pass. | Kill `cms/wordpress.py` after its header (synthetic): gate green today. |
| O8 | RFA challenge-marker scan: `grep -lE "$_MARKERS" "$OUT"/*.log` | Reads every log in the case folder, including logs a previous run of the same slug left behind. | not observed | cosmetic | S | Per-run dir, or limit to files newer than `.run-start`. | Two runs of one slug, the first one walled. |
| O9 | RFA order and BCR inputs | `social.log`, `<slug>-discovery.json`, `<slug>-cms.json`, the link map and the attribution log reach no report. Stages 8 and 9 run after the report is built. | all 7 (by design) | silent-skip | M | Run 8 and 9 before 7. C2F reads social and discovery output into findings with ids (section 7.1). | `grep -c social build-crawl-report.js` is 0. |
| O12 | `content-attribution.py`: `subprocess.run(['node', tf.name, url], ..., timeout=120)` is not inside a `try` | `TimeoutExpired` on one slow page ends the stage; `DONE[attribution]` stays unset with no reason. | not observed | silent-skip | S | Catch it, print `UNKNOWN <url> (render timeout)`, continue. | A URL that hangs (local test server). |
| O13 | `render-check.py` (`UA = "Mozilla/5.0 (X11; ...` fixed string); `perf-audit.sh` and `pixel-consent-check.py` read neither `CRAWL_PROXY` nor `CRAWL_UA` | Stages see the site through different transports. A walled host shows the crawl the real page, Lighthouse a challenge page, the render check something else again. | case 3 (render "denied"), case 1 (render captcha) | wrong-fact | M | One `transport.json` per run (UA, proxy, delay, whether Chrome can use the proxy) that every stage reads and prints in its first log line. `render-check.py` already declares a mismatch when the proxy has credentials; extend that to UA. | A local test server that answers 403 to the render-check UA and 200 to `CRAWL_UA`: today render reports CSR/blocked while the crawl is fine. |
| O14 | `render-check.py`: `nojs, js = os.path.abspath(f"render-{slug}-nojs.png") ...` | Screenshots land in the current directory (RFA `cd`s to `KIT_HOME`), so the repo root fills with PNGs and runs overwrite each other's. | n/a | cosmetic | S | Write to `$CASES_DIR/<slug>/render/` (pass `--outdir`). Do not delete existing PNGs automatically. | n/a |
| O15 | `filter-url-scan.py` and `hack-scan.py` | Search-API calls are spent on every audit, even when the filter scan found no filters. | all | cosmetic | S | Run the index check only when filters were found. | n/a |
| O16b | RFA gate exit | Gate misses still exit with the miss count (1 to 15), which overlaps the fatal codes 1, 2 and 4. A caller cannot tell "2 stages skipped" from "preflight failed". | all | cosmetic | S | Gate misses exit `10 + n`. Document the table in the header. | n/a |
| O17 | Docs vs code | Docs drift from code (number of checks, stage order). | n/a | cosmetic | S | `verify-skill-docs.sh` already exists; extend it to assert the stage table in `docs/01-PIPELINE-ANATOMY.md` against the `step` lines in RFA. | n/a |

### 4.2 Checks (`audit-checks.py`) and converter (`checks-to-findings.py`)

| ID | Where | Failure scenario | Observed | Sev | Eff | Fix | Control that must fail before the fix |
|---|---|---|---|---|---|---|---|
| C2 | C2F, render-derived findings ("titles rewritten by JavaScript", "blank until JavaScript") | They ship as separate findings next to the CSR-shell finding: one defect, three accordions. | cases 2, 4 | wrong-fact (duplicate) | S | When the CSR-shell finding exists, fold them into it as `evidence`. | Synthetic `render.log` with `CONTENT: CSR` and `TITLE DIFFERS`: today 2+ findings. |
| C3 | AC check 21: `_sig21.setdefault((_wc21, _h121), [])` | "N pages serve IDENTICAL content" when 3+ pages share a word count and H1 tuple. No text is compared. A template with fixed-length location pages trips it; pages whose paths differ only in letter case (a real duplicate) are not tested. | case 3 (pages were 53% similar) | wrong-fact | M | `crawler/simhash.py` exists in the kit. Store a simhash of the main text per row in FX; require Hamming distance of 3 or less. Add a case-folded path rule. | Synthetic crawl: 3 pages, 1292 words each, same H1, different text: today CRITICAL. |
| C4 | AC check 8, the "copied title" branch (`if not (2 <= _c8 < 5)`) | **Partly fixed:** copied titles in groups of 2 to 4 are now reported. Still silent: a group whose members share one language root, or whose title has fewer than 2 distinctive tokens, is skipped with no output. Check 22 can still call a copied title "slug describes a different topic". | case 7 | wrong-fact | S | Print the plain "these pages share the identical title" line for every skipped group. Check 22 names the page that owns the title. | Synthetic: 2 pages, same 2-word title. Today: no output. |
| C5 | AC check 9: `SHOWN = _r2.compile(r'\(?\b([2-9]\d{2})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b')`, applied to tag-stripped text | Separators are optional, and URLs written as text are not removed, so any 10 digits in visible text count as a displayed phone number, including a video ID in a pasted link. | case 7 (video ID), case 1 (shipped as "confirm") | wrong-fact | S | Remove `https?://\S+` and bare `host.tld/path` runs first. Require a separator unless a `tel:` link has the same digits. Print 80 characters of context per number. Move the logic into an importable function so tests can call it. | String `Insert this video here https://vimeo.com/2345678901`: today yields a "phone". |
| C7 | `hack-scan.py`: `if hits:` after `serp(probe)`; C2F: `urls = re.findall(r"https?://\S+", hack)` then `urls[:20]` | An `inurl:.php` probe raises the alarm on any organic hit; when the search API drops `site:` it returns the whole web. The CRITICAL's URL list is the first 20 URLs anywhere in `hack.log`, which are the orphan list printed earlier. | cases 2, 3 (both false CRITICAL) | wrong-fact | S | Keep only hits whose host is the apex or a subdomain of it. C2F takes URLs only from the indented lines under a siren line. Skip estimates when the crawl has fewer than 3 indexable rows. | Synthetic `hack.log`: an orphan list, then a siren line with 4 off-domain hits. Today: 1 CRITICAL listing the orphans. |
| C8 | (no check) | Hidden off-screen link blocks (anchors inside `left:-9999px` or similar) are not detected. The real hack in case 3 (60 casino links) was found by a person. | case 3 (missed) | silent-skip | M | New static check: anchors inside elements positioned off-screen or `display:none` containers, plus a spam-vocabulary count. | Synthetic page with 60 off-screen anchors: today 0. |
| C9 | `url-discovery.py`: Wayback sample `_samp = _rnd.sample(wb_urls, ...)` probed with `status(_u, 10)` (default `follow=True`); fetch from `web.archive.org/cdx/search/cdx` | The liveness sample includes assets (C2F now filters them at report time, but the percentages in the log still count them), and the probe follows redirects, so a redirect to the wrong page counts as "live". **NEW:** the CDX endpoint has been unreliable; the timemap JSON endpoint is the documented alternative (confirm before relying on either). | case 5 (8 of 10 "dead" were assets; the wrong-target redirects were missed) | wrong-fact + silent-skip | M | Drop assets before sampling. Probe with `follow=False` and use 4 buckets: 2xx; 3xx to the same path; 3xx to home or another section; 404/410. If the archive fetch fails, print `UNKNOWN`, never "0 URLs ever archived". | Stubbed archive list with 8 image URLs and 2 pages that 301 to `/`. |
| C10 | AC check 17 prints `_noidx[:6]`; C2F counts printed URL lines: `_nn = len(re.findall(r"^\s+https?://\S+", body, re.M))` | The title count is capped at 6. The real count is in the header line AC prints (`N crawled page(s) carry noindex`). | case 6 (title said 6, truth 16) | wrong-fact | S | `_nn = int(re.search(r"(\d+) crawled page\(s\) carry noindex", body)[1])`. Long term: structured check output (section 7.2). | Synthetic crawl with 16 noindexed pages: title starts with "6" today. |
| C11 | AC check 6 prints `list(sorted(mism.items()))[:8]` targets and `list(srcs)[:2]` sources; C2F pairs with `re.findall(r"(https?://\S+?)\s+referenced from:\s+(https?://\S+)", body)` | The title counts at most 8 targets and one source per target. "3 links use the www version" can ship as "2". | case 6 (2 vs 3) | wrong-fact | S | AC prints one `PAIR <target> <source> <anchor>` line per pair, no cap. C2F counts `PAIR` lines. | Synthetic crawl: 1 wrong-host target linked from 3 pages. |
| C12 | AC check 6: `h2.lstrip('w.') == site_host.lstrip('w.')` (three places); raw scan regex `(?:src\|href)=["'](https?://...)` | `lstrip` strips a **character set**, not a prefix: `wexample.com` and `example.com` compare equal, so another domain is reported as a wrong-host variant of the site. The raw scan also matches `<link rel=canonical href=...>`, so canonical tags count as links. `site_host` is re-assigned inside check 6 (it is first set near the top of `main()`), which later checks then read. | an earlier client site, 2026-08 (6 phantom links) | wrong-fact | S | `re.sub(r'^www\.', '', h)`. Scan only `<a href>`, `<img src>`, `<script src>`. Use a new variable name in check 6. | Synthetic crawl for `example.com` with an external link to `https://wexample.com/`: today flagged. |
| C13 | AC check 15 (`_redir_targets`, counted per href) and FX (redirect rows store the destination's links) | Check 15 treats a 302 to the home page and a 301 to a new address the same ("old address"). Redirect rows inherit the destination's links, which inflates any "N pages carry X" count. | case 3 ("68 old addresses", "92 carriers"), case 5 (policy link on 509 pages shown as a tidy-up) | wrong-fact | M | Group by `final_url`. A separate "forwards to the home page" block. Spam slugs go to the hack section. A `carriers(href)` helper that drops `redirected` rows and collapses `?query` views of one path. | Synthetic crawl: 1 page carries a spam link, 40 redirect rows point to it: carrier count must be 1. |
| C14 | AC check 28: `_ur2.urlopen(_rq, timeout=20)` on made-up paths | urllib follows redirects, so a 301 to home reads as "200, soft 404". The "page does not admit it is an error" clause reads the raw body, which on a CSR site is an empty shell. | cases 3, 4 | wrong-fact | S | Use the no-redirect opener `_NR` that AC already defines; report `301 -> <Location>` as "missing pages forward to ...". Skip the clause under CSR. | Local test server: unknown path returns 301 to `/`. Today: "answers with HTTP 200". |
| C15 | AC `_LOCAL_SUBTYPES` (allowlist of schema.org LocalBusiness subtypes) | A local entity is recognised only by type name. A valid node with a PostalAddress but a type the list lacks, or a custom type, gets "generic company label". Schema address and phone are never compared with the footer. | case 3 (false "generic label"; the real address mismatch was missed) | wrong-fact | S | A node with a PostalAddress, or telephone plus geo, counts as local. Compare schema address/phone with the visible footer text and report a mismatch. | Synthetic page: `{"@type":"PestControl","address":{...}}`: today flagged. |
| C17 | `render-check.py` (`dom["words"] < 25`) vs `content-attribution.py` (`total <= 3`) | Two different "render was blocked" thresholds. A Chrome error page with 10 words passes attribution as content. | case 3 | wrong-fact (operator) | S | One threshold in one shared module. Also treat `main-frame-error` and "was denied" bodies as blocked. | Synthetic rendered body of 10 words "Access denied ...": attribution reports content. |
| C18 | `detect-cms.py` fingerprints | `unknown` for app-builder SPAs and for Astro (no `/_astro/` rule), so no platform-specific checks run and the report cannot name the platform. | cases 2, 4, 5, 7 | silent-skip | S | Add fingerprints: `/_astro/` assets, common app-builder script hosts and meta generators. | Synthetic HTML with `<script src="/_astro/x.js">`: today `unknown`. |
| C21 | C2F list caps (`[:25]`, `[:40]`, `srcs[:8]`, `_urls[:40]`) and `_client_copy()` truncating detail to 1500 characters | Lists are cut with no "first N of M" label; a reader (or a verifier) "fixes" a title to match the visible list. | not observed in the seven cases | wrong-fact | S | Write full lists to the sidecar; let the builder decide what to show and label it. Never truncate detail mid-sentence. | Synthetic finding with 68 targets: sidecar holds 25, title says 68. |
| C22 | C2F final `json.dump(... open(out, "w"))` | Re-running C2F overwrites the sidecar and destroys hand edits made during verification. | an earlier client site, 2026-08 | wrong-fact | M | Split generated and override files (section 7.2). | Edit a sidecar, re-run C2F, diff. |
| C23 | BCR auto findings (off-domain canonical, missing H1, missing meta, OG) vs C2F checks 1, 2, 21 | One defect can ship twice (one from the sidecar, one from the builder) because nothing ties them together. `suppress_builder` (title substrings in the sidecar) is a manual workaround. | not observed in the seven cases | wrong-fact (duplicate) | M | Ids on both sides; BCR drops an auto finding whose id a sidecar finding `supersedes` (section 7.1). | Synthetic crawl with one off-domain canonical: today two CRITICALs. |
| C24 | C2F subdomain finding | "Additional live website on your domain" for a link tracker, a funnel page or an internal tool. `verify-audit.py` now gathers first-screen title and login hints per subdomain, but C2F does not use them. | cases 1, 2, 5 | wrong-fact (wording) | M | Classify each subdomain by fetch (title, size, login form, redirect) before choosing the wording. | Synthetic subdomain list with a 3-byte "OK" endpoint: today "live website". |
| C25 | C2F `SEV = {` section-number to severity map | Severity does not scale with share: 2 of 428 sitemap URLs became CRITICAL. | cases 3, 5 | wrong-fact (severity) | S | Downgrade when the affected share is under 1%, unless the id is on an "always critical" list (hack, off-domain canonical, sitewide noindex). | Synthetic sitemap of 428 URLs with 2 bad. |

### 4.3 Report builder (`build-crawl-report.js`)

| ID | Where | Failure scenario | Observed | Sev | Eff | Fix | Control that must fail before the fix |
|---|---|---|---|---|---|---|---|
| B2b | BCR: `const slug = fs.existsSync(path.join(dir, \`${runSlug}-findings.json\`)) ? runSlug : D.site;` | **Partly fixed** (C2F writes one file). If the run-slug sidecar is missing, BCR silently falls back to a host-named file, which may be a stale one from an older run. | case 3 (before the fix) | wrong-fact | S | Read only the run-slug file. If a host-named file exists and is newer, exit 6 with "host-named sidecar is newer; it is not read". Always print `Using sidecar <path> (N findings)`. | Synthetic: no run-slug file, an old host-named file with 1 finding. Today: that finding ships. |
| B3 | BCR: `sidecarBroken = manual.find(f=>/do(es)? not exist\|doesn'?t exist\|aren'?t there\|isn'?t there\|broken (internal )?link/i.test(f.title\|\|''))`; `shellFinding = manual.find(f=>/empty shells until JavaScript/.test(f.title\|\|''))` | The broken-link merge and the CSR suppression match title text. A verifier who retitles the finding in plain words ships the defect twice. Any other title containing "isn't there" captures the merge. | cases 1, 2, 4, 7 | wrong-fact | M | Match on `id` (section 7.1). Exactly one match, else exit 4. Keep the title regex only as a warned fallback during migration. | Sidecar with the broken-link finding retitled "Your guide download is broken": two broken-link accordions today. |
| B4 | BCR `isShell`: `(csrShell && (p.word_count\|\|0) < 200)` | When a CSR-shell finding exists, **any** page under 200 words leaves the H1, meta and OG findings, including real short pages such as a thank-you page. | the builder test in 02:§3.2 | silent-skip | S | Suppress only URLs listed in the shell finding's `urls`. | Synthetic: shell finding listing 0 URLs, an 87-word `/thank-you` page without OG: today dropped. |
| B5 | BCR: `const p200 = pages.filter(...)` is computed **before** the transient re-probe block that rewrites `p.status` | Rows recovered by the re-probe (a 508 that is 200 live) vanish from every on-page finding and from "All pages". | an earlier client site on shared hosting, 2026-08 | silent-skip | S | Compute `p200` after the recovery block. Label recovered rows. | Synthetic crawl with 5 rows at 508 and a stub server that returns 200. |
| B7 | BCR probe 2 (broken-link targets): loop condition `attempt<2 && live===null` | Probe 2 retries only on transport failure. A 503 answer keeps a healthy target as "broken". Probe 1 (transient statuses) does retry on transient codes. | not observed | wrong-fact | S | Retry on the same `_TRANSIENT_ST` set as probe 1, with a pause. | Stub that answers 503 then 200. |
| B8 | BCR re-probes (both blocks call `curl` via `execFileSync`) | Rebuilds hit the live site, so a rebuild is not reproducible, and without the crawl's `CRAWL_PROXY` / `CRAWL_UA` in the environment the probe sees a challenge page. | case 1 (needed a proxied rebuild) | wrong-fact | M | Cache probe results in the run dir (`reprobe.json`); reuse unless `--reprobe`; print the cache age. Add `AUDIT_OFFLINE=1` to skip probes entirely (section 6, fix 0). | Rebuild twice with the network off: output must match. |
| B10 | BCR finding render: `groups.reduce((a,g)=>a+g.urls.length,0)` | A group with no `urls` throws and the build fails. | the builder test in 02 | cosmetic | S | `(g.urls\|\|[])` everywhere; make `urls` required in the schema. | Sidecar with `{"groups":[{"label":"x"}]}`. |
| B11 | BCR (various) | Plural agreement in a few auto titles; two H1 field names (`heading_counts.h1` and `headings.h1`); dead branches. | builder tests | cosmetic | S | One `h1Count()` helper; one `nPg()` for every verb; remove dead code. | n/a |

### 4.4 Fresh-machine and portability defects (NEW)

The kit targets a fresh Ubuntu machine run by a bot. These defects only show there. Several may already be closed by the ongoing portability port; check the marker before working on one.

| ID | Where | Failure scenario | Sev | Eff | Fix |
|---|---|---|---|---|---|
| P-NEW-1 | `hack-scan.py`, key loading: the `try:` opens a fixed secrets file first, and `key=os.environ.get('SERPAPI_KEY') ...` sits **inside** the same `try` after the `open` | On a machine without that file, `open` raises, the `except` skips the env lookup, and `SERPAPI_KEY` from the environment is ignored. `filter-url-scan.py` reads only the file. | silent-skip | S | Read the env var first; read an optional file only if `SEARCH_API_KEY_FILE` (or similar) is set. Never a fixed path. |
| P-NEW-2 | `hack-scan.py`: `if key:` has no `else` | With no key, the index-count comparison and the spam-path probes are skipped with no output line. The gate still marks `hackscan` done. `filter-url-scan.py` does print `INDEX-CHECK: skipped (no SerpAPI key)`; hack-scan should match it. | silent-skip | S | Print `INDEX-CHECK: UNKNOWN (no search API key)` and add it to the sidecar's `not_measured`. |
| P-NEW-3 | `content-attribution.py`: Node inline script `require(...)` of puppeteer from an absolute path | On a fresh machine the stage fails at require time; `DONE[attribution]` stays unset. | silent-skip | S | `require('puppeteer-core')` resolved from `$KIT_HOME/node_modules` (set `NODE_PATH` or run the script from `KIT_HOME`), with `executablePath: process.env.CHROME_BIN`. |
| P-NEW-4 | `verify-audit.py`, `publish-run.sh`, `run-lane.sh`, `run-full-audit-bigcrawl.sh`, `verify-skill-docs.sh` | Some still default to private folders (case root, publish root, backup root) in their headers or defaults at the time of writing. | cosmetic (fails loudly) | S | Source `kit-paths.sh` (shell) or `kit_env.py` (Python); default to `$CASES_DIR`, `$REPORTS_DIR/<domain>/`, `$KIT_WORK/publish-backups/`. |
| P-NEW-5 | AC check 6 raw sample: `headers={'User-Agent':'Mozilla/5.0'}` | Bypasses `CRAWL_UA`; a UA-sensitive wall gives the check a challenge page, so it finds no references and says "ok". | silent-skip | S | Use `crawl_transport.fetch` everywhere. |
| P-NEW-6 | RFA disk floor: `df -P "$KIT_WORK"` | Correct for the kit; `run-queue.sh` still documents the floor against `/`. A machine where `KIT_WORK` is on another volume gates on the wrong disk. | cosmetic | S | Same `df` target in both. |
| P-NEW-7 | `audit.sh` timeout: `timeout --kill-after=30 "$AUDIT_TIMEOUT" bash run-full-audit.sh` | On a kill, children started by RFA (Chrome, Lighthouse) can outlive the run. | cosmetic | M | Run RFA in its own process group (`setsid`) and kill the group on timeout; then verify no Chrome from this run remains, by PID. |

### 4.5 Process and deliverable

| ID | Where | Failure scenario | Observed | Sev | Eff | Fix |
|---|---|---|---|---|---|---|
| P2 | Plain-language report (section 9) | The plain-language version of a report was typed by hand from the verified findings, so link words, source pages and counts were copied by a person. | case 6 | wrong-fact (transcription) | M | Generate it from the findings file plus the crawl's link records (section 9). |
| P3 | Verification procedure | "Dead" or "works" claims were made with no control: one DNS resolver plus a bare port probe; a webhook check that a fake id also passes. | case 4 | wrong-fact | S | Section 8, steps 7 and 13: each claim needs a known-good and a known-bad control. |
| P4 | Operator prints | A URL cut at 100 characters in an operator print was misread as a "truncated link" on the site. | case 6 | wrong-fact (operator) | S | Never cut URLs in operator output; cut only prose. |
| P5 | Mobile layout check | Headless Chrome CLI screenshots under about 420 px look clipped when the page is fine. | case 6 | cosmetic | S | Measure with Puppeteer `setViewport({width:390,isMobile:true})` and compare `scrollWidth` with `clientWidth`. |

---

## 5. Fragile spots (not defects yet)

These have not produced a wrong report in the seven cases, but they will under a plausible change or input. Each names the trigger.

**F1. Log prose is an API.** C2F reads about 60 regexes over `checks.log`, `hack.log`, `trace.log`, `render.log` and `discovery.log`. Trigger: anyone edits a `print()` in AC or hack-scan for readability. Nothing fails; a count or a URL list silently changes. Examples in the code today: `(\d+) URL\(s\) referenced`, `in sitemap but NOT reached by crawl[^:]*:\s*(\d+)`, `ORPHAN-SAMPLE: live=(\d+) ...`, `CONTENT:\s+CSR`. Mitigation now: a test per regex with a fixture log. Real fix: section 7.2 (`checks.jsonl`).

**F2. Emoji are control characters.** The gate's critical count is `grep -c "🚩\|🚨"`; C2F decides "real alarm" by `"🚨" in hack`; `_client_copy()` strips leading `⚠🚩🚨`. Trigger: a terminal or editor that normalises emoji variation selectors, or a log written with `PYTHONIOENCODING` set to ASCII. The variation selector `U+FE0F` after `⚠` is already handled ad hoc (`️?` in the regex). Fix: structured status fields.

**F3. Operator-only routing by title regex.** C2F: `_OPERATOR = re.compile(r"consent|tracker", re.I)` moves any finding whose title contains "tracker" to the operator file. Trigger: a legitimate finding such as "A link-tracking subdomain is exposed" or "Your order tracker page is broken" disappears from the client report with no notice. Fix: route by finding id (`pre-consent-trackers`), not by title.

**F4. File freshness by mtime with 1-second resolution.** The fallback guard compares `stat -c %Y` before and after and needs a `sleep 1` to work; the perf gate and `audit.sh` (`fresh()`, `-nt "$START"`) compare mtimes. Trigger: a clock step (NTP correction), a copy with `cp -p`, or a file system with coarse timestamps. Fix: per-run directories (section 7.3), where existence means "this run".

**F5. Two names for one site.** Crawl and sidecars are keyed by slug; Lighthouse files by fetched host (with and without `www.`); BCR falls back from slug to `D.site`; RFA creates symlinks to bridge `www.` and bare spellings. Trigger: a site that redirects `example.com` to `www.example.com` mid-run, or two slugs for one host. Every past perf and sidecar collision came from here.

**F6. Build-time network calls.** BCR re-probes the live site (two blocks). The same crawl can give different reports minutes apart, and a rebuild during verification can change counts the verifier just checked. Trigger: a flaky host. Fix: B8.

**F7. `set -u` without `pipefail`, and `|| true` on every stage.** This is deliberate (a `head` in a pipe must not abort the run, and stages exit non-zero when they *find* problems). The cost: a stage that crashes and a stage that found problems look the same until the gate's grep. Every grep marker that is printed early (O6, O7) turns a crash into a pass. Rule: every gate marker must be the **last** line a stage prints.

**F8. `audit.sh` has no verification step.** It is honest about this (header: "No publish gate on this path"), and it prints banned-text hits as warnings. Trigger: a bot that takes `$REPORTS_DIR/<domain>/report.pdf` from `audit.sh` and sends it to a site owner. Every wrong-fact defect in section 4 can then reach a reader. Mitigation: `audit.sh` should print, as the first line of `report.md` and on the PDF cover, "Automated, unverified. See NOT MEASURED." and should exit non-zero (or write `VERIFIED=no` in `findings.json`) when a banned-text rule fires.

**F9. Allowlists that invent defects when they forget a member.** `_LOCAL_SUBTYPES` (C15), the CMS fingerprint table (C18), the challenge-marker list in RFA, `_TRANSIENT_ST` and `BLOCKED_ST` in BCR, `_REFUSED` in C2F. Each missing member produces a confident wrong statement, not an "unknown". Rule: when the allowlist does not match, the result is "unknown", never "absent".

**F10. Search-engine estimates treated as measurements.** `hack-scan.py` and `filter-url-scan.py` read `total_results` from a search API. The code already prints that `site:` is an estimate and that null means unknown; the converter still turns some of these into findings. Keep them as `NOTE` severity at most, unless confirmed by a second source.

**F11. Queue claim and per-slug lock are on one filesystem.** `run-queue.sh` relies on atomic `rename` between `queue/pending` and `queue/running`. Trigger: `queue/` on a network mount or across volumes, where rename is not atomic. Document it; refuse to start if the two folders are on different devices.

---

## 6. Risk ranking and the top 10 fixes

### 6.1 Risk matrix

Rank = (how often it produced a false client-facing statement in the seven cases) times (how bad the statement is), divided by effort.

| Rank | ID(s) | Class | Cases hit | Sev | Eff |
|---|---|---|---|---|---|
| 1 | C7 | hack alarm on other domains; wrong URL list | 2 | false CRITICAL | S |
| 2 | B3, B4 | title-regex merge and shell suppression | 4 | duplicate / dropped findings | M |
| 3 | C10, C11, C12 | counts from printed examples; `lstrip` | 2 (+1 earlier) | wrong numbers in titles | S |
| 4 | C13, C14 | redirect-blind checks | 3 | wrong statements | M / S |
| 5 | C5 | phone from URL digits | 2 | false MEDIUM | S |
| 6 | O1b, O5, O6, O7 | gates that check "ran", not "produced" | 1 (+latent) | silent skip | S |
| 7 | P-NEW-1, P-NEW-2 | search key ignored; skip not reported | every fresh machine | silent skip | S |
| 8 | C3 | identical-content by word count | 1 | false CRITICAL | M |
| 9 | O13, B8 | per-stage transport; live rebuilds | 2 | wrong facts on walled hosts | M |
| 10 | C9 | archived URL sample | 1 | missed real issue | M |

### 6.2 Fix 0 (enabler, about 1 hour): offline mode

Add `AUDIT_OFFLINE=1`:

- C2F `_recheck_full`: return `(None, False)` at once. The caller keeps the row as "unverified", never drops it.
- BCR: skip both re-probe blocks and print `(offline: live re-probe skipped)`.
- AC: skip the raw-HTML sample in check 6 and the probes in checks 20, 24 and 28; print `UNKNOWN (offline)` for each.

Without this, no regression test is deterministic. Every verification below runs on a synthetic fixture in a fresh temporary folder under `tests/_out/`, never on `$CRAWL_DIR` or a case folder.

### 6.3 The ten fixes

**1. Hack-scan domain filter and URL list (C7). About 1 hour.**
- Change: in `hack-scan.py`, after `hits=r.get('organic_results') or []`, keep `own = [h for h in hits if host_of(h.get('link')) == dom or host_of(h.get('link')).endswith('.' + dom)]` and alarm on `own` only; print `len(own)`, not `total_results`. In C2F's hack block, take URLs only from the indented lines under each siren line (`^\s{6,}(https?://\S+)` after a line containing `🚨`). Skip index estimates when the crawl has fewer than 3 indexable rows.
- Fixture: `tests/fixtures/synthetic/hack-offdomain.log` (an orphan list of 5 URLs, then a siren line with 4 hits on other domains) and a stubbed search response.
- Verify: run C2F on a temp case folder holding that log and a 10-row synthetic crawl. Before: 1 CRITICAL whose URLs are the orphans. After: 0 hack findings; an on-domain variant of the fixture still gives 1 with the right URLs.

**2. Stable finding ids in C2F and BCR (B3, B4, C23 first half). About 3 hours.**
- Change: C2F adds `"id"` to every finding (`broken-links`, `csr-shell`, `noindex`, `wrong-host-links`, `hack-indexed`, `soft-404`, `check-<n>` for generic sections). BCR: `const bl = manual.filter(f=>f.id==='broken-links'); if (bl.length>1){console.error('2+ broken-links findings'); process.exit(4);}`, falling back to the title regex only when no finding has an id, with a printed warning. `shellFinding = manual.find(f=>f.id==='csr-shell')`; `isShell(p)` is true only when `p.url` (trailing slash normalised) is in `shellFinding.urls`. Add `data-fid` to each rendered finding.
- Fixture: synthetic crawl with 1 broken target, plus a sidecar whose broken-link finding is retitled "Your guide download is broken" with `id: broken-links`.
- Verify: `AUDIT_OFFLINE=1 node build-crawl-report.js <tmp>/x-full.json <tmp>/out.html "T"`, then count broken-link accordions. Before: 2. After: 1. Second fixture: shell finding with 0 URLs and a short `/thank-you` page; its OG finding must remain.

**3. Counts from data, not from printed examples (C10, C11, C12). About 1.5 hours.**
- Change: C2F noindex: `_nn = int((re.search(r"(\d+) crawled page\(s\) carry noindex", body) or [0, len(_ni_urls)])[1])`. AC check 6: after the existing print, emit `   PAIR <target> <source> <anchor>` for every pair, no cap; C2F counts `PAIR` lines when present. AC: every `lstrip('w.')` becomes `re.sub(r'^www\.', '', h)`; the raw-HTML regex matches only `<a href>`, `<img src>`, `<script src>`; check 6 uses its own variable name in place of rebinding `site_host`.
- Fixture: synthetic crawl with 16 noindexed pages; a second with 1 wrong-host target linked from 3 pages; a third with an external link to `https://wexample.com/` on `example.com`.
- Verify: run AC then C2F offline on each. Before: titles start with 6 and 1, and `wexample.com` is flagged. After: 16, 3, not flagged.

**4. Gates that check this run's output (O1b, O3b, O5, O6, O7, P-NEW-2). About 2 hours.**
- Change: add 202 and challenge-titled rows to the blocked count. Drop the perf log-grep fallback; `tee` without `-a`. Gate `checks` on AC's closing `===` line (or a new `AUDIT-CHECKS COMPLETE`). Gate WordPress on `WORDPRESS DEEP-DIVE COMPLETE`, filter scan on `FILTER-VERDICT`. hack-scan prints `INDEX-CHECK: UNKNOWN (no search API key)` and the gate shows it as unknown (section 7.4).
- Fixtures: synthetic crawl of 202 challenge rows; a case folder with an old `perf.log`; a synthetic crawl that makes check 5 raise.
- Verify: extract the quality block into `crawl-quality.py <crawl.json>` (exit 0 ok, 7 thin, 8 walled) so it can be tested without running the pipeline. Before: exit 0 on the 202 fixture. After: 8.

**5. Redirect-aware checks 15 and 28 and `carriers()` (C13, C14). About 1 day.**
- Change: check 15 groups redirect targets by `final_url` and prints a separate "forwards to the home page" block. A shared `carriers(href, pages)` helper drops `redirected` rows and collapses `?query` views; every "N pages carry X" count uses it. Check 28 uses `_NR` and reports `301 -> <Location>`; under CSR it skips the "admits error" clause.
- Fixture: synthetic crawl with 1 real page carrying a spam link and 40 redirect rows that point at it; a local test server for check 28 (see 7.2 rules).
- Verify: carrier count 1, not 41; check 28 prints `301 -> /`.

**6. Phone numbers and context (C5). About 1 hour.**
- Change: new importable module (for example `audit_patterns.py`) with `shown_numbers(text)` that removes `https?://\S+` and bare `host.tld/path` runs, requires a separator unless a `tel:` link carries the same digits, and returns `(number, 80-character context)`. AC check 9 calls it.
- Verify: `python3 -I -c "import sys; sys.path.insert(0, '.'); import audit_patterns as a; assert not a.shown_numbers('Insert this video here https://vimeo.com/2345678901'); assert a.shown_numbers('Call 555-201-0199 today'); print('ok')"` from `KIT_HOME`. Before: no module. After: `ok`.

**7. Search key from the environment; skip is reported (P-NEW-1, P-NEW-2). About 30 minutes.**
- Change: both `hack-scan.py` and `filter-url-scan.py` read `SERPAPI_KEY` (or `SERP_API_KEY`) from the environment first; a file is read only if a variable names it. No key: print `INDEX-CHECK: UNKNOWN (no search API key)`; C2F adds it to `not_measured`.
- Verify: `env -u SERPAPI_KEY python3 hack-scan.py <synthetic crawl> https://example.com` under `AUDIT_OFFLINE=1` prints the UNKNOWN line. With `SERPAPI_KEY=dummy` and offline mode, the code path that builds the query runs (assert by a printed `INDEX-CHECK: offline` line, no network).

**8. One sidecar, loud fallback (B2b). About 30 minutes.**
- Change: BCR reads only `<runSlug>-findings.json`; if `<D.site>-findings.json` exists and is newer, exit 6. Print `Using sidecar <path> (N findings)` on every build.
- Verify: synthetic dir with only an old host-named sidecar. Before: its finding ships. After: exit 6 or "no sidecar" printed, never a silent read.

**9. Identical-content by text, not by size (C3). About 1 day.**
- Change: FX stores `simhash` of the main text per row (`crawler/simhash.py` already exists). Check 21 groups by simhash with Hamming distance of 3 or less, then confirms with `difflib.SequenceMatcher` ratio of 0.9 or more on two members. Add a case-folded path rule (`/About` and `/about` style pairs are real duplicates).
- Verify: synthetic crawl of 3 pages with equal word count and H1 but different text: no finding; 3 pages with the same text: one finding.

**10. One transport per run (O13). About 1 day.**
- Change: RFA writes `$CASES_DIR/<slug>/transport.json` (`ua`, `proxy_set`, `proxy_has_auth`, `delay`, `concurrency`). `render-check.py`, `perf-audit.sh`, `pixel-consent-check.py`, `content-attribution.py` and AC's raw sample read it and print it as their first log line. Chrome gets `--user-agent` and, when possible, `--proxy-server`; when the proxy has credentials, each Chrome-based stage prints `TRANSPORT MISMATCH` and its verdicts become `unknown`.
- Verify: a local server that answers 403 to any UA except a chosen one; set `CRAWL_UA` to that one. Before: render and perf measure the 403 page. After: both measure the real page, or say `TRANSPORT MISMATCH`.

### 6.4 Next five

- C9 archived-URL buckets (M). The biggest real finding in case 5 was missed by the tool.
- C15 local entity by address, plus schema vs footer comparison (S). Catches a real address mismatch.
- C8 hidden off-screen link check (M). The only hack the seven cases contained was found by hand.
- C17 one render-blocked threshold (S).
- F3 operator routing by id, not title (S). Cheap, and prevents a silent client-finding loss.

---

## 7. Target architecture

Goal: a wrong title, a wrong file, an empty capture or another run's data cannot reach a reader without a non-zero exit.

### 7.1 Stable finding ids instead of title regex

- **Producer.** Every finding C2F makes carries `id` (stable kebab-case kind, unique per run) and `source` (`{"check": "17", "log": "checks.log", "line": 70}`). Canned ids: `broken-links`, `csr-shell`, `noindex`, `wrong-host-links`, `hack-indexed`, `sitemap-orphans`, `soft-404`, `schema-local`, `phone-other`, `local-leak`, `pre-consent-trackers`, `check-<n>` for generic sections.
- **C2F's internal lookups.** Title `startswith` lookups inside C2F switch to `next(f for f in findings if f["id"] == ...)`.
- **Builder.** BCR matches on `id` (fix 2). BCR's own auto findings also get ids (`auto-canonical-offsite`, `auto-missing-h1`, `auto-missing-meta`, `auto-og`, ...). After merging, BCR drops an auto finding whose id appears in a sidecar finding's `supersedes` array. That replaces both title matchers and `suppress_builder` with one rule (C23, B3).
- **Operator routing.** `pre-consent-trackers` (and any future operator-only id) goes to the operator file by id (F3).
- **Rendering.** `data-fid` on each finding, so tests and the publish gate grep by id, not by title.

### 7.2 One findings file, a schema, a validator, and overrides

- **Files per run** (inside the run dir from 7.3):
  - `findings.generated.json`: written only by C2F; may be overwritten at any time.
  - `findings.overrides.json`: written only by a verifier (person or agent). A list of `{id, set:{...}}`, `{id, drop:true, reason}` or `{add:{...}}`, keyed by id, so regeneration keeps every edit (C22).
  - `findings.json`: written only by `merge-findings.py <run>`. Applies the overrides, refuses an override whose id no longer exists (exit 8, listing them), and records `_merged_from` hashes of both inputs. BCR reads only this file.
- **Schema** `schema/findings.schema.json` (JSON Schema 2020-12):
  - `id`: string, required. `severity`: enum `critical|high|medium|low|note`, required.
  - `title`: string, at least 10 characters.
  - `count`: integer or null; `count_basis`: string, required when `count` is set (for example "header line of check 17"). Every number in a title becomes traceable (C10, C11, C21).
  - `urls`: array; `groups[]` require `urls` (B10).
  - `where[]`: `{source_url, source_title, link_words, target_now, target_change_to}`, for the plain-language report (section 9).
  - `status`: `confirmed|unknown`; `unknown_reason` required when `unknown`.
- **Validator** `validate-findings.py <file>`: bad JSON gives exit 2 with the position; schema errors exit 3 with each failing path; ids unique; the first integer in a title equals `count`; titles matching `owner-rules.json` `finding_forbidden` fail. BCR runs it before loading; RFA uses it as the `findings` gate in place of `grep -q "finding(s)"`.
- **Structured check output.** AC keeps printing `checks.log` for people. Each check also appends one JSON line to `checks.jsonl`: `{"check": "17", "count": 16, "items": [...all URLs...], "status": "flag|ok|unknown"}`. C2F reads counts and lists from `checks.jsonl` and uses `checks.log` only for prose. This removes the parse class (F1, C7 list, C10, C11, C21) in one move.

### 7.3 Per-run output directories

- **Layout.** `RUN=$CASES_DIR/<slug>/runs/<UTC yyyymmddTHHMMSSZ>/`, created at RFA start. A `latest` symlink is updated only when the gate passes. Existing top-level files stay where they are; nothing is moved or deleted.
- **Contents.** `crawl.json`, `transport.json`, `logs/*.log`, `checks.jsonl`, `render-mode.json`, `render/*.png`, `perf/<tier>.json`, `findings.generated.json`, `findings.overrides.json`, `findings.json`, `reprobe.json`, `stages/<key>.json`, `report/site-audit.html`, `report/report.md`.
- **Changes.**
  - RFA: `J=$RUN/crawl.json`; every stage gets `$RUN` paths in place of `$CRAWL_DIR/<slug>-*`.
  - `perf-audit.sh "$URL" all "$RUN/perf"` (the out-dir argument already exists). Delete the snapshot and symlink blocks from RFA (code, not data).
  - BCR perf lookup becomes `path.join(dir, 'perf', t + '.json')` only (O4, F5).
  - C2F takes `--run $RUN` and writes only there.
  - `detect-cms.py`, `url-discovery.py`, `render-check.py` take an out dir.
  - `audit.sh` and `publish-run.sh` take `<RUN>` and copy from `report/`.
- **Effect.** No two runs read each other's perf, sidecar, render or logs: O4, O5, O8, O14, B2b, F4, F5.

### 7.4 Gates that check this run's own artifacts

- **Stage files.** Each stage writes `$RUN/stages/<key>.json`: `{"status": "ok|unknown|failed|not-applicable", "measured": <int>, "reason": "...", "inputs_sha": "..."}`. A small helper `stage_done <key> <status> <measured> <reason>` in RFA writes it for shell-gated stages during migration.
- **Gate rules** (replace the grep list in `gate()`):
  - `crawl`: `measured` = real pages. Fail under 3, unless `render-mode.json` says CSR (then `unknown`, reason "CSR; rendered routes counted separately").
  - `checks`: ok only when `checks.jsonl` holds one line per check and AC's closing line is present (O6).
  - `perf`: ok only when `$RUN/perf/desktop.json` or `mobile-4g.json` has a non-null score and `finalDisplayedUrl` on this host (O3b, O5).
  - `findings`: ok only when `validate-findings.py $RUN/findings.json` exits 0.
  - `report`: ok only when the HTML has one `data-fid` per finding in `findings.json` plus the auto findings BCR reported.
  - `hackscan`: `unknown` when no search key (P-NEW-2).
  - `filterscan`, `cmsdeep`: `not-applicable` when they really do not apply, never a fake ok (O7).
- **Exit.** The gate prints three lists (ok, unknown, failed) and exits `10 + failed`. `unknown` does not fail the run but is printed in the report as "What we could not check" (BCR already renders `not_measured`; feed it from the stage files).

### 7.5 Render-aware checks

- **Order.** Render the seed URL right after the crawl (stage 1b) and write `$RUN/render-mode.json` = `{"mode": "SSR|PARTIAL|CSR|BLOCKED", "raw_words": n, "rendered_words": n}`.
- **New stage `render-routes.py` (CSR only).** Reads the sitemap and rendered-DOM links, dumps each route with headless Chrome (`$CHROME_BIN --headless=new --dump-dom`) into `$RUN/rendered/<route>.html`, and writes `rendered-crawl.json` with the same row shape as `crawl.json`.
- **AC.** Under CSR, checks 3, 4, 8, 22, 23, 25 and the "admits error" clause of 28 run on `rendered-crawl.json` and print `basis: rendered DOM`. If rendering failed, they print `UNKNOWN (CSR, not rendered)`. The current C2F skip becomes a fallback only.
- **Effect.** C1, C2 and the CSR half of C14 cannot recur, and a CSR site gets real findings in place of a "not measured" list.

### 7.6 "Unknown" as a first-class result

- One convention for every tool: a measured value, or `null` plus `unknown_reason`. Never `0` or `[]` for "could not read". `verify-audit.py` already follows it (`"status": "measured" | "not_measured"` per section); the rest of the pipeline should match.
- Specific changes: AC sections whose only output is UNKNOWN become `status: unknown` items in `checks.jsonl`; C2F carries them into `findings.generated.json` with `status: unknown`; BCR shows them in "What we could not check", never as defects and never dropped. `url-discovery.py` archive fetch failure: `UNKNOWN`, not "0 URLs ever archived". `social-scan.py` UNKNOWN and render `RENDER BLOCKED`: stage status `unknown`.

---

## 8. Human (or verifier-agent) protocol before a report ships

Run this after the pipeline and before any report goes to a reader. It is ordered by how often each trap bit in the seven cases (03: pre-ship checklist), plus the techniques that found the biggest real issues by hand (docs/05-CASE-STUDIES.md, cross-audit section B). `verify-audit.py <slug>` gathers most of the evidence in one run; `click-test.js` decides dead-link candidates; the steps below say what to read in that evidence.

Commands marked (live) touch the audited site. Run them slowly, with the crawl's `CRAWL_PROXY` and `CRAWL_UA` when the host is walled (03:Card 16).

Set once:

```bash
SLUG=<slug>; C="$CASES_DIR/$SLUG"; J="$CRAWL_DIR/$SLUG-full.json"; H=<host without www>
E=$(ls -t "$C"/evidence-home-*.html 2>&1 | grep -v 'No such' | head -1)
# none saved yet (live):  curl -sL "https://$H/" > "$C/evidence-home-raw-$(date +%F).html"
```

1. **Crawl size and status mix.** `python3 -c "import json,collections;P=json.load(open('$J'))['pages'];print(len(P),collections.Counter(p.get('status') for p in P))"` and `grep -n 'quality:\|CRAWL LIKELY BLOCKED\|STILL THIN\|PARTIAL CRAWL\|TINY SITE\|CHALLENGE MARKERS' "$C"/run*.log`. Under 3 real pages: stop and fix the transport, or go to step 2. A green gate does not count.
2. **Render mode.** `grep -n 'CONTENT:\|RENDER BLOCKED\|TRANSPORT MISMATCH' "$C/render.log"`. If any page is CSR, render every route (live): for each `<loc>` in the sitemap (follow child sitemaps when the `<loc>` lines end in `.xml`), `"$CHROME_BIN" --headless=new --dump-dom "$u" > "$C/rendered/<safe name>.html"`. Count `<img`, `<form` and `application/ld+json` per file. Judge checks 3, 4, 8, 22, 23, 25 and 28 only on these counts (03:Card 1).
3. **Read the JS bundle on app-builder sites** (Vite, React SPAs). Get the script URL from the shell (`grep -o 'src="[^"]*\.js"' "$E"`), save it (live), then list routes and endpoints: `grep -o '"/[a-z0-9][a-z0-9/_-]*"' bundle.js | sort -u` and `grep -o 'https://[a-z0-9.-]*\(webhook\|api\)[^"]*' bundle.js | sort -u`. Render every route that is not in the sitemap. A hidden review-gating route was found this way (case 4).
4. **Follow every old and redirecting URL to its end.** This found more real issues than any other technique.
   - Redirect rows: `python3 -c "import json;P=json.load(open('$J'))['pages'];[print(p['url'],'->',p.get('final_url')) for p in P if p.get('redirected')]" | awk '{print $3}' | sort | uniq -c | sort -rn | head`. A final URL that is the home page or a generic index means a missing page or a wrong target, not an "old address". Spam slugs (casino, loan, pharma) belong to the hack finding.
   - Archived URLs (live): for every old page URL from `discovery.log` and the archive, `curl -s -o "$C/hop.tmp" -w '%{http_code} %{url_effective}\n' -L "<old-url>"` and `curl -sI "<old-url>" | grep -i '^location'` per hop. Write `$C/old-url-check.tsv`. Ship those whose final page is the home page, a generic index or a different place (case 5).
   - For any "N pages carry X" count, drop `redirected: true` rows and collapse `?query` views of one path first (03:Card 10).
5. **Soft-404 truth** (live): `curl -sI "https://$H/this-page-does-not-exist-xyz/" | head -3`. 200 = soft 404. 301 or 302 = "missing pages forward to <Location>". Under CSR, render the made-up path and read its text (03:Card 11).
6. **Test the user paths the crawl does not take** (live): thank-you pages, downloads, lead magnets. `grep -o 'href="[^"]*\.\(pdf\|zip\|docx\)"' "$C"/rendered/*.html "$E" | sort -u`, then `curl -sI <url> | head -1` for each. Search the crawl for `thank` and `guide` routes and fetch every download linked from them. A broken lead-magnet PDF was found this way (case 7). `verify-audit.py` covers this in its `downloads` section.
7. **Forms: never submit live.** Find the endpoint (step 3). Test it with a fake-id control: the real id and a fake id must answer differently, or the check proves nothing. Report "the success message proves nothing" when they answer the same.
8. **Read the sentence around every flagged number or string.** `grep -o '.\{80\}<digits>.\{40\}' <saved page>` for each phone, LOCAL_LEAK hit and slug-mismatch example. Drop IDs inside URLs and third parties named in the sentence. A leftover editor note is its own finding; scan all sampled pages for notes, with the known hit as the control (03:Cards 3, 4, 5). `verify-audit.py` prints context for phones and notes.
9. **Compare schema, footer and map embed for the same fact.** `grep -o '"address"[^}]*}' "$E"`, `grep -o 'tel:[^"]*' "$E" | sort | uniq -c`, `grep -o '<iframe[^>]*maps[^>]*' "$E"`. Any mismatch is a finding (case 3 had two different addresses). Compare review counts claimed on the site with any count the site itself links to.
10. **Hack and spam.** `grep -n -A6 'inurl:.php' "$C/hack.log"`. Every hit must be on `$H` or a subdomain, else the probe is void (C7). Compare the CRITICAL's URL list with `grep -n 'NOT reached by crawl' "$C/hack.log"`. Look for hidden blocks: `grep -o 'left:-9999px\|display:none' "$E"` and count off-topic anchors in the home page (03:Card 6; C8).
11. **Every number in a title against its own source.** Noindex: `grep -n 'carry noindex' "$C/checks.log"` (the header count, not the listed lines; C10). Wrong-host links: count pairs in the crawl, not printed lines (C11); if the count equals the page count, you are counting canonical tags (C12). Broken links: `grep -c '^\[4\|^\[5' "$C/trace.log"`. Fix the title, not the log.
12. **Duplicates.** Case-fold the paths of every "identical" set; pairs that differ only in case are real duplicates. For the rest, compare two members' main text: `python3 -c "import difflib,sys;a,b=(open(f).read() for f in sys.argv[1:3]);print(round(difflib.SequenceMatcher(None,a,b).ratio(),2))" a.txt b.txt`. Under 0.9 is a template, not a duplicate (03:Card 2).
13. **Email and DNS claims need three resolvers and a port probe with a control.** `for r in one.one.one.one dns.google <authoritative NS>; do dig +short MX $H @$r; done`. Check DKIM selectors named by the provider in the SPF include. For "cannot receive mail", probe ports 25 and 587 on the MX, with a known-good public mail server on port 25 as the control that must answer 220 in the same session.
14. **Banned text.** `grep -ciE 'before visitors can consent|pre-consent' <built html>` must print 0 (C16 is fixed, but check; F3 can also drop findings, so compare the operator file's titles with what you expect there).
15. **Sidecar edit and rebuild.** Follow 02:§7. Edit only `$CRAWL_DIR/<slug>-findings.json` (until 7.2 lands). Run `python3 -m json.tool` on it. Rebuild with the crawl's env. The builder's last line must report at least the sidecar's finding count. Grep the HTML for each corrected title (1 or more) and each withdrawn title (0). Save `findings-verified.json` in the case folder; `publish-run.sh` requires it.
16. **Look at it on a phone width.** Puppeteer `setViewport({width:390,isMobile:true})`, then compare `document.documentElement.scrollWidth` with `clientWidth`. Do not judge from a CLI `--window-size` screenshot (P5).

Steps 3, 4, 6, 8 and 9 match most of the human-only findings in docs/05-CASE-STUDIES.md (cross-audit section B): two addresses, a stray phone number, missing pages that forward home, a hidden review-gating page, overstated review counts, wrong-target redirects, a policy link on hundreds of pages, a typo redirect chain, a non-tappable header phone, a broken lead-magnet PDF, an editor note. Do not skip them to save time.

---

## 9. Plain-language report standard

The detailed builder page is long and technical. In case 6 a plain-language version (written to ASD-STE100 Simplified Technical English rules, laid out like an email) became the version the site owner read, with the detailed page linked at the end. That version was written by hand. This section specifies a generator so the two cannot disagree.

### 9.1 Shape

- Subject line, greeting, a 3-sentence introduction.
- "What works well" first.
- "Fix these first": 3 fixes, each with "What we found", "Why it is important", "What to do" as numbered steps.
- "Smaller fixes", each with a "Where to look" list.
- Platform click paths where the platform is known (from `detect-cms.py`).
- A link to the detailed page at the end.
- Every link fix names the **source page** (title and full URL), the **exact link words**, and the **address to change to**. A reader must be able to fix it without opening the detailed page.

### 9.2 Generate it from the findings file

- **Tool** `email-report.py <RUN>` writes `report/email.html` and leaves `site-audit.html` as the detailed page. `publish-run.sh` already has the `email` and `detailed` kinds.
- **Inputs:** `findings.json` (after the merge, 7.2), `crawl.json`, and a short verifier file `email.overrides.json` for the subject, introduction, "What works well" lines and the choice of the top 3 fixes (judgment the tool cannot make).
- **Per-finding fields** (added to the schema): `client.title` (one STE sentence), `client.found[]`, `client.why[]`, `client.steps[]`, `client.platform`, `where[]`.
- **`where[]` is filled from the crawl, never typed.** Crawl rows store `internal_links` as `{href, anchor, rel}`. For a link-type finding, C2F scans non-redirected rows for links whose href matches each target and writes `{source_url, source_title, link_words, target_now, target_change_to}`. `target_change_to` comes from the fix rule for that id: the canonical-host form for `wrong-host-links`, the canonical target for links to a copy of the home page, the final URL for links to redirects. Self-links (source equals target) are dropped; a hand-written list in case 6 included one by mistake.
- **Numbers come from `count` only.** The generator asserts `len(where) == count` for link findings and exits 9 on a mismatch.

### 9.3 STE checks and their control

- Descriptive sentences 25 words or fewer; procedural steps 20 words or fewer.
- A banned-word list (kept in a data file, extendable).
- No long dashes, no " - " used as a dash, no semicolons. One instruction per step.
- Active-voice heuristic: flag "is/are/was/were + past participle" as a warning.
- The checks cover every text field, including change lines that contain a URL (the hand-written tool skipped any line containing "http").
- **Exit code 1** on any hard failure.
- **Control:** `email-report.py --self-test` injects one 30-word sentence, one 22-word step, one semicolon and one long dash into a copy of the content; each must be reported and the exit must be 1. The real build refuses to run if the self-test does not fail.

---

## 10. Regression test plan

### 10.1 Where tests live today

`tests/` holds about 64 `unittest` tests (`test_builder.py`, `test_extract_and_findings.py`, `test_publish_gate.py`, `test_queue.py`, `test_verify_audit.py`, `test_click_test.py`, `test_interlink_map.py`). Run them with `python3 -m unittest discover -s tests -v` from `KIT_HOME`. Several fixes in section 3 already have tests there (unknown severity, blocked 403 is not broken, failed speed test is not zero, LOCAL_LEAK host rules, archived assets).

### 10.2 Rules for new tests

- **Synthetic fixtures only**, under `tests/fixtures/synthetic/`, built by a committed script (`tests/make_fixtures.py`) so they can be re-made. Domains are `example.com`, `example.org`, `test.example`. No real crawl data, real client HTML or real logs.
- **No network.** Every test sets `AUDIT_OFFLINE=1` (fix 0). A test that needs an HTTP answer starts a local `http.server` on `localhost` with a random port in a thread and stops it in `tearDown`; a test that needs a search-API answer passes a stub JSON file.
- **Watch it fail first.** Write the test, run it on today's code, keep the failure output in the commit message, then fix. A test that has never failed has not shown that it tests anything.
- **Output** only under `tests/_out/` or a `tempfile.mkdtemp()` folder. Never `$CRAWL_DIR`, `$CASES_DIR` or `$REPORTS_DIR`.
- **No orphaned processes.** A test that starts Chrome or a server records the PID and kills that PID in `tearDown`; never kill by name or pattern.
- `unittest` only (no new dependency). Builder tests call `node build-crawl-report.js` through `subprocess` with `stdin=subprocess.DEVNULL`.

### 10.3 One test per open defect

File: `tests/test_review_regressions.py`. One `test_<id>` per row.

| Test | Defect | Synthetic fixture | Assertion | Fails today because |
|---|---|---|---|---|
| T-O1b | O1b | 40 rows, status 202, 120-word challenge bodies | `crawl-quality.py` exits 8 (walled) | 202 not counted as blocked |
| T-O1n | O1b (negative) | 62 real 200 rows | exits 0 | (must stay green) |
| T-O2 | O2 | crawl with `partial:true`, 10 real rows | RFA quality step prints `PARTIAL` | only checked inside the fallback |
| T-O5 | O5, O3b | case dir with an old `perf.log` holding a score line; no fresh JSON; `SKIP_PERF=1` | perf gate unset | append plus log-grep fallback |
| T-O6 | O6 | crawl that makes check 5 raise | checks gate unset | marker is check 1's header |
| T-O7 | O7 | `cms/wordpress.py` stub that exits after its header | cmsdeep gate unset | header contains the marker |
| T-O12 | O12 | local server that never answers | attribution prints `UNKNOWN`, continues | `TimeoutExpired` uncaught |
| T-C2 | C2 | `render.log` with CSR and `TITLE DIFFERS` | one CSR finding with evidence | separate findings |
| T-C3 | C3 | 3 pages, same word count and H1, different text | no IDENTICAL finding | word-count key |
| T-C4 | C4 | 2 pages, identical 2-word title | a "share the identical title" line | silent skip |
| T-C5 | C5 | text with a video URL containing 10 digits; text with `555-201-0199` | no number, then one | URL digits match |
| T-C7 | C7 | orphan list then a siren with 4 off-domain hits | no hack finding | any hit alarms; URL list is the orphans |
| T-C8 | C8 | home page with 60 off-screen anchors | hidden-block check reports 60 | no check exists |
| T-C9 | C9 | stubbed archive list: 8 images, 2 pages that 301 to `/` | assets excluded; 2 in "forwards to home" | follow=True, assets sampled |
| T-C10 | C10 | 16 noindexed pages | title starts with 16 | counts 6 printed lines |
| T-C11 | C11 | 1 wrong-host target, 3 sources | title starts with 3 | one source per target |
| T-C12 | C12 | site `example.com`, link to `wexample.com` | not flagged | character-set strip |
| T-C13 | C13 | 1 carrier, 40 redirect rows | carriers == 1 | redirect rows inherit links |
| T-C14 | C14 | local server: unknown path 301 to `/` | prints `301 -> /` | urllib follows |
| T-C15 | C15 | JSON-LD node with PostalAddress and an unlisted type | local entity present | allowlist |
| T-C17 | C17 | rendered body of 10 words "Access denied" | RENDER BLOCKED in both tools | thresholds 3 vs 25 |
| T-C18 | C18 | HTML with `/_astro/` script | platform not `unknown` | no fingerprint |
| T-C21 | C21 | finding with 68 targets | sidecar holds 68 | capped at 25 or 40 |
| T-C22 | C22 | generated file plus an override | override survives regeneration | overwrite |
| T-C23 | C23 | one off-domain canonical | one CRITICAL | builder and sidecar both report |
| T-C25 | C25 | 428 sitemap URLs, 2 bad | severity below CRITICAL | fixed severity map |
| T-B2b | B2b | only an old host-named sidecar | exit 6 or "no sidecar" | silent fallback |
| T-B3 | B3 | broken-link finding retitled, `id` set | one accordion | title regex |
| T-B4 | B4 | shell finding with 0 URLs, short `/thank-you` page | OG finding still lists it | criteria suppression |
| T-B5 | B5 | 5 rows at 508, local stub returns 200 | recovered rows in "All pages" | `p200` computed first |
| T-B7 | B7 | local stub: 503 then 200 | target dropped | no retry on 503 |
| T-B10 | B10 | group without `urls` | build succeeds | TypeError |
| T-PN1 | P-NEW-1 | no secrets file, `SERPAPI_KEY` set, offline | `INDEX-CHECK: offline` line | env ignored when file missing |
| T-PN2 | P-NEW-2 | no key | `INDEX-CHECK: UNKNOWN` line | silent skip |
| T-F3 | F3 | finding titled "A link-tracking subdomain is exposed" | stays in client sidecar | `consent\|tracker` title regex |

Cosmetic defects (O8, O14 to O17, B11, P4, P5, P-NEW-4, P-NEW-6) get no test; check each fix by reading the diff.

### 10.4 Order of work

1. Fix 0 (offline mode).
2. Write T-C7, T-B3, T-C10, T-C11, T-C12, T-O6, T-PN1, T-PN2 and watch each fail.
3. Apply fixes 1, 2, 3, 4 and 7.
4. Continue down section 6.3, writing each test before its fix.
5. When 7.2 (ids, schema, `checks.jsonl`) lands, delete the title-regex fallbacks it replaces, and keep their tests as guards.
