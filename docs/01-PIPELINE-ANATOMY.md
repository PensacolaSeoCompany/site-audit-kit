# 01 - Pipeline anatomy: `run-full-audit.sh` and every stage it runs

Audience: an engineer or an autonomous bot that must run, debug or change the audit orchestrator
with no one to ask. This is the deep reference. For the short path, read `SKILL.md` at the repo root
and `docs/07-RUNBOOK.md`; for the known traps, read `docs/KNOWN-PITFALLS.md`.

Written 2026-10-14 from a read-only pass over the code. Nothing was crawled and the pipeline was not
run to write it.

Citation convention: citations name the file and a function, a variable or a quoted marker string,
not a line number, because the scripts are still being edited and line numbers move. Grep the quoted
string to find the spot. `RFA` means `run-full-audit.sh` at the repository root. Quoted source keeps
its original punctuation.

---

## 0. Paths in this kit

Every script finds the kit from its own location. Nothing depends on a fixed install folder.

| Name | Value | What lives there |
|---|---|---|
| `KIT_HOME` | the repository root (bash: the folder of the script; Python: `Path(__file__).resolve().parent`) | all code, the rules file, `kit-paths.sh`, `kit_env.py` |
| `KIT_WORK` | `${KIT_WORK:-$KIT_HOME/work}` (gitignored) | everything a run writes, except the published reports |
| `CRAWL_DIR` | `$KIT_WORK/crawl/` | crawl JSON (`<slug>-full.json`), Lighthouse JSON, CMS and discovery JSON, findings sidecars |
| `CASES_DIR` | `$KIT_WORK/cases/<slug>/` | one folder per audit: every stage log, evidence, the unpublished `site-audit.html` and `linkmap.html` |
| `REPORTS_DIR` | `$KIT_HOME/reports/<domain>/` | the published output: `site-audit.html`, `report.md`, `report.pdf`, `findings.json`, `linkmap.html`. `<domain>` is the host without `www.`, for example `example.com` |
| publish backups | `$KIT_WORK/publish-backups/<domain>/` | a copy of every published file before it is overwritten |
| temp files | `$KIT_WORK/tmp/` | short-lived node scripts and stripped HTML; each script deletes its own |
| empty stdin | `$KIT_WORK/.empty-stdin` | an empty file given as stdin to unattended children |

Shell scripts get these values from `kit-paths.sh`. Source it and you get `KIT_HOME`, `KIT_WORK`,
`CRAWL_DIR`, `CASES_DIR`, `REPORTS_DIR`, `PY`, `NODE`, `CHROME_BIN` and `EMPTY_STDIN`, all exported,
and the four folders exist. Every value can be overridden from the environment or from an optional
`config.env` file at the repo root (plain `KEY=value` lines; a value already in the environment
wins). Python scripts get the same values from `kit_env.py` (`kit_env.CRAWL_DIR`,
`kit_env.chrome_bin()`, `kit_env.puppeteer_require_js()`). `kit_env.py` ignores an environment
`KIT_HOME` on purpose, so a stale value cannot point a script at another checkout.

Tools:

- Python: `$KIT_HOME/.venv/bin/python` when it exists, else `python3` (`PY`). Every stage runs under
  `$PY`.
- Node: `NODE` (default: `node` on `PATH`). Node packages (puppeteer-core, lighthouse) live in
  `$KIT_HOME/node_modules`; `install.sh` installs them.
- Chrome: the `CHROME_BIN` environment variable, else the first of `google-chrome`,
  `google-chrome-stable`, `chromium`, `chromium-browser` found on `PATH`. `kit-paths.sh` copies it
  into `CHROME_PATH` for Lighthouse and puppeteer-style tools.
- Database: optional. Without `MYSQL_*` variables the kit uses SQLite at `$KIT_WORK/crawler.sqlite`
  (or plain JSON files). An audit needs no database server.
- Proxy: only through the environment, `CRAWL_PROXY` (and `CRAWL_UA` for the user agent). There is no
  secrets file.
- Report branding: off by default. `REPORT_BRAND` sets the footer text; `CTA_URL` turns on a
  "Book a call" box; `CTA_TEL` and `CTA_TEL_TXT` add a phone button and its label.
- Rules file: `report-rules.json` at the repo root (override with `KIT_RULES`; the older name
  `owner-rules.json` is still accepted). It lists finding text that must never be published.
- Website only: there are no Google Business Profile steps.

Entry points:

| Command | What it does |
|---|---|
| `./install.sh` | No root by default: the Python venv and npm packages; Node 22 into `$KIT_HOME/.node` when system node is missing or older than 20; Chrome for Testing into `$KIT_HOME/.chrome` when no Chrome is found; then a self-check. `--system` also installs apt packages and a system Chrome (needs sudo). `--check` (same as `./doctor.sh`) only prints the version table and self-check. Exit 0 = all OK, 1 = a self-check item failed, 2 = bad arguments, 3 = an install step failed. |
| `./audit.sh <url> [--name N] [--csr] [--proxy URL]` | The one-command path for a bot. Section 10. |
| `run-full-audit.sh` | The stage orchestrator described in this document. |
| `run-queue.sh` | A batch spool queue: `add`, `work [N]`, `status`, `stop`, `requeue`. 1 worker by default, max 2 (`QUEUE_WORKERS_UNSAFE=1` lifts the max); each takes the oldest pending job. |
| `publish-run.sh` | The only writer to `$REPORTS_DIR` for verified reports. It refuses an unverified or incomplete run (section 9). |

---

## 1. How the orchestrator works (the frame every stage sits in)

- Invocation: `run-full-audit.sh <url> <slug> "<Business Name>" [service-page-url] [deep-page-url] [max-pages]`.
  The sixth positional argument, `MAXP`, is the crawl page cap (default 1000). It is exported as
  `CRAWL_MAXP` so hack-scan can tell a capped sample crawl from a complete one.
- Slug check: the slug must match `^[A-Za-z0-9][A-Za-z0-9._-]*$`, else FATAL exit 2.
- Shell mode: `set -u` only. There is no `set -e` and no `pipefail`, on purpose: a pipe cut short by
  `head` must not stop the run, and the completion gate is the source of truth. A failing stage never
  stops the run by itself. Only the slug check, the preflight and stage 1 can stop it early.
- Unattended: `exec < "$EMPTY_STDIN"` right after `kit-paths.sh` is sourced, so no child can wait on
  a terminal.
- Working directory: the script changes into `KIT_HOME`.
- Case folder: `OUT=$CASES_DIR/<slug>` is created first. Every stage log goes there, and
  `$OUT/.run-start` is touched after the preflight so later checks can tell files from this run from
  older ones.
- Crawl JSON: `J=$CRAWL_DIR/<slug>-full.json`. This is the hub file almost every stage reads.
- `HOST` is the URL with the scheme and path removed. It keeps `www.` and any port.
- Crawl timeouts: 1200 s for pass 1 and 900 s for the fallback. `BIG_CRAWL=1` raises them to 4 h and
  3 h for sites of many thousands of pages.
- Completion record: `declare -A DONE`. Each stage sets `DONE[<key>]` when its own success test
  passes. The gate (section 4) reads the keys.
- Exit trap: `trap '_rc=$?; gate; [ "$_rc" -ne 0 ] && exit "$_rc"; exit "${GATE_MISS:-0}"' EXIT`.
  The gate runs on every exit after the preflight. A fatal stage keeps its own exit code; otherwise
  the exit code is the number of skipped keys.
- `step()` only prints a banner `=== [N] name ===`. `run-queue.sh` reads the last banner in the run
  log to show which stage a job is on.

