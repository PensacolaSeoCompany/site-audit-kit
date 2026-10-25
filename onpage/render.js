#!/usr/bin/env node
/*
 * render.js — load ONE page in headless Chrome and save the rendered DOM. Read-only: no clicks,
 * no form input; dialogs are dismissed and pop-ups closed.
 *
 * Usage: [RENDER_AS_BROWSER=1] node onpage/render.js <url> <out.json>
 * out.json: {ok, status, final_url, title, html, bytes, ms, error, user_agent: "bot"|"browser",
 *            chain, sandbox, sandbox_retry, viewport, blocked_requests, shadow_roots, log}
 * error and log hold Chrome's raw words: fetch.py keeps them for run.log only, never for the report.
 * The caller (onpage/fetch.py) has already checked robots.txt and waited its turn (crawl_transport).
 *
 * Load rule: ONE navigation (waitUntil "domcontentloaded", 30 s, so one slow image cannot hold the read),
 * then wait for network quiet (500 ms with at most 2 requests open, cap 8 s), scroll down in steps to wake lazy content (cap 6 s), wait for network quiet
 * again (500 ms with none open, cap 5 s), 300 ms settle, then read the DOM. Never a second navigation.
 * Chrome's own error page (net::ERR_*, chrome-error://) is a failed load, never the page.
 *
 * chain: every main-frame document response in order [{url, status, mitigated, body, truncated}] (body =
 * the first 30,000 characters, when Chrome still has it; truncated = the document was longer, so its size
 * says nothing about the page). fetch.py looks for a bot check at EVERY step: a check
 * that solves itself and reloads is still a check; a bot check is never bypassed.
 *
 * Capture: the doctype + <html> with open shadow roots FLATTENED: each open shadow root's content is
 * copied into its host as a first child <div data-kit-shadow="open">; the host's light-DOM children stay
 * where they are, so slotted text is counted once; a <slot> that has nodes assigned does not show its
 * fallback content, so that fallback is left out of the copy. Closed shadow roots cannot be read and are not in the
 * capture. CSS-hidden text (display:none, visibility:hidden, closed menus, consent banners) stays in the
 * HTML like any other text: this capture does not decide what a visitor sees.
 *
 * Viewport: mobile 412x915 (isMobile, touch) by default, because Google indexes the mobile page;
 * RENDER_VIEWPORT=desktop gives 1366x900. User agent: a bot visit on the phone viewport sends an Android
 * Chrome string that ENDS with "(compatible; <bot name>)", so sites that choose phone or desktop HTML by
 * user agent serve the phone page while the bot name stays visible (never another crawler's name); on
 * desktop it is the bot name alone. As a browser (RENDER_AS_BROWSER=1) the user agent is Chrome's own,
 * Android form on mobile, desktop form on desktop.
 * Size: a DOM over 8 MB is cut there; result.error says so (with ok=true) and fetch.py passes it on as a note.
 * Blocked requests (they cannot change the page text): media, fonts, and known analytics/ad hosts.
 * Images, stylesheets, scripts and data requests all load.
 * Sandbox: Chrome starts WITH its sandbox, except as root (Chrome refuses it as root) or with
 * RENDER_NO_SANDBOX=1 (override: never the sandbox). If Chrome cannot start for a sandbox reason (a
 * container without user namespaces: "No usable sandbox", the zygote cannot move to a new namespace), it
 * is started ONCE more without the sandbox: result.sandbox = false, result.sandbox_retry = true, and the
 * first start's raw error goes into result.log (for run.log only). RENDER_NO_SANDBOX=0 asks for the
 * sandbox even as root (used by the tests to see the retry). result.sandbox records what was used.
 * Profile: a fresh Chrome profile inside the folder of out.json, removed on every exit path including
 * the hard timeout (RENDER_HARD_MS, default 75000). Chrome's pid is written to <out.json>.pid so the
 * caller can kill it if this process itself is killed.
 * Exit 0 when a DOM was saved (even for a non-200 status: the caller decides), 5 when the page could
 * not be loaded, 2 on bad usage. Chrome is always closed.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const KIT_HOME = path.resolve(__dirname, '..');
function requireKit(name) {
  try { return require(path.join(KIT_HOME, 'node_modules', name)); } catch (e) { return require(name); }
}

function findChrome() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  const KIT_WORK = process.env.KIT_WORK || path.join(KIT_HOME, 'work');
  try { const f = fs.readFileSync(path.join(KIT_WORK, 'chrome-bin'), 'utf8').trim().split('\n')[0]; if (f && fs.existsSync(f)) return f; } catch (e) {}
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
  return null;
}

const UA = process.env.CRAWL_UA || 'GrokBot-SiteAudit (+https://github.com/PensacolaSeoCompany/site-audit-kit)';
const MAX_HTML = 8 * 1024 * 1024;
const MAX_STEP_BODY = 30000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Chrome flags. Sandbox ON unless root (uid 0) or env.RENDER_NO_SANDBOX === '1'; '0' keeps it ON as root. */
function chromeArgs(uid, env) {
  const ns = env && env.RENDER_NO_SANDBOX;
  const noSandbox = ns === '1' || (uid === 0 && ns !== '0');
  const args = [...(noSandbox ? ['--no-sandbox'] : []), '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check'];
  const proxy = (env && env.CRAWL_PROXY) || '';
  if (proxy && !proxy.includes('@')) args.push(`--proxy-server=${proxy}`);
  return args;
}

