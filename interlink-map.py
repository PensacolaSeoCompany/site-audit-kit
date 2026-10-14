#!/usr/bin/env python3
"""PHASE-2 granular: directed internal-link graph + anchor-text inventory from a full-extract crawl.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.
Outputs a self-contained HTML page: force-directed graph (Screaming Frog-style) where
node size = inlinks, colour = page class, edges = CONTEXTUAL links only (site-wide nav edges
would make any graph a hairball — nav targets are shown as labelled hubs instead), plus an
anchor-text inventory table and weak/orphan lists.
Usage: interlink-map.py <crawl.json> <out.html> "<Business Name>"
"""
import json, sys, html, re
from collections import Counter, defaultdict

src, out, name = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(src))
pages = d.get('pages') or []
def norm(u): return (u or '').split('#')[0].split('?')[0].rstrip('/')
BASE = re.match(r'https?://[^/]+', pages[0]['url']).group(0)
urls = {norm(p['url']) for p in pages if not norm(p['url']).endswith('.xml')}
N = len(urls)
NAVCUT = int(N * 0.75)

inl = Counter(); outmap = {}; anchors = defaultdict(Counter); edges = []
for p in pages:
    s = norm(p['url'])
    if s not in urls: continue
    tg = set()
    for l in (p.get('internal_links') or []):
        t = norm(l.get('href'))
        if t in urls and t != s:
            tg.add(t)
            a = re.sub(r'\s+', ' ', (l.get('anchor') or '')).strip()[:60]
            if a: anchors[t][a] += 1
    outmap[s] = tg
    for t in tg: inl[t] += 1
for s, tg in outmap.items():
    for t in tg:
        if inl[t] < NAVCUT:            # contextual edges only
            edges.append((s, t))

def cls(u):
    p = u.replace(BASE, '')
    if not p: return 'home'
    if inl[u] >= NAVCUT: return 'nav'
    if p.startswith('/category') or p.startswith('/author') or p.startswith('/blog'): return 'archive'
    return 'content'
COL = {'home':'#1d4ed8','nav':'#0f172a','archive':'#94a3b8','content':'#dc2626'}

nodes = [{'id':u,'label':u.replace(BASE,'') or '/','inl':inl[u],'out':len(outmap.get(u,[])),
          'cls':cls(u)} for u in sorted(urls)]
idx = {n['id']:i for i,n in enumerate(nodes)}
links = [{'source':idx[s],'target':idx[t]} for s,t in edges if s in idx and t in idx]

# Crawled URLs and anchors are hostile input (objections/SYNTHESIS.md A2/B4, RU-02, UA-04): a link
# like /a</script><img onerror=...> ran script on the public audits host. HTML text goes through
# X (html.escape); JSON inside <script> goes through J, which escapes < > & and U+2028/2029 so no
# crawled string can close the script block. The tooltip is built with textContent, never innerHTML.
X = html.escape
def J(v):
    return (json.dumps(v).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))

# anchor inventory: top targets by inlinks
inv = ''
for u,c in inl.most_common(40):
    rows = ''.join(f'<tr><td>{X(a)}</td><td class=n>{n}</td></tr>' for a,n in anchors[u].most_common(6))
    inv += (f'<details><summary><b>{X(u.replace(BASE,"") or "/")}</b>'
            f'<span class=tag>{c} inlinks</span></summary><table>{rows or "<tr><td><i>no anchor text captured</i></td></tr>"}</table></details>\n')

weak = sorted([u for u in urls if 1 <= inl[u] <= 2], key=lambda x: inl[x])
weak_html = ''.join(f'<li>{inl[u]} inlink{"s" if inl[u]>1 else ""} — {X(u.replace(BASE,""))}</li>' for u in weak)
orph = [u for u in urls if inl[u] == 0]
orph_html = ''.join(f'<li>{X(u.replace(BASE,""))}</li>' for u in orph) or '<li><i>none</i></li>'

