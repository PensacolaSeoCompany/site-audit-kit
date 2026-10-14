#!/usr/bin/env node
/* click-test.js <url> <out.json>
 *
 * Rendered click test for "links go nowhere / buttons do nothing" candidates (JP-02, 2026-10-14:
 * a makeup-artist site shipped "7 service buttons go nowhere" for 3 h 39 min; they were a working Thrive
 * button group. A source regex cannot see JavaScript wiring; a click can).
 *
 * Candidates (visible only): a[href="#"], a[href=""], a[href^="javascript:"], a without href,
 * button, [role=button]. Cap 60 elements, 90 s total.
 * READ-ONLY: it never clicks an element inside a <form>, an element with type=submit/image/reset, or
 * one whose text, label or title matches submit|send|buy|pay|order|checkout|delete|remove|subscribe|
 * sign up|sign in|log in|register|apply|book|unsubscribe|confirm. Those are listed in not_tested with
 * why "read-only: ...". It only loads the page and clicks in-page controls; it sends no form.
 * For each: scroll it into view, snapshot, click, wait 1.5 s, and record which of these changed:
 *   url        location changed (a bare trailing "#" does not count)
 *   scroll     scroll position changed (except the browser's own jump-to-top for href="#")
 *   new_visible  an element became visible that was not visible before (idle-noisy ones ignored)
 *   attr       aria-expanded / aria-selected / aria-hidden / class / hidden / open / style changed
 *              on the element, a sibling, its parent, or the element named by aria-controls
 *   dialog     alert/confirm/prompt, a popup window, or a <dialog>/[role=dialog] became visible
 *   dom        document element count changed (ignored when the idle page already changes it)
 * Each click has a CONTROL window first: the same 1.5 s at the same scroll position with no click.
 * Verdict: "works" if a signal fired that the control window did not show; "does nothing" if no
 * signal fired at all; "unclear" if the only signals are ones the page also shows with no click
 * (lazy loads, carousels). Only "does nothing" supports a "goes nowhere" finding. The page is
 * reloaded after every click that changed something, and every 10 clicks regardless.
 *
 * Output: out.json (case evidence folder; refuses /tmp). Exit 0 on a measured run, 5 when the page
 * could not be measured (load error, HTTP not 200, challenge page), 2 on bad usage.
 * Chrome: env CHROME_BIN, else google-chrome / google-chrome-stable / chromium / chromium-browser on PATH,
 * driven by puppeteer-core from $KIT_HOME/node_modules (then Node's normal lookup). Chrome is always closed.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const KIT_HOME = path.resolve(__dirname);
function requireKit(name) {   // $KIT_HOME/node_modules first, then the normal search path
  try { return require(path.join(KIT_HOME, 'node_modules', name)); } catch (e) { return require(name); }
}
const puppeteer = requireKit('puppeteer-core');
function findChrome() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  // the kit's own Chrome: work/chrome-bin (written by install.sh), then a local Chrome for Testing in .chrome/
  // (2026-10-14: as a non-root user with no system Chrome this tool said "no Chrome found" while install.sh had one)
  const KIT_HOME = path.resolve(__dirname), KIT_WORK = process.env.KIT_WORK || path.join(KIT_HOME, 'work');
  try { const f = fs.readFileSync(path.join(KIT_WORK, 'chrome-bin'), 'utf8').trim(); if (f && fs.existsSync(f)) return f; } catch (e) {}
  try {
    const base = path.join(KIT_HOME, '.chrome', 'chrome');
    for (const d of fs.readdirSync(base)) { const f = path.join(base, d, 'chrome-linux64', 'chrome'); if (fs.existsSync(f)) return f; }
  } catch (e) {}
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    for (const dir of (process.env.PATH || '').split(path.delimiter)) {
      const f = path.join(dir || '.', name);
      try { fs.accessSync(f, fs.constants.X_OK); return f; } catch (e) {}
    }
  }
  console.error('FATAL: no Chrome found. Install google-chrome or chromium, or set CHROME_BIN.');
  process.exit(2);
}
const CHROME = findChrome();
const MAX_ELEMENTS = 60;
const TOTAL_MS = 90000;
const SETTLE_MS = 1500;
const RELOAD_EVERY = 10;

const [url, outArg] = process.argv.slice(2);
if (!url || !outArg || !/^https?:\/\//.test(url)) {
  console.error('usage: node click-test.js <url> <out.json>');
  process.exit(2);
}
const out = path.resolve(outArg);
const outDir = path.dirname(out);
if (out === '/tmp' || out.startsWith('/tmp/') || out.startsWith('/var/tmp/')) {
  console.error(`REFUSED: out.json goes in the case evidence folder, never under /tmp (got ${out})`);
  process.exit(2);
}

const T0 = Date.now();
const left = () => TOTAL_MS - (Date.now() - T0);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const CHALLENGE = /<title>\s*Just a moment\.\.\.|Attention Required! \| Cloudflare|cf-chl-|challenge-platform|Checking your browser before accessing|_Incapsula_Resource|px-captcha|captcha-delivery\.com/i;

// ---- page-side helpers (serialised into the page) ----
const PAGE_HELPERS = `
window.__ct = {
  vis(el) {
    if (!el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    if (el.checkVisibility) return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
  },
  cssPath(el) {
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.documentElement) {
      if (el.id && /^[A-Za-z][\\w-]*$/.test(el.id) && document.querySelectorAll('#' + el.id).length === 1) { parts.unshift('#' + el.id); break; }
      let i = 1, s = el;
      while ((s = s.previousElementSibling)) if (s.tagName === el.tagName) i++;
      parts.unshift(el.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
      el = el.parentElement;
    }
    return parts.join(' > ');
  },
  // READ-ONLY GUARD (owner safety rule 2026-10-14): never click anything that could send a form,
  // sign in, buy, delete or subscribe. Returns the reason to skip, or null.
  unsafe(el, text) {
    if (el.closest('form')) return 'read-only: inside a form';
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'submit' || type === 'image' || type === 'reset') return 'read-only: type=' + type;
    const words = [text, el.getAttribute('aria-label') || '', el.title || '', el.value || ''].join(' ');
    if (/submit|send|buy|pay|order|checkout|delete|remove|subscribe|sign ?up|sign ?in|log ?in|register|apply|book|unsubscribe|confirm/i.test(words))
      return 'read-only: the text suggests it changes something';
    return null;
  },
  candidates() {
    const sel = 'a[href="#"], a[href=""], a[href^="javascript:" i], a:not([href]), button, [role="button"], input[type="button"]';
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      if (!this.vis(el)) continue;
      const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
      out.push({ el, text, unsafe: this.unsafe(el, text) });
    }
    return out;
  },
  snapshot(el) {
    const visible = new WeakSet();
    let nVis = 0;
    for (const e of document.body.querySelectorAll('*')) if (this.vis(e)) { visible.add(e); nVis++; }
    this.before = { url: location.href, x: scrollX, y: scrollY, nodes: document.getElementsByTagName('*').length, visible, nVis };
    this.attrHits = [];
    const watch = new Set([el, el.parentElement, ...(el.parentElement ? el.parentElement.children : [])]);
    const ctl = el.getAttribute('aria-controls');
    if (ctl) for (const id of ctl.split(/\\s+/)) { const t = document.getElementById(id); if (t) watch.add(t); }
    if (this.mo) this.mo.disconnect();
    this.mo = new MutationObserver((ms) => { for (const m of ms) if (watch.has(m.target)) this.attrHits.push(m.attributeName); });
    for (const w of watch) if (w) this.mo.observe(w, { attributes: true, attributeFilter: ['aria-expanded', 'aria-selected', 'aria-hidden', 'aria-pressed', 'class', 'hidden', 'open', 'style'] });
  },
  diff(el, noisy) {
    const b = this.before, sig = [];
    const strip = (u) => u.replace(/#$/, '');
    if (strip(location.href) !== strip(b.url)) sig.push('url');
    const isHashLink = el.tagName === 'A' && ['#', ''].includes(el.getAttribute('href'));
    if (scrollX !== b.x || scrollY !== b.y) {
      if (isHashLink && scrollX === 0 && scrollY === 0) this.jumpTop = true; else sig.push('scroll');
    }
    let fresh = 0, dialog = false;
    for (const e of document.body.querySelectorAll('*')) {
      if (!b.visible.has(e) && this.vis(e) && !(noisy.ids && e.dataset && e.dataset.ctNoisy)) {
        fresh++;
        if (e.tagName === 'DIALOG' || e.getAttribute('role') === 'dialog' || e.getAttribute('aria-modal') === 'true') dialog = true;
      }
    }
    if (fresh) sig.push('new_visible');
    if (dialog) sig.push('dialog');
    if (this.attrHits.length) sig.push('attr');
    const domDelta = document.getElementsByTagName('*').length - b.nodes;
    if (!noisy.dom && domDelta !== 0) sig.push('dom');
    if (this.mo) this.mo.disconnect();
    return { sig, fresh, attrs: [...new Set(this.attrHits)], dy: scrollY - b.y, domDelta };
  },
  // idle watch: which elements change visibility, and does the DOM size move, with nobody clicking?
  idleStart() {
    const s = new WeakSet();
    for (const e of document.body.querySelectorAll('*')) if (this.vis(e)) s.add(e);
    this.idle = { s, nodes: document.getElementsByTagName('*').length };
  },
  idleEnd() {
    let flips = 0;
    for (const e of document.body.querySelectorAll('*')) {
      if (this.idle.s.has(e) !== this.vis(e)) { e.dataset.ctNoisy = '1'; flips++; }
    }
    return { flips, dom: document.getElementsByTagName('*').length !== this.idle.nodes };
  },
};`;

let browser = null;
let result = {
  url, out, tool: 'click-test.js', started_utc: new Date().toISOString(),
  cap: { elements: MAX_ELEMENTS, seconds: TOTAL_MS / 1000, settle_ms: SETTLE_MS },
  status: 'not_measured', reason: null, http_status: null, candidates_found: 0,
  results: [], not_tested: [],
};

function write() {
  result.finished_utc = new Date().toISOString();
  result.elapsed_s = Math.round((Date.now() - T0) / 100) / 10;
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(out, JSON.stringify(result, null, 1));
}

async function closeBrowser() {
  if (!browser) return;
  const b = browser; browser = null;
  const proc = b.process();
  try { await Promise.race([b.close(), sleep(5000)]); } catch (e) { /* fall through to kill */ }
  if (proc && proc.exitCode === null && !proc.killed) { try { proc.kill('SIGKILL'); } catch (e) { /* gone */ } }
}

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, async () => { result.reason = result.reason || `stopped by ${sig}`; write(); await closeBrowser(); process.exit(1); });
}