### 1.1 Stage 0: preflight

Runs before the gate trap is installed, so a preflight refusal prints no gate and exits 2.

- Website only: no Google Business Profile steps exist in this kit.
- Disk floor: free space on the file system that holds `$KIT_WORK` must be at least
  `AUDIT_MIN_FREE_GB` (default 2) GB. Below it the run stops with `FATAL preflight`. It never deletes
  anything to make room. Setting the variable lower for one run prints a warning.
- Proxy variables: the run unsets `HTTP_PROXY`, `HTTPS_PROXY` and `ALL_PROXY` (either case) for
  itself and prints a note. Some tools honour those variables and would send lookups through a
  proxy meant only for the site crawl. `CRAWL_PROXY` is allowed and is read only
  by the crawl transports.
- Chrome: `CHROME_BIN` must be set (by you or by `kit-paths.sh`) and executable.
- Python: `$PY -c "import aiohttp, bs4"` must print nothing, else it tells you to build the venv.
- Node: `NODE` must resolve to an absolute path.

---

## 2. Stage table (run order = file order)

The order in the file is the run order. The printed stage labels are not in numeric order: 6d runs
before 6c, which runs before 6a, 6e and 6b. Paths: `J` = `$CRAWL_DIR/<slug>-full.json`,
`OUT` = `$CASES_DIR/<slug>`. Every Python stage runs as `$PY <script>`.

| # | Stage | Command (as run, cwd = `KIT_HOME`) | Main inputs | Main outputs | Gate test (marker) | DONE key | Outer timeout | On failure |
|---|---|---|---|---|---|---|---|---|
| 0 | Preflight | inline | free disk, proxy env, Chrome, Python deps, Node | `OUT/.run-start` | none | none | none | exit 2, no gate |
| 1 | Full crawl | `timeout $T_CRAWL $PY full-extract.py "$URL" --max "$MAXP" [--check-external] [proxy args] --out "$J"`; fallback `timeout $T_FALLBACK $PY full-extract.py "$URL" --max "$MAXP" --curl --concurrency 1 --delay 2 [--proxy P] --out "$J"` | URL, MAXP, BIG_CRAWL, CHECK_EXTERNAL, CRAWL_PROXY, CRAWL_DELAY, CRAWL_CONCURRENCY, CRAWL_UA, CRAWL_CURL, CRAWL_CURL_SSH, CRAWL_NO_AE, CSR | `J`; `OUT/crawl.log`; `OUT/crawl-curl.log` (fallback only) | page count from `J` > 0 | `crawl` (value = page count) | 1200 s / 900 s (BIG_CRAWL: 4 h / 3 h) | 0 pages: FATAL exit 1. Fallback wrote nothing: FATAL exit 1. Still thin after the fallback: FATAL exit 4 unless `CSR=1` or the tiny-site rule applies |
| 2 | Standing checks | `audit-checks.py "$J"` | `J`, CRAWL_UA, CRAWL_PROXY, live fetches | `OUT/checks.log` only | `grep -q "CANONICAL TARGET DOMAIN"` | `checks` | none | continue; exit 2 is normal |
| 2b | CMS detection | `detect-cms.py "$J" "$URL"` | `J`, homepage fetch, CRAWL_UA, CRAWL_PROXY | `$CRAWL_DIR/<slug>-cms.json`; `OUT/cms.log` | `grep -q "PLATFORM:"` | `cms` | none | continue |
| 3 | Broken-link trace | `trace-errors.py "$J"` | `J` | `OUT/trace.log` | exit code 0 | `trace` | none | continue |
| 4 | Render check | `render-check.py $ARGS`, ARGS = URL [+SVC] [+DEEP] [+up to two auto-picked 0-word pages] | URLs, CRAWL_PROXY, CHROME_BIN | `OUT/render.log`; `render-<urlslug>-nojs.png` and `-js.png` in `KIT_HOME` | `grep -q "CONTENT:"` | `render` | none | continue |
| 5 | Lighthouse | `timeout 600 bash perf-audit.sh "$URL" all "$CRAWL_DIR"`, skipped when `SKIP_PERF=1` | URL, SKIP_PERF, CHROME_BIN | `$CRAWL_DIR/<HOST>-{mobile-3g,mobile-4g,desktop}.json`; snapshots `<slug>-<HOST>-<tier>.json`; `www`-less symlinks; `OUT/perf.log` | a scored perf JSON for THIS host written after `.run-start`, OR `grep -q "Performance score"` | `perf` | 600 s | continue, prints WARN |
| 5b | URL discovery | `timeout 600 $PY url-discovery.py "$URL" "$J"` | URL, `J`, CRAWL_PROXY, CRAWL_UA | `$CRAWL_DIR/<slug>-discovery.json`; `OUT/discovery.log` | `grep -q "DISCOVERY"` | `discovery` | 600 s | continue |
| 6 | Social scan | `social-scan.py "${URL#https://}"` | URL, CRAWL_PROXY, CRAWL_UA | `OUT/social.log` | `grep -qE "REAL profiles\|NO functional social\|Framework/namespace\|UNKNOWN"` | `social` | none | continue; exit 2 is normal |
| 6d | Filter URL scan | `timeout 300 $PY filter-url-scan.py $_LISTINGS` | up to 3 listing URLs from the crawl, else URL; CRAWL_PROXY, CRAWL_UA; SERPAPI_KEY (optional) | `OUT/filterscan.log` | none: set unconditionally | `filterscan` | 300 s | continue; can never show SKIPPED |
| 6c | Pixel consent | `timeout 180 $PY pixel-consent-check.py "$URL"` | URL, CHROME_BIN | `OUT/pixel.log` | none | none (not gated) | 180 s | continue |
| 6a | Hack scan | `hack-scan.py "$J" "$URL"` | `J`, URL, CRAWL_MAXP, CRAWL_PROXY, CRAWL_UA, SERPAPI_KEY (optional) | `OUT/hack.log` | exit code 0 | `hackscan` | none | continue |
| 6e | CMS deep-dive | WordPress only: `timeout 600 $PY cms/wordpress.py "$J" "$URL"` | `<slug>-cms.json`, `J`, CRAWL_UA, CRAWL_PROXY | `OUT/cms-deepdive.log` | WordPress: `grep -q "WORDPRESS DEEP-DIVE"`; any other result: set unconditionally | `cmsdeep` | 600 s (WordPress) | continue |
| 6b | Findings sidecar | `checks-to-findings.py "$SLUG" "$OUT" "$CRAWL_DIR"` | `OUT/{checks,trace,hack,render,pixel,discovery,filterscan,cms-deepdive}.log`, `J` | `$CRAWL_DIR/<slug>-findings.json`; `$CRAWL_DIR/<slug>-operator-findings.json`; `OUT/findings.log` | `grep -q "finding(s)"` | `findings` | none | continue |
| 7 | Build report | `$NODE build-crawl-report.js "$J" "$OUT/site-audit.html" "$NAME"` | `J`, findings sidecar, perf JSONs, CRAWL_PROXY, CRAWL_UA, REPORT_BRAND, CTA_* | `OUT/site-audit.html` (NOT published); optional `OUT/site-audit-raw.json`; `OUT/report.log` | exit code 0 | `report` | none | continue |
| 8 | Link map | `interlink-map.py "$J" "$OUT/linkmap.html" "$NAME"`, then `publish-run.sh "$SLUG" linkmap "$OUT/linkmap.html"` | `J` | `OUT/linkmap.html`; `$REPORTS_DIR/<domain>/linkmap.html`; `OUT/linkmap.log` | exit code 0 of interlink-map | `linkmap` | none | continue |
| 9 | Content attribution | `content-attribution.py $ARGS` | the same ARGS list as stage 4 | `OUT/attribution.log` | exit code 0 | `attribution` | none (120 s per page inside) | continue; the run then ends and the trap fires |

