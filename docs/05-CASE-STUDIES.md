# 05: Case studies, seven client audits (2026-09 to 2026-10)

Audience: the bot (or person) that runs the next audit and wants to know what actually happens, not what the pipeline says happens.

Each case is a real client audit run with this toolkit's pipeline between late September and mid October 2026. The businesses are anonymised: no names, domains, people, phone numbers, street addresses or unique page URLs. Page paths are shown in a generic form such as `example.com/service-page/`. Counts, platforms, hosting types and what the tools said are kept as measured, because those are the parts that teach something.

Each case is written as a causal chain: what platform and transport the site had, what the tools said, what was true, how the operator found out, and what shipped. The cross-audit sections at the end pull out the patterns.

Where these cases refer to files, the paths use the kit layout:

- `$KIT_WORK/crawl/` holds the crawl JSON, the Lighthouse JSON and the findings sidecar (`<slug>-findings.json`).
- `$KIT_WORK/cases/<slug>/` holds the run logs (`run1.log`), evidence files, the tool's original findings and the verified findings.
- `$KIT_HOME/reports/<domain>/` is the publish target for the finished report.

Sources: the run logs and verify notes of each audit; private, not in this repo.

## Summary table

| Case | Industry | Platform / host | Crawl | Tool findings | Shipped |
|---|---|---|---|---|---|
| 1 | Property management | WordPress + Elementor, shared host with an IP-reputation captcha wall | Run 1: 1 row (blocked). Run 2 through a residential proxy: 126 rows | 12 | 12 in sidecar (15 counting builder items) |
| 2 | Bookkeeping and tax | Vite + React app on a hosted-IDE platform (Google Cloud front end) | 1 row (client-side-rendered shell) | 10 | 8 |
| 3 | Pest control | WordPress + Elementor, mainstream shared host | 888 pages, 0 blocked | 15 | 12 + 3 builder |
| 4 | Tree service and land clearing | AI-builder Vite/React SPA, Cloudflare | 1 row (CSR shell); rendered by hand | 10 | 9 |
| 5 | Warehouse space rental (multi-location, national) | Astro SSR, Cloudflare | 547 pages, 545 real | 15 | 12 + builder |
| 6 | Spray foam removal (UK) | Wix | 63 crawled, 21 blocked, curl fallback to 62 real | 5 | 8 (+ email-style report) |
| 7 | Custom home design-build | Astro on nginx | 112 crawled, 109 real | 5 | 4 |

"Tool findings" is the count printed by the pipeline's findings step in `run1.log` (the line "[6b] ... N finding(s)"), checked against the tool's original findings file kept beside the verified one.

---

## Case 1: property management, WordPress, 2026-09

### Platform, hosting, crawl
- WordPress with the Hello Elementor theme, on a shared host that fronts sites with an IP-reputation captcha wall.
- Run 1 (plain transport): the first request got 200, then the crawl burst tripped the host's wall. Every later request got a 202 captcha page. Crawl = 1 row. The completion gate still showed 16/16 green. Run 1 was declared INVALID and kept aside as a control (`run1.log` shows "crawled 1 pages ... 0 real-content" and "STILL THIN after fallback").
- Run 2: residential proxy via `CRAWL_PROXY`, a Safari 17 user agent via `CRAWL_UA`, `CRAWL_DELAY=3`, `CRAWL_CONCURRENCY=2`. Result: 126 rows, 124 x 200, 3 captcha shells retried to real pages.
- Lighthouse ran from a separate machine, one tier at a time, and each JSON was checked for the right `finalDisplayedUrl` and no `runtimeError`.
- The render check saw "Robot Challenge Screen" on pages 2 and 3; the poison guard kept all render findings out of the report.

### Tool said, what was true

| Tool said | What was true | Evidence | Shipped as |
|---|---|---|---|
| HIGH "1 link on your site leads to a page that isn't there" | `example.com/service-pages/` (linked from 26 pages) 301s to a misspelled address, which 404s. The real page `example.com/service-page/` is 200 | live re-probe through the proxy | Rewritten: "1 link on your site redirects to a misspelled address that isn't there" |
| Builder re-probe: 202 counted as "re-verified OK" | 202 was the captcha wall; it nearly dropped the real 404 | a no-proxy rebuild control now keeps the 404 | Pipeline fix in `build-crawl-report.js` |
| MEDIUM "1 additional live website found on your domain" | `links.example.com` is a link-tracking endpoint (a bare "OK!" from an Express app behind Cloudflare) | direct fetch | Rewritten and downgraded to LOW: "A link-tracking address runs on your domain (not a second website)" |
| MEDIUM "phone number that is not yours, confirm it is quoted content" | An out-of-area number inside privacy-policy text; every click-to-call link uses the business's one real number | page text | Kept as a "confirm" item |
| Gate note "3 CRITICAL" | No critical finding exists. An older gate matched the word "critical" (here in a blog slug and a sentence). `run-full-audit.sh` now counts the flag and siren markers in `checks.log` and `hack.log`, not the word | gate output vs sidecar | Not shipped |
| Run 1 gate 16/16 green | Crawl was 1 blocked row | `run1.log` | Run discarded, re-run via proxy |
| Pre-consent tracking finding | Policy: tracker/consent findings never go in a client report | kit policy | Moved to the operator-only findings file |

