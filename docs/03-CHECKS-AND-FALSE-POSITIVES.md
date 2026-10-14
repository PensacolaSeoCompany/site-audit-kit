# 03. Checks and false positives

Who this is for: the bot or person who verifies a site audit before anyone else reads it.

What it covers: every automatic check that produced a false or misleading finding in seven real audits (2026-09-29 to 2026-10-13, written up in docs/05-CASE-STUDIES.md), plus the older checks those findings rest on. Each card gives:
- **Location**: the script and the check (search for the check header; line numbers drift as the code changes).
- **What it measures**: what the code really does.
- **What a reader assumes**: the wrong reading that makes the finding dangerous.
- **How it misfired**: every recorded wrong result, anonymised (case numbers point to docs/05-CASE-STUDIES.md).
- **Verify before shipping**: the step to run before the finding goes in a report.
- **Guard**: the code fix, with the control that must fail without it. "Status" says whether the guard is in the code as of 2026-10-14; "(confirm)" means it was not checked line by line.

## Paths used on this page

| Name | Path |
|---|---|
| KIT_HOME | the repo root |
| KIT_WORK | `${KIT_WORK:-$KIT_HOME/work}` |
| PY | `$KIT_HOME/.venv/bin/python` if it exists, else `python3` |
| Crawl JSON | `$KIT_WORK/crawl/<slug>-full.json` (rows under `pages`) |
| Tool sidecar (the findings file the builder reads) | `$KIT_WORK/crawl/<slug>-findings.json` |
| Operator-only findings | `$KIT_WORK/crawl/<slug>-operator-findings.json` (never shown to a client) |
| Case folder | `$KIT_WORK/cases/<slug>/`: stage logs (`checks.log`, `hack.log`, `discovery.log`, `render.log`, `attribution.log`, `cms.log`, `pixel.log`), `run1.log`, `evidence/` |
| Final report | `$KIT_HOME/reports/<domain>/` (site-audit.html, report.md, report.pdf, findings.json, linkmap.html) |

Tool output quoted here has its long dashes replaced by a plain hyphen and its flag emoji replaced by the word "(flag)". Domains, paths, numbers and addresses from real sites are replaced with placeholders.

## Kit rules every card depends on

1. **Blocked is not broken.** A 403, 429, 503, 202 captcha page or any bot wall means "could not check". It is never "broken", "missing" or "zero".
2. **Not measured is not zero.** A run whose completion gate prints `INCOMPLETE` must declare each skipped step in the findings file under `not_measured` (see docs/07-RUNBOOK.md, step 5). A failed Lighthouse run shows "Not measured", never a score of 0.
3. **No title or meta description LENGTH findings.** No 60/160-character limits. The rules file (`report-rules.json`, or the file named by `KIT_RULES`; older copies call it `owner-rules.json`) bans this text, and `publish-run.sh` refuses a page that carries it.
4. **The pre-consent tracker finding stays operator-only.** It goes to the operator-findings file, never to a client page; the rules file bans its wording on published pages (Card 13).
5. **Never send Google traffic through a proxy.** `CRAWL_PROXY` is for the audited site only; `run-full-audit.sh` refuses to start when a general proxy variable is set.
6. **Never delete files to free disk** unless the operator decides to. The disk gates wait; they never clean up.

## Three facts that hold for every card

1. `checks-to-findings.py` turns each flagged block of `checks.log` into a finding by parsing the printed text. Several wrong counts below come from that parse, not from the check. Read the check's own number in `checks.log` before you trust the number in a finding title.
2. The crawl follows redirects and stores the destination page's data under the requested URL (`full-extract.py` resolves links against `final_url` and writes the row under `url`). Any count of "pages that carry X" that does not drop `redirected: true` rows is inflated (docs/KNOWN-PITFALLS.md (redirect destinations are not pages of this site)).
3. The completion gate proves that each stage ran, not that its input was good. A green gate on a 1-row crawl is a failed audit.

---

## Card 1. CSR / JavaScript shell family (thin site, no photos, no forms, no schema, title rewrite)

**Location.**
- Verdict: `render-check.py` (`main`); the CONTENT verdict says CSR when raw words are under 40% of rendered words.
- The CSR flag the sidecar uses: `_csr_blind` in `checks-to-findings.py`.
- Checks that read raw crawl fields: check 3 structured data, check 4 site size, check 23 lead capture (form count), check 25 visual content (image count), all in `audit-checks.py`.
- Title-rewrite finding: `checks-to-findings.py`, fires on any `TITLE DIFFERS` line in `render.log`.

**What it measures.** render-check fetches 3 pages (plus up to 2 auto-added 0-word rows) raw and in headless Chrome, and compares word, link and vocabulary counts. Checks 3, 4, 23 and 25 then count JSON-LD types, unique 200 URLs (flag at 3 or fewer), `<form>` tags and `images_total` in the raw crawl rows. On a client-rendered site the raw crawl holds one shell page with about 10 words and no links, so the crawl has 1 row and every count is zero.

**What a reader assumes.** That "too small to compete", "no photographs", "no contact form" and "no structured data" describe the site a visitor and Google see.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 4 (tree service, app-builder SPA) | Check 4: "1 unique 200-status pages crawled ... THIN SITE"; check 25: ZERO images; check 23: ZERO forms; check 3: "1 page(s) crawled, ZERO with JSON-LD" | 13 routes, 49 images, 3 forms posting to a webhook, LocalBusiness + FAQ + Breadcrumb schema after render | Headless Chrome `--dump-dom` of every sitemap route, kept in the case folder under `rendered/`; `render.log` shows `words raw/rendered 10/2047 · links 0/78 · jsonld 0/2` | 2026-10-07 |
| Case 4 | "JS rewrites titles" and "100% blank" as separate findings | One cause (the shell); folded into the CSR finding | Case write-up | 2026-10-07 |
| Case 4 | Builder "missing H1" finding returned beside the shell finding | The builder suppresses it only when the shell finding's title contains "empty shells until JavaScript" | Case write-up | 2026-10-07 |
| Case 2 (tax firm, Vite/React SPA) | "too small" (check 4: "1 unique 200-status pages crawled") | 21 routes in the menu | Pre-crawl probe: every route, a made-up path, robots.txt and sitemap.xml return the same 914-byte shell (same md5); rendered home 849 words, 21 internal routes | 2026-09-30 |
| Case 2 | H1/meta/OG findings shipped next to the 21-page shell finding | Fragments of the same cause | Case write-up | 2026-09-30 |