async function load(page) {
  const resp = await page.goto(url, { waitUntil: 'networkidle2', timeout: Math.max(5000, Math.min(30000, left() - 5000)) })
    .catch(async () => page.goto(url, { waitUntil: 'load', timeout: Math.max(5000, Math.min(30000, left() - 5000)) }));
  await page.evaluate(PAGE_HELPERS);
  return resp;
}

async function idleNoise(page) {
  await page.evaluate(() => window.__ct.idleStart());
  await sleep(SETTLE_MS);
  return page.evaluate(() => window.__ct.idleEnd());
}

// POLITE LAYER (owner safety rule 2026-10-14): the page is loaded with the kit's bot UA, and only
// when robots.txt allows it. The robots decision comes from crawl_transport.py (one RFC 9309 matcher
// for the whole kit): `python3 crawl_transport.py allowed <url>` exits 0 allowed, 3 disallowed.
const UA = process.env.CRAWL_UA || 'GrokBot-SiteAudit (+https://github.com/PensacolaSeoCompany/site-audit-kit)';
function robotsAllowed(u) {
  const { spawnSync } = require('child_process');
  const vpy = path.join(KIT_HOME, '.venv', 'bin', 'python');
  const py = fs.existsSync(vpy) ? vpy : 'python3';
  const r = spawnSync(py, [path.join(KIT_HOME, 'crawl_transport.py'), 'allowed', u], { encoding: 'utf8', timeout: 60000 });
  return r.status === 0;
}