### Findings that shipped (severity order)
1. HIGH: redirect to a misspelled 404 address, linked from 26 pages.
2. HIGH: 58 pages reachable only through the sitemap.
3. MEDIUM: duplicate title (a post and its `-2` copy).
4. MEDIUM: out-of-area number in privacy-policy text (confirm).
5. MEDIUM: `http://` serves the site with no redirect and no HSTS (verified: 200, no redirect target, 0 HSTS headers).
6. MEDIUM: own links point at 5 redirecting addresses.
7. MEDIUM: category archives indexable (3 identical 474-word pages).
8. MEDIUM: `/wp-json/wp/v2/users` lists 3 accounts; one user slug is built from a personal email address.
9. MEDIUM: search-engine index estimate lower than the crawl (an estimate; confirm in Search Console).
10-11. MEDIUM: 5 pages missing H1; 5 missing meta description (builder).
12. LOW: link-tracking subdomain (corrected wording).
13-15. LOW: security headers, version disclosure, 9 pages without a share image.

Found by a human: the typo chain behind the 404 (by following the redirect hop by hop), and the true identity of the tracking subdomain (by fetching it).

### Time sinks
- Run 1 had to be thrown away. Root cause: the host's IP-reputation wall blocks a datacenter IP after a burst, and the completion gate checked that stages ran, not that pages were fetched.
- The report rebuild had to be done again with the proxy variables exported. Root cause: the builder re-probes errors live, so a rebuild without the proxy sees the captcha again.
- The builder's re-probe code had to be patched. Root cause: it treated any non-error status (including a 202 captcha) as "re-verified OK".

---

## Case 2: bookkeeping and tax, Vite + React, 2026-09

### Platform, hosting, crawl
- Built and deployed on a hosted-IDE platform (verification TXT records on apex and www; "Google Frontend" response headers). Vite + React + Radix + Framer Motion. No CMS: every edit is code plus a redeploy. 21 routes in the menu.
- Pre-crawl probes: every route, a made-up path, `robots.txt` and `sitemap.xml` all return the identical 914-byte shell. A Googlebot user agent gets the same.
- Crawl: 1 row; the raw HTML links to nothing. Render check: CSR and GATED on all 3 sampled pages. `run1.log`: "words: rendered=825 raw=4 JS-ONLY DELTA=+821 (99% of the page)".
- Lighthouse: mobile-4g 35, LCP 7.8 s; desktop 89; mobile-3g 29.

### Tool said, what was true

| Tool said | What was true | Evidence | Shipped as |
|---|---|---|---|
| CRITICAL "Possible hacked / injected content indexed" | The search API returned 4.54 billion results for `site:<domain> inurl:.php`: the `inurl:` operator was dropped and the whole-web count came back | `run1.log` line "-> 4540000000 results" | Dropped |
| HIGH "The site is too small to compete in search" | The crawl cannot see past JavaScript; the menu has 21 routes | rendered homepage, 21 internal routes | Dropped |
| CRITICAL "1 pages are empty shells until JavaScript runs" | True for all 21 routes | identical shell md5 on every route | Widened: "All 21 pages ... empty shells until JavaScript runs" |
| MEDIUM "About 100% of your page is blank until extra code finishes running" | Same root cause as the shell finding | render check | Merged into the shell finding |
| MEDIUM "2 additional live websites found on your domain" | `go.` = a marketing-funnel page with the placeholder title "Title"; `link.` = click tracking | DNS CNAMEs to a marketing-automation vendor | Rewritten: "A marketing funnel page on go.example.com is live with the placeholder title 'Title'" |
| MEDIUM "Your site has no sitemap at all" | Also `robots.txt` is the HTML app | 914-byte shell for `robots.txt` | Rewritten: "no sitemap, and its robots.txt is actually a web page" |
| (not found by tool) | apex and www both serve the site, no redirect, no canonical | measured on 5 routes | Added as HIGH |