Older audits of the same class (2026-08): a noindex prescription that would have deindexed 36 real pages on a CSR site, and two more CSR sites crawled by curl where every raw-HTML content finding was crawler blindness (docs/KNOWN-PITFALLS.md (CSR sites break every raw-HTML check)).

**Verify before shipping.**
1. `grep -n 'CONTENT:' $KIT_WORK/cases/<slug>/render.log`. Any `CONTENT: CSR` line means every raw-HTML content finding is suspect.
2. Count crawl rows: `$PY -c "import json;print(len(json.load(open('$KIT_WORK/crawl/<slug>-full.json'))['pages']))"`. 1 row on a site with a menu is a shell, not a tiny site.
3. Render every sitemap route from the audit machine: `"$CHROME_BIN" --headless=new --no-sandbox --dump-dom <url> > $KIT_WORK/cases/<slug>/rendered/<name>.html`, then count `<img`, `<form` and `application/ld+json` in the rendered files. Judge checks 3, 4, 23 and 25 on those counts.
4. Keep the phrase `empty shells until JavaScript` in the shell finding's title (the builder matches on it), then grep the built HTML for "missing a main headline": expect 0.

**Guard.** When `_csr_blind` is true, skip checks 3, 4, 23 and 25 in the sidecar (as checks 8 and 22 already are) and record them as not measured; add one line to the CSR shell finding: "Our crawler could not count pages, photos, forms or schema; they exist only after JavaScript runs." Long term: render every sitemap route and feed the rendered counts to those checks.
- Status: in the code. `checks-to-findings.py` skips 3, 4, 23, 25 under CSR, lists them as `not_measured_csr_checks` in the operator-findings file and prints "NOT MEASURED (client-rendered site; check on rendered pages by hand)". Rendering every route is still a manual step (docs/07-RUNBOOK.md, step 3).
- Control that must fail without the guard: a synthetic 1-row crawl with `render.log` saying `CONTENT: CSR` must not yield "too small", "no photographs", "no contact form" or "no structured data".
- Positive controls that must still fire: a server-rendered site with zero forms, and one with zero images; the CSR gate does not touch them.

---

## Card 2. Duplicate content: "N pages serve IDENTICAL content" (check 21)

**Location.** `audit-checks.py`, check 21 (IDENTICAL-CONTENT PAGE SETS).

**What it measures.** For each 200, non-redirected, indexable, self-canonical, non-paginator row with 200 or more words, the key is `(word_count, tuple of H1 texts)`. Any key shared by 3 or more URLs is printed as "IDENTICAL content". No text is compared. The crawl row stores no body text or hash.

**What a reader assumes.** That the pages have the same words.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 3 (pest control, WordPress) | "5 pages serve IDENTICAL content (1292 words each)": five service-in-city pages | Same template and same word count; bodies 53% similar | difflib on the page bodies: ratio 0.53 | 2026-10-01 |
| Case 3 | 4-page sets at 1062/1039/1037 words mixed two real problems: `/service-in-city-a/` and `/service-in-City-A/` (the same page under two capitalizations, each self-canonical) grouped with `/service-in-city-b/` (a different city) | Only the capitalization pairs were duplicates: 28 city pages x2 | Case write-up | 2026-10-01 |

**Verify before shipping.**
1. For each set, case-fold the paths. Pairs that differ only in letter case are real duplicates; report them as such.
2. For the rest, fetch two members and compare the main text: `$PY -c "import difflib,sys;a,b=(open(f).read() for f in sys.argv[1:3]);print(round(difflib.SequenceMatcher(None,a,b).ratio(),2))" a.txt b.txt`. Below about 0.9 is a shared template, not duplicate content. Ship it at most as "similar template pages" with the ratio.

**Guard.** In `full-extract.py` store a 64-bit simhash of the main text in each row. In check 21, keep the `(word_count, h1)` key only as a pre-filter and require a simhash Hamming distance of 3 or less to call a set identical. Add a separate rule: URLs whose paths are equal after `.lower()` are duplicates regardless of text.
- Status: open (the check comment says the simhash pass lives in an optional module the orchestrator does not run) (confirm).
- Control that must fail without the guard: a synthetic crawl with 5 rows of equal word count and H1 but different bodies must not print IDENTICAL, while a `/a-city/` + `/A-City/` pair must still fire.

---

## Card 3. Copied titles and slug mismatch (checks 8 and 22)

**Location.** `audit-checks.py`, check 8 (copied/placeholder titles; the 2 to 4 page branch) and check 22 (slug vs title).

**What it measures.**
- Check 8 (2 to 4 pages): keeps only "distinctive" title words (4+ letters, present in under 30% of all titles). If fewer than 2 remain, the group is skipped with no output.
- Check 22: if a slug has 3+ words of 4+ letters and none of them appear in the title, it prints "slug describes a different topic than the title".

**What a reader assumes.** That check 8 "ok" means no two pages share a title, and that check 22 means the page address is wrong.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 7 (home builder, static site) | Check 22: a guidebook page "slug describes a different topic than the title"; check 8: "ok - no placeholder or mass-duplicate titles" | The slug is right. The page carries the home page title | Replaying check 8's word filter on the crawl: 4 of the shared title's 5 words appear in 30%+ of titles, so 1 word is left and the group is skipped silently | 2026-10-12 |

**Verify before shipping.** For each check-22 URL, compare its title to the home page title and to every other title: `$PY -c "import json,collections;P=json.load(open('$KIT_WORK/crawl/<slug>-full.json'))['pages'];c=collections.Counter((p.get('title') or '').strip() for p in P if p.get('status')==200 and not p.get('redirected'));[print(n,t) for t,n in c.items() if n>1]"`. If the title belongs to another page, the finding is "this page uses the X page's title", not "wrong address".

**Guard.** In check 8, replace the silent skip with the plain "N pages share the identical title" message. In check 22, before reporting a slug mismatch, test whether the page title equals another page's title; if so print "uses the title of <url>".
- Status: open (confirm).
- Control that must fail without the guard: a synthetic crawl where one inner page carries the home page title made of common words must be listed by check 8, and check 22 must name the home page as the title's owner.

