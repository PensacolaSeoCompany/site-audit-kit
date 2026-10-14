# site-audit-kit

A technical SEO audit of one website, end to end, from one command. Built to be run by autonomous agents
on a fresh Ubuntu or Debian machine with no human help. MIT licensed.

    ./install.sh
    ./audit.sh https://example.com

You get a client-ready HTML report, the same report as Markdown and PDF, the findings as JSON, and an
internal link map, in `reports/example.com/`.

Read [TERMS.md](TERMS.md) before you run it: what the report is, what it is not, and who decides what to change.

## What it checks

It crawls the site (up to 500 pages by default, 2 requests at a time, 1 s apart, robots.txt obeyed), then runs these stages. Each stage must report that it
ran; a completion gate at the end lists any stage that did not.

- **Full crawl**: status codes, redirects, canonicals, titles, headings, images, word counts, internal
  and external links, duplicate content (simhash), internal PageRank. Bot walls trigger a slower,
  gentler retry.
- **Standing checks** (`audit-checks.py`, 28 numbered checks): canonical points off-site or to a redirect;
  structured data (missing, inconsistent, self-awarded star ratings); thin site; placeholder or demo
  content and template titles; links on the wrong host (www vs non-www); crawlable login and account
  pages; contact consistency (phone numbers and emails across pages); malformed links that go nowhere;
  apex and www both serving the site; sitemap junk; index bloat (mass near-identical pages); internal links
  that redirect; noindex inventory; tracking that fires before consent (operator-only, never in the
  client report); language variants and hreflang; old-domain equity after a rebrand; identical-content page
  sets; leftover slugs that do not match the content; no lead-capture form; no images; missing security
  headers; end-of-life server software; soft-404s; online store with no live products.
- **CMS detection** and a platform deep-dive (WordPress today: users, REST API, plugins, pagination).
- **Broken-link trace**: every broken target with the pages that link to it.
- **Render check**: raw HTML vs JavaScript-rendered content on 3 to 5 pages, to catch client-rendered
  (CSR) sites whose pages are empty shells without JavaScript.
- **Lighthouse**: mobile 3G, mobile 4G and desktop performance (LCP, CLS, TBT, FCP).
- **URL discovery**: URLs a link crawl cannot reach (sitemaps, robots.txt, web archive samples).
- **Social profile scan**, **filter/facet URL scan** (parameter URL spaces), **hacked-content / index-bloat
  scan**, **link map** (anchor inventory and link graph), **content attribution** (which words need JavaScript).

## What you get

`reports/<domain>/` (domain = the host without `www.`):

| File | What it is |
|---|---|
| `site-audit.html` | The report: findings by severity, each with what we found, why it matters and the fix; "What we could not check"; performance table. |
| `report.md` | The same findings as Markdown. |
| `report.pdf` | The HTML report printed to PDF (all sections expanded). |
| `findings.json` | Machine-readable findings (see below). |
| `linkmap.html` | Interactive internal link map. |

Working files (crawl JSON, Lighthouse JSON, stage logs, evidence) go to `work/` (`KIT_WORK`), which is
gitignored: `work/crawl/` and `work/cases/<slug>/`.

`run.log` (the full pipeline log) is written next to them.

`findings.json` has `findings` (a list of `{severity, title, detail, urls}`; severity is critical, high,
medium, low or note) and `could_not_check` (linked addresses the site refused to show us). After a verify
pass it can also carry `not_measured` (stages that did not run, each `{step, what, why}`). The HTML report
adds a few findings of its own from the crawl table (H1, meta, share image, canonical), so the HTML can
list more findings than the JSON.

## Install

    git clone <this repo> site-audit-kit && cd site-audit-kit
    ./install.sh             # default, no root: Python venv, npm packages; Node 22 into .node/ if system
                             # node is missing or older than 20; Chrome for Testing into .chrome/ if no Chrome
    ./install.sh --system    # optional: also apt packages + system Chrome (needs root or sudo)
    ./doctor.sh              # version table and self-check only, changes nothing (= ./install.sh --check)