Keep the phrase "empty shells until JavaScript" in the shell finding's title: the builder folds its own H1, meta and OG fragments into it by that phrase.

### Findings that shipped (severity order)
1. CRITICAL: all 21 pages are empty shells until JavaScript runs.
2. HIGH: no structured data (none raw, none rendered).
3. HIGH: made-up addresses answer 200 (soft 404s).
4. HIGH: site answers on both the apex and www hosts (human-added).
5. MEDIUM: no sitemap; `robots.txt` is the HTML app.
6. MEDIUM: raw titles identical; JavaScript rewrites them.
7. MEDIUM: funnel subdomain with the title "Title" (human-rewritten).
8. LOW: security headers.

Also recorded but outside the site report: DNS and email. The domain was due to expire about two months after the audit; no DKIM; no DMARC; SPF covered Google only, so any marketing-platform mail sent as the domain would fail authentication.

### Time sinks
- Two of ten tool findings were false and one was a duplicate. Root cause: the pipeline reads raw HTML, and a CSR app's raw HTML is a 914-byte shell, so every "absence" finding is an artifact.
- The hacked-content critical. Root cause: the search API silently drops the `inurl:` operator and returns the whole-web count.

---

## Case 3: pest control, WordPress, 2026-10

### Platform, hosting, crawl
- WordPress 7.0.4 + Elementor on a mainstream shared host, www -> apex 301. DNS clean (SPF, DKIM selector1/2, DMARC p=reject).
- The business has two offices in one US metro (a storefront and a service-area office). The site's click-to-call uses a third number. This matters for the address finding below.
- Crawl: 888 pages, 0 blocked.
- Render step SKIPPED: headless Chrome got an "Access ... was denied" page on some URLs, so content-attribution numbers were invalid. The raw HTML is server-rendered, so there was no CSR risk.

### Tool said, what was true

| Tool said | What was true | Evidence | Shipped as |
|---|---|---|---|
| CRITICAL "Possible hacked / injected content indexed" (`inurl:.php` = 4.56 billion) | Operator dropped, as in Case 2. But a real hack existed: a hidden casino link block on the homepage | `run1.log`; saved homepage HTML in the case folder | Replaced by CRITICAL "Your homepage has 60 hidden casino links planted in it" |
| "92 pages carry spam links" | The casino URLs 301 to the homepage and inherit the homepage's link list in the crawl record. Real carrier: the homepage only | redirect trace | Rewritten as homepage-only |
| CRITICAL "The same page exists under several different addresses" (sets with the same 1292 word count) | Sets were 53% similar by difflib. Only capitalization pairs were real duplicates: 28 city pages x2, each self-canonical | difflib | Rewritten and downgraded to HIGH: "28 of your city pages exist twice, once with a capital letter" |
| MEDIUM "The hidden business label ... doesn't say you're a local business" | False: the homepage declares `PestControl`, a `LocalBusiness` subtype. Real issue: the schema address (office B) differs from the footer address (office A) | schema vs footer vs the business's public listing | Upgraded to CRITICAL "Your homepage gives Google two different addresses" |
| MEDIUM "phone number that is not yours, confirm it is quoted content" | An out-of-state number sits in the call-to-action on several posts and two Elementor templates | page text | Upgraded to HIGH |
| MEDIUM "Your own links point at 68 old addresses" | 58+ were the casino links; the rest folded into the redirect finding | link list | Folded into HIGH "Pages that don't exist forward to your homepage" |
| HIGH "Addresses that don't exist ... answer as if they do" | Every missing URL 301s home, which hides a button that points at a missing service page (178 pages) and a doubled city-page href (93 pages) | live probes | Rewritten with the hidden broken links named |
| MEDIUM "Some menu links lead nowhere" | Covered by the redirect finding | same | Folded |
| "POSSIBLE UNDER-INDEXING: Google's estimate (~10)" | An estimate only | `run1.log` | Not shipped |
| CRITICAL pre-consent trackers | Policy | kit policy | Moved to the operator-only file |

### Findings that shipped (severity order)
1. CRITICAL: 60 hidden casino links on the homepage (an Elementor HTML widget: a `div` positioned at `left:-9999px`, carrying a rotation batch id). Human found.
2. CRITICAL: the homepage gives Google two addresses (schema + map iframe = office B; footer = office A). Human found.
3. HIGH: 28 city pages duplicated by capitalization.
4. HIGH: missing pages forward home, hiding site-wide broken links.
5. HIGH: out-of-state phone number in post calls-to-action.
6. MEDIUM: footer address and hours have empty hrefs. Human found.
7. MEDIUM: duplicate titles; tag, category and author archives indexable.
8. LOW: security headers; version leak.
Builder adds: H1 missing (265), meta description (607), og:image (312).

