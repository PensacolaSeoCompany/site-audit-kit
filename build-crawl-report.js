#!/usr/bin/env node
/* build-crawl-report.js — light-mode, hard-edge (90° corners) two-tab HTML report from full-extract.json.
 *   node build-crawl-report.js <full-extract.json> <out.html> [Site Name]
 *   node build-crawl-report.js <full-extract.json> <report.md> [Site Name]   (Markdown: same findings,
 *     walk-through text, "What we could not check", errors and the performance table; no raw tab)
 * Optional branding (none by default): REPORT_BRAND = footer text (default "Site audit");
 *   CTA_URL = show a "Book a call" box linking there (CTA_TEL / CTA_TEL_TXT add a phone button).
 * READ-ONLY: it reads the crawl JSON and sidecars and re-checks a few URLs with GET; it never
 *   submits a form, logs in, or changes anything on a site.
 * Auto-loads sibling Lighthouse files <slug>-{mobile-3g,mobile-4g,desktop}.json if present.
 * Auto-loads sibling <slug>-findings.json (optional) — either an array of
 *   {severity,title,detail,urls:[]} or {findings:[...], perf_note:"..."} — merged at the top
 *   of the findings list so bespoke discoveries (root causes, CTA bugs) appear IN the report.
 * Layout: findings accordions → per-status-code error accordions (with originating pages)
 *   → pages grouped by URL structure (one parent accordion, one accordion per path segment)
 *   → condensed performance accordion at the bottom. No external-links section.
 */
const fs = require('fs'), path = require('path');
const [,, inPath, outPath, nameArg] = process.argv;
if (!inPath || !outPath) { console.error('usage: build-crawl-report.js <in.json> <out.html|out.md> [name]'); process.exit(1); }
/* §61 (a restoration-company run, 2026-08-06): passing the display name in position 2 wrote a file
   literally named after the business (no extension, in the cwd) and exited 0. A misordered
   arg must ERROR, never silently create garbage that looks like a successful build. */
const AS_MD = /\.md$/i.test(outPath);
if (!AS_MD && !/\.html?$/i.test(outPath)) {
  console.error(`ERROR: output path must end in .html or .md — got ${JSON.stringify(outPath)}.\n` +
                `       Did you pass the display name in position 2?\n` +
                `       usage: build-crawl-report.js <in.json> <out.html|out.md> [name]`);
  process.exit(2);
}
if (!/\.json$/i.test(inPath)) {
  console.error(`ERROR: input must be the crawl JSON (<slug>-full.json) — got ${JSON.stringify(inPath)}.`);
  process.exit(2);
}
if (!fs.existsSync(inPath)) {
  console.error(`ERROR: input file does not exist: ${inPath}`);
  process.exit(2);
}
const D = JSON.parse(fs.readFileSync(inPath, 'utf8'));
const NAME = nameArg || D.site || 'Site Audit';
const dir = path.dirname(inPath);
/* SLUG RESOLUTION (2026-08-05 — cross-run collision, found by a clean-agent critique).
   `slug = D.site` derives the sidecar/perf filenames from the crawl's HOST, so EVERY run of the
   same domain resolves to the same `<host>-findings.json` and later runs silently overwrite earlier
   ones — run1 and run2 of one restoration-company site shared one sidecar, and hand-corrections to the
   run2 file were ignored because the builder was reading the host-keyed name.
   FIX: prefer the RUN slug taken from the input filename (`<slug>-full.json`), which is unique per
   run, and fall back to the host only if that sidecar does not exist. */
const runSlug = path.basename(inPath).replace(/-full\.json$/, '');
const slug = fs.existsSync(path.join(dir, `${runSlug}-findings.json`)) ? runSlug : D.site;
const lh = {};
/* Perf JSONs are written by perf-audit.sh keyed on the FETCHED HOST, not the run slug, and some
   runs also produce `<runSlug>-<host>-<tier>.json`. Try every spelling so a slug-keyed report still
   finds host-keyed perf files (the §52/collision fix must not silently drop the perf section). */
/* 2026-10-14 (smoke test): with a port in the audited URL (http://127.0.0.1:8080/) perf-audit.sh
   writes `127.0.0.1:8080-<tier>.json`; D.site has no port, so the report showed 0 perf tiers.
   Also try the seed page's host:port (and without a leading www.). */
const seedHosts = (() => { const out = []; for (const u of [D.seed, (D.pages||[])[0] && D.pages[0].url]) {
  try { const h = new URL(u).host; out.push(h, h.replace(/^www\./, '')); } catch (e) {} } return [...new Set(out)]; })();
for (const t of ['mobile-3g','mobile-4g','desktop']) {
  for (const cand of [`${slug}-${t}.json`, `${D.site}-${t}.json`,
                      `${runSlug}-${D.site}-${t}.json`, `${runSlug}-${t}.json`,
                      ...seedHosts.flatMap(h => [`${h}-${t}.json`, `${runSlug}-${h}-${t}.json`])]) {
    const f = path.join(dir, cand);
    if (fs.existsSync(f)) { try { lh[t] = JSON.parse(fs.readFileSync(f,'utf8')); break; } catch(e){} }
  }
}
let manual = [], perfNote = '', suppressAuto = [], notMeasured = [], couldNotCheck = [], sidecarPartial = null;
const ff = path.join(dir, `${slug}-findings.json`);
if (fs.existsSync(ff)) { try {
  const j = JSON.parse(fs.readFileSync(ff,'utf8'));
  manual = Array.isArray(j) ? j : (j.findings||[]);
  perfNote = (!Array.isArray(j) && j.perf_note) || '';
  // 2026-10-14: verifiers can drop a builder auto finding that is false for this site
  // (a client site: og finding claimed summary_large_image). Each entry is a case-insensitive title substring.
  suppressAuto = (!Array.isArray(j) && Array.isArray(j.suppress_builder)) ? j.suppress_builder.map(x=>String(x).toLowerCase()) : [];
  // 2026-10-14 (research lesson): steps the run skipped are declared here and shown to the client as
  // "What we could not check"; publish-run.sh refuses an INCOMPLETE run whose skips are not declared.
  notMeasured = (!Array.isArray(j) && Array.isArray(j.not_measured)) ? j.not_measured : [];
  // 2026-10-14 (owner safety rule): run scripts mark an incomplete run with "partial": {"reason": "..."}
  // and list anything else they could not check in "could_not_check" ([{what, why}] or strings).
  couldNotCheck = (!Array.isArray(j) && Array.isArray(j.could_not_check)) ? j.could_not_check.map(x => typeof x === 'string' ? {what: x, why: ''} : x) : [];
  sidecarPartial = (!Array.isArray(j) && j.partial) ? j.partial : null;
} catch(e){
  // 2026-10-13: a bad sidecar used to drop every hand finding and still exit 0.
  console.error(`FATAL: ${ff} is not valid JSON (${e.message}). Fix it; nothing was built.`); process.exit(3);
} }
/* 2026-10-14 (research lesson): "Critical " with a trailing space sorted BELOW LOW. An unknown
   severity is an authoring error: refuse, do not guess. */
{ const _ok = new Set(['CRITICAL','HIGH','MEDIUM','LOW','NOTE']);
  const _bad = manual.filter(f => !_ok.has(String(f && f.severity || '')));
  const _badCase = _bad.filter(f => !_ok.has(String(f && f.severity || '').toUpperCase()));
  if (_badCase.length){ console.error(`FATAL: unknown severity in ${ff}: ${_badCase.map(f=>JSON.stringify(f.severity)+' on "'+f.title+'"').join('; ')}. Use CRITICAL, HIGH, MEDIUM, LOW or NOTE.`); process.exit(3); } }
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const lhm = (d,k)=> d&&d.audits&&d.audits[k] ? d.audits[k].displayValue||'' : '';
/* 2026-10-14 (two client sites): Lighthouse writes score:null when the run failed (NO_FCP,
   ERRORED_DOCUMENT_REQUEST). Math.round(null*100) is 0, so a test that measured nothing published as
   a score of 0. null = not measured. */
const lhscore = d => { const v = d && d.categories && d.categories.performance && d.categories.performance.score;
  return (typeof v === 'number') ? Math.round(v*100) : null; };
const lhcell = d => { const v = lhscore(d);
  if (v === null) return `<td class="sc scna">Not measured${d && d.runtimeError && d.runtimeError.code ? ` <span class=muted>(${esc(d.runtimeError.code)})</span>` : ''}</td>`;
  return `<td class="sc sc${v<50?'bad':v<90?'mid':'good'}">${v}</td>`; };
const s = D.summary||{};
/* dedupe crawl entries that differ only by trailing slash (seed vs linked form) */
const seenKey = new Set();
const pages = (D.pages||[]).filter(p=>{ const k = (p.url||'').replace(/\/$/,''); if (seenKey.has(k)) return false; seenKey.add(k); return true; });
const rel = u => { try { const x=new URL(u); return (x.pathname||'/')+(x.search||''); } catch(e){ return u; } };
const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./,''); } catch(e){ return ''; } };
const siteHost = hostOf(pages[0] ? pages[0].url : ('https://'+(D.site||'')));
// registrable-domain scope: the crawler crawls SUBDOMAINS of the audited site (studio./uat./blog.),
// so a subdomain page's self-canonical is on-domain, not off-domain. sameReg treats the audited host
// AND any subdomain of it as in-scope (2026-08-04). siteHost already has www. stripped.
const sameReg = h => h && (h === siteHost || h.endsWith('.' + siteHost));
/* One noindex definition for every stage (DE-04, 2026-10-14): same regex as crawl_transport.NOINDEX_RE.
   The crawler's stored verdict wins when present (it also reads every X-Robots-Tag header). */
const NOINDEX_RE = /(?:^|[\s,:])(?:noindex|none)(?=[\s,;]|$)/i;
const noindexed = p => (typeof p.noindex === 'boolean') ? p.noindex
  : NOINDEX_RE.test(String(p.meta_robots||'').replace(/max-image-preview\s*:\s*[a-z]+/gi, ' '));