There is no stage 7b in the kit (the website is the only subject).

The pixel check (6c) is the only stage with no DONE key. The gate lists 15 keys; none is for pixel.

---

## 3. Stage details

### Stage 1: Full crawl (`full-extract.py`; its own transport, not `crawl_transport.py`)

- Arguments (argparse at the top of the file): positional `seed`; `--max` (default 1000);
  `--concurrency` (default env `CRAWL_CONCURRENCY` or 6); `--check-external`; `--curl` (default true
  when env `CRAWL_CURL` is set); `--delay` (default env `CRAWL_DELAY` or 0); `--curl-ssh` (default env
  `CRAWL_CURL_SSH`); `--proxy` (default env `CRAWL_PROXY`); `--out` (default
  `$CRAWL_DIR/<host>-full-extract.json`). `--proxy` forces `--curl`. The orchestrator always passes
  `--out`.
- User agent: env `CRAWL_UA`, else `Mozilla/5.0 (compatible; ClientAuditBot/1.0; +SEO audit)`. This
  default is a bot-named UA, unlike the Chrome default the other stages use (`crawl_transport.UA`).
  Some WAFs refuse any bot-named UA, so set `CRAWL_UA` to a browser UA on a walled site.
- Scope: the seed host with a leading `www.` removed, plus any subdomain of it.
- External links: `--check-external` is opt-in in the orchestrator (`CHECK_EXTERNAL=1`). It
  HEAD-checks every external link, and no later stage reads those statuses, so it is off by default.
- Orchestrator proxy handling: when `CRAWL_PROXY` is set, the orchestrator adds
  `--proxy $CRAWL_PROXY --delay ${CRAWL_DELAY:-3} --concurrency ${CRAWL_CONCURRENCY:-2}` (the
  variable `PROXY_ARGS`). Without a proxy, `CRAWL_DELAY` and `CRAWL_CONCURRENCY` still reach the
  crawler through its argparse defaults. Note the `${CRAWL_PROXY:-}` form: the script runs under
  `set -u`, so a bare `$CRAWL_PROXY` would kill the run on every site where it is not exported.
- curl transport details: 4 tries per URL, 429 backoff 5/10/20 s, and a 202 challenge shell (some
  hosts answer 202 with a captcha page) retried with the same backoff. `CRAWL_NO_AE=1` drops
  `--compressed`. A raw gzip body that curl failed to decode aborts the process with exit 3.
- `--curl-ssh` (env `CRAWL_CURL_SSH`): a comma list of exits; the crawler rotates through them round
  robin and runs curl over ssh on each one, and the word `local` means this machine. Use it only when
  you control the other hosts and the target throttles one IP. Leave it unset otherwise.
- Writes: the JSON at `--out` with keys `site` (host without a leading `www.`), `seed`,
  `pages_crawled`, `summary`, `pages`, `external_links`. It prints the summary JSON to stdout and
  `[done] N pages -> path` to stderr.
- SIGTERM (sent by `timeout`) makes it dump what it has with `"partial": true` and exit 124. This
  exists because a paced fallback was once killed at its cap after a few hundred pages and never
  wrote its file (a client site, 2026-09), so every later stage read the blocked first pass.
- Orchestrator checks after pass 1 (`crawl_quality()` prints four numbers: real, blocked, total,
  challenge titles):
  - Page count `N` from `pages`; 0 means FATAL exit 1.
  - Quality guard: `real` = status 200, more than 50 words, not `.xml`; `blocked` = status 429, 403
    or 503; `challenge` = page titles matching captcha, challenge, "checking your browser",
    "just a moment", "security checkpoint" or "access denied". The fallback fires when real < 3, or
    when blocked > 2 AND blocked is at least 10 percent of rows. One deliberate 403 on an admin path
    must not trigger a full re-crawl.
  - The fallback reruns with `--curl --concurrency 1 --delay 2` and no `--check-external`. It sleeps
    1 s first so the file's modification time (1 s resolution) shows a rewrite.
  - If the crawl JSON's modification time did not change, FATAL exit 1 ("curl fallback wrote
    NOTHING"). The hint suggests `CRAWL_CONCURRENCY=1 CRAWL_DELAY=1.2`, a smaller max-pages, or
    `BIG_CRAWL=1`.
  - If the new JSON has `partial`, it prints a PARTIAL CRAWL warning.
  - Tiny-site rule: under 3 real pages, no blocked status, no challenge title and at most 5 rows is a
    small site, not a blocked one. The run continues with a `TINY SITE` warning.
  - Otherwise, under 3 real pages is FATAL exit 4 ("STILL THIN after fallback"), unless `CSR=1`. With
    `CSR=1` it warns and continues: a client-rendered site has near-empty raw HTML, and every
    crawl-based finding must then be checked on rendered pages.
- Not checked: a pass-1 crawl that hit its cap also writes `partial:true`, but the orchestrator checks
  `partial` only inside the fallback branch. A partial pass-1 crawl that passes the quality guard
  flows to every later stage with no warning. Check `partial` in the JSON yourself on a big site.
- SKIPPED: never shown as SKIPPED. Zero pages, or a thin fallback, ends the whole run instead.

### Stage 2: Standing checks (`audit-checks.py`)

- Arguments: `sys.argv[1]` = crawl JSON. Exit 2 when any CRITICAL fires
  (`sys.exit(2 if critical else 0)` at the end of the file), else 0.
- Section index (the printed headers, in run order): 1 CANONICAL TARGET DOMAIN, 2 SELF-CANONICAL,
  2b CANONICAL → REDIRECT, 3 STRUCTURED DATA, 4 SITE SIZE, 5 PLACEHOLDER / DEMO CONTENT,
  6 RESOURCE/LINK HOST MISMATCH, 7 CRAWLABLE AUTH / ACCOUNT ROUTES, 8 PLACEHOLDER / MASS-DUPLICATE
  TITLES, 9 CONTACT INTEGRITY, 10 MALFORMED HREFS, 11 PLACEHOLDER CONTENT IN URL SLUGS / BODY,
  12 HOST DUPLICATION, 13 SITEMAP INTEGRITY, 14 THIN / MASS-DUPLICATE URL PREFIXES, 15 INTERNAL
  LINKS POINTING AT REDIRECTS, 16 STRUCTURED-DATA CONSISTENCY, 17 NOINDEX INVENTORY, 18 PRE-CONSENT
  TRACKING EXPOSURE, 20 BRAND TRANSITION / PREVIOUS-DOMAIN EQUITY, 19 LANGUAGE VARIANTS / HREFLANG,
  21 IDENTICAL-CONTENT PAGE SETS, 22 RESIDUE SLUGS / SLUG-CONTENT MISMATCH, 23 LEAD CAPTURE,
  25 VISUAL CONTENT, 26 SECURITY HEADERS, 27 END-OF-LIFE SERVER SOFTWARE, 28 SOFT-404 BEHAVIOUR,
  24 STORE INVENTORY. That is 28 numbered checks plus 2b. 20 prints before 19, and 24 prints last.
  `docs/03-CHECKS-AND-FALSE-POSITIVES.md` explains each check.
- Transport: its own, not `crawl_transport.py`. UA from `CRAWL_UA`, else a Chrome 131 UA. A global
  opener adds a gzip decoder and a `CRAWL_PROXY` handler. Any private opener a check builds must add
  the proxy handler itself, or that check bypasses the proxy.