page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>{X(name)} — Internal Link Graph</title>
<script src="https://unpkg.com/d3@7/dist/d3.min.js"></script><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#eef1f4;color:#0f172a;font-family:Inter,"Segoe UI",system-ui,sans-serif;padding:34px 18px;line-height:1.5}}
.wrap{{max-width:1240px;margin:0 auto}}
.kick{{font-size:13px;font-weight:800;letter-spacing:3px;color:#dc2626;text-transform:uppercase}}
h1{{font-size:30px;font-weight:800;margin-top:4px}}
h2{{font-size:21px;font-weight:800;margin-top:34px;border-bottom:3px solid #0f172a;padding-bottom:6px}}
#g{{background:#fff;border:3px solid #0f172a;box-shadow:8px 8px 0 rgba(15,23,42,.1);margin-top:14px}}
.legend{{display:flex;gap:18px;margin-top:10px;font-size:14px;font-weight:700;flex-wrap:wrap}}
.legend span::before{{content:"●";margin-right:5px}}
.l-home::before{{color:#1d4ed8}}.l-nav::before{{color:#0f172a}}.l-archive::before{{color:#94a3b8}}.l-content::before{{color:#dc2626}}
#tip{{position:fixed;background:#0f172a;color:#fff;padding:8px 12px;font-size:13px;pointer-events:none;display:none;max-width:420px;z-index:9}}
details{{background:#fff;border:2px solid #0f172a;margin-top:8px;padding:10px 14px}}
summary{{cursor:pointer;font-size:15px}}
.tag{{background:#0f172a;color:#fff;font-size:12px;font-weight:800;padding:2px 8px;margin-left:10px}}
table{{border-collapse:collapse;margin-top:8px;width:100%}}
td{{padding:4px 10px;border-bottom:1px solid #e2e8f0;font-size:14px}} td.n{{text-align:right;font-weight:700;width:60px}}
ul{{margin:10px 0 0 22px}} li{{font-size:14px;margin-top:4px;color:#334155}}
.note{{background:#fff;border:2px solid #e2e8f0;padding:12px 16px;font-size:14px;color:#475569;font-weight:600;margin-top:12px}}
</style></head><body><div class="wrap">
<div class="kick">Internal linking &middot; granular phase</div>
<h1>{X(name)} — link graph &amp; anchors</h1>
<div class="note">Directed graph of <b>contextual</b> links (an arrow A→B = page A's body links to B).
Site-wide nav/footer targets (&ge;{NAVCUT} inlinks) are the dark hubs — their edges are omitted so the
editorial structure is visible instead of a nav hairball. Node size = inlinks. Drag to untangle; hover for URL.</div>
<div class="legend"><span class=l-home>home</span><span class=l-nav>nav-level hub</span>
<span class=l-archive>archive/category</span><span class=l-content>content page</span></div>
<div id="g"></div><div id="tip"></div>
<h2>Anchor-text inventory (top pages by inlinks)</h2>{inv}
<h2>Weak pages (1–2 inlinks) — {len(weak)}</h2><ul>{weak_html}</ul>
<h2>Orphans — {len(orph)}</h2><ul>{orph_html}</ul>
</div><script>
const nodes={J(nodes)}, links={J(links)};
const W=Math.min(1200,document.querySelector('.wrap').clientWidth-6), H=760;
const svg=d3.select('#g').append('svg').attr('width',W).attr('height',H);
svg.append('defs').append('marker').attr('id','arr').attr('viewBox','0 -4 8 8').attr('refX',14)
 .attr('markerWidth',5).attr('markerHeight',5).attr('orient','auto')
 .append('path').attr('d','M0,-4L8,0L0,4').attr('fill','#94a3b8');
const col={J(COL)};
const sim=d3.forceSimulation(nodes)
 .force('link',d3.forceLink(links).distance(46).strength(.25))
 .force('charge',d3.forceManyBody().strength(-42))
 .force('center',d3.forceCenter(W/2,H/2))
 .force('collide',d3.forceCollide().radius(d=>5+Math.sqrt(d.inl)*1.6));
const link=svg.append('g').selectAll('line').data(links).join('line')
 .attr('stroke','#cbd5e1').attr('stroke-width',1).attr('marker-end','url(#arr)');
const node=svg.append('g').selectAll('circle').data(nodes).join('circle')
 .attr('r',d=>4+Math.sqrt(d.inl)*1.4).attr('fill',d=>col[d.cls])
 .attr('stroke','#fff').attr('stroke-width',1.2)
 .call(d3.drag().on('start',(e,d)=>{{if(!e.active)sim.alphaTarget(.25).restart();d.fx=d.x;d.fy=d.y}})
 .on('drag',(e,d)=>{{d.fx=e.x;d.fy=e.y}}).on('end',(e,d)=>{{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null}}));
const tip=document.getElementById('tip');
node.on('mousemove',(e,d)=>{{tip.style.display='block';tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+8)+'px';
 const b=document.createElement('b');b.textContent=d.label;
 tip.replaceChildren(b,document.createElement('br'),d.inl+' inlinks \u00b7 '+d.out+' outlinks');}})
 .on('mouseout',()=>tip.style.display='none');
sim.on('tick',()=>{{
 link.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y).attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
 node.attr('cx',d=>d.x=Math.max(10,Math.min(W-10,d.x))).attr('cy',d=>d.y=Math.max(10,Math.min(H-10,d.y)));
}});
</script></body></html>'''
open(out, 'w').write(page)
print(f'link map -> {out}  ({len(nodes)} nodes, {len(links)} contextual edges)')