### Time sinks
- A rebuild silently kept the old findings. Root cause: the builder read `<slug>-findings.json` before `<host>-findings.json`, and the operator had edited the host file. Caught by grepping the new titles in the HTML. The kit now writes and reads only `$KIT_WORK/crawl/<slug>-findings.json`.
- "92 carriers" had to be re-traced to 1. Root cause: the crawl record copies the final page's links onto every URL that redirects to it.
- The duplicate-content sets had to be measured. Root cause: the check groups pages by equal word count, not by text similarity.

---

## Case 4: tree service and land clearing, AI-builder React SPA, 2026-10

### Platform, hosting, crawl
- A Vite/React single-page app generated with an AI site builder, behind Cloudflare; the domain was a few months old.
- Crawl: 1 row, 0 real-content. Content attribution: "rendered=1145 raw=11 ... (99% of the page)".
- After the crawl, the operator rendered every route with headless Chrome and saved the renders in the case folder. Rendered DOM: 13 routes, 49 images, 3 forms, `LocalBusiness` + `FAQPage` + `BreadcrumbList` schema.

### Tool said, what was true

| Tool said | What was true | Evidence | Shipped as |
|---|---|---|---|
| HIGH "The site is too small to compete in search" | 13 real routes | rendered DOM | Dropped |
| MEDIUM "The site has no photographs at all" | 49 images | rendered DOM | Dropped |
| MEDIUM "Visitors have no way to contact you except calling" | 3 forms, posting to a marketing-platform webhook | rendered DOM; the JS bundle | Dropped |
| HIGH "Pages are missing the structured description" | Schema present after render | rendered DOM | Folded into finding 1 |
| MEDIUM "JS rewrites titles" and "100% blank" | Same root cause | render | Folded into finding 1 |
| Builder "missing H1" | CSR artifact | render | Suppressed by titling finding 1 with "empty shells until JavaScript" |
| CRITICAL "The sitemap ... contains pages that should not be there" | The sitemap lists 13 URLs on the builder's preview host (`<project>.<builder>.app`, which 302s to the real domain) | the saved `sitemap.xml` | Rewritten: "Your sitemap lists another website's addresses" |
| HIGH soft 404 | Raw 200, rendered "Page Not Found" | render | Kept, downgraded to MEDIUM |
| (none) "the email address on the site is dead", first check | The first check used one resolver and a bare `nc` with empty output: it proved nothing | rerun with controls | Redone properly (see below), then shipped HIGH |
| (none) "does the form work?" | The webhook answered the same for real and fake IDs | fake-ID controls | Not a finding; the success message proves nothing about delivery |

### Findings that shipped (severity order)
1. CRITICAL: pages are empty shells until JavaScript runs (every route + a made-up path = the same shell md5; Googlebot and GPTBot user agents get the same).
2. CRITICAL: the sitemap lists another website's addresses (the builder's preview host). Human found by reading the sitemap host, not just its URL count.
3. HIGH: the site claims "87+ reviews" (visible and in schema `reviewCount` 87) while the business's public profile shows 25; 4 homepage testimonials labelled as Google reviews are not among the 25. Human found.
4. HIGH: a public form route with no login whose text describes a "5 star review request funnel (gate keeping negative reviews)"; it is listed under `Disallow` in `robots.txt` and is noindex after render. Human found by reading the JS bundle. Not submitted.
5. HIGH: the contact email address on the site cannot receive email. Human found.
6. MEDIUM: six city pages about 95% the same (difflib on rendered main text).
7. MEDIUM: schema has no street address, `geo` about 2 mi from the real location, `sameAs` empty. The homepage map embed points at the real business listing (credited).
8. MEDIUM: soft 404.
9. LOW: security headers.

### How the email and form claims were proven
- Email: a failed lookup is not "no MX"; no MX plus an A record means mail goes to the A record; only an RCPT probe proves a mailbox. The rerun found no MX on two public resolvers and on the authoritative name server (NOERROR, SOA only), and ports 25 and 587 on the A record timed out from two separate networks. The control, a known-good public mail server on port 25, answered 220 from both networks.
- Form: no live submission was made, because it would create a CRM contact and trigger texting. The operator tested the CORS preflight and the webhook with fake-ID controls. A GET returned the same body for the real hook, a fake hook id and a fake location id; a POST to the fake ids returned 200 "Success: test request received". So the site's success message proves nothing about delivery.

### Time sinks
- Six tool findings had to be removed or folded. Root cause: the CSR shell again; the pipeline's raw-HTML view sees no content.
- The email claim had to be redone. Root cause: the first test had no control, so an empty `nc` result was read as proof.
- The form question needed a second method. Root cause: the webhook-existence check had no fake-ID control at first, and once one was added it showed the check tested nothing.

