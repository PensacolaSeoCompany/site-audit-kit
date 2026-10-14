#!/usr/bin/env python3
"""session-title-check.py — IN-SESSION <title> persistence probe (CDP, one page context).

Drives ONE headless Chrome page target over the DevTools Protocol. After the FIRST goto,
every subsequent hop is performed by CLICKING a real in-page anchor (or by History
back/forward) — never by issuing a new goto. That is the only way to observe a client-side
router failing to update document.title, a defect a BFS crawler cannot see by construction.

Per hop it records: URL before, how it navigated, URL after, document.title read EARLY
(right after the click settles) and LATE (after an extra settle delay), og:title, first H1.
EARLY vs LATE distinguishes "never updates" from "updates late".

Usage: $PY session-title-check.py <plan.json> <out.json>     (PY = the kit's .venv python)
Chrome: env CHROME_BIN, else google-chrome / google-chrome-stable / chromium / chromium-browser on PATH.
The throwaway Chrome profile lives under $KIT_WORK (default <kit>/work) and is removed at the end.
plan.json = {"start": "<url>", "hops": [ {...}, ... ], "label": "..."}
Read-only: GET/HEAD only. It clicks ANCHORS (<a href>) and uses history back/forward; a "click"
selector that is not an <a href> (a button, a submit, a form control) is refused, so no form is
submitted and nothing is POSTed. It never types into a field or logs in.
hop kinds: {"click": "<css selector of an <a href>>"} | {"click_text": "<substring of link text>"}
           | {"back": true} | {"forward": true}
"""
import json, os, re, subprocess, sys, time, socket, shutil, tempfile
import websocket  # websocket-client
import urllib.request

KIT_HOME = os.path.dirname(os.path.abspath(__file__))
KIT_WORK = os.environ.get("KIT_WORK") or os.path.join(KIT_HOME, "work")
CHROME = os.environ.get("CHROME_BIN") or next(
    (p for p in (shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")) if p),
    None)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/146.0.0.0 Safari/537.36")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=45, max_size=100 * 1024 * 1024)
        self.i = 0

    def send(self, method, params=None, timeout=45):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(1, deadline - time.time()))
            try:
                msg = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def ev(self, expr, timeout=45):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True},
                      timeout=timeout)
        if r.get("exceptionDetails"):
            return {"__error__": str(r["exceptionDetails"].get("text"))}
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


PROBE = """(() => {
  const og = document.querySelector('meta[property="og:title"]');
  const h1 = document.querySelector('h1');
  return {
    url: location.href,
    title: document.title,
    title_tag: (document.querySelector('title') ? document.querySelector('title').textContent : null),
    og_title: og ? og.getAttribute('content') : null,
    h1: h1 ? h1.textContent.trim().slice(0,160) : null,
    ready: document.readyState,
    history_len: history.length
  };
})()"""


def probe(c):
    return c.ev(PROBE)


def settle(c, extra=0.0):
    """Wait for readyState complete + a quiet period of no new fetch/XHR."""
    t0 = time.time()
    while time.time() - t0 < 20:
        try:
            if c.ev("document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(0.25)
    time.sleep(1.2)  # baseline settle
    if extra:
        time.sleep(extra)


def main():
    plan = json.load(open(sys.argv[1]))
    outpath = sys.argv[2]
    port = free_port()
    if not CHROME:
        sys.exit("FATAL: no Chrome found. Install google-chrome or chromium, or set CHROME_BIN.")
    os.makedirs(KIT_WORK, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="cdp-title-", dir=KIT_WORK)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={profile}", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage", "--window-size=1400,2000", "--remote-allow-origins=*",
         f"--user-agent={UA}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws_url = None
    for _ in range(80):
        try:
            j = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2))
            pages = [t for t in j if t["type"] == "page"]
            if pages:
                ws_url = pages[0]["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.25)
    if not ws_url:
        print("FATAL: chrome did not expose a page target", file=sys.stderr)
        proc.kill(); sys.exit(9)

    c = CDP(ws_url)
    rows = []
    meta = {"label": plan.get("label"), "start": plan["start"], "chrome": CHROME,
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        c.send("Page.enable"); c.send("Runtime.enable"); c.send("Network.enable")
        # HOP 0 — the only goto in the whole session.
        c.send("Page.navigate", {"url": plan["start"]}, timeout=60)
        settle(c)
        p_early = probe(c)
        time.sleep(3.0)
        p_late = probe(c)
        rows.append({"hop": 0, "how": "goto (session start)", "target": plan["start"],
                     "url_before": None, "early": p_early, "late": p_late})

        for n, hop in enumerate(plan["hops"], start=1):
            before = probe(c)
            how = None
            ok = None
            if "click" in hop:
                how = f"click css: {hop['click']}"
                ok = c.ev(
                    "(()=>{const e=document.querySelector(%s);"
                    "if(!e)return {found:false};"
                    "if(e.tagName!=='A'||!e.hasAttribute('href'))return {found:false,refused:'not an <a href> (read-only: links only)'};"
                    "const h=e.getAttribute('href');"
                    "e.scrollIntoView();e.click();return {found:true,href:h};})()"
                    % json.dumps(hop["click"]))
            elif "click_text" in hop:
                how = f"click text~: {hop['click_text']}"
                ok = c.ev(
                    "(()=>{const t=%s.toLowerCase();"
                    "const as=[...document.querySelectorAll('a[href]')];"
                    "const e=as.find(a=>(a.textContent||'').trim().toLowerCase().includes(t));"
                    "if(!e)return {found:false,sample:as.slice(0,40).map(a=>a.textContent.trim().slice(0,40))};"
                    "const h=e.getAttribute('href');e.scrollIntoView();e.click();"
                    "return {found:true,href:h};})()"
                    % json.dumps(hop["click_text"]))
            elif "click_href" in hop:
                how = f"click href~: {hop['click_href']}"
                ok = c.ev(
                    "(()=>{const t=%s;"
                    "const as=[...document.querySelectorAll('a[href]')];"
                    "const e=as.find(a=>(a.getAttribute('href')||'').includes(t));"
                    "if(!e)return {found:false,sample:as.slice(0,60).map(a=>a.getAttribute('href'))};"
                    "const h=e.getAttribute('href');e.scrollIntoView();e.click();"
                    "return {found:true,href:h};})()"
                    % json.dumps(hop["click_href"]))
            elif hop.get("back"):
                how = "history.back()"
                c.ev("history.back()")
                ok = {"found": True}
            elif hop.get("forward"):
                how = "history.forward()"
                c.ev("history.forward()")
                ok = {"found": True}
            else:
                how = "UNKNOWN"; ok = {"found": False}

            settle(c)
            p_early = probe(c)
            time.sleep(3.0)
            p_late = probe(c)
            rows.append({"hop": n, "how": how, "click_result": ok,
                         "url_before": before.get("url") if isinstance(before, dict) else None,
                         "title_before": before.get("title") if isinstance(before, dict) else None,
                         "early": p_early, "late": p_late})
            print(f"hop {n}: {how} -> {p_late.get('url') if isinstance(p_late,dict) else p_late} "
                  f"| title={p_late.get('title') if isinstance(p_late,dict) else '?'}", flush=True)
    finally:
        c.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    json.dump({"meta": meta, "rows": rows}, open(outpath, "w"), indent=2)
    print(f"WROTE {outpath} ({len(rows)} hops)")


if __name__ == "__main__":
    main()