---

## Card 4. "Phone number not yours" (check 9, displayed numbers)

**Location.** `audit-checks.py`, check 9; the displayed-number pattern `SHOWN`; the "OTHER number(s) appear in the TEXT of one page" branch. Client title in `checks-to-findings.py`: "One page shows a phone number that is not yours - confirm it is quoted content".

**What it measures.** On a live sample of the home page plus 7 deep pages, it strips tags, scripts and some attributes, then matches any 10 digits shaped like a US number, with or without separators. URLs in visible text are not removed. A number that is not one of the `tel:` links, on one page only, becomes "not yours".

**What a reader assumes.** That a real phone number belonging to someone else is printed on the site.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 7 | "(212) 555-0147 on /blog/a-post/" | A video ID inside a leftover editor note: "Insert this video in this spot https://video.example.com/2125550147". The real defect is the visible editor note | Reading the sentence around the number; a note scan of all 109 pages found 1 hit. Offline replay: `SHOWN.findall('... https://video.example.com/2125550147')` returns `('212','555','0147')` | 2026-10-13 |
| Case 3 | "Phone not yours, confirm quoted" (MEDIUM) | Understated: a stray number sat in several posts, a tag page and two page-builder library templates; upgraded to HIGH | Case write-up | 2026-10-01 |
| Case 1 (property management) | "a number in the privacy-policy text ... confirm it is quoted content" shipped as MEDIUM | Not resolved before shipping; the finding asked the client to do the check | Case write-up | 2026-09-29 |

**Verify before shipping.** For every flagged number, print the sentence around it: `grep -o '.\{80\}2125550147.\{40\}' <saved page html>` (use the flagged digits). Decide one of: inside a URL or ID (drop it); a third party named in the sentence (drop it); a stray number of this business (ship it as its own finding with every page it appears on). `verify-audit.py` prints each number with its surrounding words in the `phones` section. Numbers outside the US: the check applies US rules; ignore "not a working number" for other countries.

**Guard.** Before `SHOWN.findall`, remove URLs from the visible text (`re.sub(r'https?://\S+|\b[\w.-]+\.(com|net|org)/\S*', ' ', _vis)`), and for numbers with no `tel:` link require at least one separator or parenthesis (10 bare digits is an ID far more often than a phone).
- Status: open (confirm).
- Control that must fail without the guard: a synthetic page with `https://video.example.com/2125550147` in its text must not produce a number.
- Positive control that must still fire: a synthetic page with a 555 test number written with dashes (area code, then `555-0198`) in plain text and a different `tel:` link.

---

## Card 5. Malformed hrefs: LOCAL_LEAK (check 10)

**Location.** `audit-checks.py`, check 10.

**What it measured.** In the 8-page live sample, every `<a href>` was matched against `^https?://(localhost|127\.0\.0\.1|[^/]*(staging|\.local|\.test))`. The `[^/]*` let `.local` and `.test` match anywhere in the host, so `www.localnews.example` matched (".local" right after "www"). Any hit set the critical flag.

**What a reader assumes.** That the site links to a development or staging copy of itself.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 5 (multi-location brand, static site) | "LOCAL_LEAK: 1 link(s) e.g. https://www.localnews.example/business/..." shipped as CRITICAL "links typed wrong" | A real press article on a local news site. The CRITICAL had no other evidence | Offline replay of the pattern: `www.localnews.example` True, `localnews.example` False, `www.testimonials.example` True | 2026-10-10 |

**Verify before shipping.** Open every example href. A LOCAL_LEAK is real only when the host is `localhost` or `127.0.0.1`, ends in `.local`, `.test` or `.localhost`, or has a staging/dev label of the audited domain (`staging.example.com`). Check that the count is more than the one example.

**Guard.** Parse the host and flag only reserved names (`localhost`, `127.0.0.1`, hosts ending in `.local`, `.test`, `.localhost`, `.invalid`) and staging/dev labels of the audited domain.
- Status: in the code (`_LEAK_LABELS` in `audit-checks.py`, with a comment naming this false positive).
- Control that must fail without the guard: `https://www.localnews.example/...` must not fire.
- Positive controls that must still fire: `https://dev.local/`, `https://staging.example.com/`, `http://localhost:8080/`.

---

## Card 6. Hack-scan: index estimate, `inurl:.php` probe, orphan list

**Location.** `hack-scan.py`: the `site:` estimate and the spam-path probes; client finding "Possible hacked / injected content indexed" in `checks-to-findings.py`.

**What it measures.**
- The `site:<apex>` total from a search-results API versus the crawl's indexable count; flags a surplus over 1.3x or a shortfall under 0.65x. It needs an API key; without one the step cannot measure (confirm how the kit reports that).
- Three probes (`site:<dom>/autodiscover`, `site:<dom> inurl:.php`, `site:<dom> kamagra OR viagra OR cialis`). The alarm is raised if ANY organic result comes back. It never checks that the result is on the audited domain.
- The sidecar then makes "Possible hacked / injected content indexed" (CRITICAL) and fills its URL list with the first 20 URLs found ANYWHERE in `hack.log`, which are the sitemap-orphan lines at the top of the log, not the alarm hits.

**What a reader assumes.** That Google has hacked pages of this site in its index, and that the listed URLs are those pages.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 2 | "`site:example.com inurl:.php` -> 4540000000 results" with php.net, Wikipedia's PHP page, a PHP forum; CRITICAL hacked content | The search API dropped the `site:` operator; the results were the whole web. The crawl also had 1 row (CSR), so the estimate-vs-crawl comparison was meaningless | Reading the hit hosts | 2026-09-30 |
| Case 3 | "`site:example.com inurl:.php` -> 4560000000 results"; CRITICAL with a URL list of `/client_testimonial/`, `/home/`, `/our_service/`, `/tag/...` | The URL list was the 252-row "in sitemap but NOT reached by crawl" list, not hacked pages | `hack.log` | 2026-10-01 |
| Case 3 (missed) | Nothing about the real injection | A hidden 60-link casino block on the home page (a page-builder HTML widget positioned at left:-9999px, with a rotation `data-batch` attribute); a `site:` search for "casino" gave 0 | Saved home page HTML in the case folder | 2026-10-01 |
| Case 3 (not shipped) | "POSSIBLE UNDER-INDEXING: Google's estimate (~10) is below the 794 indexable URLs" | Unverified estimate; not used | The check itself says to confirm in Search Console | 2026-10-01 |