---

## Case 5: warehouse space rental, Astro, 2026-10

### Platform, hosting, crawl
- Astro 6.4.8, server-rendered, behind Cloudflare; www -> apex 308. 428 sitemap URLs in 9 files; 33 location pages in 15 US states.
- Crawl: 547 pages, 545 real, 0 blocked. Lighthouse mobile 30-39 (TBT about 2.2 s), desktop 91.
- DNS: Microsoft 365 + a transactional-mail provider with DKIM, DMARC p=reject. SPF also lists two more mail services whose DKIM was not found by guessable selector: marked CHECK, not reported.

### Tool said, what was true

| Tool said | What was true | Evidence | Shipped as |
|---|---|---|---|
| CRITICAL "Some links on your site are typed wrong" | The only example was a real press link to a directory site, flagged because its host contains the substring "local" | the one example | Dropped |
| MEDIUM "Old addresses from a previous version ... are now dead ends" ("10 of 15 archived URLs dead") | 8 of 10 were `/wp-content/` and page-cache static assets. Following all 35 old `/locations/` URLs found the real problem: 3 variants for a location in one state land on another state's page; 17 land on the generic index though the exact page exists. An external business listing links into that chain | a URL list and a TSV of each old URL's final destination, in the case folder | Replaced by HIGH "Old location addresses send people to the wrong page, including one that lands on a different state" |
| MEDIUM "Your own links point at 15 old addresses" | The footer's Acceptable Use link (509 pages) 302s to the homepage; the policy is now a section of the terms of service; an archive capture showed the old page live three months earlier. Also two other footer links (206 and 65 pages) | live probes; archive capture | Folded into HIGH "Your footer's Acceptable Use link opens the homepage on all 509 pages" |
| MEDIUM "9 of your pages can only be found through your sitemap" | True, and 2 of them are the main money pages (2,743 and 2,653 words), 0 inlinks | crawl link graph | Upgraded to HIGH with the two pages named |
| CRITICAL "Your sitemap sends Google to 2 old addresses" | True but 2 of 428 | sitemap | Downgraded to MEDIUM |
| MEDIUM "25 additional live websites found on your domain" | Internal tool subdomains: one behind a shared password, one with no login on its first screen; the others gate to a login, SAML or a hosting provider's auth. Only first screens were read | subdomain list in the case folder | Rewritten: "Internal tool sites on your domain: confirm each one is locked" |
| CRITICAL pre-consent trackers; MEDIUM "trackers and a consent banner" | Policy | kit policy | Both moved to the operator-only file |

### Findings that shipped (severity order)
1. HIGH: old location addresses send people to the wrong page, including one that lands on a different state's page. Human found by following every old URL.
2. HIGH: the footer Acceptable Use link opens the homepage on all 509 pages. Human found.
3. HIGH: the two money pages have no inbound links.
4. HIGH: a cost-guide page 404s, linked from a comparison page.
5. MEDIUM: sitemap lists 2 redirecting URLs.
6. MEDIUM: internal tool sites, confirm each one is locked.
7. MEDIUM: 1 www internal link; duplicate titles; homepage schema missing a phone number; 6 noindex pages to confirm; slug/title mismatch.
8. LOW: security headers.

### Time sinks
- The archived-URL finding had to be rebuilt from scratch. Root cause: the discovery step counted asset URLs as pages, so "most archived URLs are dead" was mostly images and cache files.
- The footer link needed an archive lookup to prove the policy page had existed and moved. Root cause: a 302 to the homepage looks like a working link to every check that only follows status codes.

---

## Case 6: spray foam removal, Wix, 2026-10

### Platform, hosting, crawl
- A UK business. Wix (generator tag, `x-wix-request-id` header); apex and `http://` -> `https://www` 301. Google Workspace mail, DMARC p=none, no DKIM at `google._domainkey`.
- Crawl: 63 crawled, 21 blocked (Wix rate limiting, 429-type), curl fallback -> 62 real; 59 x 200: 24 posts, 23 blog tag pages, 10 core pages, 2 portfolio pages.
- Lighthouse mobile 22-33 (TBT about 2 s, CLS 0.16), desktop 83.

### Tool said, what was true