/* ---- reverse-link map: target URL -> [source pages] ---- */
/* NOT EVERY CRAWLED URL IS A PAGE (2026-10-14, a live report): "Linked from N pages" counted
   WordPress like-button requests (/wp-admin/admin-ajax.php?action=process_simple_like&post_id=...)
   as pages. A source (and a page in any page list) must be a real page: not admin-ajax.php,
   wp-login.php, /wp-admin/, /wp-json/, xmlrpc.php, /cdn-cgi/, a URL with ?action=, or a non-HTML
   asset. Same rule as crawl_transport.is_real_page() in Python; keep the two in step. */
const NOT_PAGE_RE = /\/wp-admin\/|admin-ajax\.php|wp-login\.php|\/wp-json(\/|$|\?)|xmlrpc\.php|\/cdn-cgi\/|[?&]action=/i;
const ASSET_RE = /\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp4|webm|mov|avi|mp3|wav|ogg|pdf|docx?|xlsx?|pptx?|zip|gz|rar|7z|css|js|mjs|json|xml|txt|woff2?|ttf|eot|otf)$/i;
function isRealPage(u, contentType) {
  let x; try { x = new URL(u); } catch (e) { return false; }
  if (NOT_PAGE_RE.test(x.pathname + x.search)) return false;
  if (ASSET_RE.test(x.pathname)) return false;
  if (contentType && !/html/i.test(contentType)) return false;
  return true;
}
const inMap = {};
for (const p of pages) if (isRealPage(p.url, p.content_type)) for (const l of (p.internal_links||[])) {
  const href = (typeof l === 'string' ? l : l.href) || '';
  const t = href.split('#')[0];
  if (!t) continue;
  (inMap[t] = inMap[t] || new Set()).add(p.url);
}
const sourcesOf = u => [...(inMap[u]||[])];

/* ================= FINDINGS (auto, from the crawl) ================= */
/* only HTML pages count for on-page findings/sections — XML sitemaps, feeds etc. crawl as 200
   but have no titles/H1s and would pollute the findings with false gaps */
/* a redirect destination is not a page of this site (AUDIT-PROBLEMS §16): its canonical/H1/OG
   belong to the TARGET. A garage-door site 2026-08-01: /schedule (308 → book. subdomain) shipped as TWO
   false CRITICALs + a false missing-H1 because these rows passed the status===200 filter. */
const p200 = pages.filter(p=>p.status===200 && !p.redirected && /html/i.test(p.content_type||'') && isRealPage(p.url, p.content_type));
/* 🔴 RE-VERIFY TRANSIENT NON-200s ONCE, AT THE SOURCE (a cleaning-company site, 2026-08-07).
   The broken-link re-verification below only covered targets that had a LINK SOURCE, so the
   "Errors by status code" section, the "N errors" header pill and the build-log count all still
   reported crawl-time statuses verbatim. That site is on shared hosting: our own crawl
   concurrency tripped the host's resource cap and 13 URLs returned HTTP 508 "Resource Limit Is
   Reached". Every one of them returned 200 on 26/26 paced live probes — so the deliverable
   presented 12 perfectly healthy service-area pages to the client as errors.
   §68.1: absence/failure in OUR crawl is a fact about the crawl, not about the page. Transient
   statuses (429/5xx/508) are re-probed here ONCE; anything still failing stays reported (fail
   closed), and genuine 404/403/410 are never re-probed. Uses the crawl's own transport. */
/* POLITE RE-CHECKS (owner safety rule 2026-10-14): the few live re-checks below send the kit's bot
   UA and wait CRAWL_DELAY seconds (default 1) between requests. They only re-check URLs the crawl
   already fetched, so robots.txt allowed them. */
const BOT_UA = process.env.CRAWL_UA || 'GrokBot-SiteAudit (+https://github.com/PensacolaSeoCompany/site-audit-kit)';
const _DELAY_MS = 1000 * Number(process.env.CRAWL_DELAY || 1);
let _lastReq = 0;
function politeWait() {
  const w = _lastReq + _DELAY_MS - Date.now();
  if (w > 0) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, w);
  _lastReq = Date.now();
}
const _TRANSIENT_ST = new Set([0,408,425,429,500,502,503,504,507,508,509,522,524]);
const _rawErrs = pages.filter(p=>p.status!==200);
const _recovered = [];
{
  const {execFileSync} = require('child_process');
  const _px = process.env.CRAWL_PROXY || '';
  const _ua = BOT_UA;   // the kit's bot UA (owner safety rule 2026-10-14)
  for (const p of _rawErrs){
    if (!_TRANSIENT_ST.has(p.status)) continue;
    let live=null;
    for (let a=0;a<2 && (live===null || _TRANSIENT_ST.has(live));a++){
      try{
        const args=['-4','-s','-L','--compressed','-o',require('os').devNull,'-w','%{http_code}','-m','25','-A',_ua];
        if(_px) args.push('-x',_px);
        args.push(p.url);
        politeWait(); live = parseInt(execFileSync('curl', args, {encoding:'utf8', timeout:40000}).trim(),10) || null;
      }catch(e){ live=null; }
    }
    if (live && live>=200 && live<400 && live!==202){ p._recovered_from = p.status; p.status = live; _recovered.push(p.url); }
  }
  if (_recovered.length)
    console.log(`   (${_recovered.length} transient non-200 row(s) re-verified live as OK — excluded from the error list, they are not defects)`);
}
/* 2026-10-14 (a client site: 46 Cloudflare bot-check 403s published as "Broken address" and "49 errors").
   RFC 9110: only 404/410 say a page is not there. 401/403/406/429 and "no answer" (0/null) mean the
   site or our transport refused us: we could not check. They leave the error list and the broken-link
   groups and go to "What we could not check". Persistent 5xx stays an error (visitors get it too). */
const BLOCKED_ST = new Set([0,401,403,406,429]);
const isBlocked = p => p.status==null || BLOCKED_ST.has(p.status);
const blockedRows = pages.filter(p=>p.status!==200 && isBlocked(p));
const errs = pages.filter(p=>p.status!==200 && !isBlocked(p));
const reverifiedOK = new Set();   // 2026-10-14 (a client site): URLs a live re-check found working leave the errors table too
const findings = [];   // {severity,title,detail,urls:[]}

/* broken internal links — every non-200 target that at least one page links to.
   Every source page is documented IN the finding (grouped per broken target), together with
   the href AS LITERALLY WRITTEN in the source code — the resolved URL (e.g. /blog/www.x.com
   from a protocol-less href) never appears verbatim in the code, so without the raw form the
   link cannot be found by searching the page source. */
const pageByUrl = Object.fromEntries(pages.map(p=>[p.url,p]));
function codeFormsFor(targetUrl, sources){
  const forms = new Map();
  for (const su of sources){
    for (const l of ((pageByUrl[su]||{}).internal_links||[])){
      const h = (typeof l==='string'?l:l.href)||'';
      /* suffix fallback (a garage-door site, 2026-08-01): when the broken row is the FINAL url of a 301
         chain, links point at the PRE-redirect spelling and the exact match finds nothing —
         the owner rule ("show the href as written") then silently fails */
      const hPath = h.split('#')[0].replace(/\/$/,'');
      const tPath = targetUrl.replace(/\/$/,'');
      if ((hPath!==tPath && !hPath.endsWith(tPath.replace(/^https?:\/\/[^/]+/,''))) || typeof l==='string') continue;
      const raw = l.raw_href||l.href;
      const id = [l.anchor?`link text "${l.anchor}"`:'', l.cls?`class "${l.cls}"`:''].filter(Boolean).join(', ');
      forms.set(raw+'|'+id, `href="${raw}"${id?`  (${id})`:''}`);
    }
  }
  return [...forms.values()];
}
/* Dedupe vs the sidecar (2026-07-31, a roofing site): the same broken target shipped as TWO
   separate HIGH findings — the sidecar's trace-based one and this auto one. If the sidecar already
   reports a target, attach our "written in the code as" groups to ITS finding instead of adding a
   second accordion. Cloudflare's /cdn-cgi/l/email-protection is not an owner-authored link (JS
   rewrites it to a working mailto:) — never report it as broken here; the sidecar notes it once. */
const sidecarText = JSON.stringify(manual);
/* Match the sidecar's broken-link finding by MEANING, not one exact phrase. The title is client
   copy and gets rewritten (2026-08-05 plain-language pass changed "point to pages that do not
   exist" → "lead to pages that aren't there"), which silently broke this matcher and shipped the
   SAME defect as two findings. Keep this tolerant of future wording changes. */