**Verify before shipping.**
1. `grep -n -A6 'inurl:.php' $KIT_WORK/cases/<slug>/hack.log`. If any listed hit is not on the audited domain, the probe is void.
2. Compare the CRITICAL's URL list with `grep -n 'NOT reached by crawl' $KIT_WORK/cases/<slug>/hack.log`; if they are the orphan list, the list is mislabelled.
3. If the crawl has 3 or fewer rows (CSR or blocked), drop every estimate comparison.
4. Look for hidden link blocks the scan cannot see: in the saved home page HTML, `grep -o 'left:-9999px\|data-batch="[^"]*"' evidence-home-*.html`, and count off-topic anchors (casino, loan, pharma words). Two tries out of two, the "hacked content" finding from the index estimate was false and the real injection was found by reading the HTML.

**Guard.** Keep only hits whose host equals the apex or ends with `.<apex>`; print the alarm only if at least one survives, and print the surviving count rather than the API's total. In the sidecar, take URLs only from the lines under the alarm. Skip the whole section when the crawl has fewer than 3 indexable rows. Add a static check for hidden link blocks (anchors inside an element positioned off-screen).
- Status: open; the probe loop still alarms on any organic result (confirm on the current code).
- Control that must fail without the guard: a synthetic `hack.log` whose `inurl:.php` hits are all on other hosts must not yield the CRITICAL.
- Positive control that must still fire: a site with 100+ pharma pages under `/autodiscover/` (docs/KNOWN-PITFALLS.md (orphaned hacked content)).
- Positive control for the hidden-block check: a synthetic home page with 60 anchors inside a `left:-9999px` div.

---

## Card 7. Archived URL liveness (url-discovery Wayback sample)

**Location.** `url-discovery.py`, the Wayback section: the random sample and the live test.

**What it measures.** Pulls up to 5,000 archived URLs, takes a fixed random sample of 15 (seed 7), and requests each with redirects FOLLOWED. Under 400 counts as live, 404/410 as dead. If 60%+ are dead and one first path segment holds 60%+ of the dead, it prints "MOST ARCHIVED URLs ARE NOW DEAD". Asset files (images, scripts) are not excluded. A redirect to the wrong page counts as live.

**What a reader assumes.** That old PAGES of the site are dead and their links lost, and that the live ones are fine.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 5 | "liveness sample (15 URLs): 5 live · 10 dead (66% 404/410) ... MOST ARCHIVED URLs ARE NOW DEAD ... dead prefixes: /wp-content/ (6), /cache_static/ (2), /articles/ (1)" | 8 of the 10 dead were old asset files. The real problem hid in the "live" bucket: of 35 old `/locations/` URLs, 3 variants forwarded to a page for a different state and 17 forwarded to the generic index although the exact page existed; the business's Google profile linked into that chain | Following all 35 old `/locations/` URLs to their final page | 2026-10-10 |
| Case 7 | "sample 13/15 dead spread across prefixes" | Normal churn; correctly not reported | Case write-up | 2026-10-13 |

**Verify before shipping.**
1. Drop asset paths from the dead list (`/wp-content/`, cache folders, anything ending in an image, font, CSS or JS extension).
2. For every old PAGE URL that matters (location, service, product), follow it to the end without trusting "live": `curl -sIL <old-url> | grep -i '^location\|^HTTP'` (from the audit machine, after the crawl has finished). Ship the ones whose final page is the home page, a generic index or a different place.

**Guard.** Reuse the asset pattern from `audit-checks.py` (`_ASSET_RE`) to drop assets before sampling. Probe with `follow=False` (the `status()` helper supports it) and sort results into four buckets: 2xx; 3xx to the same path on another host (fine); 3xx to the home page or a different section (ship as "old address forwards to the wrong page"); 404/410.
- Status: open for the Wayback sample (confirm).
- Control that must fail without the guard: a synthetic sample of 10 dead URLs, 8 of them assets, must report 2 dead pages; a synthetic old location URL that 301s to the generic index must land in "forwards to the wrong page".

---

## Card 8. Noindex count (check 17 and its sidecar)

**Location.** `audit-checks.py`, check 17 (it lists only the first 6 URLs). Noindex count in `checks-to-findings.py`.

**What it measures.** Check 17 counts 200, non-redirected rows whose meta robots carries `noindex` (login paths excluded) and prints the true count, a per-section breakdown and 6 example URLs. The sidecar does not read that count. It counts the URL lines printed under the block, which can never exceed 6.

**What a reader assumes.** That the number in the finding title is the number of noindexed pages.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 6 (spray-foam removal, hosted site builder) | Auto title: "6 of your pages are asking Google not to show them" | 16: a quiz page plus 15 blog tag pages. `checks.log` itself said "16 crawled page(s) carry noindex" | Replaying the sidecar regex on `checks.log`: 6 URL lines versus a header of 16 | 2026-10-12 |
| Case 7 | "2 of your pages are asking Google not to show them" | Count right; both intended (a client portal and an HTML sitemap); dropped | Case write-up | 2026-10-13 |

**Verify before shipping.** `grep -n 'carry noindex' $KIT_WORK/cases/<slug>/checks.log` and compare with the number in the finding title. Then read the per-section lines and decide per group whether it is intended (logins, portals, tag archives by choice) before shipping.

**Guard.** Parse the header count: `_nn = int(re.search(r"(\d+) crawled page\(s\) carry noindex", body).group(1))`. Better: have check 17 print every URL (or write them to a JSON file the sidecar reads) so the client list is complete.
- Status: open; the sidecar still counts URL lines (confirm).
- Control that must fail without the guard: a synthetic `checks.log` block with header 16 and 6 URL lines must give 16.
- Negative control: a block with 2 noindexed pages must stay 2.

---

## Card 9. Wrong-host links: www / non-www (check 6 and its sidecar)

**Location.** `audit-checks.py`, check 6 (link loops and a raw-HTML scan). Sidecar pair regex in `checks-to-findings.py` (`_pairs6`).

**What it measures.** Collects every `internal_links` and `external_links` href, and every `src=`/`href=` URL in 11 live pages' raw HTML, whose host differs from the crawl seed host only by "w" and "." characters. It groups by target URL and prints at most 8 targets with at most 2 "referenced from" pages each. The sidecar regex pairs each target with only the FIRST "referenced from" line, so a target linked from 2 pages counts once.