- Live probe gate: one homepage fetch with a 6 s timeout; if it fails or takes 4 s or more,
  `LIVE_OK=False` and the live re-probes inside the checks are skipped.
- Writes no file. Everything goes to stdout, which the orchestrator saves to `OUT/checks.log`.
- Gate marker: `CANONICAL TARGET DOMAIN`. That header is check 1. A crash anywhere in checks 2 to 28
  still leaves the marker in the log, so the gate shows `checks` as done. Read the end of
  `checks.log` for a traceback.
- Critical count: the orchestrator counts lines with the red flag or siren emoji in `checks.log` and
  warns that each must reach the report.
- Why the gate greps output and not the exit code: the script exits 2 by design when it finds
  criticals, so an exit-code gate would mark the dirtiest sites as "checks SKIPPED".

### Stage 2b: CMS detection (`detect-cms.py`)

- Arguments: `argv[1]` crawl JSON, optional `argv[2]` homepage URL.
- Transport: its own. UA from `CRAWL_UA` else Chrome 131; a global proxy opener from `CRAWL_PROXY`.
- Verdict rule: the top score must be at least 4 and higher than the runner-up, else `ambiguous` (a
  tie) or `unknown`. Only `wordpress` has a module (`HAS_MODULE = {"wordpress"}`). The docstring
  draws a `cms/shopify.py (stub)` branch, but `cms/` holds only `wordpress.py`.
- Writes: `<slug>-cms.json` in the same folder as the crawl JSON, where `<slug>` is the crawl file
  name with `-full.json` removed.
- Stdout: `PLATFORM: <name>  (confidence N)` and `DEEP-DIVE MODULE: ...`. The orchestrator echoes
  both lines.
- Gate marker: `PLATFORM:`.

### Stage 3: Broken-link trace (`trace-errors.py`)

- Arguments: `crawl`, optional `--all` (include 3xx). The orchestrator never passes `--all`.
- Bad statuses: 401, 403, 404, 410 and 5xx. Prints `[status] url` then `linked from:` lines, or
  `No non-200 errors in this crawl.`
- Writes no file. No network.
- The orchestrator prints a count of lines starting `[40` or `[50`. That count includes 401 and 403,
  which the builder later treats as "could not check", not as broken (see 02, section 4).
- Gate: exit code 0. The script has no explicit non-zero exit, so only a crash marks it SKIPPED.

### Stage 4: Two-layer render check (`render-check.py`)

- Arguments: any number of URLs; `#fragments` are stripped and words starting `--` are dropped.
- URL list built by the orchestrator (`ARGS`): the homepage, then `SVC` and `DEEP` if given, then up
  to two 200-status, non-redirected, HTML, 0-word pages from different first path segments. The
  auto-sample exists because operators tend to pick static pages; on one client site (2026-09) three
  hand-picked pages rendered fine while 165 client-rendered pages shipped as "missing title/H1/meta"
  instead of one "empty until JavaScript" finding. Stage 9 reuses the same `ARGS`.
- Transport: raw HTML by urllib, falling back to curl with `-x $CRAWL_PROXY`. The rendered DOM and
  screenshots come from headless Chrome at `kit_env.chrome_bin(required=True)`. Chrome gets
  `--proxy-server` only when the proxy has no `user:pass@`, because Chrome cannot use an
  authenticated proxy without a prompt. With an authenticated proxy it prints
  `RENDER TRANSPORT MISMATCH`. The UA is a fixed Chrome 126 string; `CRAWL_UA` is not read here.
- Per page it prints `CONTENT: SSR|PARTIAL|CSR` and `VISUAL: OK|GATED|?`. A page whose render has
  under 25 words while the raw HTML has over 100 prints `RENDER BLOCKED` and no CONTENT line.
- Writes: `render-<urlslug>-nojs.png` and `render-<urlslug>-js.png` in the current folder, which is
  `KIT_HOME` (`shoot_pair()` uses `os.path.abspath`). They are never cleaned up and pile up in the repo
  root across runs; they are not under `KIT_WORK`. The stripped no-JS HTML goes to `$KIT_WORK/tmp/`
  and is deleted.
- Screenshots retry once on a Chrome hang (`screenshot()` catches `TimeoutExpired`).
- Exit codes: 0 clean, 2 any CSR or blocked page, 3 visually gated. The orchestrator ignores the exit
  code.
- Gate marker: `CONTENT:`. One good page out of several is enough. If every page is RENDER BLOCKED,
  the stage shows SKIPPED.
- Warns when no service URL was given ("only 1 page checked").

### Stage 5: Lighthouse (`perf-audit.sh`)

- Arguments: `<url> [profile] [outdir]`. Profile `mobile-4g` is the default; `all` runs mobile-3g,
  mobile-4g and desktop and prints a comparison table. `outdir` defaults to `$CRAWL_DIR`; the
  orchestrator passes it.
- Tools: it sources `kit-paths.sh`, refuses (exit 3) without an executable `CHROME_BIN`, sets
  `CHROME_PATH` to it, and runs `$KIT_HOME/node_modules/.bin/lighthouse` (or a `lighthouse` on
  `PATH`, with a warning; none at all is exit 3).
- File key: `SLUG` inside perf-audit.sh is the host with scheme and path removed, so it keeps `www.`.
  Output: `<outdir>/<HOST>-<tier>.json`.
- No proxy and no `CRAWL_UA`: Lighthouse runs from this machine's own IP. On a WAF host the result can
  be a challenge page; see the challenge-marker guard below.
- Stdout in `all` mode: a table with `score=` per tier, or `BLOCKED` when the score is null. The words
  `Performance score` are printed only by the single-profile `detail()` path, which the orchestrator
  does not use (it passes `all`).
- Before the run (slug-namespace guard): it copies `<HOST>-<tier>.json` and
  `<HOST without www>-<tier>.json` to `<slug>-<basename>` when that name does not exist yet. These
  copies hold the PREVIOUS run's data under the CURRENT slug name, so an earlier run's evidence
  survives. The loop matches only names that start with the host. An earlier wildcard version also
  matched its own snapshots and produced `<slug>-<slug>-<host>-...` files that grew on every run.
- After the run: for `<HOST>-<tier>.json` and `<slug>-<HOST>-<tier>.json` it creates a `www`-less
  symlink if missing, because perf-audit names files by the FETCHED host while the builder looks up
  the crawl's `site` field (no `www.`).
- `SKIP_PERF=1`: no Lighthouse; it appends a note to `perf.log` with `tee -a`. Because it appends, any
  old `perf.log` content stays.
- Gate: `DONE[perf]` is set when a perf JSON for this host (`<host>-desktop.json` or
  `<host>-mobile-4g.json`, with or without `www.`) has a non-null performance score AND was modified
  after `OUT/.run-start`, or when `perf.log` contains `Performance score`. A JSON that exists but
  carries `runtimeError` and `score: null` measured nothing and does not pass. With `SKIP_PERF=1` the
  reused files are older than `.run-start`, so `perf` shows SKIPPED. That is correct; declare the
  skip in the sidecar's `not_measured` before publishing (section 9).
- Challenge-marker guard: right after stage 5 it greps every `OUT/*.log` for WAF markers
  (`sg-captcha`, `Robot Challenge`, `Checking your browser`, `cf-chl`, `challenge-platform`,
  `Just a moment`, `x-vercel-mitigated`, `Vercel Security Checkpoint`) and prints a warning. It sets
  or clears no DONE key. It can also match logs left in the case folder by a previous run of the same
  slug, because later stages have not overwritten them yet. The lesson behind it: on one WordPress
  host (2026-08) a 202 challenge screen produced plausible CLS, CSR and "no sitemap" results for a
  whole run. The gate proves stages ran; it cannot prove they measured the site.