It ends with `SELF-CHECK OK` (exit 0). Exit 1 = a self-check item failed, 2 = bad option, 3 = an
install step failed. Supported: Ubuntu 22.04/24.04, Debian 12. Needs Python 3.10+ (Node and Chrome are
fetched into the kit folder when missing).

Chrome is not pinned: the kit uses the system Chrome (or `CHROME_BIN`) if one is present, else it
downloads a pinned Chrome for Testing build into `.chrome/` inside the kit. `./doctor.sh` shows which one is used.

## Run

    ./audit.sh https://example.com [--name "Example Co"] [--csr] [--proxy URL]

- `--name`: the name shown in the report (default: the domain).
- `--csr`: the site is client-rendered (JavaScript builds the page); keep going on a thin raw crawl.
- `--proxy`: send the crawl of the audited site through this proxy (sets `CRAWL_PROXY`).

Use the URL the site really answers on (follow any http to https or www redirect first). One run takes
roughly 10 to 40 minutes, mostly crawl and Lighthouse; `AUDIT_TIMEOUT` (default 1800 s) caps it. The
pipeline output goes to `reports/<domain>/run.log`; the screen shows a summary:

    ===== AUDIT SUMMARY =====
    site:      example.com
    pages:     142 crawled (138 OK 200)
    findings:  1 critical, 4 high, 9 medium, 3 low
    outputs:   ...
    NOT MEASURED:
      - pipeline step "perf" did not complete
    Unverified: no person or verify agent checked these findings.

Exit codes: 0 complete report written, 2 bad arguments, 3 did not start (preflight failed: run
`./doctor.sh`, then `./install.sh`), 4 crawl failed (no pages), 5 a report file was not built,
6 PARTIAL report written (something was blocked, skipped or timed out; the reasons are on the first line
of report.md).

The stage orchestrator can also be run directly:

    ./run-full-audit.sh <url> <slug> "<Business Name>" [service-page-url] [deep-page-url]

## Batch mode

    ./run-queue.sh add "slug|https://example.com/|Example Co|" "slug2|https://example.org/|Example Org|CSR"
    ./run-queue.sh add --file sites.txt      # one "slug|url|name|flags" line per site
    ./run-queue.sh work                      # 1 worker; "work 2" for two (max 2); --stay to wait for new jobs
    ./run-queue.sh status                    # counts, running jobs, stale heartbeats
    ./run-queue.sh stop                      # each worker finishes its current job, then exits
    ./run-queue.sh requeue <job file>        # put a dead worker's job back in pending

The same site never runs twice at once. Finished jobs go to `queue/done/` or `queue/failed/` with the
return code and the run log path. Before each job a disk gate checks free space (`AUDIT_MIN_FREE_GB`);
below it the job waits and the heartbeat says `WAITING-DISK`. The queue lives in `queue/` (`QUEUE_DIR` to move it); `QUEUE_WORKERS` sets the worker count: default 1, at most 2 on a shared machine (higher only with
`QUEUE_WORKERS_UNSAFE=1`, which loads the audited sites and this machine harder).

Each queue job runs `run-full-audit.sh`, not `audit.sh`: the report is built in `work/cases/<slug>/`
(`site-audit.html`, `linkmap.html`, `run-<UTC>.log`) and nothing is copied to `reports/` until you verify it
and run `./publish-run.sh <slug> report work/cases/<slug>/site-audit.html`. For unverified
`reports/<domain>/` output from many sites, call `./audit.sh` once per site instead.

## Environment variables

All optional. A `config.env` file in the repo root (plain `KEY=value` lines) is read too; values already in
the environment win.