const sidecarBroken = manual.find(f=>/do(es)? not exist|doesn'?t exist|aren'?t there|isn'?t there|broken (internal )?link/i.test(f.title||''));
let brokenTargets = errs.map(p=>({p, src: sourcesOf(p.url)})).filter(x=>x.src.length)
  .filter(x=>!x.p.url.includes('/cdn-cgi/l/email-protection'))
  /* status 0 = OUR transport failed (no HTTP answer at all) and 406 = the origin refused a
     non-browser client (Shopify account routes answer 302 to a browser, 406 to curl). Neither is
     a page a visitor cannot reach — a Shopify store 2026-09-22 shipped '8 broken' for 1 real 404.
     Fail-closed exception: keep a 0-status row only if it has NO query string (a plain page we
     could not fetch is worth a look); parameterised ones are almost always encoding artifacts. */
  .filter(x=>!(x.p.status===406 || ((x.p.status===0 || x.p.status===null) && x.p.url.includes('?'))));
/* 🔴 RE-VERIFY BEFORE SHIPPING (2026-08-06, a restoration-company run). checks-to-findings.py has
   re-checked crawl-time non-200s since 2026-07-31 ("a crawl-time 5xx is often a transient
   hiccup") — but THIS file never did, so a transient status shipped as a HIGH "broken internal
   link" from the builder even when the sidecar had already dropped it. Sucuri 403'd
   /insurance/supplements/ once mid-crawl; it returned 200 on 7/7 live probes minutes later.
   Rule 13 (fix the RULE, not the instance): the same re-verification must run in BOTH producers.
   Uses the crawl's own transport env (CRAWL_PROXY/CRAWL_UA) so a WAF'd host is re-probed the same
   way it was crawled. A URL we cannot reach at all stays reported (fail closed). */
if (brokenTargets.length){
  const {execFileSync} = require('child_process');
  const _px = process.env.CRAWL_PROXY || '';
  const _ua = BOT_UA;   // the kit's bot UA (owner safety rule 2026-10-14)
  brokenTargets = brokenTargets.filter(x=>{
    let live = null;
    for (let attempt=0; attempt<2 && live===null; attempt++){
      try{
        const args=['-4','-s','-L','--compressed','-o',require('os').devNull,'-w','%{http_code}','-m','25','-A',_ua];
        if(_px) args.push('-x',_px);
        args.push(x.p.url);
        politeWait(); live = parseInt(execFileSync('curl', args, {encoding:'utf8', timeout:40000}).trim(),10) || null;
      }catch(e){ live = null; }
    }
    if (live && live>=200 && live<400 && live!==202){
      console.log(`   (re-verified OK, dropped from report: [${x.p.status}] -> ${live}  ${x.p.url})`);
      reverifiedOK.add(x.p.url);
      return false;
    }
    return true;
  });
}
if (sidecarBroken){
  /* merge ALL targets into the sidecar's finding (a client site 2026-08-02: "51 dead links" and
     "35 broken targets" shipped as two separate findings for one defect — partial coverage
     left the remainder to spawn a duplicate accordion) */
  sidecarBroken.groups = (sidecarBroken.groups||[]).concat(brokenTargets.map(x=>({label:`Broken address: ${rel(x.p.url)} (${x.p.status}). Where the link is written — search the code of ${x.src.length>1?'these pages':'this page'} for the string below:`, code: codeFormsFor(x.p.url, x.src), urls: x.src.map(rel).sort()})));
  brokenTargets = [];
}
if (brokenTargets.length) {
  const totalSrc = new Set(brokenTargets.flatMap(x=>x.src)).size;
  findings.push({severity:'HIGH', title:`${brokenTargets.length} broken internal link target${brokenTargets.length>1?'s':''} across ${totalSrc} page${totalSrc>1?'s':''}`,
    detail:'Links on the site point to addresses that do not exist. IMPORTANT: the pages listed under each broken address load perfectly fine when you visit them — the problem is a link INSIDE their code pointing at the broken address. "Written in the code as" shows the exact string to search for in the page source / template — the broken address itself may never appear verbatim in the code.',
    groups: brokenTargets.map(x=>({label:`Broken address: ${rel(x.p.url)} (${x.p.status}). Found inside the code of these ${x.src.length} page${x.src.length>1?'s':''} — these pages themselves load fine:`, code: codeFormsFor(x.p.url, x.src), urls: x.src.map(rel).sort()}))});
}

/* per-URL provenance: which pages the crawler found this URL linked from */
const withProv = (p, note) => ({url: rel(p.url), note: note||'', sources: sourcesOf(p.url).map(rel).sort()});

/* canonical tag pointing to a DIFFERENT domain (indexable pages only) */
const offDomain = p200.filter(p=>p.canonical && hostOf(p.canonical) && !sameReg(hostOf(p.canonical)) && !noindexed(p));
if (offDomain.length) findings.push({severity:'CRITICAL', title:`${offDomain.length} page${offDomain.length>1?'s':''} tell Google the "real" page lives on another website`,
  detail:'The rel=canonical tag on these pages points to a different domain. Google treats that as "index the other site instead", which can remove these pages from search entirely. Usually a leftover template/global canonical setting. The page loads fine when you visit it — the problem is an invisible tag in its HTML. "Found on" shows which pages link to it.',
  urls: offDomain.map(p=>withProv(p, `  →  ${p.canonical}`))});

/* on-domain canonical mismatch (excluding the correct ?query → clean-URL case) */
const titleByUrl = Object.fromEntries(p200.map(p=>[p.url.replace(/\/$/,''), (p.title||'').trim()]));
/* BROKEN canonical VALUES (a client site, 2026-08-02): spaces-in-URL, doubled slashes, missing
   slash — not deferrals, just broken strings. Split them from the "confirm each is deliberate"
   cross-page group and rate them CRITICAL. */
const brokenCanon = p200.filter(p=>{
  const c = p.canonical||'';
  if (!c) return false;
  return /\s/.test(c) || /^https?:\/\/[^/]+\/\//.test(c) || /&amp;|&#0?38;/.test(c);
});
if (brokenCanon.length) findings.push({severity:'CRITICAL', title:`${brokenCanon.length} page${brokenCanon.length>1?'s':''} declare a BROKEN official-version address`,
  detail:'These pages\' hidden canonical tag — the address they tell Google to index instead of themselves — contains a malformed value: spaces, doubled slashes, or raw HTML entities. Google cannot resolve a broken address, so these pages\' index signals go nowhere. This is a template bug, not a content decision: fix the canonical field in the page template.',
  urls: brokenCanon.map(p=>withProv(p, `  →  ${p.canonical}`))});
const brokenCanonSet = new Set(brokenCanon.map(p=>p.url));
const mismatch = p200.filter(p=>{
  if (brokenCanonSet.has(p.url)) return false;
  /* same-title canonical target = a deliberate duplicate pointing at its master (a client site:
     /miami-remodeling-company -> / with identical content) — correct behavior, not a defect */
  const t = (p.canonical||'').replace(/\/$/,'');
  if (t && titleByUrl[t] !== undefined && titleByUrl[t] === (p.title||'').trim()) return false;
  if (!p.canonical || !sameReg(hostOf(p.canonical)) || noindexed(p)) return false;
  const norm = u => u.replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,'');
  const cu = norm(p.canonical), pu = norm(p.url), puClean = norm(p.url.split('?')[0]);
  // a queued URL that 301'd to its canonical target is correct hygiene, not a mismatch —
  // the crawler stores the post-redirect page under the original URL
  if (p.redirected && norm(p.final_url||'')===cu) return false;
  // Shopify collection-scoped product paths (/collections/<c>/products/<p>) canonicalize to
  // /products/<p> BY DESIGN — that is the platform doing the right thing, not a defect
  // (a Shopify store 2026-09-22: 147 of 157 'mismatches' were exactly this).
  const pSeg = puClean.split('/').pop();
  if (/\/collections\/[^/]+\/products\/[^/]+$/.test(puClean) && cu.endsWith('/products/'+pSeg)) return false;
  // case/encoding-only differences (search?q=Cleats -> q=cleats, %2c vs ,) are normalization, not a different page
  // '+' and '%20' are the same space in a query string (Shopify canonicals write '+', links write '%20'):
  // a Shopify store 2026-09-22 — 82 'mismatches' were exactly this on /search?q= and /collections/types?q=.
  const loose = u => { try { return decodeURIComponent(u).toLowerCase().replace(/\+/g,' '); } catch { return u.toLowerCase().replace(/\+/g,' '); } };
  if (loose(cu)===loose(pu) || loose(cu)===loose(puClean)) return false;
  return cu!==pu && cu!==puClean;
});
if (mismatch.length) findings.push({severity:'MEDIUM', title:`${mismatch.length} page${mismatch.length>1?'s':''} point their canonical at a different page on this site`,
  detail:'These pages load normally, but an invisible tag in their HTML declares another URL as the "master copy", so Google may index that one instead. Sometimes intentional (a duplicate form pointing at the main one) — confirm each is meant to defer. "Found on" shows which pages link to it.',
  urls: mismatch.map(p=>withProv(p, `  →  ${rel(p.canonical)}`))});

/* duplicate query-string pages with no canonical */
const cleanSet = new Set(p200.filter(p=>!p.url.includes('?')).map(p=>p.url.replace(/\/$/,'')));
/* AUTH/UTILITY ROUTES ARE NOT DUPLICATE CONTENT (clean-agent catch, a client run 2026-08-05).
   This check fired on a single URL — /wp-login.php?action=lostpassword — and shipped the standard
   "filter, sort option, or add-to-cart button" copy telling the client to add canonical tags. A
   password-reset form is not a duplicate of the login page, is not content, and must never be
   indexed anyway; the copy's own explanation contradicted the URL it was built from (§49 family).
   Skip login/admin/auth routes and the known WP action-parameter set. */
const AUTH_PARAM_RE = /wp-login\.php|\/wp-admin\/|\/(login|signin|sign-in|logout|register|lostpassword|reset-password|my-account)\b/i;
const dupNoCanon = p200.filter(p=>p.url.includes('?') && cleanSet.has(p.url.split('?')[0].replace(/\/$/,'')) && !p.canonical && !AUTH_PARAM_RE.test(p.url));
if (dupNoCanon.length) findings.push({severity:'MEDIUM',
  title:`${dupNoCanon.length} of your pages are reachable at more than one address`,
  whats_happening:
    'Every page has an address. Some of your pages can be reached at their normal address AND at '
    + 'extra versions of it with something tacked on the end after a "?" — usually added by a filter, '
    + 'a sort option, or an add-to-cart button.\n\n'
    + `We found ${dupNoCanon.length} of these extra addresses. They show the same page, and none of `
    + 'them tells Google which address is the real one.',
  see_it:
    'Open any address from the list below. Then open the same page normally through your menu.\n\n'
    + 'Compare the two addresses in the bar at the top — one has extra characters after a "?".',
  youll_see:
    'The same page content both times, at two different addresses.',
  why:
    'Google treats each address as a separate page unless you tell it otherwise. So instead of one '
    + 'page collecting all the attention, you have several copies splitting it between them — and '
    + 'Google has to guess which one to show.\n\n'
    + 'There is a standard way to say "this address is a copy, the real one is over here". These '
    + 'pages do not have it.',
  fix:
    'Ask whoever manages your site to make these pages point at their main address as the official '
    + 'one. On most platforms this is a setting rather than hand-written code — the term they will '
    + 'know is a "canonical tag".\n\n'
    + 'Where the extra addresses come from links on your own pages, pointing those links at the '
    + 'clean address fixes it at the source.',
  urls: dupNoCanon.map(p=>withProv(p))});

/* missing on-page basics (indexable pages only) */
/* shell pages (no title + no h1 + tiny wc) belong to the sidecar's unified CSR finding —
   listing them again as missing-H1/meta fragments one defect into three (a client site, 2026-08-02) */