### Stage 5b: URL discovery (`url-discovery.py`)

- Arguments: `argv[1]` domain or URL, optional `argv[2]` crawl JSON.
- Transport: imports `crawl_transport`, so `CRAWL_PROXY` and `CRAWL_UA` apply. Channels: certificate
  transparency logs (crt.sh), the Wayback CDX endpoint `web.archive.org/cdx/search/cdx`, the CMS
  API, sitemaps and a hack-path probe. The CDX endpoint has been unreliable; when it fails, that
  channel returns nothing (see `docs/KNOWN-PITFALLS.md`, discovery channels).
- Writes: `<slug>-discovery.json` next to the crawl JSON when the crawl path ends in `-full.json`,
  else `$CRAWL_DIR/url-discovery.json`.
- Stdout: `DISCOVERY DIFF: ... NOT REACHED BY THE CRAWL`, or `DISCOVERY TOTAL:` when no crawl JSON
  exists. The orchestrator echoes alarm lines.
- Gate marker: `DISCOVERY`. It prints only at the very end, so a timeout or crash shows SKIPPED.
- Why it exists: a link crawl reaches only linked pages. Injected, orphaned and staging URLs are
  invisible to it. On one client site the path probe found a spam page with hundreds of pharma terms
  and two undocumented staging folders that no crawl, sitemap or CMS API listed.

### Stage 6: Social scan (`social-scan.py`)

- Argument: one domain or URL. The orchestrator passes `${URL#https://}`; an `http://` URL passes
  unchanged and still works, because `scan()` keeps any string that starts with `http`.
- Fetches `/`, `/contact`, `/about` with `curl -4 -sL --compressed --max-time 25`, adding the proxy
  and UA from the environment.
- Stdout: `REAL profiles (sameAs-eligible):`, `NO functional social profiles found`,
  `Framework/namespace noise`, or `UNKNOWN` when the fetch hit a challenge or a body under 2000 bytes.
  Exit 2 only for a real negative.
- Gate marker: any of the three result strings or `UNKNOWN`. So a blocked fetch now counts as done:
  the stage ran and honestly reported that it could not measure. Read `social.log` when you need the
  answer.
- Output reaches nobody downstream: neither checks-to-findings.py nor build-crawl-report.js reads
  `social.log`.
- `entity_match_warn()` is defined after the `__main__` block and is never called by the script.

### Stage 6d: Filter URL scan (`filter-url-scan.py`)

- Arguments: listing URLs. The orchestrator picks up to three 200-status crawl URLs ending in
  `/blog`, `/shop`, `/products`, `/collections`, `/articles`, `/news`, `/store`; with none it passes
  the homepage. Listing pages often build `?param=` filter URLs in JavaScript, which a raw crawl
  cannot see.
- Transport: imports `crawl_transport`; the puppeteer render (a temp node script under
  `$KIT_WORK/tmp/`, with puppeteer from `node_modules` and Chrome from `CHROME_BIN`) gets `SCAN_UA` =
  the transport UA.
- Index check: after `main()` it always runs `index_check()`, even when no filters were found. With
  `SERPAPI_KEY` set (environment variable, optional; `SERP_API_KEY` also works) it queries
  `site:<host> "<family>"` for five or more parameter families, up to two pages each, so every audit
  spends search credits. Without a key it prints `INDEX-CHECK: skipped (no SerpAPI key)` and the
  result is UNKNOWN, never "clean".
- Stdout lines the sidecar reads: `FILTER-COMBOS`, `FILTER-VERDICT`, `INDEX-CHECK`, `INDEXED-PARAMS`.
- Writes no file (apart from the temp script).
- Gate: none. `DONE[filterscan]=1` is set unconditionally. It can never show SKIPPED.

### Stage 6c: Pre-consent pixel check (`pixel-consent-check.py`)

- Arguments: one or more URLs. The orchestrator passes the homepage only.
- Uses puppeteer from `node_modules` and Chrome from `kit_env.chrome_bin(required=True)`. It reads no
  `CRAWL_*` variable, so it never uses the proxy.
- Exit 2 when a tracker fired. The orchestrator ignores it.
- Writes no file (a temp script under `$KIT_WORK/tmp/`). `pixel.log` is read later by the sidecar.
- Not gated: no DONE key. A bot-walled site makes this fail quietly.
- Publishing: the default rules file forbids pre-consent tracking wording in a published page, and
  checks-to-findings.py moves any consent or tracker finding to the operator-only file (stage 6b).
  So this stage informs the operator; its finding does not reach the client page. Edit the rules
  file if you want it published.

### Stage 6a: Hack / index-bloat scan (`hack-scan.py`)

- Arguments: `argv[1]` crawl JSON, `argv[2]` homepage URL. Module-level script, no `main()`.
- Transport: imports `crawl_transport`, which installs a global proxy opener. Its own `get()` sends
  the UA `Mozilla/5.0`. SerpAPI calls go through that same `get()`, so with `CRAWL_PROXY` set, the
  SerpAPI traffic goes through the proxy too.
- Uses `CRAWL_MAXP`: when the crawl has at least that many rows and the sitemap is bigger, it skips
  the orphan test and prints `SAMPLE CRAWL`. On a capped crawl, "nothing links here" is a property of
  the sample, not of the site.
- SerpAPI: with `SERPAPI_KEY` set (environment variable, optional), one `site:<apex>` query (always
  the apex, never `www.`, which would scope the query to one subdomain) plus three probe queries.
  Without it the index comparison is skipped.
- Writes no file. The orchestrator echoes alarm lines.
- Gate: exit code 0. The script has no explicit non-zero exit, so only a crash (for example a bad
  `argv`) shows SKIPPED.
- Order: it must run before 6b, or a detected hack never reaches the report. It once ran after the
  report build, and a site with two hack alarms in `hack.log` shipped a report that mentioned neither.
  Never move it after 6b.

### Stage 6e: CMS deep-dive dispatch

- Reads `platform` from `$CRAWL_DIR/<slug>-cms.json`.
- `wordpress`: runs `cms/wordpress.py <crawl.json> <url>` with a 600 s cap. The module reads
  `CRAWL_UA` and `CRAWL_PROXY` itself. It prints `=== WORDPRESS DEEP-DIVE ===` near the top and
  `WORDPRESS DEEP-DIVE COMPLETE` at the end. The gate greps `WORDPRESS DEEP-DIVE`, which the header
  already matches, so a crash after the header still counts as done.
- Empty, `unknown`, `ambiguous`, or any other platform: prints a note and sets `DONE[cmsdeep]=1`
  ("correctly skipped, not failed").
- Output: `OUT/cms-deepdive.log`, read by the sidecar.
- Must precede 6b so the deep-dive flags reach the sidecar.

### Stage 6b: Findings sidecar (`checks-to-findings.py`)

- Arguments: `<slug> [audit-dir] [reports-dir]`. Defaults: `$KIT_WORK/cases/<slug>` and
  `$KIT_WORK/crawl` (the script finds `KIT_HOME` from its own location and reads `KIT_WORK` from the
  environment). The orchestrator passes all three.
- Reads from the case folder: `checks.log`, `trace.log`, `hack.log`, `render.log`, `pixel.log`,
  `discovery.log`, `filterscan.log`, `cms-deepdive.log` (all through one `read()` helper). It reads
  the crawl JSON many times and imports `crawl_transport`, so it makes live requests (broken-link
  re-checks).
- Render poisoning guards: challenge markers in `render.log`, a 0 percent raw verdict on a site the
  crawl read fine, and the zero-links-zero-headings shape all mark the render verdict as untrusted.
