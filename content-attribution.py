#!/usr/bin/env python3
"""PHASE-2 granular: per-page RAW vs RENDERED word attribution.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.
For each URL: words in raw HTML vs rendered DOM, the DELTA, and WHICH containers hold the
JS-only words (so "700 words" becomes "400 raw + 300 rendered-only, of which 280 = Trustindex
review widget"). This is the distinction the plain word_count hides.
Usage: content-attribution.py <url> [url2 url3 ...]   (runs headless Chrome per page)
"""
import sys, json, re, subprocess, tempfile, os, urllib.request
# §65 2026-08-06: this stage had ZERO proxy support and died with an unhandled 403 traceback
# on Vercel/Sucuri sites that rate-limit our crawl IP after a crawl. Importing installs the
# CRAWL_PROXY opener globally, so the bare urlopen() calls below are routed too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl_transport as _tx
import kit_env

URLS = sys.argv[1:]
NODE_SCRIPT = r'''
const puppeteer = __PUPPETEER__;
(async()=>{
 const b=await puppeteer.launch({headless:'new',executablePath:__CHROME__,
   args:['--no-sandbox','--disable-gpu','--disable-dev-shm-usage']});
 const p=await b.newPage();
 await p.setUserAgent(__UA__);
 await p.goto(process.argv[2],{waitUntil:'networkidle2',timeout:60000});
 await p.evaluate(async()=>{for(let y=0;y<document.body.scrollHeight;y+=600){window.scrollTo(0,y);await new Promise(r=>setTimeout(r,180));}});
 await new Promise(r=>setTimeout(r,2500));
 const data=await p.evaluate(()=>{
   const words=t=>(t||'').split(/\s+/).filter(Boolean).length;
   // per top-level-ish container: words rendered
   const out=[];
   document.querySelectorAll('div,section,article,aside').forEach(el=>{
     if(el.children.length>25) return;                    // skip page-level wrappers
     const t=el.innerText||'';
     if(words(t)<15) return;
     const key=(el.id?'#'+el.id:'')+(el.className&&typeof el.className==='string'?'.'+el.className.trim().split(/\s+/).slice(0,3).join('.'):'');
     out.push({key:key||el.tagName, words:words(t), sample:t.replace(/\s+/g,' ').slice(0,90)});
   });
   const frames=[...document.querySelectorAll('iframe')].map(f=>({src:(f.src||'').slice(0,120),
     w:f.clientWidth,h:f.clientHeight})).filter(f=>f.src);
   return {total: words(document.body.innerText), containers: out, frames};
 });
 console.log(JSON.stringify(data));
 await b.close();
})();
'''
NODE_SCRIPT = NODE_SCRIPT.replace('__UA__', json.dumps(_tx.UA)).replace('__PUPPETEER__', kit_env.puppeteer_require_js()).replace(
    '__CHROME__', json.dumps(kit_env.chrome_bin(required=True)))
(kit_env.KIT_WORK / 'tmp').mkdir(parents=True, exist_ok=True)
tf = tempfile.NamedTemporaryFile('w', suffix='.cjs', delete=False, dir=kit_env.KIT_WORK / 'tmp')
tf.write(NODE_SCRIPT); tf.close()

def rawwords(url):
    """-> (wordset, wordcount, html) or (None, None, None) on transport failure.

    §65 2026-08-06: this used to let HTTPError escape, killing the whole stage with a raw
    traceback the first time a WAF answered 403 (a property-valuation site: Vercel challenges our
    crawl IP AFTER the crawl). A blocked fetch is missing measurement, not a site defect —
    return None so the caller can say UNKNOWN instead of the stage dying or, worse, a zero
    word-count being read as "this page is empty".
    """
    st, h = _tx.fetch(url, timeout=30)
    if st != 200 or not h:
        print(f"   ⚠️ UNKNOWN — {url} returned {st} to the audit fetch "
              f"({'proxied' if _tx.PROXY else 'direct'}); attribution NOT measured for this URL",
              file=sys.stderr)
        return None, None, None
    body = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', h, flags=re.S | re.I)
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', body))
    return set(w.lower() for w in text.split() if len(w) > 2), len(text.split()), h

for url in URLS:
    if not _tx.allowed(url):
        print(f"{url}\n   SKIPPED — robots.txt disallows this URL; not loaded (not measured)")
        continue
    rawset, rawn, rawhtml = rawwords(url)
    if rawset is None:
        continue
    _tx.pacer(url).wait()
    r = subprocess.run(['node', tf.name, url], capture_output=True, text=True, timeout=120)
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        print(f'{url}\n  RENDER FAILED: {r.stderr[-200:]}'); continue
    total = data['total']
    delta = total - rawn
    print(f'\n{url}')
    # GUARD: if headless render returns ~0 words but raw HTML has real content, Chrome was BLOCKED
    # (Shopify/WAF challenge), not "content is JS-only". Report honestly, don't emit garbage %.
    if total <= 3 and rawn > 50:
        print(f'  ⚠️ RENDER BLOCKED: headless Chrome got {total} words vs {rawn} in raw HTML — the site')
        print(f'     (Shopify/WAF) served a bot challenge to Chrome. Raw-HTML content is present ({rawn}w);')
        print(f'     attribution unavailable for this page. NOT a JS-only-content finding.')
        continue
    pct = delta*100//max(1,total)
    # A NEGATIVE delta means RAW has MORE words than RENDERED — the opposite of JS-only content.
    # It happens when markup is present in the served HTML but collapsed/hidden at runtime (Webflow
    # dropdown menus, accordions, tabs). Labeling that "JS-ONLY DELTA=-565 (-48%)" directly
    # contradicted render-check's "raw = 100% of rendered" on the SAME pages in the SAME run
    # (a cleaning-company site 2026-07-31). Only a POSITIVE delta is JS-injected content.
    if delta < 0:
        print(f'  words: rendered={total}  raw={rawn}  (raw has {abs(delta)} MORE words than rendered '
              f'= markup present but COLLAPSED/HIDDEN at runtime — dropdowns, accordions, tabs.')
        print(f'     This is NOT hidden-from-Google content: it IS in the served HTML. No action.)')
    else:
        print(f'  words: rendered={total}  raw={rawn}  JS-ONLY DELTA=+{delta} ({pct}% of the page)')
    # attribute: containers whose sample text is NOT in raw html
    hidden = []
    for c in sorted(data['containers'], key=lambda x: -x['words']):
        toks = [w.lower() for w in re.findall(r'[A-Za-z]{4,}', c['sample'])]
        if len(toks) < 5: continue
        overlap = sum(1 for w in toks if w in rawset) / len(toks)
        if overlap < 0.5:
            c['overlap'] = overlap; hidden.append(c)
    seen = set(); shown = 0
    for c in hidden:
        k = c['key'][:70]
        if k in seen: continue
        seen.add(k); shown += 1
        print(f'    JS-only container ({c["overlap"]*100:.0f}% words in raw): {k}  (~{c["words"]}w)  "{c["sample"][:70]}"')
        if shown >= 6: break
    if not hidden:
        print('    (no significant JS-only containers — content is server-rendered)')
    for f in data.get('frames', []):
        print(f'    IFRAME ({f["w"]}x{f["h"]}): {f["src"]}')
        print('      -> iframe content belongs to the iframe URL, NOT this page, in Google\'s eyes')
os.unlink(tf.name)
