# Known pitfalls

This is the kit's list of ways an audit has gone wrong. Every entry here came from a real audit that
shipped, or nearly shipped, a wrong or incomplete result. Each one says what you will see, why it
happens, and what the kit now does about it (or what you must still do by hand).

How to use it:
- Search for the text you see in a log (for example `UNKNOWN`, `202`, `RENDER TRANSPORT MISMATCH`,
  `empty shells until JavaScript`) or for a script name (`full-extract.py`, `checks-to-findings.py`).
- Each entry has a stable ID (P-001, P-002 ...). Cite the ID in commits, issues and notes. IDs are
  never reused; a retired entry keeps its number.
- "Seen" lines are anonymised. Dates are when the problem was found.
- "Status: open" means the guard is not in the code yet. Do the hand step.

Paths below use the kit layout: `KIT_HOME` is the repo root, `KIT_WORK` is `${KIT_WORK:-$KIT_HOME/work}`,
crawl data lives in `$KIT_WORK/crawl/`, each audit's logs and evidence in `$KIT_WORK/cases/<slug>/`,
and published output in `$KIT_HOME/reports/<domain>/`. The entry points are `./audit.sh <url>`,
`run-full-audit.sh` (the stage orchestrator), `run-queue.sh` (batches) and `publish-run.sh`.

## The rules that matter most

1. The completion gate proves each stage RAN. It does not prove the stage's INPUT was good. A
   blocked crawl passes every later stage on garbage. Check the data, not the tick marks.
2. A 403, 429 or bot wall means "could not check". It never means "broken" or "missing".
3. An INCOMPLETE run must list every skipped step in `not_measured`. `publish-run.sh` refuses it
   otherwise.
4. A failed Lighthouse run is "Not measured". It is never a score of 0.
5. No title-length or meta-description-length findings.
6. Never send search-engine lookups through a proxy. `CRAWL_PROXY` is for the audited site only.
7. A check may only state what it measured in the same run.

## Groups