- Writes ONE client sidecar, `<reports-dir>/<slug>-findings.json`, named by the run slug, with keys
  `findings`, `perf_note` and `could_not_check`. It also writes `<slug>-operator-findings.json`
  (operator-only findings, including any consent or tracker finding, and the checks that could not be
  measured on a client-rendered site). The older host-named second copy is gone; see 02, section 1.
- Stdout: `<path>: N finding(s) (M critical)` then the top `[severity] title` lines.
- Gate marker: `finding(s)`. It prints only after the write, so a crash shows SKIPPED.
- Full detail of the file and its schema: `docs/02-BUILDER-AND-SIDECAR.md`.

### Stage 7: Build report (`build-crawl-report.js`)

- Arguments: `<full-extract.json> <out.html|out.md> [Site Name]`. Exit 1 when arguments are missing;
  exit 2 when the output does not end in `.html` or `.md`, the input does not end in `.json`, or the
  input is missing; exit 3 when the sidecar is not valid JSON or a finding has an unknown severity.
- The orchestrator builds into the CASE FOLDER only: `OUT/site-audit.html`. Nothing reaches
  `$REPORTS_DIR` on this path until the findings are verified and `publish-run.sh` runs (section 9).
  `audit.sh` is the exception (section 10).
- Sidecar choice, perf lookup, live re-probes, schema and defects: `docs/02-BUILDER-AND-SIDECAR.md`.
- Writes: the HTML (or Markdown) at the output path and, for HTML, when the crawl has over 60 pages
  or over 2 MB, `<out-name>-raw.json` next to it. The last stdout line starts `Wrote <path>`.
- Gate: exit code 0.
- What the report cannot contain: stages 8 and 9 run after it, and the builder reads no `linkmap`,
  `attribution`, `social`, `-cms.json` or `-discovery.json` file. Those results reach the reader only
  through other files (the link map page) or by hand.

### Stage 8: Link map (`interlink-map.py`)

- Arguments: three required positionals, `src`, `out`, `name`. Missing one is an `IndexError`.
- Writes the HTML at `out` (the orchestrator passes `OUT/linkmap.html`) and prints
  `link map -> <out> (N nodes, M contextual edges)`.
- Gate: exit code 0 of interlink-map. On success the orchestrator also runs
  `publish-run.sh <slug> linkmap OUT/linkmap.html`, which copies it to
  `$REPORTS_DIR/<domain>/linkmap.html`. The link map needs no verification. A publish refusal is
  logged in `linkmap.log` and does not clear the key.

### Stage 9: Content attribution (`content-attribution.py`)

- Arguments: URLs. It gets the stage 4 `ARGS` list, including the auto-added 0-word pages.
- Raw HTML via `crawl_transport.fetch`; a non-200 prints `UNKNOWN` to stderr and skips that URL.
  Render via a temp node puppeteer script (under `$KIT_WORK/tmp/`) with a 120 s `subprocess.run`
  timeout. A `TimeoutExpired` there is not caught, so one slow page ends the script with a traceback
  and the stage shows SKIPPED. An unparseable render prints `RENDER FAILED` and moves on.
- Stdout lines echoed by the orchestrator: `JS-ONLY DELTA`, `JS-only container`, `IFRAME`.
- Writes no file (the temp node script is deleted at the end).
- Gate: exit code 0.

---

## 4. The completion gate

The gate function, as it stands in RFA (the file listing at its end is shortened here):

```
gate(){
echo; echo "===== AUDIT COMPLETION GATE ====="
MISS=0
for s in crawl checks cms cmsdeep trace findings render perf social report linkmap attribution hackscan discovery filterscan; do
  if [ -n "${DONE[$s]:-}" ]; then echo "  ✓ $s"; else echo "  ✗ $s  — SKIPPED"; MISS=$((MISS+1)); fi
done
if [ "$MISS" -eq 0 ]; then echo "ALL STEPS COMPLETE"; else
  echo "INCOMPLETE — $MISS step(s) skipped, DO NOT report as done"; fi
# criticals present != incomplete: report them separately so they are never conflated
_C=$(cat "$OUT"/checks.log "$OUT"/hack.log 2>&1 | grep -c "🚩\|🚨" || true)
[ "${_C:-0}" -gt 0 ] && echo "NOTE: ${_C} CRITICAL finding(s) in the logs — verify each reached the report"
GATE_MISS=$MISS
echo; echo "===== FILES WRITTEN BY THIS RUN ====="
...   # find: files in OUT, CRAWL_DIR (<slug>-*, <host>-*) and REPORTS_DIR/<domain> newer than .run-start
}
trap '_rc=$?; gate; [ "$_rc" -ne 0 ] && exit "$_rc"; exit "${GATE_MISS:-0}"' EXIT
```

What it means in practice:

- 15 keys. "SKIPPED" means only "the key was not set". A stage that ran and failed its own test also
  shows SKIPPED.
- Success line: `ALL STEPS COMPLETE`. Anything else prints `INCOMPLETE - N step(s) skipped, DO NOT
  report as done`. `publish-run.sh` reads the last of these two lines in the run log (section 9).
- File list: after the gate, `FILES WRITTEN BY THIS RUN` lists every absolute path the run wrote (by
  modification time against `.run-start`). Use it instead of guessing names.
- Exit code: a fatal stage keeps its own code (1 for a crawl FATAL, 4 for STILL THIN). Otherwise the
  code is the count of missing keys; 0 means complete. A slug or preflight refusal exits 2 with no
  gate, because the trap is not installed yet.
- Critical findings are counted from `checks.log` and `hack.log` only and printed as a NOTE. They do
  not change the exit code. Criticals present is not the same as incomplete.
- How each key is earned, grouped by method:
  - Output marker grep: checks, cms, render, discovery, social, findings, cmsdeep (WordPress).
  - Exit code 0: trace, hackscan, report, linkmap (of interlink-map), attribution.
  - Value check: crawl (page count > 0); perf (a scored perf JSON for this host written during this
    run, or the `Performance score` text).
  - Always set: filterscan; cmsdeep for every non-WordPress result.
- Keys that can pass without this run measuring anything:
  - `filterscan` (set unconditionally).
  - `cmsdeep` for any non-WordPress platform (by design), and for WordPress when it crashes after its
    header.
  - `checks` (the marker is check 1; a crash in a later check still passes).
  - `social` when the scan only printed `UNKNOWN`.
  - `perf` when `SKIP_PERF=1` appended to an old `perf.log` that still contains `Performance score`
    from a single-profile run.
- The gate tells you the stages RAN. It does not tell you they measured the site (see the
  challenge-marker guard in stage 5). The verify step (`verify-audit.py`, `VERIFY-AGENT-BRIEF.md`)
  and the publish gate exist for that.

---

## 5. Data flow (plain text)