// Chrome's own words when its sandbox cannot start (container without user namespaces, root without the flag).
const SANDBOX_FAIL = /No usable sandbox|zygote_host|Failed to move to new namespace|setuid[_ ]sandbox|SUID sandbox|without --no-sandbox|credentials\.cc/i;
/** true when a failed Chrome start was caused by the sandbox (worth one start without it). */
function sandboxFailure(msg) { return SANDBOX_FAIL.test(String(msg || '')); }

/**
 * Start Chrome with args; when that fails for a sandbox reason and the sandbox was on, start it ONCE more
 * with --no-sandbox. launch(args) -> browser (puppeteer.launch); beforeRetry() runs between the two starts.
 * -> {browser, sandbox, retried, first_error}. Any other failure (or a second failure) is thrown.
 */
async function launchChrome(launch, args, beforeRetry) {
  try {
    return { browser: await launch(args), sandbox: !args.includes('--no-sandbox'), retried: false, first_error: null };
  } catch (e) {
    const msg = String(e && e.message || e);
    if (args.includes('--no-sandbox') || !sandboxFailure(msg)) throw e;
    if (beforeRetry) await beforeRetry();
    const browser = await launch(['--no-sandbox', ...args]);
    return { browser, sandbox: false, retried: true, first_error: msg };
  }
}

function viewportFor(env) {
  return (env && env.RENDER_VIEWPORT === 'desktop')
    ? { width: 1366, height: 900, deviceScaleFactor: 1, isMobile: false, hasTouch: false }
    : { width: 412, height: 915, deviceScaleFactor: 1, isMobile: true, hasTouch: true };
}

// Hosts that only measure or advertise: their scripts do not write the page's own text.
// (Tag managers are NOT here: a tag manager can inject content.)
const NOISE_HOSTS = /(^|\.)(google-analytics\.com|analytics\.google\.com|doubleclick\.net|googlesyndication\.com|googleadservices\.com|adservice\.google\.com|connect\.facebook\.net|hotjar\.com|clarity\.ms|bat\.bing\.com|scorecardresearch\.com|quantserve\.com|criteo\.(com|net)|taboola\.com|outbrain\.com|adnxs\.com|amazon-adsystem\.com|segment\.(com|io)|mixpanel\.com|fullstory\.com|newrelic\.com|nr-data\.net)$/i;

/** true when a request cannot change the page text and is aborted. */
function blockable(resourceType, url) {
  if (resourceType === 'media' || resourceType === 'font') return true;
  try { return NOISE_HOSTS.test(new URL(url).hostname); } catch (e) { return false; }
}

// Runs in the page: doctype + <html> with open shadow roots flattened (see the header).
function captureInPage() {
  const dt = document.doctype;
  const d = dt ? `<!DOCTYPE ${dt.name}${dt.publicId ? ` PUBLIC "${dt.publicId}"` : ''}${dt.systemId ? ` "${dt.systemId}"` : ''}>\n` : '';
  const hosts = [];
  const mark = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) { el.setAttribute('data-kit-shadow-host', String(hosts.length)); hosts.push(el); mark(el.shadowRoot); }
    }
  };
  mark(document);
  if (!hosts.length) return { html: d + document.documentElement.outerHTML, shadow_roots: 0 };
  // A <slot> that has light-DOM nodes assigned does not show its fallback content: drop it from the copy
  // (the light-DOM text stays in the host and is counted once).
  const filled = [];
  for (const h of hosts) for (const s of h.shadowRoot.querySelectorAll('slot')) {
    if (s.assignedNodes().length && s.childNodes.length) { s.setAttribute('data-kit-slot-filled', ''); filled.push(s); }
  }
  const inner = hosts.map((h) => h.shadowRoot.innerHTML);   // nested hosts carry their marks
  for (const s of filled) s.removeAttribute('data-kit-slot-filled');
  const outer = document.documentElement.outerHTML;
  for (const h of hosts) h.removeAttribute('data-kit-shadow-host');
  const doc = new DOMParser().parseFromString(outer, 'text/html');   // inert: no scripts, no custom elements
  let el, guard = 0;
  while ((el = doc.querySelector('[data-kit-shadow-host]')) && guard++ < 10000) {
    const k = Number(el.getAttribute('data-kit-shadow-host'));
    el.removeAttribute('data-kit-shadow-host');
    el.insertAdjacentHTML('afterbegin', `<div data-kit-shadow="open">${inner[k] || ''}</div>`);
  }
  for (const s of doc.querySelectorAll('slot[data-kit-slot-filled]')) { s.removeAttribute('data-kit-slot-filled'); s.textContent = ''; }
  return { html: d + doc.documentElement.outerHTML, shadow_roots: hosts.length };
}