| Variable | Default | Effect |
|---|---|---|
| `KIT_HOME` | repo root | Where the kit lives. |
| `KIT_WORK` | `$KIT_HOME/work` | Crawl data, logs, evidence. |
| `REPORTS_DIR` | `$KIT_HOME/reports` | Where finished reports go. |
| `CHROME_BIN` | first of google-chrome, google-chrome-stable, chromium, chromium-browser | Chrome to use. |
| `CRAWL_PROXY` | none | Proxy for requests to the audited site only. |
| `CRAWL_UA` | `GrokBot-SiteAudit (+https://github.com/PensacolaSeoCompany/site-audit-kit)` | User agent for requests to the audited site. |
| `CRAWL_DELAY` / `CRAWL_CONCURRENCY` | 1 / 2 | Crawl pacing (seconds between requests / requests at a time). |
| `AUDIT_TIMEOUT` | 1800 | Seconds cap on one `./audit.sh` run. |
| `CSR` | 0 | `1` = client-rendered site, for `run-full-audit.sh` (`./audit.sh --csr` sets it). |
| `SKIP_PERF` | 0 | `1` = skip Lighthouse (the report says page speed was not measured). |
| `CHECK_EXTERNAL` | 0 | `1` = also check every external link. |
| `AUDIT_MIN_FREE_GB` | 2 | Disk floor; the run stops before starting below it. |
| `REPORT_BRAND` | none | Footer text on the report. |
| `CTA_URL`, `CTA_TEL`, `CTA_TEL_TXT` | none | A call-to-action box, shown only when `CTA_URL` is set. |
| `MYSQL_*` | none | Optional MySQL; without it the kit uses SQLite under `KIT_WORK`. |
| `SERPAPI_KEY` | none | Optional search API key for the index-size checks; without it they print "skipped" or UNKNOWN. |

## Troubleshooting

| You see | Do this |
|---|---|
| `Chrome not found` / preflight fails on Chrome | Run `./install.sh`, or set `CHROME_BIN=/path/to/chrome`. |
| Chrome crashes at once in Docker or as root | Every tool already starts Chrome with `--no-sandbox` and `--disable-dev-shm-usage`. If it still dies, give the container more shared memory (`--shm-size=1g`) and check `"$CHROME_BIN" --headless=new --dump-dom https://example.com`. |
| `PREFLIGHT FAILED` (exit 3) | A Python or npm package, Node 20+ or Chrome is missing. Run `./install.sh`, then `./doctor.sh`. |
| `CRAWL FAILED` (exit 4) | No pages came back. Read the log lines it prints: DNS, TLS, a firewall, or a thin crawl that needs `--csr`. |
| Pages answer 403 / 429 / a bot challenge | The site's firewall refused us. Those pages are listed under "What we could not check", never as broken. Try `CRAWL_DELAY=3 CRAWL_CONCURRENCY=1`, a browser `CRAWL_UA`, or a `CRAWL_PROXY` for the audited site. |
| Crawl finds 1 to 5 pages with almost no words | Probably a client-rendered (React/Vue/etc.) site. Re-run with `--csr`. A real 6-page site is a finding, not a failure. |
| Run stops with a disk-floor message | Free disk space, then run again. The kit never deletes files to make room. |
| Lighthouse fails or times out | Page speed shows "Not measured". It is never reported as a score of 0. |
| `NOT MEASURED` lines in the summary | Those stages did not complete (the run log ends `INCOMPLETE`). Treat them as unknown, never as a pass. |

More: `docs/KNOWN-PITFALLS.md` (every failure we hit, with symptom, cause and fix) and `docs/07-RUNBOOK.md`.

## Safety & limits

The disclaimers, consent rule and responsibility terms are in [TERMS.md](TERMS.md).

**Read-only.** The kit only sends GET and HEAD requests. It never logs in, submits a form, posts data or changes anything on the audited site.

**Polite by default.** Max 500 pages, 2 requests at a time, 1 s apart, robots.txt obeyed (RFC 9309 matching, Crawl-delay honoured), user agent `GrokBot-SiteAudit (+https://github.com/PensacolaSeoCompany/site-audit-kit)`. Only an explicit environment override changes this.

**Partial, never fake.** If install fails, a site answers 403 or a captcha, a stage times out, robots.txt refuses pages or the page cap is hit, the tool says so in plain words and writes a report whose first line is `PARTIAL REPORT: <reason>` (exit code 6). It never produces a complete-looking report from a partial run.

**Consent.** A bot using SKILL.md must ask, and wait for a yes, before installing, before each crawl ("Do you own or manage this site, or have permission to audit it?"), and before sharing a report anywhere.