```
URL, SLUG, NAME, SVC, DEEP, MAXP
   |
   v
[0] preflight (disk, proxy env, Chrome, Python, Node) ──> OUT/.run-start
[1] full-extract.py ──> $CRAWL_DIR/<slug>-full.json  (J) ───────────────────────────┐
        |                                                                          |
        +── OUT/crawl.log, OUT/crawl-curl.log                                      |
   J ──> [2]  audit-checks.py ──────> OUT/checks.log ───────────────┐              |
   J ──> [2b] detect-cms.py ────────> <slug>-cms.json ──> [6e] dispatch            |
                                   └> OUT/cms.log                    |             |
   J ──> [3]  trace-errors.py ──────> OUT/trace.log ──────────────┐  |             |
   J ──> [4]  ARGS (URL+SVC+DEEP+0-word pages) ──> render-check.py ──> OUT/render.log ─┐
                                   └> render-*.png in KIT_HOME                     |  |
   URL ─> [5] perf-audit.sh ────────> <HOST>-<tier>.json (+ snapshots, symlinks) ──> [7]
   URL+J > [5b] url-discovery.py ───> <slug>-discovery.json (no reader in the pipeline)
                                   └> OUT/discovery.log ────────────────────────────┐ |
   URL ─> [6]  social-scan.py ──────> OUT/social.log (no reader in the pipeline)    | |
   J ──> listing URLs ─> [6d] filter-url-scan.py ─> OUT/filterscan.log ────────────┤ |
   URL ─> [6c] pixel-consent-check.py ──> OUT/pixel.log ───────────────────────────┤ |
   J+URL > [6a] hack-scan.py ───────> OUT/hack.log ────────────────────────────────┤ |
   J+URL > [6e] cms/wordpress.py ───> OUT/cms-deepdive.log ────────────────────────┤ |
                                                                                   v v
   checks, trace, hack, render, pixel, discovery, filterscan, cms-deepdive logs + J
         ──> [6b] checks-to-findings.py ──> $CRAWL_DIR/<slug>-findings.json  (the builder reads this)
                                         └> $CRAWL_DIR/<slug>-operator-findings.json (never published)
   J + <slug>-findings.json + <HOST>-<tier>.json
         ──> [7] build-crawl-report.js ──> OUT/site-audit.html  (+ OUT/site-audit-raw.json on big crawls)
   J ──> [8] interlink-map.py ──> OUT/linkmap.html ──> publish-run.sh linkmap ──> $REPORTS_DIR/<domain>/
   ARGS ─> [9] content-attribution.py ──> OUT/attribution.log (after the report)
   all DONE[] keys + OUT/checks.log + OUT/hack.log ──> completion gate (EXIT trap) + file list

   later, after verification:
   OUT/site-audit.html + OUT/findings-verified.json ──> publish-run.sh report ──> $REPORTS_DIR/<domain>/
```

All `<slug>-*` and `<HOST>-*` JSON files without a folder above live in `$CRAWL_DIR`.

---

## 6. Environment variables

| Variable | Default | Read by | Effect |
|---|---|---|---|
| `KIT_HOME` | the repo root, found from the script's own location | shell scripts (`kit_env.py` ignores the env value) | Where the code lives. |
| `KIT_WORK` | `$KIT_HOME/work` | `kit-paths.sh`, `kit_env.py`, install.sh | Where runs write. |
| `CRAWL_DIR`, `CASES_DIR`, `REPORTS_DIR` | `$KIT_WORK/crawl`, `$KIT_WORK/cases`, `$KIT_HOME/reports` | `kit-paths.sh` (shell only; Python derives them from `KIT_WORK`) | Override single folders for the shell side. Keep them at their defaults unless you also change the Python side. |
| `PY` | `$KIT_HOME/.venv/bin/python`, else `python3` | `kit-paths.sh`, install.sh, audit.sh | Python for every stage. |
| `NODE` | `node` on `PATH` | `kit-paths.sh`, RFA, audit.sh | Node for the builder and the PDF printer. |
| `CHROME_BIN` | first of google-chrome, google-chrome-stable, chromium, chromium-browser on `PATH` | `kit-paths.sh`, `kit_env.py`, every Chrome stage | Chrome binary. `kit-paths.sh` copies it into `CHROME_PATH`. |
| `CHROME_PATH` | set from `CHROME_BIN` | Lighthouse | Chrome for Lighthouse. Do not set it by hand. |
| `CRAWL_PROXY` | unset | RFA (stage 1 args), full-extract.py, crawl_transport.py, audit-checks.py, detect-cms.py, render-check.py, social-scan.py, cms/wordpress.py, build-crawl-report.js | Proxy URL (`http://user:pass@host:port`) for every Python and curl transport. Not read by perf-audit.sh or pixel-consent-check.py. Chrome in render-check uses it only when it has no credentials. |
| `CRAWL_UA` | per file: full-extract uses a bot-named UA; the others a Chrome 131 UA | full-extract.py, crawl_transport.py, audit-checks.py, detect-cms.py, cms/wordpress.py, social-scan.py, build-crawl-report.js | User agent override. Not read by render-check.py (fixed UA), perf-audit.sh, pixel-consent-check.py, or hack-scan's own `get()`. |
| `CRAWL_DELAY` | 3 when the proxy is set (RFA); else 0 (full-extract.py) | RFA, full-extract.py | Seconds before each curl request. Ignored by the aiohttp transport. The fallback always uses `--delay 2`. |
| `CRAWL_CONCURRENCY` | 2 when the proxy is set (RFA); else 6 (full-extract.py) | RFA, full-extract.py | Crawl workers. The fallback always uses 1. There is no variable named plain `CONCURRENCY`. |
| `CRAWL_CURL` | unset | full-extract.py | Any non-empty value forces the curl transport in pass 1. |
| `CRAWL_CURL_SSH` | unset | full-extract.py | Comma list of exits for the curl transport, used round robin over ssh; `local` = this machine. Only for hosts you control. |
| `CRAWL_NO_AE` | unset | full-extract.py | Drop `--compressed` from the curl crawl. |
| `BIG_CRAWL` | 0 | RFA | `1` raises the crawl timeouts to 4 h (pass 1) and 3 h (fallback). |
| `CHECK_EXTERNAL` | 0 | RFA | `1` adds `--check-external` to pass 1. |
| `CRAWL_MAXP` | exported from the 6th argument, default 1000 | hack-scan.py | Lets hack-scan detect a capped sample crawl. |
| `CSR` | 0 | RFA | `1` lets a client-rendered site continue after a thin fallback instead of FATAL exit 4. |
| `SKIP_PERF` | 0 | RFA | `1` skips Lighthouse and appends a note to `perf.log`. |
| `AUDIT_MIN_FREE_GB` | 2 | RFA preflight, run-queue.sh | Disk floor in GB. |
| `SERPAPI_KEY` (or `SERP_API_KEY`) | unset (optional) | hack-scan.py, filter-url-scan.py | Search index checks. No key: those checks print a skipped/UNKNOWN line. |
| `SCAN_UA` | set internally to the transport UA | filter-url-scan.py | Internal, not an operator knob. |
| `REPORT_BRAND` | unset (footer reads "Site audit") | build-crawl-report.js | Footer text, and the name in the call box. |
| `CTA_URL`, `CTA_TEL`, `CTA_TEL_TXT` | unset | build-crawl-report.js | `CTA_URL` turns on the "Book a call" box; `CTA_TEL` adds a phone button; `CTA_TEL_TXT` is its label. |
| `KIT_RULES` | `$KIT_HOME/report-rules.json` | publish-run.sh | The rules file. |
| `PUBLISH_DOMAIN` | from the crawl JSON | publish-run.sh | The `<domain>` folder to publish into. |
| `PUBLISH_BACKUP_DIR` | `$KIT_WORK/publish-backups` | publish-run.sh | Where overwritten published files are copied first. |
| `PUBLISH_REASON` | unset | publish-run.sh | Required for `kind=correction`. |
| `AUDIT_TIMEOUT` | 1800 | audit.sh | Seconds for the whole `audit.sh` run. |

---

## 7. File-naming rules (slug vs host)

