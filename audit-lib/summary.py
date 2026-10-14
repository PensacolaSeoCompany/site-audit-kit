"""audit-lib/summary.py: the verdict step of audit.sh (COMPLETE or PARTIAL), the final report.md and
the summary printed at the end of a run.

  python summary.py <crawl.json> <out-dir> <run.log> <operator-findings.json> <notes.txt> <rules.json>
                    <builder.md> <verdict-file>

A run is PARTIAL when anything was skipped, refused, cut short or failed: the pipeline exit code
(noted by audit.sh), a "SKIPPED" step in the completion gate, the sidecar keys "partial" and
"not_measured", addresses the site refused (401/403/406/429, no answer) or that robots.txt keeps us
from, a partial crawl, performance results missing from the report. A PARTIAL report.md starts with
"# PARTIAL REPORT: <reason>" and its summary block says PARTIAL. Env DISCLAIMER is appended to report.md.
Writes <out-dir>/report.md, may add a PARTIAL banner to <out-dir>/site-audit.html, writes the verdict file.
"""
import html as H
import json
import os
import re
import sys

J, OUT, LOG, OPF, NOTES, RULES, MDSRC, VERDICT = sys.argv[1:9]
DISCLAIMER = os.environ.get("DISCLAIMER", "").strip()


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


d = load(J, {})
pages = d.get("pages") or []
ok200 = sum(1 for p in pages if p.get("status") == 200)
side = load(os.path.join(OUT, "findings.json"), {})
findings = side if isinstance(side, list) else (side.get("findings") or [])
side = {} if isinstance(side, list) else side
log = open(LOG, encoding="utf-8", errors="replace").read()

sev = {}
for f in findings:
    k = str(f.get("severity", "?")).strip().lower()
    sev[k] = sev.get(k, 0) + 1
order = ["critical", "high", "medium", "low", "note"]
sev_txt = ", ".join(f"{sev[k]} {k}" for k in order + sorted(set(sev) - set(order)) if sev.get(k)) or "none"

# ── reasons the run is PARTIAL, and what was not measured ────────────────────────────────────
reasons, not_measured = [], []
reasons += [n for n in open(NOTES, encoding="utf-8").read().splitlines() if n.strip()]
skipped = re.findall(r"✗ (\S+)\s+— SKIPPED", log)
if skipped:
    reasons.append("step(s) did not complete: " + ", ".join(skipped))
p = side.get("partial")
if p:
    reasons.append(p if isinstance(p, str) else (p.get("reason") if isinstance(p, dict) and p.get("reason")
                                                 else "the pipeline marked this run partial"))
for x in side.get("not_measured") or []:
    t = (f"{x.get('what') or x.get('step')}: {x.get('why', '')}".rstrip(": ") if isinstance(x, dict) else str(x))
    not_measured.append(t)
if not_measured:
    reasons.append(f"{len(not_measured)} part(s) not measured")
if d.get("partial"):
    reasons.append("the crawl is a partial sample (it hit its time limit)")
refused = [x for x in pages if x.get("status") in (None, 0, 401, 403, 406, 429)]
robots = [x for x in pages if x.get("robots_blocked") or x.get("blocked_by_robots") or x.get("skipped") == "robots"]
for u in (d.get("robots_disallowed") if isinstance(d.get("robots_disallowed"), list) else []):
    robots.append({"url": u} if isinstance(u, str) else u)   # full-extract.py: {"url", "note": "robots-disallowed"}
if refused:
    reasons.append(f"{len(refused)} address(es) refused our crawler (bot block, captcha, login or no answer)")
    not_measured.append(f"{len(refused)} address(es) refused our crawler: " +
                        ", ".join(f"{x.get('url')} ({x.get('status')})" for x in refused[:10]))
if robots:
    reasons.append(f"{len(robots)} address(es) not checked because robots.txt disallows them")
    not_measured.append("robots.txt disallows: " + ", ".join(str(x.get("url")) for x in robots[:10]))
for x in side.get("could_not_check") or []:
    not_measured.append(f"{x.get('what')}: {x.get('why')}")
csr = load(OPF, {}).get("not_measured_csr_checks") or []
if csr:
    reasons.append("client-rendered site: some checks need rendered pages")
    not_measured.append("client-rendered site: checks " + ", ".join(map(str, csr)) + " need rendered pages")