(async () => {
  if (!robotsAllowed(url)) {
    result.reason = 'robots.txt disallows this URL; not loaded (not measured)';
    console.error(`SKIPPED: robots.txt disallows ${url}`);
    write();
    process.exit(5);
  }
  const hardStop = setTimeout(async () => {
    result.reason = (result.reason || '') + ' hard stop at the 90 s cap + 20 s grace';
    write(); await closeBrowser(); process.exit(result.status === 'measured' ? 0 : 5);
  }, TOTAL_MS + 20000);
  try {
    browser = await puppeteer.launch({
      executablePath: CHROME, headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check'],
    });
    const page = await browser.newPage();
    await page.setUserAgent(UA);
    await page.setViewport({ width: 1366, height: 900 });
    let dialogs = 0, popups = 0;
    page.on('dialog', async (d) => { dialogs++; try { await d.dismiss(); } catch (e) { /* closed */ } });
    page.on('popup', async (p) => { popups++; try { await p.close(); } catch (e) { /* closed */ } });

    const resp = await load(page);
    result.http_status = resp ? resp.status() : null;
    const html = await page.content();
    if (!resp || resp.status() !== 200) { result.reason = resp ? `HTTP ${resp.status()}` : 'no response'; return; }
    if ((resp.headers()['cf-mitigated']) || CHALLENGE.test(html.slice(0, 20000))) { result.reason = 'challenge page'; return; }

    let noisy = await idleNoise(page);
    result.idle_noise = noisy;
    let cands = await page.evaluate(() => window.__ct.candidates().map((c) => ({ selector: window.__ct.cssPath(c.el), text: c.text, unsafe: c.unsafe })));
    result.candidates_found = cands.length;
    for (const c of cands.filter((x) => x.unsafe)) result.not_tested.push({ selector: c.selector, text: c.text, why: c.unsafe });
    cands = cands.filter((x) => !x.unsafe).map(({ unsafe, ...c }) => c);
    for (const c of cands.slice(MAX_ELEMENTS)) result.not_tested.push({ ...c, why: `over the ${MAX_ELEMENTS}-element cap` });
    cands = cands.slice(0, MAX_ELEMENTS);
    result.status = 'measured';

    let sinceReload = 0, dirty = false;
    for (let i = 0; i < cands.length; i++) {
      const c = cands[i];
      if (left() < SETTLE_MS + 3000) {
        for (const r of cands.slice(i)) result.not_tested.push({ ...r, why: 'over the 90 s time cap' });
        result.timed_out = true;
        break;
      }
      if (dirty || sinceReload >= RELOAD_EVERY || page.url().replace(/#$/, '') !== url.replace(/#$/, '')) {
        await load(page);
        noisy = await idleNoise(page);
        sinceReload = 0; dirty = false;
      }
      const h = await page.$(c.selector);
      const visible = h && await h.evaluate((el) => window.__ct.vis(el));
      if (!visible) { result.not_tested.push({ ...c, why: 'not found or not visible after reload' }); continue; }
      const tag = await h.evaluate((el) => ({ tag: el.tagName.toLowerCase(), href: el.getAttribute('href'), role: el.getAttribute('role') }));
      await h.evaluate((el) => el.scrollIntoView({ block: 'center', inline: 'center' }));
      await sleep(700);   // let scroll-triggered lazy loads / entrance animations finish before the snapshot
      // control window: the same 1.5 s with NO click. Whatever changes here changes on its own
      // (lazy loads, carousels, scroll anchoring), so it cannot prove the click did something.
      const nz = { dom: noisy.dom, ids: noisy.flips > 0 };
      await page.evaluate((el) => window.__ct.snapshot(el), h);
      await sleep(SETTLE_MS);
      const ctl = await page.evaluate((el, z) => window.__ct.diff(el, z), h, nz).catch(() => ({ sig: [] }));
      await page.evaluate((el) => { window.__ct.jumpTop = false; window.__ct.snapshot(el); }, h);
      const d0 = dialogs, p0 = popups;
      let clickErr = null;
      try { await h.click({ delay: 20 }); } catch (e) {
        clickErr = String(e.message || e).slice(0, 120);
        try { await h.evaluate((el) => el.click()); } catch (e2) { /* navigated away */ }
      }
      await sleep(SETTLE_MS);
      let d;
      try {
        d = await page.evaluate((el, z) => window.__ct.diff(el, z), h, nz);
      } catch (e) {
        d = { sig: ['url'], fresh: 0, attrs: [], note: 'page navigated (context destroyed)' };   // a navigation is a change
      }
      if (dialogs > d0 || popups > p0) d.sig.push('dialog');
      const jumpTop = await page.evaluate(() => !!(window.__ct && window.__ct.jumpTop)).catch(() => false);
      const fired = [...new Set(d.sig)];
      // a signal the control also showed still counts when the click made it much bigger
      // (a lightbox opening = dozens of new visible nodes; lazy-load noise = a few)
      const bigger = (x) => (x === 'new_visible' && d.fresh > 2 * (ctl.fresh || 0) + 5)
        || (x === 'dom' && Math.abs(d.domDelta || 0) > 2 * Math.abs(ctl.domDelta || 0) + 5);
      const own = fired.filter((x) => !ctl.sig.includes(x) || bigger(x));
      // works: a signal the control window did not show. unclear: only signals the page also shows
      // with no click (never ship as "does nothing"). does nothing: no signal at all.
      const verdict = own.length ? 'works' : (fired.length ? 'unclear' : 'does nothing');
      const row = { selector: c.selector, text: c.text, ...tag, verdict, signals: own };
      if (ctl.sig.length) row.changes_without_click = [...new Set(ctl.sig)];
      if (d.dy) row.scroll_dy = d.dy;
      if (d.attrs && d.attrs.length) row.attributes_changed = d.attrs;
      if (d.fresh) row.new_visible_elements = d.fresh;
      if (d.note) row.note = d.note;
      if (clickErr) row.click_error = clickErr;
      if (jumpTop && verdict === 'does nothing') row.note = 'only the browser default jump to top for href="#" (a "back to top" link works this way; read its text)';
      result.results.push(row);
      sinceReload++;
      if (fired.length || clickErr) dirty = true;   // anything changed: reload before the next click
    }
  } catch (e) {
    result.reason = result.reason || String(e && e.message || e).slice(0, 300);
    if (result.status === 'measured') result.error_after_start = result.reason;
  } finally {
    clearTimeout(hardStop);
    result.works = result.results.filter((r) => r.verdict === 'works').length;
    result.does_nothing = result.results.filter((r) => r.verdict === 'does nothing').length;
    result.unclear = result.results.filter((r) => r.verdict === 'unclear').length;
    write();
    await closeBrowser();
    console.log(`click-test ${result.status}${result.reason ? ' (' + result.reason + ')' : ''}: ${result.results.length} clicked, `
      + `${result.works} works, ${result.does_nothing} does nothing, ${result.unclear} unclear, ${result.not_tested.length} not tested -> ${out}`);
    for (const r of result.results.filter((x) => x.verdict === 'does nothing')) console.log(`  does nothing: ${JSON.stringify(r.text)} ${r.selector}`);
    process.exit(result.status === 'measured' ? 0 : 5);
  }
})();