async function main() {
  const puppeteer = requireKit('puppeteer-core');
  const [url, out] = process.argv.slice(2);
  if (!url || !out || !/^https?:\/\//i.test(url)) { console.error('usage: render.js <http(s) url> <out.json>'); process.exit(2); }
  const t0 = Date.now();
  const result = { ok: false, status: null, final_url: null, title: null, html: null, bytes: 0, ms: 0, error: null,
                   chain: [], sandbox: null, sandbox_retry: false, viewport: null, blocked_requests: 0, shadow_roots: 0,
                   log: [] };
  const write = () => { result.ms = Date.now() - t0; fs.writeFileSync(out, JSON.stringify(result)); };
  const CHROME = findChrome();
  if (!CHROME) { result.error = 'no Chrome found'; write(); process.exit(5); }
  const outDir = path.dirname(path.resolve(out));
  const pidFile = out + '.pid';
  const profile = fs.mkdtempSync(path.join(outDir, 'chrome-profile-'));
  let browser = null;
  let chromeProc = null;
  const chromeGone = () => !chromeProc || chromeProc.exitCode !== null || chromeProc.signalCode !== null;
  const killChrome = () => {
    if (!chromeProc || chromeGone()) return;
    try { process.kill(-chromeProc.pid, 'SIGKILL'); } catch (e) { try { chromeProc.kill('SIGKILL'); } catch (e2) { /* gone */ } }
  };
  // Remove the profile only after Chrome has exited (a live Chrome keeps writing into it).
  const cleanup = async () => {
    for (let i = 0; i < 50 && !chromeGone(); i++) await sleep(100);
    for (let i = 0; i < 5; i++) {
      try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 }); } catch (e) { /* retry */ }
      if (!fs.existsSync(profile)) break;
      await sleep(200);
    }
    try { fs.rmSync(pidFile, { force: true }); } catch (e) { /* none */ }
  };
  const HARD_MS = Number(process.env.RENDER_HARD_MS) || 75000;
  const hard = setTimeout(async () => {   // never leave Chrome running or its profile behind
    result.ok = false; result.html = null; result.error = `hard timeout ${Math.round(HARD_MS / 1000)} s`; write();
    killChrome();
    await cleanup();
    process.exit(5);
  }, HARD_MS);
  try {
    const args = chromeArgs(process.getuid ? process.getuid() : null, process.env);
    result.sandbox = !args.includes('--no-sandbox');
    const started = await launchChrome(
      (a) => puppeteer.launch({ executablePath: CHROME, headless: true, args: a, userDataDir: profile }), args,
      async () => {   // a fresh, empty profile for the second start
        try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* gone */ }
        fs.mkdirSync(profile, { recursive: true });
      });
    browser = started.browser;
    result.sandbox = started.sandbox;
    result.sandbox_retry = started.retried;
    if (started.retried) result.log.push(`Chrome did not start with its sandbox; started again without it. First error: ${started.first_error.slice(0, 600)}`);
    chromeProc = browser.process();
    if (chromeProc) fs.writeFileSync(pidFile, String(chromeProc.pid));
    const page = await browser.newPage();
    const vp = viewportFor(process.env);
    // RENDER_AS_BROWSER=1: the user's own page after the bot name was refused,
    // or a competitor page the user said yes to. Chrome's own user agent with the headless marker removed.
    const asBrowser = process.env.RENDER_AS_BROWSER === '1';
    // Bot visits keep the honest bot name but carry a phone browser's tokens (like Google's smartphone
    // crawler), so sites that pick mobile or desktop HTML by user agent serve the same phone page that a
    // browser read (RENDER_AS_BROWSER=1) gets. Desktop viewport: the bot name alone.
    const ver0 = ((await browser.userAgent()).match(/Chrome\/([\d.]+)/) || [])[1] || '130.0.0.0';
    let ua = vp.isMobile ? `Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${ver0} Mobile Safari/537.36 (compatible; ${UA})` : UA;
    if (asBrowser) {
      const desk = (await browser.userAgent()).replace('HeadlessChrome', 'Chrome');
      const ver = (desk.match(/Chrome\/([\d.]+)/) || [])[1] || '130.0.0.0';
      ua = vp.isMobile ? `Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${ver} Mobile Safari/537.36` : desk;
    }
    result.user_agent = asBrowser ? 'browser' : 'bot';
    await page.setUserAgent(ua);
    await page.setViewport(vp);
    result.viewport = { width: vp.width, height: vp.height, mobile: vp.isMobile };
    page.on('dialog', async (d) => { try { await d.dismiss(); } catch (e) { /* closed */ } });
    page.on('popup', async (p) => { try { await p.close(); } catch (e) { /* closed */ } });
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      if (req.isInterceptResolutionHandled()) return;
      if (blockable(req.resourceType(), req.url())) { result.blocked_requests++; req.abort('blockedbyclient').catch(() => {}); }
      else req.continue().catch(() => {});
    });
    const bodies = [];
    page.on('response', (r) => {
      try {
        if (!r.request().isNavigationRequest() || r.frame() !== page.mainFrame()) return;
        const step = { url: r.url(), status: r.status(), mitigated: r.headers()['cf-mitigated'] || null, body: null, truncated: false };
        result.chain.push(step);
        if (step.status < 300 || step.status >= 400) {
          bodies.push(r.text().then((t) => { step.body = t.slice(0, MAX_STEP_BODY); step.truncated = t.length > MAX_STEP_BODY; }).catch(() => {}));
        }
      } catch (e) { /* page gone */ }
    });
    let resp = null;
    try { resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }); }
    catch (e) {
      const msg = String(e && e.message || e);
      if (/net::ERR_|chrome-error/i.test(msg)) throw new Error(msg.slice(0, 200));   // Chrome's own error page is never the page
      // a timeout: keep what has loaded (one navigation only)
    }
    await page.waitForNetworkIdle({ idleTime: 500, concurrency: 2, timeout: 8000 }).catch(() => {});
    // Scroll in steps so lazy sections render; stop at the bottom or after 6 s.
    await page.evaluate(async () => {
      const end = Date.now() + 6000;
      let y = 0;
      while (Date.now() < end) {
        y += Math.max(400, window.innerHeight);
        window.scrollTo(0, y);
        await new Promise((r) => setTimeout(r, 150));
        if (y >= document.documentElement.scrollHeight) break;
      }
      window.scrollTo(0, 0);
    }).catch(() => {});
    await page.waitForNetworkIdle({ idleTime: 500, concurrency: 0, timeout: 5000 }).catch(() => {});
    await sleep(300);
    let cap = null;
    for (let i = 0; i < 3 && !cap; i++) {   // a page that navigated itself (e.g. a check reloading) needs a moment
      cap = await page.evaluate(captureInPage).catch(() => null);
      if (!cap) await sleep(1000);
    }
    await Promise.all(bodies);
    const last = result.chain.length ? result.chain[result.chain.length - 1] : null;
    result.status = last ? last.status : (resp ? resp.status() : null);
    result.final_url = page.url();
    if (!/^https?:/i.test(result.final_url)) throw new Error(`Chrome error page (${result.final_url.slice(0, 80)})`);
    if (!cap) throw new Error('could not read the page DOM');
    result.title = await page.title().catch(() => null);
    let html = cap.html;
    result.shadow_roots = cap.shadow_roots;
    if (html.length > MAX_HTML) { html = html.slice(0, MAX_HTML); result.error = 'html truncated at 8 MB'; }
    result.html = html;
    result.bytes = Buffer.byteLength(html, 'utf8');
    result.ok = true;
  } catch (e) {
    result.error = String(e && e.message || e).slice(0, 300);
  } finally {
    if (browser) await browser.close().catch(() => {});
    killChrome();
    await cleanup();
    clearTimeout(hard);
  }
  write();
  process.exit(result.ok ? 0 : 5);
}

module.exports = { chromeArgs, viewportFor, blockable, captureInPage, sandboxFailure, launchChrome };
if (require.main === module) main();