const shellFinding = manual.find(f=>/empty shells until JavaScript/.test(f.title||''));
const csrShell = shellFinding && /empty shells until JavaScript|invisible until JavaScript/.test(shellFinding.title||'');
const isShell = p => shellFinding && (
  (!(p.title||'').trim() && !((p.headings||{}).h1||[]).length && (p.word_count||0) < 400)
  || (csrShell && (p.word_count||0) < 200)   // CSR curl-crawl: thin rows are unrendered shells
);
const noH1 = p200.filter(p=>!((p.heading_counts||{}).h1) && !noindexed(p) && !isShell(p));
// EXTRACTION-GAP GUARD (a client site, 2026-08-04): page builders like Elementor emit the H1 in markup the
// raw-HTML crawler sometimes doesn't parse, so it records h1:null on nearly every page while the H1
// is plainly present live. When the missing-H1 rate is very high AND some pages DID yield an H1
// (proving the site uses H1s), assert-every-page-is-broken is wrong — reframe as a caveated warning
// and tell the reader to spot-check live before treating it as a sitewide fix.
const someHaveH1 = p200.some(p=>(p.heading_counts||{}).h1);
const h1GapRate = p200.length ? noH1.length / p200.length : 0;
const h1ExtractionGap = someHaveH1 && h1GapRate > 0.7 && noH1.length >= 10;
if (noH1.length && h1ExtractionGap) findings.push({severity:'MEDIUM',
  title:`Most pages record no main headline (H1) — verify this against the live pages first`,
  detail:`Our crawler read no H1 headline on ${noH1.length} of ${p200.length} pages. That rate is high enough that it may be a reading limitation on our side rather than a real gap: some page builders (e.g. Elementor) place the H1 in markup our raw-HTML crawl doesn't always capture, and a few of your pages DID return an H1, which shows the site uses them. Before treating this as a sitewide fix, open two or three of the listed pages and check whether a large headline is actually present. Where it genuinely is missing, add one clear H1 per page; where it's present, this is a crawl artifact you can ignore.`,
  urls: noH1.map(p=>withProv(p))});
else if (noH1.length) findings.push({severity:'MEDIUM', title:`${noH1.length} page${noH1.length>1?'s':''} missing a main headline (H1)`,
  detail:'Every page should have one main text headline (H1). These pages load fine but have none in their HTML — usually listing/pagination templates. "Found on" shows which pages link to each.',
  urls: noH1.map(p=>withProv(p))});
// Only SELF-CANONICAL pages own their meta description — a row whose canonical points elsewhere is
// a duplicate whose snippet belongs to the target (a Shopify store 2026-09-22: 97 of 124 'missing' were
// Shopify collection-scoped product copies). Mirrors check 8's canonicalised-row exclusion.
const selfCanon = p => { const c=(p.canonical||'').replace(/\/$/,''); return !c || c===p.url.replace(/\/$/,'') || c===p.url.split('?')[0].replace(/\/$/,''); };
const noMd = p200.filter(p=>!p.meta_description && !noindexed(p) && !isShell(p) && selfCanon(p));
if (noMd.length) findings.push({severity:'MEDIUM',
  /* A cleaning-company run 2026-08-07: the verb and pronoun were not pluralised with the noun, so a
     single-page case shipped as "1 page don't tell Google ... underneath them". Client-facing
     copy — agree the whole clause, not just the noun. */
  title:`${noMd.length} page${noMd.length>1?'s':''} ${noMd.length>1?"don't":"doesn't"} tell Google what to write underneath ${noMd.length>1?'them':'it'} in search results`,
  whats_happening:
    'When your page shows up on Google, there are two lines: the blue clickable headline, and a short '
    + 'description underneath it in grey. That description is not written by Google — it comes from a '
    + 'line you put in the page. It has a name: the meta description.\n\n'
    + `On ${noMd.length} of your pages that line is blank. You haven't given Google anything to show, `
    + 'so it grabs whatever text it can find on the page instead.',
  see_it:
    'Go to Google and search for one of your pages — the easiest way is to search for the page\'s '
    + 'address, or your business name plus the page topic.\n\n'
    + 'Look at the grey text underneath the blue headline.',
  youll_see:
    'The grey line will be a sentence scraped from somewhere on the page — often mid-sentence, or a '
    + 'menu, or whatever text happened to be near the top. It reads like it was cut out of context, '
    + 'because it was.',
  why:
    'That grey line is your advert. It sits right there in the results next to your competitors, and '
    + 'it is often what decides whether someone clicks you or clicks them. Letting Google pick it at '
    + 'random means handing over the one bit of sales copy you get for free.\n\n'
    + "It doesn't directly change your ranking — it changes how many people choose you once you're "
    + 'showing.',
  fix:
    'Write one for each page: a sentence or two, in plain language, saying what that page offers and '
    + 'why someone should click. Make each one different — they describe different pages.\n\n'
    + 'On most website platforms there\'s a box for this when you edit a page, usually labelled '
    + '"meta description" or "search engine description".',
  urls: noMd.map(p=>withProv(p))});
const altPages = p200.filter(p=>p.images_missing_alt>0);
const altTotal = altPages.reduce((a,p)=>a+p.images_missing_alt,0);
if (altTotal) findings.push({severity:'LOW', title:`${altTotal} image${altTotal>1?'s':''} missing alt text on ${altPages.length} page${altPages.length>1?'s':''}`,
  detail:'Alt text describes an image for search engines and screen readers. The count per page is shown in parentheses — the images are on that page. Add a short description to each.',
  urls: altPages.map(p=>withProv(p, `  (${p.images_missing_alt} image${p.images_missing_alt>1?'s':''} on this page)`))});
/* incomplete OG = has OG tags but missing og:title OR missing an image (og:image / twitter:image).
   A client run 2026-08-01: 219 of 221 pages declared twitter:card=summary_large_image with NO image
   tag anywhere — every share renders without a thumbnail — and the title-only test never fired. */
/* §66 a pest-control run 2026-08-06: this required a NON-EMPTY open_graph object, so pages with
   NO OG tags at all were excluded from a finding titled "pages have no share image". Reported 30
   when 33 genuinely lacked one — the 3 most broken pages were the ones filtered out. A page with
   zero OG tags has no share image in the fullest sense; judge the ABSENCE, not the partiality.
   Assets (images/PDF/CSS) are not pages and are excluded from both the list and the denominator. */