Known code defects: `lstrip('w.')` is a character-set strip, not a prefix strip (`'www.wow.com'.lstrip('w.')` gives `'ow.com'`), and the raw scan counts each page's own `<link rel="canonical">` as a link. Both are recorded in docs/KNOWN-PITFALLS.md (www links and canonical tags) (confirm the topic name).

**What a reader assumes.** That the number in the title is the number of links to fix.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 6 | Auto title "2 links use the wrong version of your own address" | 3 links: one on an about page (written as another domain of the same business), one "Contact us" link that went home, not to the contact page, and one "home page" link | `checks.log` check 6 shows 2 targets and 3 "referenced from" lines; replaying the sidecar regex returns 2 pairs. The loss is the sidecar parse, not the check | 2026-10-12 |
| An older audit (photo-booth site) | "6 links use the www. version" | 0 absolute internal links; the 6 were canonical tags, one per page | docs/KNOWN-PITFALLS.md (www links and canonical tags) | 2026-08-09 |

**Verify before shipping.**
1. Count `referenced from:` lines inside the check-6 block of `checks.log` and compare with the title's number. If the block hits the 8-target or 2-source print cap, read the crawl instead: `$PY -c "import json,re;P=json.load(open('$KIT_WORK/crawl/<slug>-full.json'))['pages'];[print(p['url'],l['href']) for p in P if not p.get('redirected') for l in (p.get('internal_links') or [])+(p.get('external_links') or []) if re.sub(r'^https?://','',l.get('href') or '').split('/')[0] in ('<apex>','www.<apex>') and not (l.get('href') or '').startswith('https://<canonical-host>')]"`.
2. If the count equals the page count, you are counting canonical tags.
3. For each link, give the reader the page and the link words, so they can find and fix it.

**Guard.** Have check 6 print one machine line per (target, source) pair (`PAIR <target> <source>`) with no cap, and have the sidecar count those lines. Replace `lstrip('w.')` with `re.sub(r'^www\.', '', h)` and restrict the raw scan's `href=` half to `<a ...>` tags.
- Status: open; `lstrip('w.')` is still in check 6 (confirm).
- Control that must fail without the guard: a synthetic check-6 block with 2 targets and 3 sources must give 3.
- Controls for the other two defects: a synthetic site whose only www references are canonical tags must give 0; host `www.wow.com` must compare as `wow.com`.

---

## Card 10. Links to redirects and "old addresses" (check 15), and redirect rows that inherit links

**Location.** `audit-checks.py`, check 15; sidecar title "Your own links point at N old addresses instead of the current ones" in `checks-to-findings.py`. The inheritance comes from the crawler (`full-extract.py`, see fact 2 above).

**What it measures.** Takes every crawl row that really redirected (path changed, not only a slash), then counts how many internal links in non-redirected rows point at those URLs. It does not look at WHERE they redirect. A link to a page that 302s to the home page (a missing page) and a link to a page that 301s to its new address get the same wording, "old address".

Separately, a redirected row stores the destination's link list under the requested URL. So when 58 spam URLs 301 to the home page, the crawl holds 58 more "pages" that each carry the home page's links.

