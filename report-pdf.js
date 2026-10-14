#!/usr/bin/env node
/* report-pdf.js: print an HTML report to PDF with puppeteer-core + Chrome.
 *   node report-pdf.js <in.html> <out.pdf>
 * Chrome: CHROME_BIN env, else $KIT_WORK/chrome-bin (written by install.sh), else the first of
 * google-chrome, google-chrome-stable, chromium, chromium-browser on PATH.
 * Every <details> is opened before printing, so accordion content is in the PDF.
 * Exit: 0 ok, 1 bad args or missing input, 2 Chrome not found, 3 print failed.
 */
const fs = require('fs'), path = require('path');
const [,, inPath, outPath] = process.argv;
if (!inPath || !outPath || !/\.pdf$/i.test(outPath)) {
  console.error('usage: report-pdf.js <in.html> <out.pdf>'); process.exit(1);
}
if (!fs.existsSync(inPath)) { console.error(`ERROR: no such file: ${inPath}`); process.exit(1); }

function findChrome() {
  if (process.env.CHROME_BIN && fs.existsSync(process.env.CHROME_BIN)) return process.env.CHROME_BIN;
  const work = process.env.KIT_WORK || path.join(__dirname, 'work');
  try {
    const p = fs.readFileSync(path.join(work, 'chrome-bin'), 'utf8').trim();
    if (p && fs.existsSync(p)) return p;
  } catch (e) {}
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    for (const dir of (process.env.PATH || '').split(':')) {
      const f = path.join(dir, name);
      if (dir && fs.existsSync(f)) return f;
    }
  }
  return null;
}

let puppeteer;
try { puppeteer = require('puppeteer-core'); }
catch (e) { console.error('ERROR: puppeteer-core is not installed. Run ./install.sh'); process.exit(2); }

(async () => {
  const chrome = findChrome();
  if (!chrome) { console.error('ERROR: Chrome not found. Run ./install.sh or set CHROME_BIN'); process.exit(2); }
  let browser;
  try {
    browser = await puppeteer.launch({
      executablePath: chrome, headless: true,
      args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--no-first-run'],
    });
    const page = await browser.newPage();
    await page.goto('file://' + path.resolve(inPath), { waitUntil: 'load', timeout: 120000 });
    await page.evaluate(() => {
      document.querySelectorAll('details').forEach(d => { d.open = true; });
    });
    await page.emulateMediaType('screen');
    await page.pdf({
      path: outPath, format: 'A4', printBackground: true,
      margin: { top: '12mm', bottom: '12mm', left: '10mm', right: '10mm' },
    });
    const n = fs.statSync(outPath).size;
    console.log(`Wrote ${outPath} (${n} bytes)`);
  } catch (e) {
    console.error(`ERROR: PDF print failed: ${e.message}`); process.exitCode = 3;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
})();