- [A. Crawl and transport / bot walls](#a-crawl-and-transport--bot-walls)
- [B. Lighthouse and Chrome stages](#b-lighthouse-and-chrome-stages)
- [C. Client-rendered (CSR) sites and the render check](#c-client-rendered-csr-sites-and-the-render-check)
- [D. Redirects, hosts and canonicals](#d-redirects-hosts-and-canonicals)
- [E. Broken links and status codes](#e-broken-links-and-status-codes)
- [F. Duplicate content, titles and headings](#f-duplicate-content-titles-and-headings)
- [G. Sitemaps, orphans, pagination and filter URLs](#g-sitemaps-orphans-pagination-and-filter-urls)
- [H. Schema, contact details and business integrity](#h-schema-contact-details-and-business-integrity)
- [I. Hack scan and index estimates](#i-hack-scan-and-index-estimates)
- [J. URL discovery, subdomains and archives](#j-url-discovery-subdomains-and-archives)
- [K. CMS deep-dive (WordPress and others)](#k-cms-deep-dive-wordpress-and-others)
- [L. Tracker and consent probes](#l-tracker-and-consent-probes)
- [M. Findings sidecar and report builder](#m-findings-sidecar-and-report-builder)
- [N. Report wording](#n-report-wording)
- [O. Publishing, gates and incomplete runs](#o-publishing-gates-and-incomplete-runs)
- [P. Concurrency and shared state](#p-concurrency-and-shared-state)
- [Q. Disk, processes and environment](#q-disk-processes-and-environment)
- [R. Evidence discipline](#r-evidence-discipline)

---

## A. Crawl and transport / bot walls

### P-001. A blocked crawl looks like a tiny site
- Symptom: few pages crawled; the home page status is 429, 403 or 503; pages have `word_count` near 0 or null; content-attribution says "rendered=1 word" on a real page.
- Cause: the default Python HTTP client is fingerprinted as a bot and rate-limited per IP.
- Fix / guard: after the crawl, `run-full-audit.sh` counts real-content pages (status 200 and more than 50 words) and blocked pages. With fewer than 3 real pages, or any 429/403/503, it re-crawls with the curl fallback (`--curl --concurrency 1 --delay 2`). If the crawl is still thin: on WordPress, the REST API (`/wp-json/wp/v2/pages?per_page=100`) and the sitemaps are often open when page HTML is blocked. To tell a tiny site from a blocked one, fetch the home page with a full browser user agent: 200 with real words means tiny (that is the finding); 429/403 means blocked.
- Seen: two Shopify stores (429 after 6 pages), a kitchen remodeler behind Cloudflare (403), 2026-07.

### P-002. A one-page crawl passes the completion gate
- Symptom: the gate shows every stage ticked, but the crawl holds 1 page, and that page is the bot wall ("Checking your browser before accessing").
- Cause: the gate checks that stages ran. A wall page has a title, so "missing title" checks look clean too.
- Fix / guard: always read the page count in the crawl JSON. A 1-page crawl is a failed audit, not a clean one. Re-run with the transport settings in P-003 to P-005. Note: `full-extract.py` reads `CRAWL_CURL` itself (the `--curl` default), so `CRAWL_CURL=1` does switch the crawler to curl.
- Seen: a carpet cleaner's WordPress site behind a JS challenge, 2026-08-09.

### P-003. IP-reputation walls block every datacenter IP
- Symptom: the home page returns HTTP 202 with about 167 bytes: a meta refresh to `/.well-known/sgcaptcha/...`, header `sg-captcha: challenge`, `x-robots-tag: noindex`. Only one page gets through, however slowly you crawl.
- Cause: some hosting firewalls (SiteGround's captcha is the common one) key on IP reputation. Any datacenter IP is walled.
- Fix / guard: route the crawl through a residential-grade proxy: `CRAWL_PROXY=http://user:pass@host:port`. It implies the curl transport. Pace it: `CRAWL_DELAY=3 CRAWL_CONCURRENCY=2`, and set `CRAWL_UA` to the browser user agent the site accepts (P-006). Expect the proxy's clean rate to vary from day to day; plan retries. Proxy only the audited site, never search-engine lookups (P-090). Chrome stages are not covered by the proxy (P-024).
- Seen: a medical research group on SiteGround, 2026-08-04; a garage-door company, 2026-09-22.

### P-004. Some proxy exits still get the 202 challenge shell
- Symptom: through a rotating proxy, a few pages (2 of 49 in one run) come back as a 202 with 0 words and no title. The same URLs return 200 on the next try.
- Cause: some exit IPs in the pool are already flagged.
- Fix / guard: `full-extract.py` (`fetch_curl`) treats a 202 whose body contains `sgcaptcha`, `sg-captcha` or `Robot Challenge` like a 429: up to 3 retries with 5/10/20 s backoff, logged as `[challenge] <url>: 202 sg-captcha shell, retry n/3`. `crawl_transport.fetch()` does the same (3 attempts, 3/6 s backoff), so every stage on the shared transport inherits it. An unmarked 202 is left alone. After a proxied run, count `status == 202` rows in the crawl JSON. Re-fetch any survivor by hand; never ship it as "missing title".
- Seen: a garage-door company, 2026-09-22.

### P-005. The wall keys on the Accept-Encoding header, not the user agent
- Symptom: a JS challenge ("Just a moment...") on every page. Changing `CRAWL_UA` does not help. A residential proxy does not help.
- Cause: the cheapest walls fire on a request header curl adds by default. Here, any `Accept-Encoding` header got a 403; no `Accept-Encoding` header got 200 with real HTML. curl's `--compressed` adds that header.
- Fix / guard: set `CRAWL_NO_AE=1` (with `CRAWL_CURL=1`). curl then sends no `Accept-Encoding`. The raw-gzip abort (P-011) still protects you if the origin compresses anyway. When a user-agent change does not clear a challenge, bisect the request headers (Accept-Encoding, Accept, Connection) before you blame TLS fingerprinting or IP reputation.
- Seen: a carpet cleaner's WordPress site, 2026-08-10.

### P-006. The firewall is user-agent sensitive even through the proxy
- Symptom: the crawl (proxied, Safari user agent) reads the site fine, but a later stage through the same proxy gets a branded 403 and reports "no sitemap".
- Cause: the firewall blocked a Chrome user agent and passed a Safari one. Stages that hard-coded a Chrome user agent kept failing.
- Fix / guard: every stage takes its headers from `crawl_transport.HDRS`, which honours `CRAWL_UA`. A stage that imports `crawl_transport` must also use its `HDRS`/`UA`. After any firewall incident, grep the stages for a hard-coded Chrome user-agent string.
- Seen: a medical research group, 2026-08-04; again in four stages of a garage-door audit, 2026-09-22.

### P-007. The proxy covered the crawl but not the checks
- Symptom: a clean proxied crawl, but the report still says "no sitemap at all", "empty shells until JavaScript", "JavaScript rewrites titles". Or a stage dies with an unhandled `HTTPError: 403` traceback.
- Cause: `CRAWL_PROXY` support had been added file by file, wherever the last symptom showed up. Five stages had none, so their live fetches left from the audit machine's own IP and hit the wall.
- Fix / guard: one module owns the transport: `crawl_transport.py`. It reads `CRAWL_PROXY`/`CRAWL_UA`, installs a global opener so bare `urlopen()` calls are routed too, and its `fetch()` never raises: it returns `(None, "")` on failure so a blocked fetch becomes UNKNOWN, not a crash and not "this page is empty". Any stage that makes an HTTP request imports from it. Never build a bare `Request()` in a stage script.
- Seen: a medical research group, 2026-08-04; two Vercel-hosted sites, 2026-08-06.

### P-008. Private openers bypass the installed proxy
- Symptom: on a proxied run, the host-duplication check reads `202 / 202` for apex and www, and the orphan probe prints `ORPHAN-SAMPLE error=3 of 3` on pages that are live.
- Cause: stages that build their own opener (`build_opener(...)`, used to stop redirects) ignore the globally installed proxy.
- Fix / guard: those openers now add a `ProxyHandler` when the proxy is set. After a firewall run, run `grep -n "build_opener(" *.py cms/*.py`. Every hit must carry the proxy handler, or be a Chrome stage.
- Seen: a garage-door company, 2026-09-22.

### P-009. The host rate-limits you after the crawl
- Symptom: the crawl reads every page, then later stages (render, attribution, live checks) get challenge pages or 403s. The same URL is 200 during the crawl, 403 minutes later, 200 again from another IP.
- Cause: some firewalls (Sucuri, Vercel's `x-vercel-mitigated: challenge`) throttle an IP for about 10 minutes after a burst. Manual probing before the run makes it worse.
- Fix / guard: do not audit the same host twice back to back. Do your recon before launch and do not probe the site by hand while the orchestrator is crawling. Honour `Crawl-delay`. Run live re-verification through `CRAWL_PROXY` (a different IP). Challenge markers in stage logs mark the run as poisoned (P-031).
- Seen: a water-damage company behind Sucuri, 2026-08-05 (reproduced on three runs); two Vercel sites, 2026-08-06; a site whose Lighthouse run measured a captcha after hand probes, 2026-09-19.

### P-010. The checks stage alone exhausts the rate limit
- Symptom: re-running the checks minutes after a large crawl gives UNKNOWN or 429 on most live checks, and "N sitemap URLs do NOT load (HTTP 429)" as a critical.
- Cause: the checks make their own burst of requests (sitemap probes, re-verification, home-page fetches). On a per-IP velocity limit, that is enough.
- Fix / guard: on such hosts, crawl directly with curl pacing, then run the checks stages with `CRAWL_PROXY` and a matching `CRAWL_UA`. Sitemap live-verification is capped (P-095).
- Seen: a Shopify catalog store, 2026-09-22.

### P-011. A gzip body parsed as text
- Symptom: many pages with a blank title, no canonical, no headings and no `lang`, yet 500 to 800 "words". An earlier unproxied run of the same site had none.
- Cause: curl ran without `--compressed`, an origin (behind a proxy) sent gzip anyway, and the parser read gzip bytes as text. The word count was counting garbage.
- Fix / guard: `full-extract.py` and `render-check.py` send `--compressed` on curl (unless `CRAWL_NO_AE=1`). If a curl body starts with the gzip magic bytes `1f 8b 08`, the crawler prints `[FATAL] ... response is RAW GZIP` and exits 3 rather than parsing it. The urllib side decodes on the `Content-Encoding` header or the magic bytes (`_GzipDecode` in `audit-checks.py`, `_decode()` in `crawl_transport.py`). Note: a later claim that one origin gzipped every response unconditionally did not reproduce; verify such claims per origin (P-185).
- Seen: a water-damage company through a residential proxy, 2026-08-05 (66 of 132 pages would have shipped as "no title, no canonical").

### P-012. A timed-out fallback crawl left the old crawl in place
- Symptom: the log says "after curl fallback: 58 real-content pages", but the checks run on 934 throttled rows.
- Cause: the fallback crawl hit its time cap, was killed before it wrote its JSON, and the stages read the previous pass's file.
- Fix / guard: `full-extract.py` traps SIGTERM and writes what it has with `"partial": true`. `run-full-audit.sh` refuses a fallback whose JSON did not change and warns `PARTIAL CRAWL` on a partial file. Say "partial sample" in the report notes.
- Seen: a Shopify catalog store, 2026-09-22.

### P-013. A large catalog needs sizing and a page cap
- Symptom: a crawl of a store with thousands of products times out, or every uncrawled product prints as an orphan.
- Cause: a per-IP limit of roughly 0.6 pages/s (measured at concurrency 2, delay 1.0) makes a full crawl longer than the stage timeout. A capped crawl is a sample, but the orphan test treated it as the whole site.
- Fix / guard: size the site from `/sitemap.xml` first (products, collections, pages, posts). Run with `CRAWL_CURL=1 CRAWL_CONCURRENCY=1 CRAWL_DELAY=1.2` and a page cap (`CRAWL_MAXP`, or the 6th argument of `run-full-audit.sh`) that the timeout can finish (300 pages took about 9 minutes). `hack-scan.py` skips the orphan test when the row count equals the cap and the sitemap is larger. Write "template sample of N pages" in the report.
- Seen: a Shopify store with about 2,000 products, 2026-09-22.

### P-014. Crawl-delay is only honoured on the curl path
- Symptom: a site whose `robots.txt` says `Crawl-delay: 1` is crawled at full speed.
- Cause: `full-extract.py` applies the delay only in its curl fetcher; the default aiohttp path ignores it.
- Fix / guard: Status: open. When `robots.txt` sets a delay, run with `CRAWL_CURL=1 CRAWL_DELAY=<n>`.
- Seen: a consulting site, 2026-10-13.

### P-015. Normalised URLs were used as fetch targets
- Symptom: 93% of crawl rows carry a "redirect"; the report says "131 internal URLs redirect" (real: 1 stale link), "394 of 395 sitemap URLs unreachable", and "avg 0 words, noindex the prefix" about real 1,000-word articles.
- Cause: the crawler stripped trailing slashes for the fetch queue, not just for de-duplication. On a trailing-slash site every request hit `/page`, got a 301 to `/page/`, and every downstream count used the fake redirects.
- Fix / guard: normalised forms are de-duplication keys, never fetch targets. Fetch the spelling the site itself uses (the href as written, the sitemap `<loc>` as listed). Any probe that rewrites a URL before requesting it tests its own rewrite, not the site.
- Seen: a roofing contractor's WordPress site, 2026-07-31.

### P-016. Percent-escapes were decoded in paths
- Symptom: a link to `/terms-%26-conditions-1` was fetched as `/terms-&-conditions-1`, producing a false 404, a false sitemap redirect and a false "old address" finding. Elsewhere, 73 rows that only differed in percent-encoding were marked `redirected` and dropped from every content check.
- Cause: aiohttp passes URLs through yarl, which decodes reserved escapes in the path; `redirected` was a plain string comparison.
- Fix / guard: `full-extract.py` sends every GET/HEAD as `yarl.URL(crawl_transport.wire_url(u), encoded=True)`. `redirected` means a recorded hop, or `crawl_transport.same_url()` says the final URL really differs (scheme/host case, default port, escape hex case, unreserved escapes do not count). Each row gets `redirect_hops` (`[]` = no hop, `null` = not recorded). `audit-checks.py` un-flags encoding-only "redirects" in older crawls. Pin aiohttp and yarl versions in `requirements.txt`.
- Test: `tests/test_extract_and_findings.py`: the DE-03 tests, including `test_de03_real_redirect_keeps_hop_list`, `test_de03_external_head_sent_encoded`, `test_de03_url_equivalence`.
- Seen: a site built on a hosted website builder, 2026-10-14.

### P-017. Links with literal spaces became "broken"
- Symptom: `/search?q=Dock Hardware` shows as status 0 and a broken link.
- Cause: curl exits with code 3 on an unencoded space.
- Fix / guard: the crawler encodes spaces the way a browser does.
- Seen: a Shopify store, 2026-09-22.

### P-018. A bare user agent gets 406
- Symptom: a Python fetch returns HTTP 406 on a page that loads in a browser.
- Cause: some servers reject a bare `Mozilla/5.0` user agent.
- Fix / guard: every urllib fetch sends a full browser header block (user agent, Accept, Accept-Language), from `crawl_transport.HDRS`.
- Seen: a law firm's site, 2026-07.

### P-019. One edge IP fails some of the time
- Symptom: random stages fail on the same run: a 503 on the home page, 0 sitemap URLs, an UNKNOWN probe.
- Cause: one of several A records for the apex dropped TLS on about 10% of connections.
- Fix / guard: measure per IP with `curl --resolve host:443:IP`, then round-robin, and from a second network if you have one. Ship it as its own HIGH finding with the denominators. Re-run the poisoned stages once.
- Seen: a cleaning company, 2026-09-23.

## B. Lighthouse and Chrome stages

### P-020. Lighthouse is blocked by the same firewall
- Symptom: the perf stage "ran", but there is no JSON, or the JSON has a null score, or `runtimeError` is `ERRORED_DOCUMENT_REQUEST`.
- Cause: the firewall that blocks the crawl also blocks headless Chrome from a datacenter IP.
- Fix / guard: the perf gate in `run-full-audit.sh` requires a real score in at least one tier; a JSON with `runtimeError` or a null score measured nothing. Options, in order: run Lighthouse from a machine with a residential IP and a real browser, copy the JSON in (P-022), and run with `SKIP_PERF=1`; or use the Google PageSpeed Insights API (Google's servers fetch the page; it also returns real-user field data; the anonymous quota is tiny, so use your own API key); or record "Not measured" and say the site blocks automated fetches. Never invent or default a score.
- Test: `tests/test_builder.py` `test_failed_speed_test_is_not_zero`.
- Seen: two hardened Shopify stores, 2026-07-27.

### P-021. Lighthouse measured the challenge page
- Symptom: a plausible CLS or score (for example CLS 0.193) on a site behind a captcha.
- Cause: Lighthouse loaded the wall, not the site, and scored it.
- Fix / guard: before using any perf JSON, check that `finalDisplayedUrl` is the audited site and `runtimeError` is empty. Status: open for the gate itself: it should also read `finalDisplayedUrl` and the page title and mark a challenge page as Not measured.
- Seen: a site behind SiteGround's captcha, 2026-09-19; a site behind Vercel bot protection, 2026-09.

### P-022. Lighthouse writes the JSON and then exits non-zero
- Symptom: of three chained tier runs (`a && b && c`), only the first JSON exists.
- Cause: on some systems Lighthouse writes its result, then fails to delete its temporary Chrome profile and exits 1. The chain stops.
- Fix / guard: run each tier as its own command. The file is the result; the exit code is noise. Check each JSON (`finalDisplayedUrl`, `runtimeError`), copy it to `$KIT_WORK/crawl/<host>-{desktop,mobile-4g,mobile-3g}.json` before stage 5, then run with `SKIP_PERF=1`. Use the `perf-audit.sh` flags for each tier exactly. Afterwards make sure no Chrome or Lighthouse process is left running.
- Seen: a garage-door company, 2026-09-22.

### P-023. Reused or old perf files were shown as current
- Symptom: a run with `SKIP_PERF=1` shows "2 perf tiers" that came from an earlier run; or the perf section is missing because the file was named for `www.` and the run looked for the bare host.
- Cause: perf JSONs are host-keyed and were looked up by pattern. `SKIP_PERF=1` reuses whatever exists.
- Fix / guard: the builder tries every spelling (host, bare host, `<slug>-<host>`). The gate still requires perf JSONs to exist, so a first audit cannot skip measurement silently. When you reuse perf data, state its date in the report.
- Seen: a medical research group, 2026-08-05; a cleaning company, 2026-07-30.

### P-024. Chrome cannot use a proxy that needs a password
- Symptom: with `CRAWL_PROXY` set, the render stage still shows the firewall page; or `raw = 1316% of rendered words`.
- Cause: `CRAWL_PROXY` routes curl and urllib, not headless Chrome. Chrome's `--proxy-server` ignores `user:pass@` credentials and cannot answer the auth prompt headless. So the raw side came through the proxy and the rendered side did not.
- Fix / guard: `render-check.py` passes `--proxy-server` to Chrome only when the proxy has no credentials. With credentials it sets `CHROME_PROXY_UNAVAILABLE` and prints `⚠️ RENDER TRANSPORT MISMATCH`; `checks-to-findings.py` treats that banner as render-poisoned, so no "empty shells", "titles rewritten" or "N% blank" finding can ship. A raw percentage far above 100% means the two sides used different transports. `CRAWL_PROXY` does not make an audit fully proxied: render, pixel-consent and Lighthouse are not. On a host that blocks your IP, accept UNKNOWN for those stages, or run them from a residential machine. (A Puppeteer probe can answer proxy auth with `page.authenticate`.)
- Seen: a water-damage company behind Sucuri, 2026-08-05.

### P-025. One CLS sample is not a verdict
- Symptom: a CLS value quoted as a fact varies widely between runs.
- Cause: CLS changes from run to run.
- Fix / guard: take a median over several runs before reporting CLS as a finding. The same rule applies to every sampled external estimate (P-118).
- Seen: a roofing contractor, 2026-07-31.

### P-026. A URL fragment made Chrome scroll
- Symptom: a fully server-rendered page reported as "58% hidden content".
- Cause: the render URL had a `#fragment`; Chrome scrolled to it and the visual diff measured the scroll offset.
- Fix / guard: `render-check.py` strips `#fragments` before rendering.
- Seen: a resale shop, 2026-08-02.

### P-027. Speed advice did not match the measurement
- Symptom: lazy-image LCP advice on a page whose LCP element was text; TBT advice about images.
- Cause: the advice was hard-coded.
- Fix / guard: the advice follows the measured LCP element. TBT over 600 ms gets the note "the cause is JavaScript, not images". CLS is included in the summary table.
- Seen: a cleaning company, 2026-07-31; a garage-door company, 2026-08-01.

## C. Client-rendered (CSR) sites and the render check

### P-028. One CSR cause was shipped as many findings
- Symptom: on a React/Gatsby/Vite site: "36 missing H1", "36 missing meta description", "same page under several addresses", "no phone anywhere". The duplicate-content advice (noindex) would have removed 36 real pages from Google.
- Cause: the raw HTML is an empty app shell until JavaScript runs. Every raw-HTML check measured the shell.
- Fix / guard: when `render-check.py` says `CONTENT: CSR`, `checks-to-findings.py` ships ONE critical: "N pages are empty shells until JavaScript runs", with the single fix (pre-render at build). Shell pages (no title, no H1, under 400 words) are excluded from the missing-H1/meta lists, and the identical-content and no-phone findings are suppressed. Keep the phrase "empty shells until JavaScript" in that finding's title: the builder finds it by that phrase (P-142).
- Seen: a SaaS marketing site, 2026-08-02.

### P-029. A curl-crawled CSR site produces false orphans and misses the real defect
- Symptom: "6 missing H1", "pages nothing links to", sitemap orphans, on a Vite single-page app whose rendered pages have H1s and nav links. Meanwhile 5 inner pages that declare the home page as canonical go unreported.
- Cause: the curl fallback saw 1% of rendered words and 0 internal links. A CSR verdict invalidates every raw-HTML content signal.
- Fix / guard: `checks-to-findings.py` sets `_csr_blind` from the render verdict. Under it: any 200 HTML row under 200 words counts as a shell; orphan and "nothing links to" findings are suppressed; the builder's shell test excludes those rows. A new finding fires when 2 or more inner 200 pages declare canonical = home page ("N inner pages tell Google they are copies of your homepage").
- Seen: a UK card shop's single-page app, 2026-08-04.

### P-030. Duplicate-title and slug checks did not know about CSR
- Symptom: "N pages share the same title" and "every address mismatches its content" on a client-rendered site.
- Cause: the raw crawl sees the one pre-hydration shell title on every route.
- Fix / guard: checks 8 and 22 are skipped under `_csr_blind`. Server-rendered sites keep their real duplicate-title findings.
- Seen: a children's story app, 2026-08-04.

### P-031. Challenge pages poison every later stage
- Symptom: a whole run of plausible results that are all measurements of a 33 KB challenge page: CLS 0.208, "CSR", "no sitemap", "no schema", "no trackers".
- Cause: a stage that got a challenge page still writes normal-looking output.
- Fix / guard: `run-full-audit.sh` greps every stage log for challenge markers (`sg-captcha`, `Robot Challenge`, `Checking your browser`, `cf-chl`, `Just a moment`, `You are being redirected`, `cloudproxy`, `Access Denied`, `Rate limit`, `Too Many Requests`, `DDoS protection`, `Verifying you are human`, `Attention Required`, `challenge-platform`) and warns loudly. Live-fetch checks print UNKNOWN on challenge responses. `checks-to-findings.py` sets `_render_poisoned` when `render.log` holds a marker; then the CSR, title-rewrite and visual findings are suppressed and the curl crawl is treated as the truth.
- Seen: a law firm behind SiteGround, 2026-08-01; a medical research group, 2026-08-04.

### P-032. Marker lists always lag the next firewall
- Symptom: "21 pages are empty shells", "JavaScript rewrites your titles", "100% of your page is blank", on a site the crawl had just read in full. The render's raw title was "You are being redirected...".
- Cause: the rate-limit page matched no known marker.
- Fix / guard: a structural cross-check in `checks-to-findings.py`: if render says raw is 0% of rendered words but the crawl captured 5 or more pages with over 100 words, both cannot be true; the render fetch failed, so `_render_poisoned` is set whatever the wording. Visual "GATED" findings are suppressed under the same flag. Genuinely CSR sites keep their findings, because their crawl is also thin.
- Seen: a water-damage company behind Sucuri, 2026-08-05.

### P-033. A throttled render returns a partial body
- Symptom: `CONTENT: PARTIAL (raw = 92% of rendered words)`, then "JavaScript rewrites your page titles" and "57% of your page is blank".
- Cause: a throttled fetch returned a truncated body: 841 words, but 0 links, 0 headings, no title. The 0% cross-check (P-032) was itself a list of one failure shape.
- Fix / guard: "CROSS-CHECK 2" in `checks-to-findings.py`: render parsed 0 links AND 0 headings AND `TITLE DIFFERS raw ''`, while the rendered side saw some, AND the crawl read 5 or more pages with a real title and an H1, means poisoned. A document with 800 words but no `<a>`, no heading and no `<title>` is a truncated response, not a web page. The raw title is the discriminator: a real app shell ships a title.
- Seen: a water-damage company, 2026-08-06.

### P-034. The render sample skipped the empty pages
- Symptom: render-check says SSR on 3 pages, while 165 town and blog pages (0 words, no raw title) were about to ship as "169 pages missing title/H1/meta".
- Cause: the operator passed three static pages to render.
- Fix / guard: stage 4 of `run-full-audit.sh` adds up to two 0-word 200 rows from different path families to the render list.
- Seen: a lawn-care company's Vite/React site, 2026-09-22.

### P-035. Rendered facts were judged from the raw crawl
- Symptom: on a single-page app, the report says "too small to compete", "no photographs", "no contact form". The rendered site has 13 routes, 49 images and 3 forms.
- Cause: those findings read the raw crawl.
- Fix / guard: on a CSR site, render every sitemap route with headless Chrome (`--dump-dom`) after the crawl and judge content findings on the rendered DOM. Read the JS bundle for routes (`path:"/..."`) and form endpoints; a hidden internal route may only be visible there and in `robots.txt`.
- Seen: a tree-service site built with an AI site builder, 2026-10-07.

### P-036. A blocked render is not hidden content
- Symptom: content-attribution reports "-220900% JS-only".
- Cause: headless Chrome was blocked, so rendered words were near 0 while raw HTML was full.
- Fix / guard: `content-attribution.py` detects `rendered <= 3 and raw > 50` and prints "RENDER BLOCKED, attribution unavailable, NOT a JS-only finding". Check raw HTML first: content in raw HTML is server-rendered and fine.
- Seen: a Shopify store, 2026-07-27.

### P-037. JS-widget reviews are invisible to crawlers
- Symptom: many reviews on the business's profiles, but no review text in the raw HTML; render-check percentages look fine.
- Cause: reviews load from a third-party widget, sometimes in an iframe. Iframe content belongs to the iframe's URL, not the site.
- Fix / guard: `content-attribution.py` reports the raw-vs-rendered word gap per page, names JS-only containers and flags iframes. The fix for the site owner: put review text in the served HTML, with Review markup.
- Seen: a chiropractor (review widget behind its own bot wall) and a law firm (iframe widget), 2026-07.

### P-038. JS-rewritten titles never reached the report
- Symptom: `render.log` records `TITLE DIFFERS`, and nothing in the report mentions it.
- Cause: no handler consumed the line.
- Fix / guard: `checks-to-findings.py` turns `TITLE DIFFERS` into "JavaScript rewrites your page titles" (medium): the indexed title depends on whether Google's render pass runs. It is dropped when the run is render-poisoned (P-031) or the "rendered" title is a challenge page.
- Seen: an insurance agency running a tag-manager title experiment, 2026-08-01.

### P-039. A render finding was phrased site-wide from one page
- Symptom: "About 51% of your page is blank" and "Your pages are built..." from one rendered page. Two more pages rendered at 99% and 91%.
- Cause: no service or deep URLs were passed to render; the orchestrator warned `only 1 page checked`, and nothing downstream read the warning.
- Fix / guard: pass service pages to the render stage. The copy states the sample ("we loaded three of your pages; the two inner pages were fine"). Status: open: the sidecar should read the `only N page(s) checked` warning and refuse site-wide wording.
- Seen: a cleaning company, 2026-08-07.

### P-040. The "blank until code runs" finding invented a cause
- Symptom: "images render as blank" on a page with no images; "75% blank" where the no-JS screenshot held all body text and only the hero and menu were missing.
- Cause: the copy was canned, and the percentage came from pixels, not words.
- Fix / guard: the image sentence is conditional on `data-src` evidence in `render.log`; Elementor entrance animations are named when they are the cause. Status: open: compare no-JS against JS visible word count instead of pixels.
- Seen: a resale shop, 2026-08-02; a mortgage broker and a party-rental site, 2026-09.

### P-041. A shell that paints a generic page passes as SSR
- Symptom: render-check says `CONTENT: SSR` on a help-centre subdomain where 88 article URLs all return the same byte-identical JS shell.
- Cause: the no-JS screenshot showed the help-centre index list: real content, but not the requested article. The render check asks "did content paint?", not "did THIS URL's content paint?".
- Fix / guard: Status: open. By hand: compare the painted text with the requested URL's title/slug/H1 from the crawl; low overlap means a shell. Fetch the host's root, a real path and a made-up path, decode them, and compare hashes; identical bodies mean an unbounded URL space. A shell without noindex or robots.txt controls is the finding; one with controls is contained.
- Seen: a video-software company's subdomains, 2026-09-08.

### P-042. A stale `<head>` after client-side navigation
- Symptom: none in the crawl; every page's cold title is right.
- Cause: on router-link transitions the whole `<head>` (title, canonical, og:title, og:url, meta description) stays on the previous route while the body updates. A crawler fetches each URL cold and cannot see a defect that exists only across hops in one session.
- Fix / guard: `session-title-check.py` drives one browser session where every hop is a real click or back/forward, never a fresh `goto`, and diffs each hop's head with the cold crawl. Required guards: run a known-good control first; read early AND after 3 s (a healthy app updates late; "never updates" and "updates late" are different findings); hard-navigation hops must pass or the harness is broken; assert the URL actually changed; an empty title is UNKNOWN. Report hops and URLs tested; do not extrapolate. Status: open: not wired into the orchestrator.
- Seen: a video-software company, 2026-09-08.

## D. Redirects, hosts and canonicals

### P-043. Redirect destinations were judged as pages of the site
- Symptom: alarming content findings on URLs that are not pages: "canonical points off-domain" on a tracking endpoint; "missing H1" and "missing meta description" on redirect rows; a login redirect listed as "noindexed, confirm this".
- Cause: `full-extract.py` follows redirects and records the destination's data under the requested URL. A CRM button that returned a 307 was stored as a 200 page with someone else's canonical.
- Fix / guard: `audit-checks.py` builds `P_content` (rows without `redirected: True`, status 200, not an asset) and every content check reads it; link and status checks keep the full set. The builder's `p200` excludes redirected rows too. The noindex inventory also excludes standard WordPress login paths. When a rule is learned, grep every consumer of the same data for the same mistake: the builder learned this a day after the checks did.
- Seen: an insurance agent's site (4 of 5 findings came from this), 2026-07-31; a garage-door company, 2026-08-01; a carpet cleaner, 2026-08-04.

### P-044. CMS fuzzy-slug redirects looked like live injections
- Symptom: probes of `/dev/` and `/test/` "return 200" and are reported as live injected pages.
- Cause: WordPress 301s an unknown path to the nearest matching post. Suppressing all redirects fixed that but killed a real detection whose apex 301s to www.
- Fix / guard: compare the redirect target path with the requested path. The same path on a host variant (apex to www, http to https) is canonicalisation: follow it. A different path is a CMS guess: ignore it.
- Seen: a fire-safety school and a chiropractor, 2026-07-31.

### P-045. "The whole site is served twice" from one home-page probe
- Symptom: a critical claiming site-wide duplication when only `/` answered on both hosts.
- Cause: the check probed only the home page on apex and www.
- Fix / guard: check 12 samples up to 8 crawled inner paths on both hosts and reports one of three verdicts: all identical (flag), mixed (warning, "NOT a clean sitewide duplicate"), none reachable (UNKNOWN). Bodies are decompressed before comparison. The copy states the sample size.
- Seen: a water-damage company, 2026-08-05.

### P-046. The http:// variants were never checked
- Symptom: "served twice" when the site is really served four times, two of them unencrypted.
- Cause: check 12 probed only the two HTTPS hosts.
- Fix / guard: check 12 also probes the `http://` variants and raises its own flag with the true count.
- Seen: a water-damage company, 2026-08-05.

### P-047. HSTS means a visitor never sees the http copy
- Symptom: a finding told the reader "type http://example.com/ and watch it load insecurely"; the browser went straight to https.
- Cause: the origin answers 200 on port 80 (curl proves it), but the site sends `Strict-Transport-Security`, so browsers upgrade the request themselves. curl ignores HSTS.
- Fix / guard: check 12 reads the HSTS header first. With HSTS: a note, not a flag ("browsers upgrade it; a server-side 301 still helps non-browser clients and first visits; do not tell the reader to watch it fail"). Without HSTS: the flag stands and says so. Any check whose evidence comes from a non-browser client must ask whether a browser behaves differently (HSTS, service workers, cookie-gated content, meta-refresh and JS redirects).
- Seen: a moving company, 2026-08-05.

### P-048. Real duplication with a mitigation was overstated
- Symptom: a critical for apex/www duplication on a site where every page self-canonicalises to one host and the sitemap lists only that host.
- Cause: the finding ignored the mitigation.
- Fix / guard: the finding states it: "every page already names itself as the official version; more of a tidy-up than an emergency, but the tag is a hint and a redirect settles it".
- Seen: a water-damage company, 2026-08-05.

### P-049. A redirect allow-list forgot 307
- Symptom: a clean single-host site (apex 307 to www) reported as "a bot challenge or block interfered".
- Cause: the allowed codes were `(200, 301, 302, 308)`.
- Fix / guard: check 12 allows `(200, 301, 302, 303, 307, 308)`. An allow-list that forgets a member is the deny-list bug in reverse (P-183).
- Seen: a pest-control company, 2026-08-06.

### P-050. An unverified TLS context hid an expired certificate
- Symptom: check 12 says UNKNOWN for `www.`, and the duplication claim is (correctly) suppressed. Meanwhile anyone who types `www.` gets a full-page browser security warning.
- Cause: the crawl uses an unverified SSL context (right for crawling), so urllib saw a 403 where a browser sees `CERTIFICATE_VERIFY_FAILED`. `www.` was a dangling CNAME to an old platform with a certificate expired months earlier.
- Fix / guard: when a host variant answers abnormally, check 12 re-probes with browser-strict TLS verification and ships a certificate finding of its own. Suppression removes the unproven claim, not the investigation (P-189).
- Seen: a UK card shop after a platform migration, 2026-08-06.

### P-051. Slash-only differences were counted as redirects
- Symptom: "the home page is linked 828 times through redirects"; curl shows no redirect at all.
- Cause: links without a trailing slash were counted as redirect links (crawler residue, P-015 family).
- Fix / guard: check 15 requires the path to actually differ. Real sitemap redirects survive.
- Seen: a law firm, 2026-08-01.

### P-052. "Links to redirects" fired on every site
- Symptom: check 15 flagged all five test sites, one with only two redirecting URLs; one finding merged 34 renamed pages and 75 www/slash variants into "109 URLs redirect".
- Cause: one threshold answers "is this unusual?", not "is this the defect?". A count is not a finding.
- Fix / guard: check 15 needs the redirect share to be 20% or more of the crawl AND one URL linked 10 or more times (a template link). The finding prints `old → now lives at: new` pairs and separates renamed pages from host/slash variants.
- Seen: an insurance agent and a law firm, 2026-07-31.

### P-053. Canonicals that differ only in form were flagged
- Symptom: 147 of 157 "canonical points elsewhere" rows on a Shopify store; 82 rows "canonical points at a different page" where only `+` vs `%20` differed.
- Cause: `/collections/<c>/products/<p>` canonicalising to `/products/<p>` is Shopify working correctly; `+` and `%20` are the same space in a query string; case-only and encoding-only differences are not different pages.
- Fix / guard: `build-crawl-report.js` exempts the collection-scoped product shape and folds `+`/`%20`, case and encoding differences before comparing.
- Seen: a Shopify store, 2026-09-22.

### P-054. A subdomain's self-canonical was called off-domain
- Symptom: subdomain pages flagged "canonical points at another website".
- Cause: the off-domain test compared against the exact seed host.
- Fix / guard: `audit-checks.py` (`same_reg`) and `build-crawl-report.js` (`sameReg`) treat any host under the registrable domain as the same site.
- Seen: a photography studio with a booking subdomain, 2026-08-04.

### P-055. Wrong-host link count equals the page count
- Symptom: "6 links use the www. version of your own address" on a 6-page site with no absolute internal links; it then fed a critical "sitemap contradicts your canonical".
- Cause: two bugs in check 6 of `audit-checks.py`. (1) The raw-HTML scan matches any `href=`, so each page's own `<link rel="canonical" href="https://www...">` counts as a link. (2) `h.lstrip('w.')` strips a SET of characters, not a prefix, so `www.wow.com` becomes `ow.com`; any host starting with `w` mis-compares.
- Fix / guard: Status: open. Treat check 6 as untrusted. Signature: wrong-host count equal to page count means you are counting canonical tags. Fix direction: restrict the href half to `<a ... href=`, and use `re.sub(r'^www\.', '', h)`. Negative-test with a fixture that has no absolute internal links plus an off-host canonical, and a host starting with `w`.
- Seen: an event-rental site on a JS site builder, 2026-08-09; a builder's site, 2026-08-07.

### P-056. Slash-redirect sites lose their pages in the report
- Symptom: a 15-page site shows "1 of 15 pages OK".
- Cause: the builder counts `redirected` rows out of "OK (200)", and every row was a slash redirect to its final 200.
- Fix / guard: Status: open. Workaround: build from a copy of the crawl JSON re-keyed to `final_url`. Fix direction: follow a same-host redirect to its final 200 row.
- Seen: a tour operator's site, 2026-09-19.

### P-057. A previous brand's domain is part of the site
- Symptom: none; the home page said "X is now part of Y" and no check looked.
- Cause: the old domain's home page redirected to the new site, but every deep URL returned 404 (stranded links and citations), `www.` did not resolve, and no schema `alternateName` linked them.
- Fix / guard: check 20 (brand transition): match transition wording on tag-stripped text (the phrase had tags inside it); treat any external domain on the home page whose home page redirects into this site as a previous identity; probe deep-path redirect vs 404, the www DNS record and `alternateName`. Probes use GET, not HEAD (some hosts answer 405 to HEAD). Read the old domain's archive history before prescribing redirects: in this case it hosted spam for a period, so the fix is a hand-picked redirect map, never a catch-all.
- Seen: an insurance agency, 2026-08-01.

### P-058. Old addresses redirect to the wrong pages
- Symptom: all old page URLs "redirect fine".
- Cause: they redirected, but to the wrong targets (another state's page, a generic index).
- Fix / guard: follow each old URL to its final page and check that it is the matching content.
- Seen: a storage company, 2026-10-10.

## E. Broken links and status codes

### P-059. A 403 bot challenge was reported as a missing page
- Symptom: "49 links on your site lead to pages that aren't there"; 46 were Cloudflare `cf-mitigated: challenge` 403s, 3 were real 404s.
- Cause: `trace-errors.py` lists 401/403/404/410/5xx, and every entry became "isn't there".
- Fix / guard: only 404 and 410 are "not there". 401/403/406/429, status 0, and re-checks that return a challenge marker go to the sidecar key `could_not_check`, which is not a finding. A 5xx stays an error only if the re-check is also 5xx. The title says "N of M checked links".
- Test: `tests/test_builder.py` `test_blocked_403_is_not_a_broken_link`; `tests/test_extract_and_findings.py` `test_de05_persistent_5xx_stays_an_error` and the DE-05 challenge test.
- Seen: a business site behind Cloudflare, 2026-10-14.

### P-060. 406 and status-0 rows are not broken links
- Symptom: "8 broken internal link targets" for 1 real 404.
- Cause: `/account` redirected to an account subdomain that answers 406 to curl; parameterised rows with status 0 had no HTTP answer.
- Fix / guard: the builder drops 406 and parameterised status-0 rows from the broken-link finding.
- Seen: a Shopify store, 2026-09-22.

### P-061. Transient statuses defaulted to "defect"
- Symptom: an "Errors by status code → 508 (12 URLs)" section listing healthy pages; "no sitemap at all" from a site that publishes 4 sitemaps; a check that "died on HTTP Error 508" still shipping a finding.
- Cause: the host answered 508 "Resource Limit Is Reached" when concurrency exceeded its shared-hosting cap. All 13 URLs returned 200 when re-probed slowly. A status code with no branch in the code is treated as a defect.
- Fix / guard: `build-crawl-report.js` re-probes transient statuses (0, 408, 425, 429, 5xx, 508, 522, 524) once before deriving errors, so the status table, header pill and log count all inherit it. Enumerate transient statuses explicitly and re-probe before any of them reaches a client sentence.
- Seen: a cleaning company on shared hosting, 2026-08-07.

### P-062. Re-verified links stayed in the errors table
- Symptom: a URL dropped from the broken-link finding still appears under "Errors by status code" and in the header count.
- Cause: the builder re-verified it but only removed it from one view.
- Fix / guard: `build-crawl-report.js` keeps a `reverifiedOK` set that every view reads.
- Seen: a site on a hosted website builder, 2026-10-14.

### P-063. The builder counted a 202 captcha as "re-verified OK"
- Symptom: a real 404 was dropped from the report (`[404] -> 202 dropped`).
- Cause: the builder's live re-probe treats 200 to 399 as OK; run without the proxy, the firewall answered 202.
- Fix / guard: 202 no longer counts as OK in either builder re-probe. A manual rebuild on a walled host must export the same `CRAWL_PROXY` and `CRAWL_UA` as the run.
- Seen: a property-care company behind SiteGround, 2026-09-29.

### P-064. Two producers, one rule
- Symptom: the builder shipped a broken-link HIGH that the sidecar had already dropped after re-checking.
- Cause: `checks-to-findings.py` re-verified crawl-time non-200s; the builder read crawl status directly.
- Fix / guard: the builder re-verifies over the crawl's own transport and fails closed on unreachable URLs. A genuinely dead URL still ships as HIGH. When the same rule lives in two producers, fix both (P-186).
- Seen: a water-damage company, 2026-08-06.

### P-065. Disguised 404s look fine in a browser
- Symptom: the reader opens a "broken" URL and sees a normal page.
- Cause: the server returns status 404 but serves a full normal page (a copy of another page, real nav).
- Fix / guard: the broken-link finding detects 404 with substantial content (`word_count > 100`) and says so: "when you open this it looks fine; that is your error page dressed as a normal page; Google goes by the error". Any finding whose evidence contradicts what the reader will see must answer that in its own copy.
- Seen: a remodeler on Squarespace, 2026-08-02.

### P-066. Soft 404s were undetectable
- Symptom: none; the site returns 200 with "404" rendered for every unknown URL.
- Cause: nothing compared the status code with the content for addresses that do not exist.
- Fix / guard: check 28 requests three impossible paths and compares status with body. Placeholder URLs in the sitemap (`[company]`, `%5B...`) that return 200 with "page doesn't exist" text are flagged by check 13.
- Seen: a UK card shop, 2026-08-06; a SaaS site, 2026-08-02.

### P-067. Cloudflare email protection is not a broken link
- Symptom: `/cdn-cgi/l/email-protection` reported as a HIGH broken link.
- Cause: it is Cloudflare's email obfuscation endpoint.
- Fix / guard: reported as one LOW note ("no action needed"), never HIGH.
- Seen: a roofing contractor, 2026-07-31.

### P-068. Malformed and placeholder links
- Symptom: dead nav: `href="#www.example.com/service-locations/..."` on every page orphaned 51 pages; or nine working popup buttons nearly reported as dead.
- Cause: the browser treats `#...` as a same-page fragment, and crawlers follow nothing. The crawl JSON resolves and discards `#` hrefs and stores no raw HTML, so a check reading the JSON reported "no malformed hrefs" on a site with 39 per page.
- Fix / guard: check 10 fetches live HTML (home page plus one deep page per section). Bare `#`, `#anchor-id` and `#/route` (SPA routes, membership-portal buttons) are legitimate; only `#` followed by a scheme, host name or `/path` is malformed. `href="#"` with nav-noun text (privacy, terms, sitemap, policy) or 3+ words is a placeholder link. `<noscript>` blocks are stripped and a widget with more than 30 distinct texts is ignored (a country selector produced 6,641 false hits).
- Seen: a cleaning company (39 bad links per page), 2026-07-30; a one-page bathroom site, a Shopify store and a newsletter blog, 2026-08-02.

### P-069. "Buttons go nowhere" needs a real click
- Symptom: "7 service buttons go nowhere" shipped; they were a working JS button group.
- Cause: a source regex cannot see JavaScript wiring.
- Fix / guard: `click-test.js` clicks each candidate in Chrome with a no-click control window, and only "does nothing" supports the finding. `publish-run.sh` refuses a "goes nowhere" finding without click-test evidence. "Goes nowhere useful" is a different claim, not a dead click.
- Test: `tests/test_publish_gate.py` `test_goes_nowhere_needs_click_test`, `test_nowhere_useful_is_not_a_dead_click`; `tests/test_click_test.py` `test_tab_works_dead_link_does_nothing`.
- Seen: a make-up artist's site, 2026-10-14.

### P-070. Hidden downloads fail behind a success view
- Symptom: the crawl reports a PDF 404 as an ordinary broken link.
- Cause: a lead-magnet download sits inside a hidden "ready to download" view; the broken file is the real defect.
- Fix / guard: look for success views and test the file they offer. `verify-audit.py` lists `.pdf`/`.zip` links on sampled pages with their live status.
- Seen: a consulting site, 2026-10-13.

### P-071. A broken link was really a wrong-town link
- Symptom: a plain 404 finding, where the raw data showed anchor text for one town linking to another town's URL, itself 404 from a slug typo.
- Cause: the finding reported the status, not the mistake.
- Fix / guard: by hand: when anchor text and target disagree, state the mislink and that both real destinations exist.
- Seen: a law firm, 2026-08-01.

## F. Duplicate content, titles and headings

### P-072. "Identical content" signatures need more than size
- Symptom: "same page under many addresses" on paginated archives that list different posts; on noindexed cart URLs; on two different city pages from one template with the same word count (text similarity 0.53).
- Cause: check 21 matched identical `word_count` plus H1 tuple.
- Fix / guard: check 21 skips paginator URLs (`/(page|p)/\d+`, `?paged=`), rows canonicalised elsewhere, and rows that are noindex or not indexable (excluding them is the fix the check recommends). Status: open: compare text similarity, not just word count, before calling two pages identical. Note: the noindexed-row false positive had hidden a real one: 22 indexable add-to-cart URLs with no canonical.
- Seen: a remodeler's tag archives (true positive), 2026-08-02; an IT company's blog pages, 2026-08-04; a UK WooCommerce shop, 2026-08-05; a pest-control company, 2026-10-01.

### P-073. Paginated listings are not duplicate titles
- Symptom: "19 duplicate titles: write 19 unique titles".
- Cause: they were `?page=2..18` views of one listing.
- Fix / guard: check 8 recognises query pagination and frames it as a pagination-canonical question. True duplicate groups list sample URLs.
- Seen: an insurance agency's glossary, 2026-08-01.

### P-074. Language variants share titles legitimately
- Symptom: duplicate titles across `/de/`, `/fr/` ... versions of one path.
- Cause: these are hreflang alternates.
- Fix / guard: check 8 ignores the same title across language-prefix variants of one path. Check 22 skips slug comparison for non-ASCII titles and folds accents before token overlap. Check 14 strips the language prefix before bucketing.
- Seen: a multilingual app publisher, 2026-08-02.

### P-075. A cache artifact looked like 26 duplicate titles
- Symptom: 26 pages titled just the brand name.
- Cause: the crawl captured an edge-cache fallback during streaming render; live titles were unique.
- Fix / guard: check 8 skips a duplicate-title group whose title is 2 words or fewer and equals the brand token. It is bad crawl data, not a site defect.
- Seen: a payments site on Vercel, 2026-08-04.

### P-076. "Copied title" needs a clear owner
- Symptom: "this title was COPIED from another page" with no clear original.
- Cause: the check implied theft without an owner page.
- Fix / guard: check 8 calls it "copied" only when one page's slug matches the title by distinctive tokens (site-generic trade words excluded). Otherwise: "N pages share the identical title". It is suppressed when the mass-duplicate bar already fired. Canonicalising a duplicate to its master is correct and is not a finding.
- Seen: a carpet cleaner and a remodeler, 2026-08.

### P-077. No title-length or meta-length findings
- Symptom: findings such as "title longer than 60 characters".
- Cause: length limits are not a defect the kit reports.
- Fix / guard: `publish-run.sh` refuses any finding text that matches the length rule in `owner-rules.json` (`finding_forbidden`, id `title-meta-length`). A page table that shows a title's length is fine.
- Test: `tests/test_publish_gate.py` `test_refuses_meta_length_finding`, `test_length_words_outside_findings_are_fine`.
- Seen: a manufacturer's audit, 2026-10-14.

### P-078. Missing titles and header noindex were captured but never reported
- Symptom: rows with no title and rows with an `X-Robots-Tag: noindex` header never reached a report. `noindex` spelled `none` or in capitals was treated as indexable.
- Cause: three different noindex definitions across stages; only the first `X-Robots-Tag` header was kept; the curl path stored an empty value; no stage read `missing_title` or the header.
- Fix / guard: one definition, `crawl_transport.NOINDEX_RE` (case-insensitive, matches `noindex` and `none`, after removing `max-image-preview:<value>`); `audit-checks.py` keeps a byte-identical copy and a test fails on drift. `full-extract.py` stores `noindex`, `meta_robots_tag` and every `xrobots_headers` value on HTML and non-HTML rows. New findings: "N pages tell Google not to show them, in a hidden server setting" and "N pages have no title, so Google writes its own headline". Status: open for the builder: its `noindexed` test should read the stored `noindex` field.
- Test: `tests/test_extract_and_findings.py` `test_de04_every_xrobots_header_kept`, `test_de04_none_and_upper_case_mean_noindex`, `test_de04_pdf_xrobots_header_recorded`, `test_de04_one_definition_crawl_transport`, `test_de04_audit_checks_mirrors_crawl_transport`, `test_de04_xrobots_noindex_header_is_reported`, `test_de04_missing_title_is_reported`; `tests/test_builder.py` `test_noindex_definition_matches_the_crawler`.
- Seen: several crawls in the 30 newest, 2026-10-14.

### P-079. Meta-description and blank-title counts included pages that do not need them
- Symptom: "124 pages missing meta description" (true: 27); blank-title findings on `.xlsx` downloads.
- Cause: canonicalised copies and noindexed pages were counted; non-HTML downloads were treated as pages.
- Fix / guard: missing meta description counts self-canonical, indexable pages only. Blank-title checks exclude non-HTML files.
- Seen: a Shopify store, 2026-09-22; a tracking-software site, 2026-08-04.

### P-080. There is no scalar `h1` key
- Symptom: "no page on this site has a headline" or "47 pages, only 1 distinct H1".
- Cause: `p.get("h1")` returns None on every row because the key does not exist. The data is `headings.h1` (list) and `heading_counts.h1` (int).
- Fix / guard: `full-extract.py` writes `"h1": "USE headings.h1 (list) or heading_counts.h1 (int) - this key is not the H1 data"`, so a wrong read cannot look like "none". Similarly, Open Graph data is under `open_graph`, not `og`.
- Seen: two independent agents on two sites, 2026-08-05.

### P-081. The crawler missed H1s that the live page has
- Symptom: `h1: null` on 120 of 128 pages while the home page H1 is plainly visible.
- Cause: some page builders emit headings the raw extraction misses.
- Fix / guard: when the missing-H1 rate is over 70% and some pages did yield an H1, `build-crawl-report.js` reframes "N pages missing H1" as a caveated warning ("verify against the live pages first").
- Seen: a home builder on Elementor, 2026-08-04.

### P-082. Random-looking slugs and slugs that describe another page
- Symptom: Squarespace clone residue (`...-np4r4-frpjn-c763y...`) whose slug describes a different article; or a normal slug like `8-in-nylon-solar-cleat-light` flagged as residue.
- Cause: the residue rule first counted any 5-letter token, then any one-vowel word.
- Fix / guard: check 22: residue = 3 or more trailing 5-character tokens, of which 2 or more are hash-like; a hash has zero vowels (y counts as a vowel). It also flags zero overlap between slug tokens and title. Queued: a detector for `-2` numbered duplicate posts.
- Seen: a remodeler, 2026-08-02; a Shopify store, 2026-09-22.

## G. Sitemaps, orphans, pagination and filter URLs

### P-083. The data source cannot see what the check looks for
- Symptom: a thin-page check found nothing on a site where the sitemap declared 8,338 thin profile pages (80% of the site).
- Cause: mass-thin pages are orphaned; a link-following crawl never reaches them, and the check counted the crawl.
- Fix / guard: check 14 counts the sitemap when it is larger than the crawl. Ask of every new check: can my data source see the thing I am looking for?
- Seen: a fire-safety school, 2026-07-31.

### P-084. Orphan means zero inbound links, not "not crawled"
- Symptom: after seeding the crawl from the sitemap, the orphan count was 0 while 151 live posts had no inbound links. Earlier, a diff against the indexable subset called `/about` (in the global nav) unreachable.
- Cause: a sitemap-seeded crawl reaches every sitemap URL by construction.
- Fix / guard: orphans are sitemap URLs whose crawl row has 0 inlinks, plus ones never fetched. A "did the crawl get there at all" diff compares against all reached URLs, including redirect destinations. Never compare against the indexable subset.
- Seen: a roofing contractor, 2026-07-31.

### P-085. An orphan finding depended on an unrelated flag
- Symptom: the orphan finding never fired.
- Cause: it sat inside the branch for the Google index-estimate flag; with the search API quota exhausted there was no flag.
- Fix / guard: a finding's trigger depends only on its own inputs. The orphan finding is independent and fires at 10 or more orphans; 3 to 9 ship as medium. `hack-scan.py` probes 12 listed orphans (HEAD, no follow) and prints `ORPHAN-SAMPLE: live/redirect/error`, so the report can say "live pages nothing links to" vs "stale sitemap entries".
- Seen: a law firm, 2026-07-31; an insurance agency (2 orphans stripped everywhere), 2026-08-01.

### P-086. Orphan findings must name the pattern
- Symptom: "184 orphans", where 184 = one tag-archive template times 8 languages.
- Cause: a raw count hides that it is one gap multiplied.
- Fix / guard: the orphan finding computes section concentration (locale prefix stripped, first two segments) and locale share, and adds "one page type; one template change fixes every language". Single-locale sites get neither note.
- Seen: a multilingual app publisher, 2026-08-02.

### P-087. Pages nothing links to have never been checked
- Symptom: a blog's pages 2 to 35 exist but each declares `rel=canonical` to page 1, so Google discards them and the ~340 post links they carry. No check saw it.
- Cause: when a finding says "X is unreachable by links", no check that needs X's HTML has run on X.
- Fix / guard: diagnosis of unreachable pages must probe them directly. When `hack-scan.py` finds a JS-only pager (`<a data-page>` with no href; `ORPHAN-CAUSE`), it fetches `<index>/page/2/` and emits `PAGINATION-CANONICAL-TRAP` if the canonical points at the base. Paginated archives must self-canonicalise and use real `href` pager links.
- Seen: a roofing contractor's blog with a faceted pager, 2026-07-31.

### P-088. Count linked URLs, not synthetic ones
- Symptom: "effectively infinite" or "4,416 duplicate addresses" from a filter scan.
- Cause: the scan counted URLs it built itself (`?page=999`, made-up combinations, every click output). Google follows links; it does not invent URLs.
- Fix / guard: when sizing a URL space (filters, facets, pagination, parameters), count only what the site exposes: rendered `<a href>` with parameters, URLs the filter UI generates when clicked, real paginator links followed to the real last page, and sitemap entries. An absurd-value probe answering 200 is a severity modifier ("the server accepts anything"), never the count. Report what is linked or declared; adjust severity by what is possible. Note: faceted-search markup can sit inside a JSON blob with escaped quotes; regexes need `\\?"` tolerance.
- Seen: a roofing contractor and a multilingual app publisher, 2026-07-31 and 2026-08-03.

### P-089. Filter URL spaces need their own always-on stage
- Symptom: filter buttons that build `?category=x&tag=y` URLs were only found when an unrelated orphan probe fired.
- Cause: the faceted-URL detector lived inside the orphan branch of `hack-scan.py`.
- Fix / guard: `filter-url-scan.py` (stage 6d, gate-checked) renders listing pages, harvests filter controls, clicks buttons and records pushState URLs, then probes them. Three tiers by the crawlable (linked) set: UNBOUNDED-INDEXABLE (self-canonical, indexable) = HIGH; CANONICAL-PROTECTED (canonical to the clean listing) = MEDIUM; INTERACTION-ONLY (buttons without href) = MEDIUM, never LOW: Google's renderer can fire events, so such URLs are discovered unreliably, not never. Fix: real links plus a robots.txt parameter block or 404 for invalid values.
- Seen: a multilingual app publisher, 2026-08-02 and 2026-08-03.

### P-090. A robots.txt block is not a noindex
- Symptom: filter and sort URLs called "harmless, blocked by robots.txt" while a `site:` search showed hundreds of them indexed.
- Cause: a Disallow stops crawling, so Google never sees the canonical or noindex on that URL; any internal link to it (a "clear filter" chip) is enough to index the bare address.
- Fix / guard: a filter or sort question is answered by the index, not the markup. Never call a robots-blocked URL that the site links to harmless. The fix is crawlable plus noindex (or canonical), and re-pointing the links, never a Disallow alone. `filter-url-scan.py` runs an index check (`INDEXED-PARAMS`) when `SERPAPI_KEY` is set. Search lookups go direct, never through `CRAWL_PROXY` or a generic proxy (`run-full-audit.sh` refuses to start if `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` are set).
- Seen: a Shopify store, 2026-09-22.

### P-091. The index check printed numbers that measured nothing
- Symptom: `INDEXED-PARAMS: 0` on every audit; lines with estimates in the billions.
- Cause: the lookups returned 429s, timeouts, TLS errors, quota exhaustion, or global estimates that ignored the `site:` operator.
- Fix / guard: treat the index half of stage 6d as Not measured unless each lookup succeeded and the result is scoped to the site. A quota error returning zero is not a clean index. The on-page filter-scan half works.
- Seen: eight audits, 2026-10-14.

### P-092. "Blocked in robots.txt" came from the meta robots tag
- Symptom: published lines read "...; blocked in robots.txt" for pages whose meta robots said "index, follow".
- Cause: `checks-to-findings.py` printed filter-url-scan's `robots=` value (the meta tag) as a robots.txt block. robots.txt was never fetched there.
- Fix / guard: the line now says what was measured: "the page's robots tag says noindex" / "allows indexing" / "the page has no robots tag".
- Test: `tests/test_extract_and_findings.py`, the DE-01 meta-robots test.
- Seen: four published audits, 2026-10-14.

### P-093. Filter and orphan findings can look like one fact twice
- Symptom: two headings both starting with "184", one HIGH and one MEDIUM, about the same blog tags, with opposite duplicate-content verdicts.
- Cause: the orphan finding counted 23 tags x 8 locales of path archives (`/tag/x/`, self-canonical); the filter finding counted 8 categories x 23 tags of `?tag=` URLs (canonical-protected). Same size, different URLs and different fixes.
- Fix / guard: keep both, cross-reference them, and do not let the filter finding lead with the same bare number; state its arithmetic. Fold the filter finding into the orphan finding only when its verdict is CANONICAL-PROTECTED and every filter value is already an orphaned path page. Never de-duplicate by matching numbers. Keep a negative case where an UNBOUNDED-INDEXABLE filter space has no matching orphans and stays HIGH.
- Seen: a multilingual app publisher, 2026-08-02.

### P-094. Sitemap child URLs with escaped query strings
- Symptom: "26 sitemap URLs" on a 2,285-URL site; the child sitemaps themselves shipped as "pages only found through your sitemap".
- Cause: Shopify's index lists `sitemap_products_1.xml?from=...&amp;to=...`. Walkers tested `loc.endswith('.xml')` (false) and fetched the literal `&amp;`.
- Fix / guard: `_sm_unescape()` and `_sm_is_index()` (path-only `.xml` test) in `full-extract.py`, `audit-checks.py` (checks 13 and 14), `hack-scan.py` and `url-discovery.py`. Sitemap-index children expand into page URLs; they are not crawled as pages.
- Seen: a Shopify store, 2026-09-22.

### P-095. Live-verifying every sitemap URL tripped the rate limit
- Symptom: "1,536 sitemap URLs do NOT load (HTTP 429)" as a critical, and later stages reading "sitemap URLs: 0".
- Cause: check 13 probed all 1,885 URLs the capped crawl missed.
- Fix / guard: at most 40 random probes, 0.5 s apart. 403/405/406/408/429/5xx are filed as UNKNOWN ("a block or throttle, not verified"), never as dead.
- Seen: a Shopify store, 2026-09-22.

### P-096. "Not in our crawl" was reported as "orphan or stale"
- Symptom: check 13 said a sitemap URL was orphaned or stale; it returned 200 on 7 of 7 live probes.
- Cause: the firewall 403'd it once mid-crawl. Absence from the crawl is a fact about the crawl.
- Fix / guard: check 13 fetches before judging: dead = flag, live = ok (not a finding), unreachable = UNKNOWN.
- Seen: a water-damage company, 2026-08-06.

### P-097. Sitemap redirects are not dead addresses
- Symptom: "your sitemap sends Google to N addresses that no longer exist" for URLs that 301 to working pages.
- Cause: the copy treated any redirect as a dead end.
- Fix / guard: check 13's finding says "lists N addresses that redirect instead of loading directly" and explains Google files them as "Page with redirect"; nothing is broken for visitors. Check 13 still flags sitemap redirects that land on a different page (stale migrations, a different city via fuzzy slug).
- Seen: a home builder, 2026-08-04; a roofing contractor, 2026-07-31.

### P-098. Sitemap findings must state the true sitemap state
- Symptom: "sitemap contains pages that should not be there" over a body that says "no sitemap URLs found"; "no sitemap" where robots.txt was missing and the sitemap undeclared.
- Cause: canned titles without a body-state route (P-152).
- Fix / guard: "Your site has no sitemap at all" only when measured; a robots.txt existence probe; robots-404-only gets its own title. Queued: flag contradicting robots.txt groups (for example a CDN-managed `Disallow: /` for AI crawlers next to a site-authored `Allow: /` for the same agents); report the contradiction, never predict what a crawler will do.
- Seen: a one-page bathroom site, 2026-08-02; a cleaning company, 2026-08-02; a coaching site, 2026-08-04.

## H. Schema, contact details and business integrity

### P-099. Defects that return 200 and valid HTML
- Symptom: a site passes every status and HTML check while failing the business: the click-to-call number on 12 money pages belongs to another state's area code; city pages show three different numbers; a lorem-ipsum page sits in the sitemap.
- Cause: every check asked "did the server respond correctly?"; none asked "does this page do what it claims for the business?".
- Fix / guard: checks 9 (contact integrity), 10 (malformed hrefs) and 11 (placeholder slugs) fetch live HTML. When auditing, ask directly: does the phone dial the right company, does the nav navigate, does the call-to-action convert?
- Seen: a cleaning company, found by an independent no-context audit, 2026-07-30.

### P-100. Phone checks: consistency, not count
- Symptom: a critical "inconsistent phone numbers" on a business that prints a main and an alternate line identically on every page; on a multi-location business with one line per metro; on a review page quoting a third party's support line.
- Cause: the check counted numbers.
- Fix / guard: check 9: same area code and uniform across sampled pages = ok; several metros but uniform = warning, verify; non-uniform = critical. Per-location lines that are uniform within their `/locations/` subtree plus one shared toll-free line are deliberate. An extra number visible on exactly one page is an editorial mention = verify, never critical. A shared geographic number that matches no local area code still flags (the original wrong-state catch). `tel:` numbers normalise to the last 10 digits for NANP (`+1888...` equals `888...`). Format numbers as (NNN) NNN-NNNN, never as Python lists.
- Seen: a fire-safety school, 2026-07-31; an insurance agency and a garage-door company, 2026-08-01.

### P-101. Phone numbers outside North America
- Symptom: `+65 9751 0475` rendered as a US number; a UK national number `07...` masked with a made-up area code; the same Turkish line written `+90...` and `0...` reported as two different numbers (a false critical); three UK branch lines called "not a working number".
- Cause: NANP rules were applied to everything; per-page sets could not see a duplicate that spans pages.
- Fix / guard: normalisation keeps a `+` country code; a leading `0` followed by 10 or 11 digits is a national trunk form and never NANP; `_merge_trunk_forms()` folds `0<national>` onto `+CC<national>`, and a site-wide reconciliation after the page loop deletes groups that collapse to one number. Compare subscribers, not strings. Area-code analysis only runs on NANP-shaped numbers. Status: open: the "not a working number" rule must detect the country before applying NANP tests.
- Seen: a newsletter blog (Singapore), a UK WooCommerce shop, 2026-08; a Turkish physiotherapy clinic, 2026-08-07; a UK wheel-repair company, 2026-10-14.

### P-102. Fake or unassigned area codes
- Symptom: none; a payment site printed a placeholder number in an unassigned area code.
- Cause: nothing checked that a number could work.
- Fix / guard: a NANP-only check: unassigned codes and structural N11/0X/1X tests ship "your phone number is not a working number" (critical). Numbers with a `+` country code are skipped.
- Seen: a payments site, 2026-08-04.

### P-103. Digits that are not a phone number
- Symptom: "phone number not yours" built from a video ID inside a leftover editor note.
- Cause: the number extractor read any digit run.
- Fix / guard: by hand: read the sentence around any flagged number. `verify-audit.py` prints each number with surrounding words.
- Seen: a consulting site, 2026-10-13.

### P-104. One type list per concept
- Symptom: "has Organization schema but NO LocalBusiness node" on a page with a full `CleaningService` node; a `LegalService` with self-authored reviews passing one check while another said "schema present"; a page typed `["LocalBusiness","Store"]` getting "no local entity"; "generic company label" on a `PestControl` page.
- Cause: three hand-maintained subtype lists (checks 3, 16 and the business-model gate) had diverged; 44 real subtypes were missing; array `@type` values were joined into one string; property lookup was case-sensitive.
- Fix / guard: one module-scope `_LOCAL_SUBTYPES` in `audit-checks.py` that all three read; array `@type` split; case-tolerant property lookup (an invalid `AggregateRating` key is evidence too); a nested `publisher` Organization counts as an Organization node. An allow-list kept in N places diverges in N-1 of them. Add missing subtypes when seen (for example `Physiotherapy` alone is not in the list).
- Seen: a law firm, 2026-08-01; a resale shop, 2026-08-02; a cleaning company, 2026-08-07; a pest-control company, 2026-10-01.

### P-105. Real-world schema is malformed
- Symptom: "homepage fetch/parse failed" on a site that loaded fine.
- Cause: arrays where objects belong (`geo`, `priceRange`, nested `sameAs`, a list of PostalAddress) crashed the check with `'list' object has no attribute 'get'`. That looks like a network hiccup, which is worse than a wrong verdict.
- Fix / guard: coerce shapes defensively. The malformation is itself evidence.
- Seen: a law firm, 2026-08-01; a SaaS site, 2026-08-02.

### P-106. Several schema nodes are not always a conflict
- Symptom: "two conflicting descriptions of your business, delete the hand-coded block" on a site where that block was the only one with address, geo and hours; on a correct graph where a Person `worksFor` a business.
- Cause: check 16 compared names across nodes with no awareness of plugin-plus-hand-coded layering or of `@id`, `founder`, `employee`, `worksFor`, `parentOrganization`.
- Fix / guard: identical names and zero contradictions = note, never critical; a linked graph (`_linked16` reachability) is valid. Real conflicts: different names, the same phone written differently (normalise digits first), junk `openingHours`, raw HTML or double-encoded `&amp;amp;` inside strings. Multi-node with no conflict says so honestly. Check 16 also names exactly which of phone or address is missing from the business node. There were two copies of the name-conflict test in the file; fix every copy (P-186).
- Seen: a roofing contractor, 2026-07-31; a cleaning company, 2026-08-02; a property-value site, 2026-08-06.

### P-107. "Fabricated rating" was asserted without looking for reviews
- Symptom: "aggregateRating reads as fabricated when no reviews appear on the page" on a home page showing 5 stars and three named testimonials.
- Cause: the check saw the property and never searched the visible text.
- Fix / guard: check 16 measures the visible text first, and only asserts absence when the raw page is server-rendered enough to trust (120 or more raw words). Self-authored ratings with no visible reviews are a medium finding ("publishes its own star rating"), not "conflicting descriptions".
- Seen: a pest-control company, 2026-08-06.

### P-108. The local-business gap died as a note
- Symptom: none in the report; check 3 printed "no LocalBusiness/Organization node" as `note:`.
- Cause: notes are not shipped. Also, the locality test relied on a live home-page fetch that returns 0 bytes on walled hosts.
- Fix / guard: check 3 is three-way: local subtype present = ok; Organization plus a local signal = flag; Organization only = non-local ok. The local signal comes from crawl fields first (home title and meta description `City, ST`), live fetch only as fallback. It has its own client title. Status: open: `City, ST` is US-only; also accept schema `addressLocality`/`addressRegion` and forms like `City · Region`.
- Seen: a carpet cleaner, 2026-08-04; a pest-control company, 2026-08-04; a Turkish clinic, 2026-08-07.

### P-109. The business-model gate suppressed real local checks
- Symptom: no phone, NAP or form findings for local painters; or a "zero products" critical on a non-store.
- Cause: the gate classified "no LocalBusiness schema and no store" as an app publisher.
- Fix / guard: a home-page `tel:` or a `City, ST` string means local regardless of schema (the missing schema is a finding). Organisations and app publishers get the absence checks as informational skips. Check 24 (store inventory) needs 3 or more product-like URLs or store markers before probing.
- Seen: a painter and a grill retailer, 2026-08-04; a multilingual app publisher, 2026-08-02.

### P-110. Store, form and image absence checks
- Symptom: an online store with zero live products and every "Shop now" a 404, with no finding; "no way to contact you except calling" on sites that use embedded form iframes.
- Cause: nothing tested the store; check 23 counts raw `<form>` tags only.
- Fix / guard: check 24 probes `/products.json`; zero products = critical "your store has nothing in it". Check 25 flags zero images site-wide as its own finding. No phone anywhere for a local business is a warning finding. Status: open: check 23 should count known form-vendor iframes in the rendered DOM before claiming "no form".
- Seen: a Shopify store, 2026-08-02; three service businesses with form iframes, 2026-09.

### P-111. Fake testimonials and development assets
- Symptom: none; testimonials used stock generated faces, and production loaded a development CSS CDN.
- Cause: not checked.
- Fix / guard: testimonial images from random-face services are a critical with its own title ("computer-generated stock faces"); a development-only CDN in production is a warning. By hand: verify a schema address that may be a bank branch or other unrelated business, reused portfolio photos, stale copyright years.
- Seen: a one-page bathroom site, 2026-08-02.

### P-112. Share-preview (Open Graph) findings
- Symptom: nothing fired on 219 of 221 pages that declare `summary_large_image` with no image; a count inflated by 52 pages; 3 pages with no OG tags left out of the "missing OG" list; copy claiming a large preview where there were no tags.
- Cause: the check tested `og:title` only; key mismatch (`twitter_cards` vs `twitter`); the builder needed a non-empty `open_graph` object; copy was not tied to the data.
- Fix / guard: test the image, not just the title (a missing thumbnail is the common case); read the right keys; include pages with no OG at all; name the dominant template ("one template fix, not 677 edits"); copy follows the measured tags.
- Test: `tests/test_builder.py` `test_share_preview_copy_matches_the_site`, `test_share_preview_large_sentence_when_true`.
- Seen: a law firm, 2026-08-01; a painter, 2026-08-06; a bonus-offers site, 2026-10-14.

### P-113. Social profile checks
- Symptom: a personal Facebook profile endorsed as the business's presence; "NO social profiles" about a business with a large following, because the fetch hit a checkpoint page; `FINDING: broken/absent social icons` printed directly under the stage's own UNKNOWN.
- Cause: existence was taken as fitness; a challenge page was read as absence; the UNKNOWN branch fell through to the canned negative.
- Fix / guard: `social-scan.py` marks personal-profile patterns (`name.name.N`, `profile.php?id`, `linkedin.com/in/`) as weak `sameAs` targets; a blocked fetch prints UNKNOWN, and the UNKNOWN branch stops there. Queued: also mark off-brand handles. The social stage has its own transport; on walled hosts it may stay UNKNOWN.
- Seen: a law firm, 2026-08-01; a resale shop, 2026-08-02; a pest-control company, 2026-08-06.

### P-114. hreflang and language checks
- Symptom: a critical "translations invisible" on a site with textbook hreflang; Spanish pages with `lang="en-US"` not reported.
- Cause: the hreflang regex was case-sensitive (React emits `hrefLang`); no check compared `lang` with content.
- Fix / guard: check 19 matches hreflang case-insensitively, flags language sections with no hreflang and a `lang` that declares the wrong language, and uses the crawl row's stored `lang` (from the transport that worked). Queued: derive language sections from path prefixes when hreflang was not captured.
- Seen: a multilingual app publisher, two law firms, 2026-08.

### P-115. Alt-text and lazy-image counts
- Symptom: missing alt text overcounted about 2x (1,903 vs 151).
- Cause: `alt=""` is correct decorative markup; lazy-load pairs (a `data:` stub plus `data-lazy-src`) are one image; the lazy detector missed `data-lazy-src`.
- Fix / guard: only an absent `alt` attribute counts; lazy pairs count once; the lazy detector reads `data-lazy-src` and `data-src`.
- Seen: a roofing contractor, 2026-07-31.

### P-116. Version evaluation goes stale
- Symptom: WordPress 6.9.5 (a current release) called "~2022, should be updated"; later 6.8.6 called "about 2 years old" when that line shipped the year before.
- Cause: the version table keyed on the major digit, then on a hand-kept `_WP_LATEST_KNOWN` constant that goes stale when upstream ships.
- Fix / guard: `cms/wordpress.py` compares `(major, minor)`. Old 4.x/5.x cores ship their own finding with the age. Check 27 reads `X-Powered-By` for end-of-life PHP. Status: open: read the live WordPress version-check API and fall back to the table only when it fails.
- Seen: a painter (WordPress 4.9, PHP 7.4), 2026-08-06; a cleaning company, 2026-08-07; a Turkish clinic, 2026-08-07.

## I. Hack scan and index estimates

### P-117. Injected spam pages are invisible to a crawl
- Symptom: the `site:` index count is far above the crawl count; foreign-language or odd `.php` pages in the index.
- Cause: injected pages are not linked and not in the sitemap. Attackers push them to Google through a sitemap or indexing API they control.
- Fix / guard: `hack-scan.py` (stage 10) compares the `site:` estimate with the crawl count and probes hack-signature paths when `SERPAPI_KEY` is set. A hacked site is the report headline. Fix for the site owner: remove injected files, patch core and plugins, rotate passwords, check Search Console for an unknown sitemap or verified user, then request removals.
- Seen: a chiropractor with 126+ injected pharma pages, 2026-07.

### P-118. `site:` is an estimate: sample it
- Symptom: "possible under-indexing (~61%)" from one sample. Re-running the same query gave 551, 867, 558, 867, 734, 869.
- Cause: one reading of a fluctuating estimate was treated as a count.
- Fix / guard: Status: open. By hand: sample 5 or more times, report the range and median, fire only if the whole band is below the threshold, state that `site:` is an estimate, and exclude failed or quota-limited samples (they are UNKNOWN, not low). The denominator excludes non-200, redirected, canonicalised-elsewhere and noindex rows; if indexed results span subdomains, the denominator must too (prove which hosts are indexed first).
- Seen: a video-software company, 2026-09-08; a law firm (407 vs 1,000 raw rows read as 40%; true 91% of 444 indexable), 2026-07.

### P-119. Search results that are not about this site
- Symptom: "HACKED" on a cleaning company; off-domain spam hits counted; billions of results for a `site:` query.
- Cause: an unbounded pharma regex; the search API dropped the `site:` operator; results from other domains were not filtered.
- Fix / guard: keep only results whose host ends with the audited domain and report the rest as "off-domain, ignored" (status: open in `hack-scan.py`; do it by hand). A result count in the billions means the operator was dropped: discard it. Stage output must be about THIS business: check the host, phone and address before using any external match.
- Seen: a cleaning company, 2026-07-31; a mortgage broker, 2026-09-21; a tax firm, 2026-09-30.

### P-120. Index-vs-crawl comparisons are meaningless on a CSR site
- Symptom: "possible hacked/injected content" and "40 orphans" on a client-rendered site.
- Cause: shells carry no links, so the link graph is blind, and the crawl sees one page.
- Fix / guard: read the render verdict before the orphan or index comparison; drop both until pages are rendered.
- Seen: a lawn-care company, 2026-09-22; a tax firm, 2026-09-30.

### P-121. Index-estimate copy ignored the direction
- Symptom: the log said "~74x MORE" and the report said "came back noticeably lower".
- Cause: the copy was direction-blind.
- Fix / guard: the copy follows the measured direction; a surplus is an index-bloat lead.
- Seen: a Shopify store, 2026-09-22.

### P-122. Hidden link injection is not caught
- Symptom: none from hack-scan; inflated "pages carrying" counts.
- Cause: a hidden block (`left:-9999px`) of spam links; the injected URLs 301 to the home page, so the crawl credits the home page's links to each of them.
- Fix / guard: Status: open. By hand: look for off-screen link blocks and internal links that redirect to the home page (`verify-audit.py` lists those).
- Seen: a pest-control company, 2026-10-01.

## J. URL discovery, subdomains and archives

### P-123. Subdomains of the audited site are the site
- Symptom: staging apps, exposed admin panels and separate blogs on the client's own domain were never crawled; a finding said "N additional sites, NOT examined".
- Cause: the crawler scoped to the exact seed host.
- Fix / guard: `full-extract.py` `same_site(u)` returns `h == REG or h.endswith("." + REG)`, where REG is the literal seed host with a leading `www.` removed (so `.co.uk`-style suffixes work without a public suffix list, and `evil-example.com` or `example.com.evil.net` stay out). Strip `www.` as a prefix only, never with a global replace. Large subdomains can use up the page budget; raise the cap if one dominates.
- Seen: several sites in a 20-site batch, 2026-08-04.

### P-124. Subdomains found by DNS or certificate logs were mis-described
- Symptom: "3 additional live websites ... NOT examined", where one was a 301 to the apex, one a full indexable copy of the site with a broken canonical, and one an open directory listing. Another was an internal operations console served publicly.
- Cause: hosts discovered via DNS/certificate logs rather than links are not crawled; the copy falls back to a caveat.
- Fix / guard: classify each discovered host: redirect (not a website), directory listing, full site (crawl it), login-gated app. A malformed-looking hostname can still be a real DNS record (test two nonsense names to rule out wildcard DNS). Describe exposure narrowly: "an internal console is publicly reachable and indexable", not "an open customer database". `verify-audit.py` captures each subdomain's first-screen title, login hints, robots meta and robots.txt.
- Seen: a moving company, 2026-08-05; a Turkish clinic, 2026-08-07.

### P-125. Archived dead URLs: churn, assets and real migrations
- Symptom: "MOST ARCHIVED URLs ARE NOW DEAD" for ordinary content churn; or where 12 of 13 "dead pages" were cached images and CDN endpoints; or mapped to the orphan finding with no URLs and a truncated body.
- Cause: one threshold; files sampled with pages; two different flags routed to one title.
- Fix / guard: two conditions: 60% or more of sampled archived URLs dead AND 60% or more of the dead in one prefix. `checks-to-findings.py` judges each dead prefix on its archived URLs and ignores prefixes that are mostly files (non-page extensions, asset folders, `/cdn-cgi/`); it needs 3 or more dead page URLs and lists page URLs only. It ships as its own MEDIUM: "Old addresses from a previous version of this site are now dead ends". Status: open: the sampling fix belongs in `url-discovery.py`.
- Test: `tests/test_extract_and_findings.py` `test_jp05_archived_dead_pages_still_reported` and the two JP-05 asset tests.
- Seen: an insurance agent and a chiropractor, 2026-07-31; a QR-code app, 2026-08-04; a UK wheel-repair company and a bonus-offers site, 2026-10-14.

### P-126. "Local or staging link" matched real hosts
- Symptom: a critical "links typed wrong" for a real external press link whose host contains "local", and for a real site named `tests.com`.
- Cause: the pattern `[^/]*(staging|\.local|\.test)` matched inside real host names.
- Fix / guard: `audit-checks._local_leak()` parses the host: localhost, 127.x.x.x, 0.0.0.0, ::1, `*.local`, `*.test`, `*.localhost`, `*.invalid`, and staging/dev/uat/qa/preview/test labels of the audited domain only.
- Test: `tests/test_extract_and_findings.py` `test_jp05_real_local_and_staging_hosts_fire` and the JP-05 real-site test.
- Seen: a storage company, 2026-10-10; a practice-test site, 2026-10-14.

### P-127. Directory listings never reached the report
- Symptom: `url-discovery.py` printed `DIRECTORY LISTING enabled` on `/wp-content/uploads/`; the report said nothing.
- Cause: `checks-to-findings.py` had no handler for that signal. Also, the listing rows load by JavaScript, so a raw fetch shows only the table header and looks empty.
- Fix / guard: every flag a stage prints needs a sidecar handler (see P-139). Render the listing in Chrome to count entries.
- Seen: a Turkish clinic, 2026-08-07.

## K. CMS deep-dive (WordPress and others)

### P-128. Platform detection must use first-party evidence only
- Symptom: an ASP.NET site detected as WordPress and Drupal; "no SEO plugin" on a site running Yoast.
- Cause: outbound links to other sites' `/wp-content/` and `/sites/default/files/` paths were counted; on walled hosts the home-page markup was empty; an optimiser stripped HTML comments that carried the plugin fingerprint.
- Fix / guard: `detect-cms.py` uses only same-domain URLs, markup and headers; needs a score of 4 or more and a clear lead (a near tie is `ambiguous`, else `unknown`); sets `html_available` false on a failed fetch (UNKNOWN, never "no plugin") and strips challenge pages. Plugin detection tries home-page markup, then the sitemap's XSL path or generator comment, then plugin folder names in asset URLs. When sitemaps exist and no fingerprint is found, never claim "no sitemap control". Builder signatures must be anchored (`\bet_pb_` and `/themes/Divi/`, not a bare `divi`, which matches "divider").
- Seen: an IT company, a pest-control company and a home builder, 2026-08-05; a UK WooCommerce shop, 2026-08-05.

### P-129. WordPress archives: the sitemap is the authority
- Symptom: "358 thin pages" on a site whose SEO plugin was already noindexing them; or archive counts that disagree with the sitemap they cite.
- Cause: the crawl reaches few archive pages; paginators were counted as archives.
- Fix / guard: `cms/wordpress.py` reports each archive type (tag, category, author, attachment) as leaking or correctly handled, using the sitemaps the SEO plugin publishes (it only lists archive types it has set indexable) and the declared count (215 attachment pages declared vs 5 crawled). Paginators are excluded from counts. Report the declared number and say so. A "correctly handled" result is as useful as a flag. The CMS stage (6e) must run before the sidecar stage (6b).
- Seen: a home builder and a pest-control company, 2026-08-04 and 2026-08-05.

### P-130. WordPress exposure checks
- Symptom: login names exposed by `/wp-json/wp/v2/users` missed by a manual pass; an author slug built from an email address; "xmlrpc not answering" on an enabled endpoint; "readme version (2)".
- Cause: these were not checked, or were checked with the wrong method: a GET for a body string under throttling saw 405/429; the readme "2" is the GPL licence number.
- Fix / guard: `cms/wordpress.py` checks `/wp-json/wp/v2/users`, author slugs that look like emails, and xmlrpc with a paced POST of `system.listMethods` and backoff (enabled / absent / UNKNOWN are distinct). The core version comes from the `generator` meta tag. Probe the capability the endpoint implements, not the request that is convenient to send. Recording a defect in a notes file is not fixing it: the readme bug was written up and shipped again.
- Seen: a home builder, 2026-08-05; a cleaning company, 2026-08-07.

### P-131. Plugin sample content is a finding class
- Symptom: none; demo courses, lessons and categories from an LMS plugin (17 indexable pages, 8 in Google) on a cleaning company's site, reachable only via the sitemap.
- Cause: nobody looks at plugin demo content.
- Fix / guard: by hand: in any WordPress crawl look for `/course/`, `/lesson/`, `/course-category/`, `/product/sample-`, `/hello-world/` families.
- Seen: a cleaning company, 2026-09-23.

### P-132. Login, cart and admin routes
- Symptom: indexed `/user-account` and `/log-in` routes missed; later, a noindexed login page flagged critical "can be indexed"; a Shopify `/cart` flagged as an auth route.
- Cause: checks were added one pattern at a time; a 200 with noindex was treated as indexable.
- Fix / guard: check 7 flags crawlable auth routes and splits noindexed ones (200 plus noindex = ok). WordPress `wp-login.php`/`wp-admin` defaults are excluded. Shopify `/cart` is platform-standard: downgrade to LOW or drop. Habit: when reviewing any crawl, ask what in it should NOT be indexed (thin pages, parameter URLs, staging paths, template fragments, auth routes). robots.txt cannot de-index a known URL; noindex plus removal is the fix.
- Seen: a cleaning company, 2026-07-30; a tracking-software site, 2026-08-04; a Shopify store, 2026-09-22.

## L. Tracker and consent probes

By default `publish-run.sh` refuses pages that carry pre-consent tracking wording (rule `consent-finding` in `owner-rules.json`). The probe lessons below still matter for any tracker measurement. Edit `owner-rules.json` if your use needs the finding.

### P-133. A consent banner is not blocking
- Symptom: trackers plus a consent banner read as a pass.
- Cause: on many sites the trackers are wired outside the consent manager's control and fire while the banner is on screen.
- Fix / guard: trackers plus banner is "verify blocking", never a pass; only the live network log closes it. Advice must match what fired: a site with no banner is told to remove or gate; only a banner site is told to verify blocking.
- Seen: a roofing contractor, 2026-08-01.

### P-134. Delayed-JS sites need real user gestures
- Symptom: a probe reports no trackers; a real visitor triggers Meta, GA4, Hotjar, Clarity and more.
- Cause: "delay JavaScript until user interaction" features hold every script until a trusted input event. `window.scrollBy()` from page JS is not trusted.
- Fix / guard: `pixel-consent-check.py` loads cold, then sends CDP-level input (mouse moves, `mouse.wheel`, a key press) and waits about 7 s, and labels which phase each tracker fired in.
- Seen: a roofing contractor, 2026-08-01; a local-services site, 2026-08-12.

### P-135. Static and live tracker reads disagree both ways
- Symptom: a static read says "1 tracker"; the live probe sees two GA4 properties and session replay (loaded via a tag manager). Elsewhere, static matches were only `dns-prefetch` hints.
- Cause: a tag-manager container loads trackers that never appear in HTML; resource hints look like trackers to a regex.
- Fix / guard: the live layer (stage 6c) decides, and its evidence merges into the static finding. If the static layer says ok, a standalone fallback still ships the live result. Google tag IDs are labelled by prefix: `AW-` = Ads remarketing, `G-` = GA4, `GT-` = container.
- Seen: an insurance agency, 2026-08-01; a garage-door company, 2026-08-01; two sites, 2026-08-09.

### P-136. A tracker probe on the wrong transport measured a captcha
- Symptom: "TRACKERS FIRED: NONE" with `gtag` and `fbq` undefined; the page title was "Robot Challenge Screen".
- Cause: headless Chrome was served the challenge; the crawl had needed a proxy.
- Fix / guard: print the page title in every probe and read it. If the crawl needed a special transport, the probe needs it too. A negative result is only as good as its access. `pixel-consent-check.py` reports a document state (body length, title, challenge regex) and returns UNKNOWN when the load was blocked.
- Seen: a medical research group, 2026-08-09.

### P-137. Consent-manager and privacy-policy signatures need context
- Symptom: "confirm the banner blocks" about a banner that does not exist; "privacy policy present" on a site with none.
- Cause: a bare vendor name matched a disabled integration key in a plugin's config (`"cookiebot_integration_enabled":false`); a bare substring matched a platform config key (`privacyPolicy`).
- Fix / guard: consent-manager signatures are loader URLs and runtime markers only (for example `window.Cookiebot`, `OptanonWrapper`). Privacy-policy detection probes common paths and scans footer anchors in context.
- Seen: a cleaning company and a remodeler, 2026-08-02.

### P-138. The request-count floor for "blocked" was wrong
- Symptom: a static one-page site reported as blocked; a 2-request challenge page reported as clean.
- Cause: "fewer than 10 requests" was used as the blocked test.
- Fix / guard: blocked = the document failed or was empty, or carries challenge text. Navigation falls back to `domcontentloaded` plus 12 s when `networkidle2` never settles (chat widgets that long-poll).
- Seen: a resale shop and a Shopify store, 2026-08-02; a SaaS site, 2026-08-02.

## M. Findings sidecar and report builder

### P-139. Findings that stay in a log do not exist
- Symptom: the report shows 5 generic findings while the logs hold criticals (dead nav, indexed login routes); or a stage prints "report it as a finding" and nothing does.
- Cause: there was no path from stage logs to the report.
- Fix / guard: `checks-to-findings.py` (stage 6b) converts flags into the findings sidecar before the build. Any new detection stage must run before 6b, and every signal needs a handler in the sidecar (render `VISUAL: GATED`, directory listings, `TITLE DIFFERS` and others were all missed this way). Verify by grepping the built HTML for each critical, never by trusting the builder's "N findings" line.
- Seen: a cleaning company, 2026-07-31; a remodeler, 2026-08-02; a Turkish clinic, 2026-08-07.

### P-140. Runs of one domain shared one sidecar
- Symptom: run 2 and run 3 of a domain show the same findings; hand-corrections to a run's file are ignored.
- Cause: the builder took the slug from the crawl JSON's `site` field (the host), so every run read `<host>-findings.json`.
- Fix / guard: the builder derives the run slug from the input file name (`<slug>-full.json`) and prefers `<runSlug>-findings.json`, falling back to the host file. When hand-correcting, edit the run-slug file and check the "Wrote ... N findings" count and a grep of the new title in the HTML.
- Seen: a water-damage company (three runs), 2026-08-05; a pest-control company, 2026-10-01.

### P-141. Regenerating the sidecar destroys hand-corrections
- Symptom: after a round of corrections, one re-run restored every original defect: a removed false positive came back, a fixed count reverted.
- Cause: `checks-to-findings.py` regenerates the sidecar; it does not merge. Also, for findings the builder computes itself, a sidecar `detail` field is discarded; only `title` and the structured fields are used.
- Fix / guard: copy the corrected sidecar into `$KIT_WORK/cases/<slug>/` before running anything that may regenerate it. After any regeneration, grep the built HTML for the corrected wording (expect 1 or more) and the withdrawn wording (expect 0). To change a builder-owned finding body, supply `whats_happening`, `see_it`, `youll_see`, `why`, `fix`. Durable improvements belong in `checks-to-findings.py`, not one site's sidecar.
- Seen: an event-rental site, 2026-08-09.

### P-142. The builder finds some findings by their title
- Symptom: after a hand-retitle, the broken-link defect shipped twice, or the CSR shell rows came back as "1 page missing a main headline" next to the shell finding.
- Cause: `build-crawl-report.js` matches the broken-link finding with `/do(es)? not exist|doesn't exist|aren't there|isn't there|broken (internal )?link/i` and the CSR finding with `/empty shells until JavaScript/`.
- Fix / guard: keep those phrases in any hand-edited title and re-check the page after every rebuild. Logic keyed to client-facing copy must match on meaning, not one phrase, because copy changes.
- Seen: a property-care company and a tax firm, 2026-09-29 and 2026-09-30; a tree-service site, 2026-10-07.

### P-143. One canned title per check, whatever signal fired
- Symptom: "Your site answers at two different addresses" while the check's own log said `ok - one canonical host` (the http flag fired instead); "two conflicting descriptions of your business" about a star rating; a malformed-href critical with `urls: []`; a WordPress category finding listing an author URL.
- Cause: the sidecar mapped check number to one title, fired on any flag in the section, and parsed with fixed windows.
- Fix / guard: `_SIGNAL_TITLES` in `checks-to-findings.py` maps signal regex to title; the fired signal chooses. When a check gains a signal, add a row instead of a guard. A check's output must print inside its own numbered section; the sidecar trusts section boundaries (a block printed before the next header shipped under the wrong title twice). New checks need a CONTEXT row, or their raw log heading ships as the title.
- Seen: a moving company, a carpet cleaner and a medical research group, 2026-08-05; a pest-control company, 2026-08-06; a multilingual app publisher and a lawn-care company, 2026-08-02 and 2026-09-22.

### P-144. UNKNOWN was shipped as a finding
- Symptom: "Your site answers at two different addresses" while the check said `⚠️ UNKNOWN - a bot challenge or block interfered`; "two conflicting descriptions" after a check died on `HTTP Error 503`.
- Cause: `⚠️` meant both "warning" and "could not measure", and the section gate treated both as a defect.
- Fix / guard: a section with no flag whose every `⚠️` line matches the UNKNOWN pattern (`UNKNOWN|could not|cannot be judged|unreachable|bot challenge|blocked|not probed|interfered|fetch/parse failed|HTTP Error|probe failed|Service Unavailable|not measured|re-probe`) is skipped. A section that measured nothing ships nothing. Better still: checks should emit a distinct token for "unmeasurable" so the gate does not pattern-match prose.
- Seen: a medical research group and a moving company, 2026-08-05; a lawn-care company and a cleaning company, 2026-09-22 and 2026-09-23.

### P-145. Routing on node count instead of issue count
- Symptom: a 3-issue schema case shipped as the single-issue "publishes its own star rating".
- Cause: sidecar routing keyed on "is it multi-node", not "how many issues fired".
- Fix / guard: routing counts issues.
- Seen: a law firm, 2026-08-01.

### P-146. Builder arguments in the wrong order
- Symptom: a file literally named after the business appears, and the build exits 0.
- Cause: `build-crawl-report.js <in.json> <out.html> [name]`; the name was passed second.
- Fix / guard: the builder validates its arguments and errors.
- Seen: a water-damage company, 2026-08-06.

### P-147. Counts built from rows, not pages
- Symptom: "schema on 17 of 136 pages (13%)", a coverage critical, when all 17 real pages had schema; 112 of 136 rows were image files.
- Cause: `P_content` filtered redirects but not assets. The same sentence also used two denominators.
- Fix / guard: `_ASSET_RE` excludes images, PDFs, CSS and JS from `P_content` and from the builder's OG candidates. One denominator per sentence. Check 14 also excludes non-HTML assets. Rates need a shared denominator.
- Seen: a painter, 2026-08-06 (4 false criticals cleared across 45 archived crawls).

### P-148. Numbers must agree across title, chip and list
- Symptom: "30 addresses" in a title, a "20 URLs" chip, 10 listed pairs; "47 pages" with none listed.
- Cause: three separate caps in three stages.
- Fix / guard: caps removed; chips count subject lines; a site-wide source is named as the template ("appears on N pages"), otherwise the full list is shown in a scroll box. Checks that cite a count enumerate members (`MEMBER:` lines).
- Seen: a roofing contractor, 2026-07-31; a moving company, 2026-08-04.

### P-149. Unknown severities sorted wrong
- Symptom: a finding with severity "Critical " (trailing space) sorted below LOW.
- Cause: severity strings were not validated.
- Fix / guard: the builder refuses an unknown severity. Severity chips are uppercase-keyed.
- Test: `tests/test_builder.py` `test_unknown_severity_refused`.
- Seen in testing, 2026-10-14.

### P-150. Crawled text can break the link map page
- Symptom: none yet; a crawled title containing `</script>` or HTML would run in the reader's browser.
- Cause: site-derived text was embedded in an HTML page.
- Fix / guard: `interlink-map.py` escapes every crawled string, embeds JSON without angle brackets or separators, and never uses `innerHTML` for tooltips.
- Test: `tests/test_interlink_map.py` (`test_no_script_breakout`, `test_embedded_json_has_no_angle_brackets_or_separators`, `test_tooltip_does_not_use_innerHTML`, `test_lists_escape_crawled_text`).
- Seen in testing, 2026-10-14.

## N. Report wording

### P-151. A finding answers which, where to, and write what
- Symptom: readers could not understand findings like "109 URLs redirect, linked 203x".
- Cause: a count is not a finding.
- Fix / guard: every finding names which thing, where it points, and what to write instead (`old → now lives at: new`, `linked from: <source page>`). Every URL in a line is clickable. Long criticals get paragraph breaks. When a broken link's sources span 20 or more pages, say it is in the site-wide navigation.
- Seen: a law firm, 2026-07-31.

### P-152. Canned titles must not contradict their bodies
- Symptom: "site loads on two addresses" over a body saying `www` does not resolve; "sitemap contains pages that should not be there" over "no sitemap URLs found".
- Cause: canned titles had no route for UNKNOWN or absent states.
- Fix / guard: every canned title has a body-state route; NXDOMAIN on `www` means only one host exists, a clean verdict (the stored error is truncated, so match its prefix). Absent states get their own copy.
- Seen: a one-page bathroom site, 2026-08-02.

### P-153. Plain-language copy must keep the measured meaning
- Symptom: plain-English rewrites that sounded right and were wrong: "Google can't tell where your business is" (the check measures schema only); "your phone number isn't on the homepage" (it measures the schema node); "your site is missing safety settings" (it measures five headers; HTTPS is not one of them).
- Cause: copy was simplified without reading what the check measures.
- Fix / guard: read the check's code before renaming its finding. Name which kind it is (visible text or schema). Every finding uses five parts (`whats_happening`, `see_it`, `youll_see`, `why`, `fix`) rendered by `findingCard()`; technical strings go under a muted "for your web person" aside. A glossary sits at the top of each report.
- Seen: a review of all copy, 2026-08-05.

### P-154. "Open this and see for yourself" must survive the reader doing it
- Symptom: the reader followed the instruction and saw the opposite.
- Cause: the evidence came from curl; a browser behaves differently (P-047), or the page looks fine though its status is an error (P-065).
- Fix / guard: once a finding says "see it yourself", the claim must hold in a real browser. Pre-empt any contradiction in the copy.
- Seen: a moving company, 2026-08-05; a remodeler, 2026-08-02.

### P-155. Operator jargon and log formatting leaked into reports
- Symptom: "a conversion hole and a NAP-signal gap", `⚠️ 1 page(s) whose ADDRESS...`, "slug", Python dict reprs, lowercase severity chips with no colour, "1 page don't tell Google".
- Cause: log text went straight into client copy.
- Fix / guard: `_client_copy()` in `checks-to-findings.py` scrubs at the form level (glyphs, `(s)` plurals, caps emphasis, "slug" to "address", "NAP" to "name/address/phone") so every check inherits it. Only scrub the kit's own text, never quoted site content. Real plurals and agreement everywhere. Training notes and internal vocabulary never reach the page.
- Seen: a Canadian agency site, 2026-08-04; a cleaning company, 2026-08-07; a law firm, 2026-08-01.

### P-156. Thin-site and title-level copy needs context
- Symptom: a raw "SITE SIZE" heading shipped as a HIGH title with an operator-voice body.
- Cause: the section had no client copy.
- Fix / guard: the thin-site section says "too small to compete in search" and gives generic advice (categories, not only locations).
- Seen: a one-page bathroom site, 2026-08-02.

### P-157. Judgment items stay manual
- Symptom: not applicable.
- Cause: some patterns may be strategy, not defects.
- Fix / guard: do not automate: a title that targets a small town while the H1 targets the metro; `blog-` slug prefixes; single-sample CLS. Flag them by hand when seen.
- Seen: a roofing contractor, 2026-07-31.

## O. Publishing, gates and incomplete runs

### P-158. The gate proves stages ran, not that the input was good
- Symptom: all ticks, but a stage ran on blocked or empty data.
- Cause: the gate checks execution, not quality.
- Fix / guard: the crawl-quality guard (P-001), challenge-marker scan (P-031), perf score gate (P-020) and partial-crawl warning (P-012) are data checks. Any stage that can "succeed" on garbage needs a plausibility check, not an exit-code check.
- Seen: two Shopify stores, 2026-07-27.

### P-159. Gate on log content and output files, not exit codes
- Symptom: dirty sites marked "SKIPPED"; the gate never exited non-zero; every clean run errored on an integer comparison.
- Cause: `audit-checks.py` and `social-scan.py` exit 2 by design when they find problems; render-check exits 2/3 by design; `grep -c` prints 0 AND exits 1 on no match, so `|| echo 0` added a second line.
- Fix / guard: the gate reads log content and output files. Use `|| true` after `grep -c`. The orchestrator exits with the number of skipped stages (0 = complete) unless a fatal stage set its own code. When a gate bug is fixed for one stage, sweep every stage for the same wiring.
- Seen: a cleaning company, 2026-07-31; a one-page bathroom site, 2026-08-02; a law firm, 2026-08-01.

### P-160. The gate counted "critical" in ordinary words
- Symptom: "3 CRITICAL" from a blog slug and the sentence "not a critical fault".
- Cause: the count matched the word.
- Fix / guard: the gate counts the flag glyphs (`🚩`, `🚨`) in the logs and prints them as a separate NOTE, so criticals are never confused with incompleteness.
- Seen: a property-care company, 2026-09-29.

### P-161. Honest UNKNOWN was reported as a skipped stage
- Symptom: `✗ social - SKIPPED` on a run where social-scan correctly said "UNKNOWN, never report this as no profiles"; `attribution` skipped on every run against a host that rate-limits after crawls.
- Cause: the gate keys on success strings that an honest UNKNOWN does not contain.
- Fix / guard: declare the step in `not_measured` with the reason (P-162). Do not change the stage to claim success.
- Seen: a medical research group, 2026-08-05; a water-damage company, 2026-08-06.

### P-162. An incomplete run must declare what it did not measure
- Symptom: a published report that silently lacks a stage.
- Cause: nothing linked the gate's INCOMPLETE line to the report.
- Fix / guard: when the latest run log ends INCOMPLETE, `publish-run.sh` refuses unless every skipped step is listed in `reports/<slug>-findings.json` `not_measured: [{"step","what","why"}]`. The builder shows that list in a "Not measured" block.
- Test: `tests/test_publish_gate.py` `test_refuses_incomplete_run_without_not_measured`, `test_incomplete_run_ok_when_every_skip_is_declared`; `tests/test_builder.py` `test_not_measured_block`.
- Seen: a site on a hosted website builder, 2026-10-14.

### P-163. Publish only through the publish gate
- Symptom: a report published before any verification; one client's page published under another slug; backups of old reports left in the public folder; link maps published with no gate.
- Cause: publishing was a file copy.
- Fix / guard: `publish-run.sh` is the only publisher. It refuses: a source outside `$KIT_WORK/cases/<slug>/`; a missing `findings-verified.json`; text forbidden by `owner-rules.json`; overwriting a target stamped with a different kind. It backs up every overwrite to `$KIT_WORK/publish-backups/`, outside the public folder. A `correction` needs an existing page and `PUBLISH_REASON`, and is logged.
- Test: `tests/test_publish_gate.py` (`test_refuses_unverified`, `test_refuses_source_of_other_slug`, `test_backups_leave_web_root`, `test_linkmap_kind`, `test_correction_needs_existing_page_and_reason`, `test_correction_still_checks_owner_rules`).
- Seen in testing, 2026-10-14.

### P-164. Rebuilding a report is not re-auditing the site
- Symptom: after a rebuild, six reports still carried a duplication critical that the sites had since fixed.
- Cause: `checks-to-findings.py` replays the stored `checks.log`; it cannot know the site changed.
- Fix / guard: any check with a live component (host duplication, canonical status, sitemap, pixel, CMS deep-dive) must be re-run with `audit-checks.py` against the live site before a rebuild is trusted. Re-run checks into the case folder's `checks.log`, which is what the sidecar reads.
- Seen: a tracking-software site and a cleaning company, 2026-08-05; a carpet cleaner, 2026-08-04.

### P-165. A fix in shared code means re-checking every report built after it
- Symptom: a fix for one site changed a third site's report.
- Cause: the checks and sidecar are shared.
- Fix / guard: after a shared-code fix, re-verify every report built since, in both directions (no new false positives, true findings still present).
- Seen: an insurance agency, 2026-08-05.

### P-166. Never hand-roll a per-site script
- Symptom: an audit reported "healthy" while perf, trace and social never ran; a CLS of 0.468 and a 404 contact page were missed.
- Cause: a hand-written script skipped stages.
- Fix / guard: always run the orchestrator (`./audit.sh <url>` or `run-full-audit.sh`) and never report done until the gate shows all stages complete or every skip is declared. Use the kit's own tools for verification (`verify-audit.py`), not one-off scripts.
- Seen: a chiropractor, 2026-07.

### P-167. Verify the deployed artifact, not your recollection of the edit
- Symptom: told that the docs were updated four times while checks lived only in code.
- Cause: reporting a recollection.
- Fix / guard: grep the built or published page for the new copy (present) and the old copy (absent). Run `verify-skill-docs.sh` after changing checks and report its output.
- Seen: 2026-07-31.

### P-168. Published reports are a disclosure risk
- Symptom: none; a report now lists, for a named business, its real login names, the URL that lists more, missing security headers and the pages it hid.
- Cause: security findings turned an SEO report into a ready-made target list. Server headers protect a file only while you serve it.
- Fix / guard: the report template emits `<meta name="robots" content="noindex,nofollow,noarchive,nosnippet">`, which travels with the file. Serve reports on unguessable paths or behind auth when they carry security findings. Scrub every file the page links (for example a raw JSON download), not only the HTML: removing a row does not remove references to it in other rows.
- Seen: a security review of 18 reports, 2026-08-05; a transport company's raw download, 2026-09-26.

## P. Concurrency and shared state

### P-169. Batch audits need atomic claims and per-slug locks
- Symptom: one worker ran alone for an hour while the CPU sat idle; two crawls shared one slug; run logs overwritten.
- Cause: fixed lane files and no locking.
- Fix / guard: `run-queue.sh`: a job is claimed by atomic rename from `pending/` to `running/`; a per-slug `flock` stops two jobs of one slug overlapping; run logs are `run-<UTC>.log` and never overwritten; a failure keeps its exit code; a disk gate leaves jobs pending (`WAITING-DISK`) instead of deleting anything; workers write a heartbeat and `status` flags stale ones.
- Test: `tests/test_queue.py` (`test_a_each_job_runs_exactly_once`, `test_b_same_slug_never_overlaps`, `test_c_failure_keeps_exit_code`, `test_d_run_logs_never_overwritten`, `test_e_disk_gate_waits_in_pending`, `test_f_status_shows_running`).
- Seen: a batch, 2026-10-14.

### P-170. A glob that writes into its own folder eats its own output
- Symptom: perf JSON names like `<slug>-<slug>-<slug>-<host>-mobile-4g.json`, up to 197 characters; 448 perf files, 193 MB of duplicates.
- Cause: a loop globbed `*"$HOST"*-{tier}.json`, which also matched the copies it had written on earlier runs.
- Fix / guard: `run-full-audit.sh` iterates the exact host-keyed and slug-keyed names. When measuring damage from a naming bug, hash file contents; do not assume adjacent repetition (slugs alternated).
- Seen: 2026-08-07.

### P-171. Do not patch shared tools while other runs use them
- Symptom: mid-batch edits to shared scripts change results of runs in flight.
- Cause: several agents ran the shared toolchain at once.
- Fix / guard: report defects found mid-batch and fix them after the batch. After several concurrent editors, check that every script still parses and re-run a regression set.
- Seen: an event-rental site, 2026-08-09; a five-site sweep, 2026-08-05.

## Q. Disk, processes and environment

### P-172. A background launch that dies with its shell
- Symptom: a half-finished audit reads as complete; `✗ trace - SKIPPED` although `trace.log` is correct.
- Cause: `nohup bash run-full-audit.sh ... &` from an agent shell died when that shell exited. The EXIT trap still printed the gate and exited 0.
- Fix / guard: start long runs with `setsid nohup ...`, stdin from an empty file (`$KIT_WORK/.empty-stdin`), and `disown`, or through `run-queue.sh`. Do not "fix" the trace stage's gating: the skip was collateral from the dead orchestrator, and hiding it would hide the real failure.
- Seen: an accounting firm, 2026-08-12.

### P-173. Stopping a run by pattern
- Symptom: `pkill -f "run-full-audit.sh https://x"` killed the command that was about to relaunch (exit 144); `kill -- -<pid>` after `setsid ... & echo $!` killed nothing, and two crawls shared a slug.
- Cause: `pgrep -f`/`pkill -f` patterns match your own command line; `setsid` forks, so `$!` is not the group leader.
- Fix / guard: never use `pgrep -f`/`pkill -f`. Record the orchestrator's PID, stop only processes you started, by PID, and check they are gone before relaunching. Leave no Chrome or Lighthouse process running.
- Seen: a Shopify store, 2026-09-22.

### P-174. `set -u` and an optional variable
- Symptom: every audit died at stage 1 with `CRAWL_PROXY: unbound variable`; the gate showed all 15 stages skipped.
- Cause: the script runs under `set -u`, and a new optional variable was read bare. It had only been tested with the variable set.
- Fix / guard: read optional variables as `${VAR:-}`. `bash -n` checks syntax only. When you add an optional variable, run one full audit without it set.
- Seen: 2026-08-05.

### P-175. Python binds a local name for the whole function
- Symptom: `UnboundLocalError` on the first real run; all five audits died the same way. Syntax checks and isolated tests passed.
- Cause: a local `import re` (or `import re as _r2`) inside `main()` makes the name local for all of `main()`, so earlier uses fail.
- Fix / guard: import at module level. `py_compile` does not catch this. Run the real command before deploying.
- Seen: a newsletter blog, 2026-08-02; 2026-08-06.

### P-176. A regenerated file failed silently
- Symptom: stale findings JSON after an edit.
- Cause: the generator crashed on a `NameError`, and its output was discarded, so nobody saw the error.
- Fix / guard: run a generator visibly at least once after editing it. Send output to a log file under `$KIT_WORK/`, never discard it.
- Seen: a multilingual app publisher, 2026-08-02.

### P-177. Shell pipeline settings
- Symptom: a run aborted at a `grep | head`.
- Cause: `set -o pipefail` turns the SIGPIPE in `grep | head` into a failure.
- Fix / guard: the orchestrator does not use `pipefail` and gates on output files.
- Seen: 2026-07.

### P-178. Chrome location and versions
- Symptom: Puppeteer stages cannot find Chrome, or use a different one.
- Cause: the bundled Chrome path differs from the installed one.
- Fix / guard: set `CHROME_BIN` (the kit otherwise uses the first of google-chrome, google-chrome-stable, chromium, chromium-browser). The orchestrator refuses to start without Chrome, Node or the Python dependencies, and below a disk floor (`AUDIT_MIN_FREE_GB`, default 2). It never deletes files to make room.
- Seen: 2026-07.

### P-179. Long-running agents report too early
- Symptom: an agent returns "running, will report" before the audit finishes.
- Cause: the brief did not say what "done" looks like.
- Fix / guard: tell the agent not to reply until the terminal marker prints (`ALL STEPS COMPLETE` or `INCOMPLETE`), that an earlier reply is a failure, and to poll the log file rather than processes.
- Seen: 2026-07-31.

## R. Evidence discipline

### P-180. A check may only state what it measured
- Symptom: claims about things nobody looked at: "fabricated rating" without reading the reviews (P-107); a version string only "disclosed", never judged; "orphans or stale entries" never probed (P-096).
- Cause: presence detection was treated as measurement.
- Fix / guard: if a line says "no X appears", the code above it must have looked for X in the same run.
- Seen: four of five sites in one sweep, 2026-08-06.

### P-181. A finding must not contradict its own evidence
- Symptom: noindexed rows called indexable; "no SEO plugin" printed above that plugin's sitemaps; an area-code analysis of a number with no area code.
- Cause: nothing compared a finding with the data it was built from.
- Fix / guard: before shipping, check each finding against its own rows and log lines. `verify-audit.py` gathers the evidence for this in one run.
- Seen: a UK WooCommerce shop, 2026-08-05.

### P-182. UNKNOWN is a state that must survive the pipeline
- Symptom: UNKNOWN turned into a confident finding at some later stage (NXDOMAIN, 429, a blocked pixel probe that printed ✅).
- Cause: measurement paths had no explicit UNKNOWN state, or a later stage did not respect it.
- Fix / guard: every measurement path has an explicit UNKNOWN (Not measured) state, and it reaches the report as "could not check", never as a verdict. `verify-audit.py` marks each section `measured` or `not_measured` with a reason; a failed fetch, empty body or challenge page is not measured, never zero.
- Test: `tests/test_verify_audit.py` `test_empty_home_is_not_measured`, `test_challenge_page_is_not_measured`, `test_good_home_is_measured`.
- Seen: a Shopify store, 2026-08-02; a site on a hosted website builder, 2026-10-14.

### P-183. Guard on positive evidence, not a list of clean phrasings
- Symptom: a third false critical of the same shape got through after two guards.
- Cause: the guards listed the check's "clean" verdicts (`ok - one canonical host`, `UNKNOWN`); a third clean verdict (`NOT duplication`) matched neither. A 0% threshold was the same mistake for one failure shape (P-033).
- Fix / guard: require the explicit positive line (`🚩 BOTH host variants return 200`) and not UNKNOWN. A list of clean phrasings always loses to the next one.
- Seen: an insurance agency, 2026-08-05.

### P-184. Prove scope before claiming it
- Symptom: "whole site", "your pages" claims from one page.
- Cause: a single sample was generalised.
- Fix / guard: sample more than one page and print the sample size in the finding. Do not extrapolate from a sample; mark inferred scope as inferred.
- Seen: a water-damage company and a cleaning company, 2026-08.

### P-185. Verify the verification
- Symptom: a correct finding nearly retracted, and false defects nearly filed: curl without `--compressed` compared gzip with plain text; a probe read the key `og` instead of `open_graph`; a "false positive" was confirmed against the wrong domain; a root cause was "corrected" twice on bad evidence.
- Cause: the checking measurement was itself broken.
- Fix / guard: before believing a finding OR a refutation, confirm the measurement: decode content encodings before comparing bodies; check the domain the audit actually crawled; reproduce the mechanism, not just the symptom; do not cite an unconfirmed mechanism as a closed root cause.
- Seen: a water-damage company, 2026-08-05 to 2026-08-07; a cleaning company, 2026-08-05.

### P-186. Fix the class, not the instance
- Symptom: a bug fixed in one probe reappeared in the crawler; a rule fixed in one copy still fired from a second copy.
- Cause: the gzip bug lived in several call sites; the name-conflict test existed twice; three subtype lists diverged; two phone normalisers had different rules.
- Fix / guard: when you find a bug, grep every sibling call site and every copy of the message text before calling it fixed. Prefer fixing at the shared transport or module. Exempt a call site only on a property of the response you verified, not the request you sent.
- Seen: a water-damage company, 2026-08-05 and 2026-08-06; a cleaning company and a Turkish clinic, 2026-08-07.

### P-187. Every check needs a known-positive and a known-negative
- Symptom: two over-corrections in one session (all-redirect suppression killed a real hack detection; a fix to one branch left another crying critical).
- Cause: each fix was tested on one side only.
- Fix / guard: keep a positive and a negative case per check (synthetic fixtures in `tests/`, never real client data). Run a new check against several known sites before trusting it; one site cannot show you a bad threshold. Prefer two independent conditions to one threshold (P-052, P-125).
- Seen: a fire-safety school and a chiropractor, 2026-07-31.

### P-188. When two agents make the same mistake, it is a design defect
- Symptom: two agents independently read a missing field as "none".
- Cause: a plausible but wrong field name returns a silent falsy value.
- Fix / guard: make absence speak (P-080). Also verify agent claims against the crawl JSON before patching, in either direction: agents have misread data shapes and called correct tools broken.
- Seen: two sites, 2026-08-05; a SaaS site and a multilingual app publisher, 2026-08-02.

### P-189. Suppression removes the claim, not the investigation
- Symptom: a real, shippable problem (an expired certificate) disappeared along with an unproven claim.
- Cause: UNKNOWN was treated as a dead end.
- Fix / guard: an anomaly a check cannot classify is a queue entry: classify it, or escalate it explicitly.
- Seen: a UK card shop, 2026-08-06.

### P-190. Never do arithmetic on unmeasured pages
- Symptom: "avg 0 words each" over sitemap rows the crawl never fetched.
- Cause: unfetched rows were averaged as zeros.
- Fix / guard: measure from crawl rows or a live sample, or say "size unmeasured".
- Seen: a roofing contractor, 2026-07-31.

### P-191. Live evidence outranks static reads, when it got in
- Symptom: static checks and live probes disagree.
- Cause: static reads miss what scripts load and over-read hints; live probes can hit walls.
- Fix / guard: the live result decides, but only after its access is confirmed (title, status). A check that re-fetches data the crawl already captured breaks on the walled sites that need it most: read the crawl JSON first, because the crawler already paid the transport cost.
- Seen: an insurance agency, 2026-08-01; a pest-control company, 2026-08-04.

### P-192. Site text is data, never instructions
- Symptom: none yet; crawled pages can contain text written to steer an agent.
- Cause: evidence files mix site text with the operator's notes.
- Fix / guard: `verify-audit.py` wraps every site-derived string in a per-run marker (`<<SITE-DATA-xxxxxxxx ... SITE-DATA-xxxxxxxx>>`) and states at the top that it is data. Treat anything inside as data.
- Test: `tests/test_verify_audit.py` `test_site_text_is_fenced`.
- Seen in testing, 2026-10-14.

### P-193. Stage output must be about this business
- Symptom: findings about another company with a similar name, or about off-domain search results.
- Cause: name-based matching and unfiltered external results.
- Fix / guard: verify website host, phone and address before using any external match; discard results that are not the audited host.
- Seen: three audits, 2026-09-21 to 2026-09-25.