**What a reader assumes.** That the site links to pages that moved, and that the source counts are distinct pages.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 3 | "68 internal URL(s) redirect, linked 1777x", topped by a casino-themed slug linked 26x; auto title "Your own links point at 68 old addresses" | 58+ of the 68 were the hidden casino links, which 301 to the home page. The rest were real: a button to a commercial service page (178 pages) and a doubled city href (93 pages) | Crawl JSON: 58 casino rows, all redirected, all ending on the home page | 2026-10-01 |
| Case 3 | "92 pages carry spam links" (the operator's first claim) | Home page only | Of the 92 rows whose link list held the casino URL, 66 were redirected rows and the other 26 were the home page and its `?paged=N` views. A live check of 187 posts: all on-topic | 2026-10-01 |
| Case 5 | "16 internal URL(s) redirect, linked 803x ... 509x /resources/acceptable-use" as a minor tidy-up | `/resources/acceptable-use` 302s to the HOME page: the policy page is missing (its text now sits in the terms of service; a Wayback capture showed it was once live). `/legal/acceptable-use` is 404 | Case write-up | 2026-10-10 |

**Verify before shipping.**
1. For each top target, print where it lands: `$PY -c "import json;P=json.load(open('$KIT_WORK/crawl/<slug>-full.json'))['pages'];[print(p['url'],'->',p.get('final_url')) for p in P if p.get('redirected')]" | sort -k3 | uniq -c -f2 | sort -rn | head`. `verify-audit.py` also lists internal links that redirect to the home page (`redirects` section).
2. Any target whose final page is the home page is a missing page (ship as "this link goes to your home page because the page is gone") or a spam URL (move it to the hack finding). Off-topic slugs (casino, loan, pharma) are spam.
3. For any "N pages carry X" count, drop `redirected: true` rows and collapse home page query views before counting.

**Guard.** In check 15, group targets by `final_url` and print a separate block for "forwards to the home page" (final path `/`). Move targets whose slug carries spam words (casino, betting, loan, viagra and the like) to the hack-scan section. Add a crawl-wide helper `carriers(href)` that returns distinct non-redirected pages after collapsing `?query` views of one path, and use it wherever a "pages carrying" count is printed.
- Status: open (confirm).
- Control that must fail without the guard: a synthetic crawl with 5 casino URLs that 301 to `/` must put them in the spam block, and their carrier count must be 1 (home page).
- Second control: a policy URL that 302s to `/` must appear under "forwards to the home page"; a `/quote` to `/book` 301 must stay an ordinary redirect.

---

## Card 11. Soft-404 and "missing page forwards home" (check 28)

**Location.** `audit-checks.py`, check 28 and its "does the page admit an error" test. Client title "Addresses that don't exist on your site answer as if they do" in `checks-to-findings.py`.

**What it measures.** Requests 3 made-up paths with `urllib` (which follows redirects). A final status of 200 counts as a soft 404. It then greps the first 200 KB of the RAW body for "404" or "page not found" to say whether the page admits the error.

**What a reader assumes.** That the server answers 200 at the made-up address, and (from "page does not even say it is an error") that a visitor sees no error message.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 3 | "3 of 3 made-up test addresses ... -> 200 OK (page does not even say it is an error)" | Every missing URL 301s to the home page; the 200 was the home page after the redirect. That also hid real broken links (a commercial service URL linked from 178 pages) | Live probe without `-L` | 2026-10-01 |
| Case 4 | "2 of 3 ... -> 200 OK (page does not even say it is an error)" | Raw 200, but the RENDERED page says "Page Not Found"; the raw body was the JS shell | Rendered DOM | 2026-10-07 |
| Case 2 | 3 of 3 -> 200 | Correct: the shell answers 200 everywhere; shipped HIGH | Case write-up | 2026-09-30 |

**Verify before shipping.** `curl -sI https://<host>/this-page-does-not-exist-xyz/` (no `-L`). 200 = soft 404. 301/302 = "missing pages forward to <Location>" (say where). On a CSR site, render the made-up path and read its visible text before saying anything about the message.

**Guard.** Probe with a no-redirect opener (the file already builds one for check 7). Report three outcomes: 404/410 ok; 200 soft 404; 3xx with its `Location`, worded "every missing address forwards to <target>". When `_csr_blind`, do not print the "does not even say it is an error" clause from the raw body.
- Status: open; the clause is still printed from the raw body (confirm).
- Control that must fail without the guard: a synthetic server that 301s every unknown path to `/` must print "301 -> /", not "200 OK".
- Second control: a synthetic CSR shell must not print "page does not even say it is an error".
- Positive control that must still fire as a 200 soft 404: a server that answers 200 with the same shell for every path (docs/KNOWN-PITFALLS.md (soft-404s)).

---

## Card 12. Schema "generic company label" (check 3, local-entity branch)

**Location.** `audit-checks.py`, check 3; the hand-kept allowlist `_LOCAL_SUBTYPES` near the top of the file. Sidecar title "The hidden business label in your page code doesn't say you're a local business".

**What it measures.** Gathers every JSON-LD `@type` string across the crawl, lowercases them, and intersects with `_LOCAL_SUBTYPES`. If no member matches, Organization is present, and the home page title or meta has a "City, ST" string, it prints "has Organization schema but NO LocalBusiness node". The set contains `pestcontrolservice`, not `pestcontrol`.

**What a reader assumes.** That the site uses the generic company type and lacks a local-business node.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 3 | "has Organization schema but NO LocalBusiness node" while its own printed type list was `['FAQPage', 'OfferCatalog', 'Organization', 'Person', 'PestControl', 'WebPage']`; client finding "generic company label" | The home page declared PestControl. The real defect was the schema address (one street in one city, no telephone) differing from the footer and the business's Google profile (another street in another city) | Reading the home page JSON-LD | 2026-10-01 |

An older miss of the same class: a forgotten `CleaningService` member of the allowlist (comment in `audit-checks.py`).

**Verify before shipping.** Read the printed `Types:` list in the check-3 block. If any type other than Organization/WebPage/WebSite/Person/FAQPage/BreadcrumbList is present, open the home page JSON-LD and check whether that node has `address`, `telephone` and `geo`. Then compare its address and phone with the footer (and the Google profile, if you have one); a mismatch is the finding to ship. `verify-audit.py` prints schema address versus visible text (`address` section).

**Guard.** Judge the node by its properties, not only its name: any node with a `PostalAddress` `address` (or `telephone` plus `geo`) counts as a local entity whatever its type string; keep the allowlist only as a fallback, and add `pestcontrol`. When a Google profile id is given (`GBP_FTID`, off by default), compare the schema address and phone with the profile.
- Status: open; `pestcontrol` is not in the allowlist (confirm).
- Control that must fail without the guard: a synthetic home page with a `PestControl` node that has a `PostalAddress` must print "ok - local entity present".
- Positive control that must still fire: a site with Organization only and a "City & City, ST" title.

---

## Card 13. Pre-consent trackers (check 18 and pixel-consent-check.py)

**Location.** Static layer: `audit-checks.py`, check 18. Live layer: `pixel-consent-check.py` (`check`), with one trusted wheel gesture. Finding "Advertising trackers on your site run before visitors can consent" in `checks-to-findings.py`.

**What it measures.** The live layer cold-loads the page in headless Chrome, moves the mouse, sends one wheel event and lists known tracking hosts contacted before any consent answer. It collects `document.title` but does not print it.

**What a reader assumes.** A legal-risk finding for the site owner.

**Kit rule.** The measurement can be real, but the finding never goes in a client page. The rules file (`report-rules.json`, `page_forbidden`) bans its wording, and `publish-run.sh` refuses any page that carries it.

**Recorded results (removed by rule, not false).** The tool produced this finding on 5 of the 7 audits in docs/05-CASE-STUDIES.md (cases 1, 3, 5, 6, 7; CRITICAL in the auto sidecar on three of them). Each time it was removed by hand from the sidecar the builder reads.

Older probe failures of this check (each can make a NEGATIVE result false): wrong transport, the probe measured a captcha page; wrong layer, a static grep matched `dns-prefetch` hints; wrong interaction, sites that delay their scripts until a real gesture showed nothing to a probe that did not scroll (docs/KNOWN-PITFALLS.md (pre-consent tracking)).

**Verify before shipping.** `grep -c 'before visitors can consent\|Advertising trackers' <built report html>` must be 0. Keep `pixel.log` in the case folder as the record.

**Guard.** Write the check-18 finding to `$KIT_WORK/crawl/<slug>-operator-findings.json` instead of the client sidecar. Print the page title in `pixel-consent-check.py` so a captcha page is visible in `pixel.log`.
- Status: the operator-findings file exists and receives operator-only findings; the publish gate refuses the wording. Printing the page title: open (confirm).
- Control that must fail without the guard: a synthetic page that loads a known tracker before any click must leave 0 tracker findings in the client sidecar and 1 in the operator file; a page carrying the wording must be refused by `publish-run.sh` (`tests/test_publish_gate.py`).

---

## Card 14. Content attribution (content-attribution.py)

**Location.** `content-attribution.py`, the blocked-render guard.

**What it measures.** Raw words versus rendered words per page, and which containers hold JS-only words. It treats the render as blocked only when the rendered page has 3 words or fewer (`total <= 3`). render-check uses a different line: under 25 words.

**What a reader assumes.** That the numbers describe the site's content.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 3 | A city service page: "rendered=17 raw=1359 (raw has 1342 MORE words ... COLLAPSED/HIDDEN at runtime - dropdowns, accordions, tabs ... No action.)" with container `#main-frame-error.interstitial-wrapper` "Access to example.com was denied" | Chrome's own access-denied error page (17 words). No measurement of the site | `attribution.log` | 2026-10-01 |

**Verify before shipping.** `grep -n 'main-frame-error\|was denied\|RENDER FAILED\|RENDER BLOCKED' $KIT_WORK/cases/<slug>/attribution.log $KIT_WORK/cases/<slug>/render.log`. Any hit voids that page's numbers: the page was not measured.

**Guard.** Use one threshold for both scripts (under 25 rendered words with 100+ raw words = blocked) and treat Chrome error markers (`main-frame-error`, "was denied") as blocked. Add "was denied|main-frame-error" to the poisoned-render pattern in `checks-to-findings.py`.
- Status: open; the threshold is still `total <= 3` (confirm).
- Control that must fail without the guard: a synthetic rendered page holding Chrome's access-denied text (17 words) against 1,359 raw words must print RENDER BLOCKED.
- Negative control: a page whose raw HTML really holds more words than the render (content collapsed at runtime) must still print the "raw has MORE words" line.

---

## Card 15. CMS detection (detect-cms.py)

**Location.** `detect-cms.py` (`main`); the verdict line `PLATFORM:`.

**What it measures.** Platform fingerprints from the crawl JSON and a home page fetch; drives the WordPress deep-dive (`cms/wordpress.py`).

**Recorded results.** No false platform claim in the seven audits. It said `unknown` for two app-builder SPAs (cases 2 and 4) and two static-site-generator sites (cases 5 and 7), the right hosted builder for case 6, and `wordpress` for cases 1 and 3. The platform in each write-up came from reading by hand (headers, DNS TXT, generator tag). The risk is the reverse: `unknown` means no platform deep-dive ran, so platform-specific defects (bundle routes and form endpoints on app builders) were found only by reading the JS bundle.

**Verify before shipping.** If `cms.log` says `unknown`, identify the platform by hand (response headers, `generator` meta, DNS TXT verification records) and note it in the case folder. On an app-builder SPA, list the routes in the bundle: `grep -oE 'path:"/[^"]*"' bundle.js` (case 4 found a hidden form route this way).

**Guard.** Add fingerprints for common app builders (builder domains in the sitemap or bundle, verification TXT records) and static generators (`astro-island`, `/_astro/`).
- Status: open (confirm).
- Control that must fail without the guard: synthetic home pages carrying those markers must stop returning `unknown`.

---

## Card 16. Crawl blocking, the curl fallback, and live re-probes

**Location.** `run-full-audit.sh` stage 1 (the `quality:` line, the "CRAWL LIKELY BLOCKED" retry, the "STILL THIN" stop) and the challenge-marker scan after stage 5; the builder's live re-probe of broken links in `build-crawl-report.js`.

**What it measures.** After the crawl it counts real pages (200 and over 50 words) and blocked rows (429, 403, 503 only). Under 3 real pages, or a 10%+ blocked share, triggers one paced curl re-crawl. 202 (a captcha wall that answers 202) is not in the blocked list, but its marker (`sg-captcha`) is in the challenge-marker scan.

**What a reader assumes.** That a green gate means the site was read.

**How it misfired.**
| Case | Tool said | Truth | Proof | Date |
|---|---|---|---|---|
| Case 1, run 1 | Gate 16/16 green | Crawl = 1 row (403); every request from the audit machine got a 202 captcha page. Invalid; kept as a control | Case write-up | 2026-09-29 |
| Case 1, rebuild | Builder dropped a real 404 as "re-verified OK" because its re-probe got the 202 captcha | Real 404 (a service URL 301s to a misspelled URL that is 404, linked from 26 pages) | Case write-up | 2026-09-29 |
| Case 1 | Gate note "3 CRITICAL" | No critical finding. The older gate matched the word "critical" (here in a blog slug and in "not a critical fault"). The gate now counts the flag and siren emoji in checks.log and hack.log instead | Case write-up | 2026-09-29 |
| Cases 2 and 4 | The fallback re-crawled the same JS shell; stages ran on 1 row | CSR, not blocking; the fallback cannot help | Case write-ups | 2026-09-30, 2026-10-07 |
| Case 3 | Render and attribution stages ran | Headless Chrome got "Access ... was denied" on some pages | Card 14 | 2026-10-01 |
| Case 6 | 21 of 63 rows rate-limited by the host | The fallback worked: 62 real pages (control: the fallback CAN work) | Case write-up | 2026-10-12 |

**Verify before shipping.**
1. `grep -n 'quality:\|CRAWL LIKELY BLOCKED\|STILL THIN\|after curl fallback\|CHALLENGE MARKERS\|PARTIAL CRAWL' $KIT_WORK/cases/<slug>/run1.log`.
2. Crawl rows and statuses: `$PY -c "import json,collections;P=json.load(open('$KIT_WORK/crawl/<slug>-full.json'))['pages'];print(len(P),collections.Counter(p.get('status') for p in P))"`.
3. Every 403/429/503/202 row is "could not check", never "broken" (kit rule 1). The builder shows them as "not checked".
4. On a walled host, every rebuild and every `verify-audit.py` run must export the same `CRAWL_PROXY` and `CRAWL_UA` as the crawl, or the re-probes test a captcha page.

**Guard.** "STILL THIN" stops the run (exit 4) unless `CSR=1`; add 202 to the blocked list; when the render stage says CSR, print "render every sitemap route" instead of retrying curl.
- Status: the stop is in the code (exit 4, with "re-run with CSR=1 if the site is client-rendered"); a fallback that writes nothing exits 1; 202 is still not in the blocked-status list (confirm).
- Control that must fail without the guard: a synthetic 1-row crawl of 403 rows must not give a green gate.
- Negative control: a crawl with 21 rate-limited rows that the fallback recovers to 62 real pages must stay green.

---

## Card 17. Builder and sidecar plumbing (where counts and titles go wrong after the checks)

This is not a check, but it produced a wrong or doubled finding on 6 of the 7 audits, more than any single check.

**Location.** The sidecar file choice in `build-crawl-report.js` (run slug first, then host name). Title matchers in the builder for broken links ("isn't there", "aren't there", "does not exist", "broken link") and the CSR shell ("empty shells until JavaScript"). Regenerating the sidecar overwrites hand edits. Count parses: Cards 8 and 9.

**What a reader assumes.** That editing the sidecar and rebuilding changes the report, and that the numbers in titles come from the checks.

**How it misfired.**
| Case | What happened | Date |
|---|---|---|
| Case 3 | Edited the host-named findings file; the builder read the run-slug file; the rebuild silently kept the old findings | 2026-10-01 |
| Case 1 | A hand-retitled broken-link finding no longer matched the builder's words; the defect shipped twice | 2026-09-29 |
| Case 2 | Retitled shell finding; H1/meta/OG findings shipped in fragments beside it | 2026-09-30 |
| Case 4 | The builder's "missing H1" returned until the title held "empty shells until JavaScript" | 2026-10-07 |
| Case 6 | Titles said 2 wrong-host links and 6 noindex pages; the logs said 3 and 16 | 2026-10-12 |
| Case 7 | The builder duplicated a lead-magnet PDF 404 as its own finding until the sidecar title held "isn't there" | 2026-10-13 |

**Verify before shipping.**
1. Edit the run-slug file (`$KIT_WORK/crawl/<slug>-findings.json`). Leave any host-named file alone: the builder ignores it whenever the run-slug file exists.
2. Validate the JSON before you build: `$PY -m json.tool $KIT_WORK/crawl/<slug>-findings.json > $KIT_WORK/json-check.txt`. Bad JSON can drop every hand finding.
3. To drop a builder auto-finding that is false for this site, add `"suppress_builder": ["<title substring>"]` at the top level of the findings file; the build log prints what it suppressed.
4. After every rebuild, grep the built HTML for each corrected title (expect 1 or more) and each withdrawn title (expect 0), including "Advertising trackers" and "missing a main headline".
5. Copy the corrected list to `$KIT_WORK/cases/<slug>/findings-verified.json` before running anything that can regenerate the sidecar. `publish-run.sh` refuses to publish without that file.

**Guard.** Give every sidecar finding a stable `kind` field (`broken_links`, `csr_shell`, `wrong_host_links`, `noindex`) and match on `kind` in the builder instead of title words. Make the builder stop with an error when both sidecar files exist and differ.
- Status: open; matching is still by title words (`suppress_builder` exists as the manual override) (confirm).
- Control that must fail without the guard: a synthetic verified sidecar with a broken-link finding retitled WITHOUT "isn't there" must ship the 404 once, not twice.

---

# Pre-ship verification checklist

Order: how often each trap bit in the seven audits of docs/05-CASE-STUDIES.md. Count = audits where it produced a wrong, doubled or misleading result, or (item 2) needed a by-rule removal. `verify-audit.py` gathers most of the evidence in one run; `VERIFY-AGENT-BRIEF.md` is the step list for a verify agent.

1. **Sidecar and builder plumbing (6 of 7).** Edit the run-slug sidecar. Keep "isn't there" in the broken-link title and "empty shells until JavaScript" in the shell title. After the rebuild, grep the HTML for every corrected title (1 or more) and withdrawn title (0). Compare every number in a title with the check's own line in `checks.log` (noindex header, "referenced from" count). Cards 17, 8, 9.
2. **Pre-consent tracker finding (5 of 7, by rule).** Not in any client page; grep the HTML for "Advertising trackers": 0. `publish-run.sh` refuses it anyway. Card 13.
3. **Redirects that land on the home page (4 of 7: cases 2, 3, 4, 5).** For check-15 targets, check-28 probes and old archived URLs, print the final page. Home page or generic index = missing page or wrong target, not "old address"; spam slugs go to the hack finding; a "200" soft 404 may be a 301 to home. Never count "pages carrying a link" without dropping redirect rows and home page query views. Cards 7, 10, 11.
4. **CSR / JS shell (2 of 7, 8 false findings).** If `render.log` says `CONTENT: CSR` or the crawl has 1 to 3 rows, render every sitemap route and recount pages, images, forms and schema before shipping anything from checks 3, 4, 23, 25, 28. Card 1.
5. **Hack-scan `inurl:.php` (2 of 7, both CRITICAL, both false).** Every hit must be on the audited domain; the URL list must be the hits, not the orphan list. Look for hidden off-screen link blocks by hand. Card 6.
6. **Crawl blocking and transport (3 of 7: case 1 behind a captcha wall, case 3 render denied, case 2 a 1-row crawl of a CSR shell).** Check the row count and status mix, not the gate. Rebuild with the crawl's proxy and UA. Void any render or attribution page that shows a captcha or "was denied". Blocked rows are "could not check", never "broken". Cards 14, 16.
7. **Phone "not yours" (3 of 7 fired: 1 false, 1 understated, 1 shipped unverified).** Read the sentence around each number. Card 4.
8. **Duplicate content by word count (1 of 7).** Case-fold paths for real duplicates; difflib the rest. Card 2.
9. **LOCAL_LEAK (1 of 7, shipped as CRITICAL).** Open the example; the reserved name must be the end of the host. Card 5.
10. **Schema local-entity label (1 of 7).** Read the printed type list; compare the schema address and phone with the footer. Card 12.
11. **Copied title shown as slug mismatch (1 of 7).** Check whether the page's title belongs to another page. Card 3.
12. **Noindex groups (2 of 7).** Use the header count; confirm each group is unintended before shipping. Card 8.
13. **CMS `unknown` (4 of 7, no false claim).** Identify the platform by hand and read the JS bundle on app builders. Card 15.
14. **"Links go nowhere / buttons do nothing".** Ship only elements that `click-test.js` marks "does nothing"; `publish-run.sh` refuses the wording without that evidence. A source-only candidate list is never enough (an older audit shipped a working tab group as "7 buttons go nowhere"). docs/07-RUNBOOK.md, step 4.
15. **Not measured (every audit).** Every `✗ <step> - SKIPPED` line in the run log is declared in `not_measured`; every `NOT MEASURED` section of `evidence.md` stays unknown, never zero; a failed Lighthouse run reads "Not measured". No title or meta description length findings.