- Run slug files (the second CLI argument):
  - `$CRAWL_DIR/<slug>-full.json`
  - `$CRAWL_DIR/<slug>-cms.json` (named from the crawl file name)
  - `$CRAWL_DIR/<slug>-discovery.json`
  - `$CRAWL_DIR/<slug>-findings.json` and `<slug>-operator-findings.json`
  - `$CASES_DIR/<slug>/*.log`, `site-audit.html`, `site-audit-raw.json`, `linkmap.html`,
    `findings-verified.json`, `evidence/`
- Host files:
  - `$CRAWL_DIR/<HOST>-<tier>.json`, where HOST keeps `www.`. The orchestrator adds a `www`-less
    symlink.
  - `$CRAWL_DIR/<site>-findings.json` (host without `www.`): no longer written by
    checks-to-findings.py, but the builder still falls back to it when `<slug>-findings.json` is
    missing. One left from an older tool version is a trap (02, section 1).
- Mixed: `$CRAWL_DIR/<slug>-<HOST>-<tier>.json`, snapshots of the previous run's perf files.
- Published: `$REPORTS_DIR/<domain>/`, where `<domain>` is the host without `www.`. `publish-run.sh`
  takes it from its 4th argument, else `PUBLISH_DOMAIN`, else the crawl JSON's `site` field (or its
  first page URL), else the slug itself.
- `audit.sh` slugs: the domain with every character outside `a-z 0-9` replaced by `-`
  (`example.com` becomes `example-com`).
- Builder precedence: `const slug = fs.existsSync(path.join(dir, `${runSlug}-findings.json`)) ? runSlug : D.site;`
  in build-crawl-report.js, with `runSlug` from the input file name. So `<run-slug>-findings.json`
  wins over `<host>-findings.json`.
- Collision risk: host-keyed files (the perf JSONs) are shared by every run of the same domain. Two
  concurrent audits of the same host overwrite each other's perf files. `run-queue.sh` takes a
  per-slug lock, which does not cover two different slugs for one host. See `docs/KNOWN-PITFALLS.md`
  (concurrent audits sharing state).

---

## 8. Where the docs and the code disagree

- detect-cms.py's docstring draws a `cms/shopify.py (stub)` branch; the file does not exist.
- RFA's comment above `SKIP_PERF` says the gate "still requires them to EXIST, so a first-ever
  audit cannot silently skip measurement". With the run-start check, reused files do not pass at all;
  the comment understates the gate.
- The stage-4 banner says "3 pages", but the list can hold up to five (URL, SVC, DEEP and two
  auto-picked 0-word pages).
- Only url-discovery, filter-url-scan, hack-scan, content-attribution and checks-to-findings import
  `crawl_transport.py`. full-extract, audit-checks, detect-cms, cms/wordpress, render-check,
  social-scan and build-crawl-report have their own proxy code; perf-audit.sh and
  pixel-consent-check.py have none. Any statement that "all transport goes through
  crawl_transport.py" is wrong.
- The RFA preflight error text says "Install Node 18+", while `install.sh` and `audit.sh` require
  Node 20 or newer. Use 20+.
- `audit.sh` checks its rules warnings against `owner-rules.json`, while `publish-run.sh` reads
  `report-rules.json` first (section 10).

---

## 9. After the run: verify, then publish

On the `run-full-audit.sh` path the orchestrator never publishes the report. The sequence is:

1. Run: `run-full-audit.sh ...` (or `run-queue.sh`). Read the gate and the file list.
2. Verify: `verify-audit.py <slug>` gathers evidence from the crawl JSON and a bounded set of live
   requests and writes `$CASES_DIR/<slug>/evidence/evidence.json` and `evidence.md`. A section that
   could not be fetched says NOT MEASURED, never zero. Exit 5 when the homepage could not be
   measured. The verifier (person or agent, `VERIFY-AGENT-BRIEF.md`) then writes
   `$CASES_DIR/<slug>/findings-verified.json`.
3. Publish: `publish-run.sh <slug> <kind> <source.html> [domain]`.

`publish-run.sh` kinds: `report` and `email` publish as `site-audit.html`, `detailed` as
`site-audit-detailed.html`, `linkmap` as `linkmap.html`, and `correction` replaces an already
published `site-audit.html` with a text fix (needs `PUBLISH_REASON`, logged to
`<case>/CORRECTIONS.log`). The target folder is `$REPORTS_DIR/<domain>/`. Publishing only copies
files into that folder; it never touches a web server.

It refuses (exit 3) when:

- the slug or the domain has characters outside `a-z 0-9 . _ -`;
- the source file is not inside `$CASES_DIR/<slug>/` (no publishing one site's file under another
  slug);
- for report, email and detailed: `findings-verified.json` is missing in the case folder;
- for report, email and detailed: the newest `run*.log` in the case folder ends `INCOMPLETE` and a
  skipped key is not declared in the sidecar's `not_measured` list (`[{"step","what","why"}]`; the
  builder shows that list to the reader as "What we could not check");
- the rules file is missing or not valid JSON, `page_forbidden` patterns match anywhere in the page,
  or `finding_forbidden` patterns match finding text (titles, walk-through steps, bodies);
- a "goes nowhere / does nothing / dead link" finding has no `click-test.js` result in
  `<case>/evidence/` with a "does nothing" verdict;
- the target exists and was stamped by an earlier publish with a DIFFERENT kind (a builder page must
  not overwrite an approved email page).

Every overwrite is backed up first to `$KIT_WORK/publish-backups/<domain>/` with a UTC timestamp.
Nothing is deleted. The published file gets a trailing HTML comment
`published-by: publish-run.sh kind=<kind> slug=<slug> src_sha256=<16 hex> at=<UTC>`.

---

## 10. `audit.sh`: the one-command path

`./audit.sh https://example.com [--name "Example"] [--csr] [--proxy URL]`

- `--name` is the display name (default: the domain). `--csr` sets `CSR=1`. `--proxy` sets
  `CRAWL_PROXY`.
- Derives `DOMAIN` (host without `www.`, lower case) and `SLUG` (the domain with non `a-z 0-9`
  characters replaced by `-`).
- Preflight (exit 3): Python packages `aiohttp`, `bs4`, `yarl`; Node 20 or newer; `puppeteer-core`
  loadable from the kit; a working `CHROME_BIN` (read from `$KIT_WORK/chrome-bin` when unset);
  `run-full-audit.sh` present. A missing `lighthouse` is only a warning (performance will be NOT
  MEASURED).
- Removes its own five output files from an earlier run in `$REPORTS_DIR/<domain>/` first, so old
  and new results never mix.
- Runs `run-full-audit.sh "$URL" "$SLUG" "$NAME"` with stdin from the empty file,
  and `timeout --kill-after=30 $AUDIT_TIMEOUT` (default 1800 s). Everything goes to
  `$REPORTS_DIR/<domain>/run.log`.
- Exit 4 when the crawl JSON is missing, older than this run, or has 0 pages.
- Builds `site-audit.html` itself if the pipeline did not, then copies it from the case folder;
  builds `report.md` with the builder's Markdown mode (falling back to a text conversion of the
  HTML); copies the sidecar as `findings.json` (or writes an empty one with a note); copies
  `linkmap.html` when it was built; prints `report.pdf` with `report-pdf.js`. Any missing report file
  is exit 5.
- Prints `AUDIT SUMMARY`: pages, findings by severity, the output paths, a `NOT MEASURED` list (every
  SKIPPED gate key, the sidecar's `not_measured` and `could_not_check`, CSR checks, a partial crawl,
  a timeout) and rules-file warnings.
- There is no publish gate on this path. The summary ends "Unverified: no person or verify agent
  checked these findings." Treat the output as a draft until someone verifies it.
- Exit codes: 0 ok, 2 bad arguments, 3 preflight, 4 crawl failed, 5 report failed.