const OG_ASSET = /\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp4|webm|mov|avi|mp3|wav|ogg|pdf|docx?|xlsx?|pptx?|zip|gz|rar|7z|css|js|mjs|json|xml|txt|woff2?|ttf|eot|otf)(\?|#|$)/i;
const ogCandidates = p200.filter(p=>!OG_ASSET.test(String(p.url||'').split('#')[0])).filter(p=>!isShell(p));  // CSR shells have no OG either — folded into the "empty until JavaScript" finding (a lawn-care site 2026-09-22)
const ogPart = ogCandidates.filter(p=>{
  const og = p.open_graph || {};
  const noTitle = !og['og:title'];
  const noImg = !og['og:image'] && !((p.twitter_cards||p.twitter) && (p.twitter_cards||p.twitter)['twitter:image']);
  return noTitle || noImg;
});
const totalPagesForOg = ogCandidates.length;
const ogPrefix = (()=>{ const c={}; for (const p of ogPart){ const seg='/'+(rel(p.url).split('/')[1]||''); c[seg]=(c[seg]||0)+1; }
  const top = Object.entries(c).sort((a,b)=>b[1]-a[1])[0];
  /* template-sentence artifacts on tiny sites ("Nearly all (1 of 1) live under // — not 1
     separate edits", a client site 2026-08-02): require a real cluster and a real prefix */
  return top && ogPart.length >= 5 && top[1] >= ogPart.length*0.7 && top[0].length > 1 ? top : null; })();
/* COPY MUST MATCH WHAT WAS MEASURED (a restoration-company run, 2026-08-05): the canned title said
   "missing title or thumbnail image" on a site where 121 pages lacked only the IMAGE and exactly 1
   lacked a title. Naming both makes the client hunt for a title problem that isn't there. Count the
   two causes separately and title the finding after whichever actually dominates. */
const ogNoT = ogPart.filter(p=>!p.open_graph['og:title']).length;
const ogNoI = ogPart.length - ogNoT;
const ogWhat = ogNoT===0 ? 'no share image' : (ogNoI===0 ? 'no share title' : 'no share image or title');
/* 2026-10-14 (a client site): the LARGE-preview sentence printed on every site, including one with
   no share tags at all, and "We checked all 1 pages" / "1 page have". Say only what the crawl shows. */
const twCard = p => String(((p.twitter_cards||p.twitter)||{})['twitter:card']||'').toLowerCase();
const ogLarge = ogPart.filter(p=>twCard(p)==='summary_large_image').length;
const nPg = (n, one, many) => n===1 ? one : many;
const ogChecked = totalPagesForOg===1 ? 'We checked the one page of your site we could read.' : `We checked all ${totalPagesForOg} pages of your site.`;
const ogMiss = totalPagesForOg===1 ? 'It does not supply that picture' : `${ogPart.length} of them ${nPg(ogPart.length,'does','do')} not supply that picture`;
const ogLargeTxt = !ogLarge ? '' : (ogLarge===ogPart.length
  ? `\n\n${nPg(ogPart.length,'This page also tells','These pages also tell')} those apps to use the LARGE picture-style preview — the wide one with a big photo. So the site is asking for a big photo and then not providing one.`
  : `\n\n${ogLarge} of these pages also tell those apps to use the LARGE picture-style preview — the wide one with a big photo — and then do not provide the photo.`);
if (ogPart.length) findings.push({severity:'LOW', title:`${ogPart.length} page${ogPart.length>1?'s':''} ${nPg(ogPart.length,'has','have')} ${ogWhat} set for social media`,
  whats_happening:`When someone shares one of your pages on Facebook, LinkedIn or in a text message, the site is supposed to hand over a small preview: a headline and a picture. ${ogChecked} ${ogMiss}${ogNoT?` and ${ogNoT} ${nPg(ogNoT,'does','do')} not supply the headline either`:''}.${ogLargeTxt}`,
  see_it:`Paste one of these addresses into a Facebook post or a message to yourself, but DO NOT send it — just wait a second for the preview box to appear:\n\n${ogPart.slice(0,2).map(p=>p.url).join('\n')}`,
  youll_see:'The preview box comes up plain — usually just the web address and maybe a line of text, with a blank grey space or no picture at all where a photo should be.',
  why:'A share with a picture gets noticeably more clicks than a bare link. This affects every place your link gets posted — your own social posts, a customer recommending you in a Facebook group, someone texting your link to a friend. Right now all of those show up looking unfinished.',
  fix:'Ask your web person to add a share image tag (og:image) to the page template — one photo, at least 1200 pixels wide, such as your logo on a clean background or a photo of a crew on a job. Because these pages are built from a shared template, it is normally one edit that fixes all of them at once'+(ogPrefix?`, and nearly all of these (${ogPrefix[1]} of ${ogPart.length}) live under ${ogPrefix[0]}/`:'')+'.',
  urls: ogPart.map(p=>withProv(p))});

/* manual findings from <slug>-findings.json go FIRST (bespoke root causes).
   A manual finding may set expand_inlinks:"<path or URL>" — the builder attaches the full
   list of pages linking to that URL, so affected pages are documented in the report. */
const origin = (()=>{ try { return new URL(pages[0].url).origin; } catch(e){ return 'https://'+(D.site||''); } })();
for (const f of manual) if (f.expand_inlinks) {
  const t = f.expand_inlinks.startsWith('http') ? f.expand_inlinks : origin + f.expand_inlinks;
  const srcFull = sourcesOf(t);
  if (srcFull.length) (f.groups = f.groups||[]).push({label:`Broken address: ${rel(t)}. Found inside the code of these ${srcFull.length} page${srcFull.length>1?'s':''} — these pages themselves load fine:`, code: codeFormsFor(t, srcFull), urls: srcFull.map(rel).sort()});
}
const _suppressed = findings.filter(f => suppressAuto.some(x => (f.title||'').toLowerCase().includes(x)));
if (_suppressed.length) console.error(`suppressed ${_suppressed.length} builder finding(s) by sidecar suppress_builder: ${_suppressed.map(f=>f.title).join(' | ')}`);
const allFindings = [...manual, ...findings.filter(f => !_suppressed.includes(f))];
const sevRank = {CRITICAL:0,HIGH:1,MEDIUM:2,LOW:3,NOTE:4};
/* severity may arrive lowercase from checks-to-findings.py — normalise before ranking, else the
   lookup misses, every sidecar finding ranks 9, and CRITICALS sort BELOW the builder's own LOW
   alt-text finding (observed 2026-07-31). Manual findings also win ties (they are root causes). */
const sevKey = f => String(f && f.severity || '').toUpperCase();
const isManual = new Set(manual);
allFindings.sort((a,b)=>{
  const d=(sevRank[sevKey(a)]??9)-(sevRank[sevKey(b)]??9);
  return d!==0 ? d : (isManual.has(b)?1:0)-(isManual.has(a)?1:0);
});

/* URL entries render as clickable links; annotation text after the first token stays plain.
   Object entries {url,note,sources[]} additionally render clickable "found on:" provenance. */
const linkify = p => { const href = p.startsWith('http') ? p : (p.startsWith('/') ? origin+p : ''); return href?`<a href="${esc(href)}" target=_blank>${esc(p)}</a>`:esc(p); };
function urlLi(u){
  if (u && typeof u === 'object'){
    const src = u.sources||[];
    const sitewide = src.length >= Math.max(20, Math.floor(pages.length*0.8));
    const prov = !src.length ? '' : sitewide
      ? `<div class=prov>found in the site-wide template — appears on ${src.length} pages (every crawled page)</div>`
      : `<div class="prov ${src.length>6?'scrolly':''}">found on: ${src.map(linkify).join(' · ')}</div>`;
    return `<li>${linkify(u.url)}${esc(u.note||'')}${prov}</li>`;
  }
  // Linkify EVERY URL in the line, wherever it sits. Lines like "↳ linked from: https://…"
  // put the URL mid-string, and the old first-token-only version left those unclickable.
  const html = String(u).split(/(https?:\/\/[^\s<>"']+)/)
    .map((seg,i)=> i%2 ? linkify(seg) : esc(seg)).join('');
  return `<li>${html}</li>`;
}
function ulist(urls){
  return `<div class="${urls.length>25?'scrolly':''}"><ul class=ul>${urls.map(urlLi).join('')}</ul></div>`;
}
function findingCard(f){
  const urls = (f.urls||[]);
  const groups = (f.groups||[]);
  /* the chip must agree with the title's count: count only SUBJECT lines (lines that lead
     with a URL/path), not "↳ …" annotations, group sources, or code-search strings —
     a roofing site 2026-07-31: "1 link…" wore a "5 URLs" chip and read as a contradiction */
  const isSubject = u => typeof u === 'object' ? true : /^(https?:\/\/|\/)/.test(String(u).trim());
  const total = urls.filter(isSubject).length || (urls.length + groups.reduce((a,g)=>a+g.urls.length,0));
  /* WALK-THROUGH BODY (owner directive 2026-08-05). The reader knows their business, not DNS,
     hosting, files, code, URLs or linking. A finding must not state a conclusion — it must walk
     them through seeing it themselves: what's happening → open this exact address → what you'll
     see when you do → why it matters → how to fix it. Findings that supply the structured fields
     render as the walk-through; anything still on plain `detail` renders as before, so the two
     can coexist while findings are converted. */
  const step = (label, body) => body
    ? `<div class=wstep><div class=wlab>${esc(label)}</div><div class=wbody>${esc(body).replace(/\n/g,'<br>')}</div></div>` : '';
  const hasWalk = f.whats_happening || f.see_it || f.youll_see || f.why || f.fix;
  const body = hasWalk
    ? step("What's happening", f.whats_happening) +
      step("See it yourself", f.see_it) +
      step("What you'll see", f.youll_see) +
      step("Why this matters", f.why) +
      step("How to fix it", f.fix)
    : `<p>${esc(f.detail||'').replace(/\n/g,'<br>')}</p>`;
  return `<details class=fx><summary><span class="sev ${esc(sevKey(f))}">${esc(sevKey(f))}</span><span class=ft>${esc(f.title)}</span>${total?`<span class=cnt>${total} URL${total>1?'s':''}</span>`:''}</summary>
  <div class=fxb>${body}${urls.length?ulist(urls):''}
  ${groups.map(g=>`<div class=glab>${esc(g.label)}</div>${(g.code||[]).length?`<div class=devnote>For your web person — the exact text to search for in the page code:</div>${g.code.map(c=>`<div class=codeline>${esc(c)}</div>`).join('')}`:''}${ulist(g.urls)}`).join('')}</div></details>`;
}

/* ================= ERROR ACCORDIONS (one per status code) ================= */
const STATUS_NAME = {301:'Moved Permanently',302:'Temporary Redirect',401:'Unauthorized (usually an intentional password page)',403:'Forbidden',404:'Not Found',410:'Gone',429:'Too Many Requests',500:'Server Error',502:'Bad Gateway',503:'Service Unavailable'};
const byStatus = {};
for (const p of errs.filter(q=>!reverifiedOK.has(q.url))) (byStatus[p.status]=byStatus[p.status]||[]).push(p);
const errSections = Object.keys(byStatus).sort().map(code=>{
  const list = byStatus[code];
  const body = list.map(p=>{
    const src = sourcesOf(p.url).map(rel).sort();
    return `<div class=errcard><div class=erru>${esc(rel(p.url))}</div>
      ${src.length?`<div class=errsrc><b>Linked from ${src.length} page${src.length>1?'s':''}:</b>${ulist(src)}</div>`:'<div class="errsrc muted">No internal page links here (found via sitemap/redirect).</div>'}</div>`;
  }).join('');
  return `<details class="fx err"><summary><span class="sev HTTP">${code}</span><span class=ft>${code} — ${esc(STATUS_NAME[code]||'HTTP '+code)}</span><span class=cnt>${list.length} URL${list.length>1?'s':''}</span></summary><div class=fxb>${body}</div></details>`;
}).join('');

/* ================= PAGES grouped by URL structure ================= */
function segOf(p){ try { const segs=new URL(p.url).pathname.split('/').filter(Boolean); return segs.length<=1?'':segs[0]; } catch(e){ return ''; } }
const groups = {};
for (const p of p200) (groups[segOf(p)]=groups[segOf(p)]||[]).push(p);
/* single-page deep groups fold into top-level */
for (const k of Object.keys(groups)) if (k && groups[k].length===1 && new URL(groups[k][0].url).pathname.split('/').filter(Boolean).length===1) { (groups['']=groups['']||[]).push(groups[k][0]); delete groups[k]; }

function kv(k,v){ return v? `<tr><th>${esc(k)}</th><td>${v}</td></tr>`:''; }
function pageCard(p){
  const flags=[];
  if(!p.title) flags.push('no title'); if(!p.meta_description) flags.push('no meta-desc');
  if((p.heading_counts&&p.heading_counts.h1||0)===0) flags.push('no H1');
  if(p.images_missing_alt) flags.push(p.images_missing_alt+' alt');
  const og=p.open_graph?Object.entries(p.open_graph).map(([k,v])=>`<div class="tg"><code>${esc(k)}</code> ${esc((v||'').slice(0,90))}</div>`).join(''):'';
  const tw=p.twitter&&Object.keys(p.twitter).length?Object.entries(p.twitter).map(([k,v])=>`<div class="tg"><code>${esc(k)}</code> ${esc((v||'').slice(0,90))}</div>`).join(''):'<span class="muted">none</span>';
  const heads=['h1','h2','h3','h4','h5','h6'].map(h=>{const arr=(p.headings&&p.headings[h])||[];return arr.length?`<div class="hd"><b>${h.toUpperCase()} (${arr.length})</b> ${arr.map(t=>esc(t)).join(' · ')}</div>`:''}).join('');
  return `<details class="pg"><summary><span class="pu">${esc(rel(p.url))}</span><span class="pstat s${p.status}">${p.status}</span>${flags.map(f=>`<span class="fl">${esc(f)}</span>`).join('')}</summary>
   <div class="pb"><table class="kv">
    ${kv('Title',esc(p.title)+` <span class="muted">(${p.title_len})</span>`)}
    ${kv('Meta description',esc(p.meta_description)+(p.meta_desc_len?` <span class="muted">(${p.meta_desc_len})</span>`:''))}
    ${kv('Canonical',esc(p.canonical))}
    ${kv('Robots',esc(p.meta_robots))}
    ${kv('Word count',p.word_count)}
    ${kv('Images',`${p.images_total} total · ${p.images_missing_alt} missing alt · ${p.images_lazy} lazy`)}
    ${kv('JSON-LD',(p.jsonld_types||[]).join(', '))}
   </table>
   <div class="sub">Headings</div><div class="heads">${heads||'<span class="muted">none</span>'}</div>
   <div class="sub">OpenGraph</div><div class="tgs">${og||'<span class="muted">none</span>'}</div>
   <div class="sub">Twitter</div><div class="tgs">${tw}</div>
   </div></details>`;
}
function groupBlock(label, list){
  list.sort((a,b)=>rel(a.url).localeCompare(rel(b.url)));
  return `<details class=grp><summary><span class=gt>${esc(label)}</span><span class=cnt>${list.length} page${list.length>1?'s':''}</span></summary><div class=grpb>${list.map(pageCard).join('')}</div></details>`;
}
const groupOrder = Object.keys(groups).sort((a,b)=> (a===''? -1 : b===''? 1 : groups[b].length-groups[a].length));
const pageSections = groupOrder.map(g=>{
  const list = groups[g];
  if (g && list.length>15){ /* sub-segment big sections (e.g. /blog/tag/) */
    const subs={};
    for (const p of list){ const segs=new URL(p.url).pathname.split('/').filter(Boolean); (subs[segs.length>=2&&segs[1]?segs[1]:'_']=subs[segs.length>=2&&segs[1]?segs[1]:'_']||[]).push(p); }
    const subKeys = Object.keys(subs).filter(k=>k!=='_'&&subs[k].length>=3);
    if (subKeys.length){
      const rest = list.filter(p=>{const segs=new URL(p.url).pathname.split('/').filter(Boolean); return !(segs[1]&&subKeys.includes(segs[1])&&subs[segs[1]].length>=3);});
      return `<details class=grp><summary><span class=gt>/${esc(g)}/</span><span class=cnt>${list.length} pages</span></summary><div class=grpb>
        ${rest.length?groupBlock(`/${g}/ — pages & posts`, rest):''}
        ${subKeys.sort((a,b)=>subs[b].length-subs[a].length).map(k=>groupBlock(`/${g}/${k}/`, subs[k])).join('')}
      </div></details>`;
    }
  }
  return groupBlock(g?`/${g}/`:'Top-level pages', list);
}).join('');

/* ================= PERFORMANCE (condensed, bottom) ================= */
const perfTable = Object.keys(lh).length? `<table class="perf"><tr><th>Tier</th><th>Score</th><th>LCP</th><th>FCP</th><th>TBT</th><th>CLS</th></tr>
  ${[['Mobile · Slow 3G','mobile-3g'],['Mobile · Slow 4G','mobile-4g'],['Desktop · WiFi','desktop']].map(([lbl,t])=>lh[t]?`<tr><td>${lbl}</td>${lhcell(lh[t])}<td>${lhm(lh[t],'largest-contentful-paint')}</td><td>${lhm(lh[t],'first-contentful-paint')}</td><td>${lhm(lh[t],'total-blocking-time')}</td><td>${lhm(lh[t],'cumulative-layout-shift')}</td></tr>`:'').join('')}
  </table><p class="muted">Targets: LCP&lt;2.5s · FCP&lt;1.8s · TBT&lt;200ms · CLS&lt;0.1. Lighthouse default = mobile Slow-4G.</p>${perfNote?`<p class=pnote>${esc(perfNote)}</p>`:''}`:'';

/* ---- raw recursive (external_links removed per report spec) ---- */
const RAW = Object.assign({}, D); delete RAW.external_links;
/* The Raw tab used to render the ENTIRE crawl JSON as nested HTML tables — on a big site
   (one client site: 426 pages / 12.8MB / 35k images) that is a multi-million-node DOM that hangs or
   CRASHES the browser tab when clicked (owner 2026-08-04). For large crawls, do NOT inline it:
   write the full JSON to a sibling file and link it, and show only a compact summary. */
const RAW_PAGES = (RAW.pages||[]).length;
const RAW_BYTES = Buffer.byteLength(JSON.stringify(D));
const RAW_HEAVY = RAW_PAGES > 60 || RAW_BYTES > 2_000_000;
let rawPane;
if (RAW_HEAVY) {
  const jsonName = path.basename(outPath).replace(/\.html$/, '') + '-raw.json';
  if (!AS_MD) try { fs.writeFileSync(path.join(path.dirname(outPath), jsonName), JSON.stringify(D)); } catch(e){}
  const sizeMB = (RAW_BYTES/1048576).toFixed(1);
  rawPane = `<div class=sec><h3>Raw crawl data</h3>
    <p>This crawl covers <b>${RAW_PAGES.toLocaleString()} pages</b> (${sizeMB} MB of data). Rendering
    that much on this page would freeze or crash your browser, so it is provided as a download
    instead.</p>
    <p style="margin-top:10px"><a href="${esc(jsonName)}" download><b>⬇ Download the full crawl data (JSON, ${sizeMB} MB)</b></a></p>
    <p class=muted style="margin-top:10px">Everything you need to act on is in the <b>Audit</b> tab.
    The JSON is the complete per-page technical export for developers.</p></div>`;
} else {
  rawPane = `<div class=sec>${rraw(RAW)}</div>`;
}
function rraw(v){ if(v==null||v===''||(Array.isArray(v)&&!v.length))return '<span class=muted>—</span>';
  if(Array.isArray(v)) return v.every(x=>typeof x!=='object')?v.map(esc).join(', '):`<div class=cards>${v.map(x=>`<div class=card>${rraw(x)}</div>`).join('')}</div>`;
  if(typeof v==='object') return `<table class=kv>${Object.entries(v).map(([k,x])=>`<tr><th>${esc(k)}</th><td>${rraw(x)}</td></tr>`).join('')}</table>`;
  return esc(String(v).slice(0,300)); }

const nErr = errs.filter(q=>!reverifiedOK.has(q.url)).length;
/* "What we could not check" (2026-10-14, research lesson + a client site): declared skipped steps first,
   then addresses that refused our checker. Unknown is said out loud, never shown as an error or a pass. */
/* PARTIAL REPORT (owner safety rule 2026-10-14): never a fake complete report. Any of these makes
   the report PARTIAL: steps not measured, things the run could not check, a sidecar "partial"
   reason, robots.txt blocking pages or not answering, the page cap, an unfinished crawl, or
   addresses that refused our checker. The reasons go in a banner at the top and in the title. */
const robotsBlocked = Array.isArray(D.robots_disallowed) ? D.robots_disallowed : [];
const nRobots = Math.max(robotsBlocked.length, Number(s.robots_disallowed) || 0);
const partialReasons = [];
if (sidecarPartial) partialReasons.push(String(typeof sidecarPartial === 'object' ? (sidecarPartial.reason || 'the run was incomplete') : (sidecarPartial === true ? 'the run was incomplete' : sidecarPartial)));
if (s.robots_txt === 'unreachable') partialReasons.push('the site\'s robots.txt did not answer, so nothing was crawled');
if (D.partial || s.partial) partialReasons.push('the crawl did not finish');
if (s.page_cap_hit) partialReasons.push(`the crawl stopped at the ${s.max_pages || ''}-page limit`.replace(' -page', ' page'));
if (nRobots) partialReasons.push(`the site's robots.txt asked us not to read ${nRobots} page${nRobots>1?'s':''}`);
if (notMeasured.length) partialReasons.push('not measured: ' + notMeasured.map(n => n.what || n.step).join(', '));
if (couldNotCheck.length) partialReasons.push('could not check: ' + couldNotCheck.map(n => n.what || n.step).join(', '));
if (blockedRows.length) partialReasons.push(`${blockedRows.length} address${blockedRows.length>1?'es':''} refused our checker`);
const PARTIAL = partialReasons.length > 0;
const partialText = PARTIAL ? 'PARTIAL REPORT: ' + partialReasons.join('; ') + '.' : '';
const DISCLAIMERS = [
  "This is an automated audit. It can miss things or flag things that aren't real problems, so have your web person check before making changes.",
  "It doesn't guarantee rankings or traffic.",
  "I didn't change anything on your site.",
  "Your site data stays on this bot's computer and isn't sent to the template creator.",
  "Some advice may not apply to your business, site, or situation. Use your own judgment and check with your web person.",
  "You choose which changes to make. The template creator and this bot are not responsible for any loss of traffic, rankings, leads, or revenue from following or not following this report.",
];
const notCheckedHtml = (notMeasured.length || couldNotCheck.length || blockedRows.length || nRobots) ? `<div class=sec><h3>What we could not check</h3>
  <p class=muted style="margin-bottom:8px">These parts are not counted as problems or as passes. We could not measure them.</p>
  ${notMeasured.map(n=>`<div class=nm><b>${esc(n.what||n.step)}</b> — ${esc(n.why||'')}</div>`).join('')}
  ${couldNotCheck.map(n=>`<div class=nm><b>${esc(n.what||n.step)}</b> — ${esc(n.why||'')}</div>`).join('')}
  ${nRobots?`<div class=nm><b>${nRobots} page${nRobots>1?'s':''} we were asked not to read</b> — the site's robots.txt tells crawlers to stay out, and we obey it. These pages were not checked.${robotsBlocked.length?ulist(robotsBlocked.map(p=>rel(p.url))):''}</div>`:''}
  ${blockedRows.length?`<div class=nm><b>${blockedRows.length} address${blockedRows.length>1?'es':''} refused our checker</b> — the site's security answered "Forbidden" or "too many requests", or did not answer. A real visitor may see these pages fine, so we do not call them broken.${ulist(blockedRows.map(p=>`${rel(p.url)}  (${p.status==null?'no answer':p.status})`))}</div>`:''}
</div>` : '';
/* Book-a-call box: OFF unless CTA_URL is set (CTA_TEL adds a phone button, CTA_TEL_TXT its label).
   When shown it sits above the findings and again at the bottom. REPORT_BRAND is the footer text. */
const CTA_URL = process.env.CTA_URL || '';
const CTA_TEL = process.env.CTA_TEL || '';
const CTA_TEL_TXT = process.env.CTA_TEL_TXT || CTA_TEL;
const BRAND = process.env.REPORT_BRAND || 'Site audit';
const ctaBox = !CTA_URL ? '' : `<div class=cta><div><b>Want these fixed?</b> Book a call${process.env.REPORT_BRAND ? ' with ' + esc(BRAND) : ''}. We will go through this report with you.</div>
  <div class=ctab><a class=ctabtn href="${esc(CTA_URL)}" target=_blank>Book a call</a>${CTA_TEL ? `<a class="ctabtn alt" href="tel:${esc(CTA_TEL)}">Call ${esc(CTA_TEL_TXT)}</a>` : ''}</div></div>`;
if (AS_MD) { writeMarkdown(); process.exit(0); }
const html=`<!doctype html><html lang=en><head><meta charset=utf-8><meta name=robots content="noindex,nofollow">
<meta name=viewport content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive,nosnippet"><title>${PARTIAL?'PARTIAL — ':''}Site Audit — ${esc(NAME)}</title><style>
*{box-sizing:border-box;margin:0;padding:0;border-radius:0!important}
body{font-family:system-ui,Segoe UI,Arial;background:#eef1f4;color:#16232e;line-height:1.5;padding:22px}
.wrap{max-width:1274px;margin:0 auto}
header.hd{background:#fff;border:2px solid #16232e;box-shadow:5px 5px 0 #c6cfd6;padding:18px 22px;margin-bottom:16px}
header.hd h1{font-size:1.35rem;color:#0b3a52}.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.pill{background:#e7edf2;border:1px solid #16232e;padding:3px 11px;font-size:.8rem;color:#16232e;font-weight:600}
.tabs{display:flex;gap:8px;margin-bottom:16px}.tabs button{background:#fff;border:2px solid #16232e;color:#16232e;padding:9px 18px;font-weight:700;cursor:pointer;box-shadow:3px 3px 0 #c6cfd6}
.tabs button.active{background:#0b3a52;color:#fff;border-color:#0b3a52}.pane{display:none}.pane.active{display:block}
.sec{background:#fff;border:2px solid #16232e;box-shadow:5px 5px 0 #c6cfd6;padding:16px 20px;margin-bottom:16px}
.sec>h3{font-size:1rem;color:#0b3a52;margin-bottom:10px;text-transform:uppercase;letter-spacing:.04em}
a{color:#0b5c8d;text-decoration:none}.muted{color:#5f7280}
details.fx{border:1px solid #16232e;background:#fafbfc;margin-bottom:8px}
details.fx>summary{cursor:pointer;list-style:none;padding:10px 12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
details.fx>summary::-webkit-details-marker{display:none}
details.fx[open]>summary{border-bottom:1px solid #16232e;background:#eef3f7}
.fxb{padding:12px 14px}.fxb p{font-size:.92rem;margin-bottom:8px}
/* --- WALK-THROUGH steps (plain-language findings, 2026-08-05) --- */
.wstep{margin-bottom:12px}
.wstep:last-child{margin-bottom:0}
.wlab{font-size:.68rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#0b5c8d;margin-bottom:3px}
.wbody{font-size:.95rem;line-height:1.55;color:#16232e}
.wstep .wbody a{text-decoration:underline}
/* --- GLOSSARY --- */
.gloss{background:#f2f7fa;border:2px solid #16232e;box-shadow:5px 5px 0 #c6cfd6;padding:14px 18px;margin-bottom:16px}
.gloss h3{font-size:.82rem;text-transform:uppercase;letter-spacing:.06em;color:#0b3a52;margin-bottom:8px}
.gloss dl{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;margin:0;font-size:.9rem}
.gloss dt{font-weight:700;color:#0b3a52;white-space:nowrap}
.gloss dd{margin:0;color:#16232e}
@media(max-width:600px){.gloss dl{grid-template-columns:1fr}.gloss dd{margin-bottom:6px}}
.ul{margin:0 0 0 18px;font-size:.85rem}.ul li{padding:1px 0;word-break:break-all}
.scrolly{max-height:360px;overflow:auto;border:1px solid #d5dde3;background:#fff;padding:6px 8px;margin-bottom:6px}
.glab{font-weight:700;font-size:.86rem;margin:10px 0 4px;color:#0b3a52;word-break:break-all}
.prov{font-size:.78rem;color:#5f7280;margin:1px 0 5px 14px;word-break:break-all}
.codehow{font-weight:700;font-size:.8rem;color:#8f1d0e;margin:4px 0 2px}
.devnote{font-weight:700;font-size:.74rem;color:#5f7280;letter-spacing:.03em;text-transform:uppercase;margin:10px 0 3px;padding-top:8px;border-top:1px dashed #c6cfd6}
.codeline{font-family:ui-monospace,Consolas,monospace;font-size:.8rem;background:#f3efe7;border:1px solid #16232e;padding:5px 8px;margin-bottom:4px;word-break:break-all}
.ft{font-weight:700;font-size:.92rem}.cnt{margin-left:auto;font-size:.76rem;font-weight:700;color:#5f7280;white-space:nowrap}
.sev{font-size:.7rem;font-weight:800;padding:2px 8px;white-space:nowrap;border:1px solid #16232e}
.CRITICAL{background:#8f1d0e;color:#fff;border-color:#8f1d0e}.HIGH{background:#f7d6cf;color:#8f1d0e}.MEDIUM{background:#e7edf2;color:#0b3a52}.LOW{background:#eef0f2;color:#3d4f5c}.NOTE{background:#fff;color:#3d4f5c}.HTTP{background:#8f1d0e;color:#fff;border-color:#8f1d0e}
.errcard{border:1px solid #d5dde3;background:#fff;padding:10px 12px;margin-bottom:8px}
.erru{font-weight:700;font-size:.9rem;word-break:break-all;color:#8f1d0e}
.errsrc{margin-top:6px;font-size:.85rem}.errsrc b{color:#16232e}
details.grp{border:1px solid #16232e;background:#fafbfc;margin-bottom:8px}
details.grp>summary{cursor:pointer;list-style:none;padding:10px 12px;display:flex;gap:10px;align-items:center}
details.grp>summary::-webkit-details-marker{display:none}
details.grp[open]>summary{border-bottom:1px solid #16232e;background:#eef3f7}
.gt{font-weight:800;color:#0b3a52;font-size:.92rem}.grpb{padding:10px 12px}
details.pg{border:1px solid #d5dde3;background:#fff;margin-bottom:6px}
details.pg>summary{cursor:pointer;list-style:none;padding:8px 12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
details.pg>summary::-webkit-details-marker{display:none}details.pg[open]>summary{background:#eef3f7;border-bottom:1px solid #d5dde3}
.pu{font-weight:700;color:#16232e;font-size:.88rem;word-break:break-all}.pstat{font-size:.74rem;font-weight:700;padding:1px 6px;border:1px solid #16232e}.s200{background:#dff2e6;color:#12603a}
.fl{font-size:.72rem;background:#fbeee0;color:#8a5a12;border:1px solid #8a5a12;padding:1px 6px}
.pb{padding:12px}.sub{font-weight:700;color:#0b3a52;font-size:.8rem;margin:12px 0 5px;text-transform:uppercase}
table.kv{width:100%;border-collapse:collapse}table.kv th{text-align:left;vertical-align:top;color:#5f7280;font-weight:600;padding:4px 12px 4px 0;white-space:nowrap;width:1%;font-size:.84rem}table.kv td{padding:4px 0;font-size:.88rem;word-break:break-word}
.tg{font-size:.82rem;padding:2px 0}.tg code{background:#e7edf2;color:#3d4f5c;font-size:.74rem;padding:1px 5px}
.heads .hd{font-size:.84rem;padding:3px 0;border-bottom:1px solid #eef1f4}.heads b{color:#0b3a52}
table.perf{width:100%;border-collapse:collapse;display:block;overflow-x:auto}table.perf th{text-align:left;font-size:.78rem;color:#5f7280;padding:5px 10px;border-bottom:2px solid #16232e}
table.perf td{padding:7px 10px;border-bottom:1px solid #e3e9ee;font-size:.9rem}
.sc{font-weight:800;text-align:center}.scna{color:#5f7280;font-weight:600}.scbad{color:#8f1d0e}.scmid{color:#8a5a12}.scgood{color:#12603a}
.pnote{font-size:.88rem;background:#eef3f7;border:1px solid #16232e;padding:8px 10px;margin-top:8px}
.cards{display:flex;flex-direction:column;gap:6px}.card{background:#fafbfc;border:1px solid #d5dde3;padding:6px 8px;font-size:.82rem}
footer{color:#5f7280;font-size:.78rem;text-align:center;margin-top:16px}
.nm{font-size:.9rem;margin-bottom:8px}
.cta{background:#0b3a52;color:#fff;border:2px solid #0b3a52;box-shadow:5px 5px 0 #c6cfd6;padding:14px 18px;margin-bottom:16px;display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.ctab{display:flex;gap:8px;flex-wrap:wrap}.ctabtn{background:#f2c14e;color:#0b3a52!important;font-weight:800;padding:9px 16px;border:2px solid #f2c14e;text-decoration:none}
.ctabtn.alt{background:transparent;color:#fff!important;border-color:#fff}
.partial{background:#8f1d0e;color:#fff;border:2px solid #8f1d0e;padding:12px 16px;margin-bottom:16px;font-weight:800;font-size:1rem}
.disc{background:#fff;border:2px solid #16232e;padding:12px 18px;margin-top:16px;font-size:.86rem;color:#3d4f5c}.disc h3{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:#0b3a52;margin-bottom:6px}.disc li{margin:2px 0 2px 18px}
</style></head><body><div class=wrap>
${PARTIAL?`<div class=partial role=alert>${esc(partialText)}</div>`:''}
<header class=hd><h1>${esc(NAME)} — Technical Site Audit</h1><div class=meta>
<span class=pill>${s.pages} URLs crawled</span><span class=pill>${p200.length} OK (200)</span><span class=pill>${nErr} error${nErr!==1?'s':''}</span>${blockedRows.length?`<span class=pill>${blockedRows.length} not checked</span>`:''}
<span class=pill>avg ${s.avg_word_count} words/page</span></div></header>
<div class=tabs><button class="tb active" data-t=audit>Audit</button><button class=tb data-t=raw>Raw data</button></div>
<div class="pane active" id=audit>
  ${ctaBox}
  <div class=gloss><h3>A few words you'll see in this report</h3><dl>
    <dt>URL</dt><dd>The address of one page, like <b>yoursite.com/contact</b>. Every page has its own.</dd>
    <dt>Link</dt><dd>Text or a button you click that sends you to another page.</dd>
    <dt>404</dt><dd>The message a website shows when a page isn't there. A dead end.</dd>
    <dt>Redirect</dt><dd>When you click one address but the site sends you to a different one instead.</dd>
    <dt>Sitemap</dt><dd>A list of your pages that your website hands to Google so Google knows what you have.</dd>
    <dt>Index</dt><dd>Google's list of pages it will show in search results. "Indexed" = Google can show it.</dd>
  </dl></div>
  <div class=sec><h3>What needs fixing <span class=muted>(${allFindings.length} finding${allFindings.length!==1?'s':''} — click each to expand)</span></h3>
   ${allFindings.map(findingCard).join('')||'<span class=muted>No issues found.</span>'}</div>
  ${notCheckedHtml}
  ${errSections?`<div class=sec><h3>Errors by status code <span class=muted>(every non-200 URL, with the pages that link to it)</span></h3>${errSections}</div>`:''}
  <div class=sec><h3>All pages</h3>
   <details class=grp><summary><span class=gt>Pages by site section</span><span class=cnt>${p200.length} pages</span></summary><div class=grpb>${pageSections}</div></details></div>
  ${perfTable?`<div class=sec><h3>Page-load performance <span class=muted>(reference — not a priority fix)</span></h3><details class=grp><summary><span class=gt>Core Web Vitals by device / connection</span></summary><div class=grpb>${perfTable}</div></details></div>`:''}
</div>
<div class="pane" id=raw>${rawPane}</div>
${ctaBox}
<div class=disc><h3>Please read</h3><ul>${DISCLAIMERS.map(d=>`<li>${esc(d)}</li>`).join('')}</ul></div>
<footer>${esc(BRAND)}</footer>
</div><script>document.querySelectorAll('.tb').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tb').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.pane').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById(b.dataset.t).classList.add('active')});</script>
</body></html>`;
fs.writeFileSync(outPath, html);
console.log(`Wrote ${outPath} — ${pages.length} pages (${nErr} errors, ${blockedRows.length} not checked, ${notMeasured.length} steps not measured), ${allFindings.length} findings, ${Object.keys(lh).length} perf tiers.`);

/* ================= MARKDOWN OUTPUT (out path ends in .md) =================
   Same content as the Audit tab: findings (walk-through or detail, URL lists, groups), "What we could
   not check", errors by status code and the performance table. Crawled text is data: it is put in
   backticks or escaped so it cannot become Markdown structure or raw HTML. */
function writeMarkdown(){
  const mdEsc = t => String(t==null?'':t).replace(/[\\`*_{}\[\]<>#|]/g, c => '\\' + c);
  const code = t => { const v = String(t==null?'':t).replace(/\r?\n/g,' '); const n = (v.match(/`+/g)||[]).reduce((m,x)=>Math.max(m,x.length),0);
    const f = '`'.repeat(n+1); return f + (v.startsWith('`')||v.endsWith('`') ? ' '+v+' ' : v) + f; };
  const para = t => String(t==null?'':t).split(/\n/).map(l => mdEsc(l)).join('  \n');
  const urlLine = u => {
    if (u && typeof u === 'object') {
      const src = u.sources||[];
      const sitewide = src.length >= Math.max(20, Math.floor(pages.length*0.8));
      const prov = !src.length ? '' : sitewide ? ` (in the site-wide template, on ${src.length} pages)`
        : ` (found on: ${src.map(code).join(', ')})`;
      return `- ${code(u.url)}${u.note ? ' ' + mdEsc(u.note) : ''}${prov}`;
    }
    return `- ${code(u)}`;
  };
  const L = [];
  L.push(`# ${PARTIAL ? 'PARTIAL: ' : ''}${mdEsc(NAME)}: technical site audit`, '');
  if (PARTIAL) L.push(`> **${mdEsc(partialText)}**`, '');
  L.push(`${s.pages} URLs crawled · ${p200.length} OK (200) · ${nErr} error${nErr!==1?'s':''}` +
         (blockedRows.length ? ` · ${blockedRows.length} not checked` : '') + ` · avg ${s.avg_word_count} words/page`, '');
  const cta = () => { if (CTA_URL) L.push(`**Want these fixed?** Book a call${process.env.REPORT_BRAND ? ' with ' + mdEsc(BRAND) : ''}: <${CTA_URL}>` +
    (CTA_TEL ? ` · Call ${mdEsc(CTA_TEL_TXT)} (tel:${CTA_TEL})` : ''), ''); };
  cta();
  L.push(`## What needs fixing (${allFindings.length} finding${allFindings.length!==1?'s':''})`, '');
  if (!allFindings.length) L.push('No issues found.', '');
  allFindings.forEach((f, i) => {
    const urls = f.urls||[], groups = f.groups||[];
    L.push(`### ${i+1}. [${sevKey(f)}] ${mdEsc(f.title)}`, '');
    const hasWalk = f.whats_happening || f.see_it || f.youll_see || f.why || f.fix;
    if (hasWalk) {
      for (const [lab, body] of [["What's happening", f.whats_happening], ['See it yourself', f.see_it],
                                  ["What you'll see", f.youll_see], ['Why this matters', f.why], ['How to fix it', f.fix]])
        if (body) L.push(`**${lab}:** ${para(body)}`, '');
    } else if (f.detail) L.push(para(f.detail), '');
    if (urls.length) { L.push(`Addresses (${urls.length}):`, '', ...urls.map(urlLine), ''); }
    for (const g of groups) {
      L.push(`**${mdEsc(g.label)}**`, '');
      if ((g.code||[]).length) L.push('For your web person, the exact text to search for in the page code:', '', ...g.code.map(c => `- ${code(c)}`), '');
      L.push(...(g.urls||[]).map(urlLine), '');
    }
  });
  if (notMeasured.length || couldNotCheck.length || blockedRows.length || nRobots) {
    L.push('## What we could not check', '', 'These parts are not counted as problems or as passes. We could not measure them.', '');
    for (const n of notMeasured) L.push(`- **${mdEsc(n.what||n.step)}**: ${mdEsc(n.why||'')}`);
    for (const n of couldNotCheck) L.push(`- **${mdEsc(n.what||n.step)}**: ${mdEsc(n.why||'')}`);
    if (nRobots) {
      L.push(`- **${nRobots} page${nRobots>1?'s':''} we were asked not to read**: the site's robots.txt tells crawlers to stay out, and we obey it. These pages were not checked.`);
      for (const p of robotsBlocked) L.push(`  - ${code(rel(p.url))}`);
    }
    if (blockedRows.length) {
      L.push(`- **${blockedRows.length} address${blockedRows.length>1?'es':''} refused our checker**: the site's security answered "Forbidden" or "too many requests", or did not answer. A real visitor may see these pages fine, so we do not call them broken.`);
      for (const p of blockedRows) L.push(`  - ${code(rel(p.url))} (${p.status==null?'no answer':p.status})`);
    }
    L.push('');
  }
  const codes = Object.keys(byStatus).sort();
  if (codes.length) {
    L.push('## Errors by status code', '', 'Every non-200 URL, with the pages that link to it.', '');
    for (const c of codes) {
      L.push(`### ${c}: ${mdEsc(STATUS_NAME[c]||'HTTP '+c)} (${byStatus[c].length} URL${byStatus[c].length>1?'s':''})`, '');
      for (const p of byStatus[c]) {
        const src = sourcesOf(p.url).map(rel).sort();
        L.push(`- ${code(rel(p.url))}` + (src.length ? `, linked from ${src.length} page${src.length>1?'s':''}: ${src.map(code).join(', ')}`
                                                     : ', no internal page links here (found via sitemap/redirect)'));
      }
      L.push('');
    }
  }
  if (Object.keys(lh).length) {
    L.push('## Page-load performance (reference, not a priority fix)', '', '| Tier | Score | LCP | FCP | TBT | CLS |', '|---|---|---|---|---|---|');
    for (const [lbl, t] of [['Mobile · Slow 3G','mobile-3g'],['Mobile · Slow 4G','mobile-4g'],['Desktop · WiFi','desktop']]) {
      if (!lh[t]) continue;
      const v = lhscore(lh[t]);
      const sc = v === null ? 'Not measured' + (lh[t].runtimeError && lh[t].runtimeError.code ? ` (${mdEsc(lh[t].runtimeError.code)})` : '') : String(v);
      L.push(`| ${lbl} | ${sc} | ${mdEsc(lhm(lh[t],'largest-contentful-paint'))} | ${mdEsc(lhm(lh[t],'first-contentful-paint'))} | ${mdEsc(lhm(lh[t],'total-blocking-time'))} | ${mdEsc(lhm(lh[t],'cumulative-layout-shift'))} |`);
    }
    L.push('', 'Targets: LCP < 2.5 s · FCP < 1.8 s · TBT < 200 ms · CLS < 0.1. Lighthouse default = mobile Slow-4G.', '');
    if (perfNote) L.push(para(perfNote), '');
  }
  cta();
  L.push('## Please read', '', ...DISCLAIMERS.map(d => `- ${d}`), '');
  L.push('---', '', mdEsc(BRAND), '');
  fs.writeFileSync(outPath, L.join('\n'));
  console.log(`Wrote ${outPath} (Markdown) — ${pages.length} pages (${nErr} errors, ${blockedRows.length} not checked, ${notMeasured.length} steps not measured), ${allFindings.length} findings, ${Object.keys(lh).length} perf tiers.`);
}