| Tool said | What was true | Evidence | Shipped as |
|---|---|---|---|
| HIGH "1 link on your site leads to a page that isn't there" | The operator first called it a "truncated link" after misreading a print that cut URLs at 100 characters. Real: a post links to another post that was deleted (404 live, "We Couldn't Find This Page") | live fetch | Kept, MEDIUM, with the source post and the link words |
| MEDIUM "2 links use the wrong version of your own address" | 3: the check found all 3 (`checks.log` lists all 3 source pages), but the sidecar pattern in `checks-to-findings.py` kept only the first source page per target, so the title said 2. One of them, "Contact us", opens the home page, not the contact page | link inventory | Corrected count to 3 with source, link words and target |
| MEDIUM "6 of your pages are asking Google not to show them" | 16: a risk quiz + 15 tag pages | noindex scan | Corrected: "16 of your pages ask Google not to show them: confirm the risk quiz" |
| (none) "9 links in 8 posts" to an old copy of the homepage (operator's first count) | 7 posts; the 8th "source" was the old copy linking to itself | link inventory | MEDIUM "Nine blog links point to an old copy of your homepage" |
| CRITICAL pre-consent trackers | Policy | kit policy | Moved to the operator-only file |

### Findings that shipped (severity order)
1. HIGH: the whole country is served from 10 core pages; there are no service or area pages. Human judgment from the page inventory.
2. MEDIUM: the header phone number is plain text; the only tap-to-call number is a different (mobile) number, which matches the schema. Human found by reading the header DOM for `tel:` hrefs.
3. MEDIUM: 9 blog links to an old copy of the homepage (canonical -> home, not in the sitemap). Human found.
4. MEDIUM: broken link to a deleted post.
5. MEDIUM: 3 non-www links.
6. MEDIUM: 16 noindex pages; confirm the risk quiz.
7. LOW: email domain not protected (no DKIM, DMARC none). Human-added.
8. LOW: security headers.
Builder adds meta description (2) and og:image (2).

### Time sinks
- The broken-link explanation was wrong once. Root cause: the operator read a print that cut URLs at 100 characters and took the cut for the defect.
- Three counts were wrong once (8 posts, 2 links, 6 noindex). Root cause: each count came from a partial source (the sidecar pattern that keeps the first source page per target, a self-link counted as a source, a partial noindex list) instead of a full inventory.
- The whole report was rewritten in a plain email style (see section C). Root cause: the builder page lists findings for an SEO reader; the client needs pages, link words and target addresses in plain sentences.

---

## Case 7: custom home design-build, Astro, 2026-10

### Platform, hosting, crawl
- Astro on nginx; `robots.txt` sets `Crawl-delay: 1`, so the run used `CRAWL_DELAY=1`.
- Crawl: 112 crawled, 109 real, 0 blocked. Lighthouse mobile 4G 99, desktop 100. 13 city pages of 2,250 to 4,000 words: a strong site.
- Email: a privacy-focused mail provider with DKIM at that provider's own three selectors; DMARC p=quarantine. Clean. Checking only the common selectors would have reported DKIM missing.
- Discovery: 918 archived URLs not reached; a sample of 15 had 13 dead, spread across path prefixes (normal churn), not reported.

### Tool said, what was true

| Tool said | What was true | Evidence | Shipped as |
|---|---|---|---|
| MEDIUM "One page shows a phone number that is not yours" | A video-host ID inside a live editor note ("Insert this video in this spot https://..."), matched as a 10-digit run | page text; a note scan of all 109 HTML pages found 1 hit (the known one fired, which served as the control) | Rewritten: "A note to your editor is showing on a live blog post" |
| MEDIUM "Some page addresses describe a different article" | A guidebook page uses the home page's title | title compare | Rewritten: "Your guidebook page uses your home page title" |
| HIGH "1 link on your site leads to a page that isn't there" | The thank-you download on a relocation-guide landing page points to a PDF that is 404: the lead magnet is broken. A second guidebook PDF is 200 | live fetch of the download | Rewritten: "Your relocation guide isn't there when a visitor signs up for it" |
| Builder duplicate of the same 404 | The builder adds its own broken-link finding unless the sidecar title contains "isn't there" | rendered report | Suppressed by putting "isn't there" in the title |
| MEDIUM "2 of your pages are asking Google not to show them" | A client portal and an HTML sitemap page: intended | page purpose | Dropped |
| CRITICAL pre-consent trackers | Policy | kit policy | Moved to the operator-only file |
| (none) | The site-wide map embed is a name+address search, not an embed of the business's own listing | iframe `src` in `run1.log` | Added as LOW |

### Findings that shipped (severity order)
1. HIGH: the relocation guide download is 404 (lead magnet broken). Human found by testing the thank-you download.
2. MEDIUM: an editor note is visible on a live blog post. Human found by reading the sentence around the "phone number".
3. MEDIUM: the guidebook page uses the home page title.
4. LOW: the site-wide map embed searches instead of showing the business's own listing.

### Time sinks
- Two tool findings needed a full rewrite. Root cause: the phone check matches any 10-digit run in text, and the slug check compares the slug to the title without noticing that the title is the home page's.
- The broken PDF showed up twice in the report. Root cause: the builder merges its own broken-link finding with the sidecar only by a title phrase.

---

## Cross-audit section A: false-positive types, frequency across all 7

Counted from the "Tool said" tables above. One case counts once per type.

| False-positive type | Cases | Count | What it really was |
|---|---|---|---|
| Pre-consent tracker finding (moved by policy, not false) | 1, 3, 5 (x2), 6, 7 | 5 | Client-report policy: operator-only file |
| "Additional live website(s)" on a subdomain | 1, 2, 5 | 3 | Link tracking, a funnel page, internal tools |
| "Phone number not yours" | 1 (kept as confirm), 3 (upgraded), 7 (was a video ID) | 3 | Quoted text, a real wrong number, an editor note |
| Count wrong in a shipped-candidate finding | 3 (92 carriers -> 1), 6 (8 -> 7 posts, 2 -> 3 links, 6 -> 16 noindex), 5 (10 dead -> 2 real pages) | 3 | Redirect inheritance, one field read, assets counted as pages |
| CSR shell artifacts (too small, no photos, no form, no schema, 100% blank, JS rewrites titles) | 2, 4 | 2 | Raw HTML of a JavaScript app |
| Hacked content from `inurl:.php` (4.5 billion results) | 2, 3 | 2 | The search API dropped the operator (Case 3 did have a real, different hack) |
| Old / archived addresses dead | 3 (68 old addresses), 5 (15 old, 10/15 archived dead) | 2 | Casino links; asset URLs |
| Severity inflated | 5 (sitemap 2 of 428 = critical), 3 (duplicate set = critical) | 2 | Downgraded |
| Builder duplicate or residue around a sidecar finding | 4 (missing H1), 7 (PDF 404 twice) | 2 | The title phrase controls the merge |
| Duplicate content by equal word count | 3 | 1 | 53% similar by difflib |
| Schema "generic label" | 3 | 1 | `PestControl` is a `LocalBusiness` subtype |
| Substring match on a host ("typed wrong") | 5 | 1 | A host that contains "local" |
| Slug/title mismatch | 7 | 1 | The page used the home page title |
| Intended noindex flagged | 7 | 1 | Client portal, HTML sitemap |
| Gate "critical" from a word match | 1 | 1 | The word in a slug (older gate; `run-full-audit.sh` now counts flag markers) |
| Completion gate green on a blocked or CSR crawl | 1 (16/16 green on 1 row); 2 and 4 also 1-row crawls | 3 | The gate checked stages, not page count (now a thin crawl stops the run) |
| Re-probe accepting a captcha status | 1 | 1 | 202 counted as OK |

Pattern: the crawl and checks are good at finding where to look, and poor at saying what it is. Of 72 tool findings across the 7 runs (12 + 10 + 15 + 10 + 15 + 5 + 5), a large share was rewritten, merged or dropped before shipping; every case had at least one finding whose wording changed meaning after a human look. See docs/KNOWN-PITFALLS.md (false positives) for the standing list.

## Cross-audit section B: only a human found it

| Finding | Case | Technique |
|---|---|---|
| 60 hidden casino links (a `div` at `left:-9999px` in an Elementor HTML widget) | 3 | Reading the raw homepage HTML after the hack check fired for the wrong reason; fetching the casino URLs with a Googlebot user agent |
| Two addresses (schema + map iframe vs footer) | 3 | Reading the schema, the iframe query and the footer side by side |
| Out-of-state phone in post calls-to-action and Elementor templates | 3 | Reading the sentence around the number |
| Missing pages 301 home, hiding a broken service-page link on 178 pages | 3 | Following each redirect to its end |
| Public review-gating form page | 4 | Reading the JS bundle, then rendering the route |
| "87+ reviews" on the site vs 25 on the public profile; testimonials not among the 25 | 4 | Rendering the DOM and comparing with the public review count |
| Sitemap on the builder's preview host | 4 | Reading the sitemap host, not just its URL count |
| Email address on the site cannot receive mail | 4 | DNS on three resolvers + port probes from two networks, with a known-good control |
| Form success message proves nothing | 4 | Fake-ID controls against the webhook |
| Wrong-state redirects for old location URLs | 5 | Following every old `/locations/` URL (35) to its end |
| Acceptable Use footer link 302s home on 509 pages; the policy moved | 5 | Following the footer link, then an archive capture |
| Link-tracking subdomain, not a website | 1 | Fetching it |
| Typo redirect chain behind a 404 | 1 | Following the redirect hop by hop |
| Apex and www both serve, no canonical | 2 | Probing 5 routes on both hosts |
| Funnel page titled "Title" | 2 | Fetching the subdomain the tool called a "website" |
| Header phone not tappable; tap calls a different number | 6 | Reading the header DOM for `tel:` hrefs |
| 9 links to an old copy of the homepage | 6 | A full link inventory by source page and link words |
| Broken lead-magnet PDF on the thank-you download | 7 | Testing the thank-you download, not only crawled links |
| Editor note on a live post | 7 | Reading the sentence around a "phone number", then scanning all 109 pages for notes, with the known hit as the control |
| Provider DKIM present (would have read "missing") | 7 | Using the mail provider's own selectors |

Techniques, ranked by how often they found something:
1. Following every old or redirecting URL to its end (Cases 1, 3, 5).
2. Reading the sentence around a number or flagged string (Cases 3, 5, 7).
3. Rendering the DOM, and reading the JS bundle for apps (Cases 2, 4).
4. Comparing the same fact across sources: schema vs footer vs public listing vs map embed (Cases 3, 4, 6).
5. Testing the user path the crawl does not take: thank-you downloads, forms (with fake-ID controls), mail ports (Cases 4, 7).

## Cross-audit section C: how the deliverable format changed

Stage 1 (Cases 1-2): builder HTML from the tool's sidecar, hand-corrected.
- The report was the light-mode page produced by `build-crawl-report.js` from the findings sidecar, published noindex.
- The operator edited the sidecar in place and kept the original beside it as `findings-before-verify.json`.

Stage 2 (Cases 3-5, and kept since): verified findings with the tool's original kept beside them.
- After the Case 3 file-precedence mistake, each case keeps, in `$KIT_WORK/cases/<slug>/`, the tool's original findings (`findings-slug-auto-original.json`), the original report (`site-audit-auto-original.html`) and a hand-built `findings-verified.json`. The verified list is what goes into `$KIT_WORK/crawl/<slug>-findings.json` before the rebuild.
- Finding titles became client sentences that say what is wrong ("Your homepage has 60 hidden casino links planted in it", "Your sitemap lists another website's addresses").

Stage 3 (Case 6): an email-style page in ASD-STE100 Simplified Technical English.
- The client-facing report became a short email-style page; the builder page was kept as a linked `-detailed.html` appendix.
- Structure: a subject line, a greeting, a three-sentence intro, then "What works well" first.
- "Fix these first" (3 fixes), each with "What we found", "Why it is important" and "What to do" as numbered steps.
- Every link fix names the exact source page (title + full URL), the exact link words, and the target address to change it to. For example: "Contact us" on post X -> "Change the link to https://www.example.com/contact".
- "Smaller fixes" use "Where to look" lists with the same three parts.
- Platform click paths are given where the fix is in a vendor dashboard (the Wix noindex setting, the mail provider's DKIM setup).
- The page build checks the copy: sentences over 25 words (descriptive) or 20 words (procedural), a banned-word list, dashes and semicolons. A deliberately bad control sentence was injected and failed as expected, which proved the check works.
- Case 7 was delivered in the Stage 2 format; whether to use the email style for every case is not yet a standing rule.
- The finished report is published to `$KIT_HOME/reports/<domain>/` only through `publish-run.sh`, never by copying files by hand.

## Five lessons for the next audit

1. Check the page count before anything else. A blocked or CSR crawl passed the completion gate green with 1 row (Cases 1, 2 and 4). Fix the transport (`CRAWL_PROXY`, `CRAWL_DELAY`, `CRAWL_UA`) or render the DOM, then audit. See docs/KNOWN-PITFALLS.md (thin crawls).
2. Treat every tool finding as a pointer, not a sentence. Read the text around the match, follow redirects to their end, and recount from a full inventory. Every case here shipped at least one finding the tool had worded wrong.
3. The highest-value findings came from paths the crawler does not walk: old URLs from the web archive and external listings, thank-you downloads, JS bundles, header `tel:` links, schema vs footer vs public listing. Budget time for these on every audit.
4. Every "it is dead / it works / it is missing" claim needs a control you watched give the other answer: a known-good mail server, a fake webhook ID, the mail provider's own DKIM selectors, a known hit for a text scan.
5. Write the client deliverable for the client: plain STE sentences, exact source page, exact link words, exact target address. Keep the builder page as the detailed appendix, and edit the sidecar the builder actually reads (`$KIT_WORK/crawl/<slug>-findings.json`), then grep the rebuilt HTML for the new titles.