tiers = re.findall(r"(\d+) perf tiers", log)
if tiers and tiers[-1] == "0":
    reasons.append("performance (Lighthouse) results did not reach the report")
    not_measured.append("performance: no Lighthouse result reached the report")
seen = set()
reasons = [r for r in reasons if not (r in seen or seen.add(r))]
verdict = "PARTIAL" if reasons else "COMPLETE"
reason_line = "; ".join(reasons)

# ── report rules (report-rules.json), as warnings: this path has no publish gate ───────────────
warn = []
html_path = os.path.join(OUT, "site-audit.html")
page = open(html_path, encoding="utf-8", errors="replace").read()
try:
    rules = load(RULES, None)
    if rules is None:
        raise ValueError(f"cannot read {RULES}")
    parts = re.findall(r"<span class=ft>(.*?)</span>|<div class=wbody>(.*?)</div>|<div class=fxb><p>(.*?)</p>", page, re.S)
    ftext = "\n".join(H.unescape(re.sub(r"<[^>]+>", " ", "".join(x))) for x in parts)
    for r in rules.get("page_forbidden", []):
        if re.search(r["re"], page, re.I):
            warn.append(f"[{r['id']}] {r.get('why', '')}")
    for r in rules.get("finding_forbidden", []):
        if re.search(r["re"], ftext, re.I):
            warn.append(f"[{r['id']}] {r.get('why', '')}")
except Exception as e:
    warn.append(f"report-rules check did not run: {e}")

# ── report.md ────────────────────────────────────────────────────────────────────────────────
md = open(MDSRC, encoding="utf-8", errors="replace").read().strip("\n").split("\n")
block = [f"> **Audit summary: {verdict}.**" + (" Parts of this audit were blocked, skipped or cut short; "
                                               "do not read it as a complete audit." if verdict == "PARTIAL" else ""),
         f"> - Site: {d.get('site') or ''}",
         f"> - Pages crawled: {len(pages)} ({ok200} answered 200)",
         f"> - Findings in the findings file: {sev_txt}"]
if reasons:
    block.append("> - Why partial: " + reason_line)
for n in not_measured:
    block.append(f"> - Not measured: {n}")
block.append("> - Nobody checked these findings by hand; they come straight from the tools.")
tail = ["", "## About this report", ""] + [f"- {x}" for x in DISCLAIMER.splitlines() if x.strip()]
if verdict == "PARTIAL":
    out_md = [f"# PARTIAL REPORT: {reason_line}", ""] + block + [""] + md + tail
else:
    out_md = md[:1] + [""] + block + [""] + md[1:] + tail
with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(out_md).rstrip() + "\n")

# PARTIAL banner at the top of the HTML (and so of the PDF printed from it)
if verdict == "PARTIAL":
    banner = ('<div style="background:#fff4e5;border:3px solid #8f1d0e;color:#16232e;padding:12px 16px;margin:0 0 12px;'
              f'font:600 15px/1.4 sans-serif"><b>PARTIAL REPORT:</b> {H.escape(reason_line)}</div>')
    page2, n = re.subn(r"(<body[^>]*>)", lambda m: m.group(1) + banner, page, count=1)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page2 if n else banner + page)

with open(VERDICT, "w") as f:
    f.write(verdict + "\n" + reason_line + "\n")

# ── stdout ───────────────────────────────────────────────────────────────────────────────────
print("\n===== AUDIT SUMMARY =====")
print(f"RESULT:    {verdict}" + (f". {reason_line}" if reasons else ". Every step ran."))
print(f"site:      {d.get('site') or ''}")
print(f"pages:     {len(pages)} crawled ({ok200} answered 200)")
print(f"findings:  {sev_txt}  (the HTML report adds its own crawl-table findings)")
print("outputs:")
for f in ("site-audit.html", "report.md", "report.pdf", "findings.json", "linkmap.html", "run.log"):
    if f == "report.pdf" or os.path.exists(os.path.join(OUT, f)):
        print(f"  {os.path.join(OUT, f)}")
print("NOT MEASURED:" if not_measured else "NOT MEASURED: nothing reported")
for n in not_measured:
    print(f"  - {n}")
if warn:
    print("REPORT-RULE WARNINGS (text the publish gate would refuse):")
    for w in warn:
        print(f"  - {w}")
print("Unverified: no person or verify agent checked these findings.")