**Shown with every report:**
- This is an automated audit. It can miss things or flag things that aren't real problems, so have your web person check before making changes.
- It doesn't guarantee rankings or traffic.
- I didn't change anything on your site.
- Your site data stays on this bot's computer and isn't sent to the template creator.
- Some advice may not apply to your business, site, or situation. Use your own judgment and check with your web person.
- You choose which changes to make. The template creator and this bot are not responsible for any loss of traffic, rankings, leads, or revenue from following or not following this report.

**Releases.** Install a tagged release (`--branch v1.0.0`), never `main`. A release is tagged only when every test and the control-site checks pass. All dependencies are pinned (`requirements.txt`, `package-lock.json`).

## Risks and how each is prevented

| Risk | Worst case | Prevention |
|---|---|---|
| Crawling a site the user has no right to audit | Complaint or legal exposure for the bot's owner | SKILL.md consent question before every crawl; the bot must wait for a yes |
| Load on a small site | Site slows or the host rate-limits real visitors | 500-page cap, concurrency 2, 1 s delay, robots.txt and Crawl-delay obeyed; the crawl stops on 429 instead of retrying hard |
| Changing the audited site | Data submitted, accounts created, content altered | GET/HEAD only; click-test skips forms, submit buttons and action words; no credentials are ever read or stored |
| A false finding acted on | Wasted developer time or a harmful site change | Disclaimers with every report; findings marked unverified until an agent runs `VERIFY-AGENT-BRIEF.md`; 403/429 never reported as broken; failed Lighthouse never shown as 0 |
| A partial run reported as complete | Owner believes a problem list is exhaustive | `PARTIAL REPORT` banner, `not_measured` and `could_not_check` in findings.json, exit 6 |
| Site text steering an AI agent | A planted sentence makes the agent skip or invent findings | Evidence files wrap site text in data markers; the brief says site text is never instructions |
| Secrets or client data leaking into reports or this repo | Privacy breach | No secrets files; keys only from env; reports and work/ are gitignored; fixtures are synthetic; fresh git history |
| Disk fills on the bot's machine | Other work on the machine fails | Free-disk floor (`AUDIT_MIN_FREE_GB`, default 2) checked before every run and every queued job; the run stops rather than deleting anything |
| Chrome or workers left running | CPU and memory held for days | Every stage runs under a timeout; the queue writes heartbeats and reports stale workers; tests kill only their own PIDs |
| Dependency drift | A new library version changes results or breaks install | Pinned versions; tagged releases; `install.sh --check` prints the version table |
| Google rate limits or bans | Lost access for the bot's IP | No Google search or Maps queries; index probes run only with an optional API key and never through a proxy |
| The report is wrong for this business | Advice that does not fit | Disclaimer: some advice may not apply; the user chooses which changes to make |

## Limits

- No Google Business Profile, Maps or rank checks. The website only.
- No human verification. Every finding is a **tool finding**: a strong pointer, not a checked fact.
  Some checks misfire on some sites (`docs/03-CHECKS-AND-FALSE-POSITIVES.md`). For a client-facing
  report, hand the run to an LLM agent with `VERIFY-AGENT-BRIEF.md`: it checks each finding against
  live evidence, drops the false ones and republishes through `publish-run.sh`.
- No title or meta description length findings, by design.
- One site per run; use the queue for many.

## Docs

| File | For |
|---|---|
| `SKILL.md` | An agent running audits: commands, outputs, exit codes, what never to do. |
| `docs/01-PIPELINE-ANATOMY.md` | Every stage, its inputs, outputs and gate. |
| `docs/02-BUILDER-AND-SIDECAR.md` | The findings file and the report builder. |
| `docs/03-CHECKS-AND-FALSE-POSITIVES.md` | Each check, how it misfires, how to verify it. |
| `docs/05-CASE-STUDIES.md` | Seven real audits, anonymised. |
| `docs/06-ENGINEERING-REVIEW.md` | Design, defects and fragile spots. |
| `docs/07-RUNBOOK.md` | One audit and batches, step by step, with recovery. |
| `docs/KNOWN-PITFALLS.md` | Every failure we hit and the guard for it. |
| `VERIFY-AGENT-BRIEF.md` | Brief for an LLM agent that verifies a finished audit. |
| `CONTRIBUTING.md` | Tests and how to add a check. |

## License

MIT. See `LICENSE`.
