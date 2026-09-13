"""Dashboard HTML.

Server-rendered, no build step. Deliberate: this is a Python trading daemon,
and adding npm + a bundler + node_modules to it would be a maintenance burden
paid forever for animation polish. React, framer-motion and 21st.dev were
considered and skipped for that reason (2026-09-12).

External assets come from CDNs already used by this app:
- **uPlot** for charts — ~45KB, built for long realtime series. Chart.js and
  friends are heavier and slower at this job.
- **GSAP** for motion, per the vendored gsap-core skill.

Design rules applied throughout:
- Money is monospace and right-aligned, so digits line up and a misread is
  harder. Green/red carry a sign as well as a colour — colour alone fails for
  the ~8% of men with red/green colour blindness, on a screen about money.
- Every number that could be stale says when it was read.
- Empty states say *why* they are empty. "No positions" and "no data provider
  attached" mean very different things to someone deciding whether to trust the
  screen.
"""

SHELL_CSS = """
:root{
  --bg:#0a0e14; --panel:#121821; --panel-2:#161d28; --border:#232c3a;
  --fg:#dbe4f0; --muted:#7d8a9e; --dim:#4a5566;
  --green:#26d97f; --red:#ff5f5f; --amber:#ffb84c; --blue:#5aa9ff; --violet:#a98bff;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;}
a{color:var(--blue);text-decoration:none}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;
  padding:12px 22px;border-bottom:1px solid var(--border);background:var(--panel);
  position:sticky;top:0;z-index:20}
header h1{margin:0;font-size:15px;letter-spacing:.3px;white-space:nowrap}
nav{display:flex;gap:4px}
nav a{padding:6px 12px;border-radius:6px;color:var(--muted);font-size:13px}
nav a:hover{background:var(--panel-2);color:var(--fg)}
nav a.on{background:var(--panel-2);color:var(--fg);box-shadow:inset 0 -2px 0 var(--blue)}
.right{display:flex;align-items:center;gap:10px;margin-left:auto}
.pill{padding:4px 10px;border-radius:999px;background:#1b2430;border:1px solid var(--border);
  font-size:12px;color:var(--muted);white-space:nowrap}
.pill.running{color:var(--green);border-color:var(--green)}
.pill.starting,.pill.stopping{color:var(--amber);border-color:var(--amber)}
button{padding:7px 15px;border-radius:6px;border:1px solid var(--border);
  background:#1b2430;color:var(--fg);cursor:pointer;font-weight:600;font-size:13px}
button.go{border-color:var(--green);color:var(--green)}
button.stop{border-color:var(--red);color:var(--red)}
button:disabled{opacity:.4;cursor:not-allowed}
main{padding:20px 22px;max-width:1500px;margin:0 auto}
.grid{display:grid;gap:16px}
.g3{grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.g2{grid-template-columns:repeat(auto-fit,minmax(380px,1fr))}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px}
.panel h2{margin:0 0 12px;font-size:12px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted)}
.stat{font:600 27px/1.15 var(--mono);letter-spacing:-.5px}
.sub{color:var(--muted);font-size:12px;margin-top:4px}
.up{color:var(--green)} .down{color:var(--red)} .flat{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:500;font-size:11px;
  text-transform:uppercase;letter-spacing:.06em;padding:6px 8px;border-bottom:1px solid var(--border)}
td{padding:8px;border-bottom:1px solid #1b2430}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-family:var(--mono)}
.empty{color:var(--dim);font-size:13px;padding:18px 4px;text-align:center;line-height:1.6}
.chart{width:100%;height:190px}
.agents{display:flex;flex-wrap:wrap;gap:8px}
.agent{position:relative;display:flex;align-items:center;gap:7px;padding:7px 12px;
  background:var(--panel-2);border:1px solid var(--border);border-radius:8px;cursor:default}
.agent .ico{font-size:17px;line-height:1}
.agent .nm{font-size:12px;color:var(--fg)}
.agent .card{position:absolute;bottom:calc(100% + 8px);left:0;width:270px;padding:10px 12px;
  background:#0d131b;border:1px solid var(--border);border-radius:8px;font-size:12px;
  color:var(--muted);opacity:0;pointer-events:none;transition:opacity .14s;z-index:30;
  box-shadow:0 10px 26px rgba(0,0,0,.6);line-height:1.5}
.agent:hover .card{opacity:1}
.agent .card b{color:var(--fg);display:block;margin-bottom:3px}
.badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;
  font-weight:600;letter-spacing:.02em}
.b-bull{background:rgba(38,217,127,.14);color:var(--green)}
.b-bear{background:rgba(255,95,95,.14);color:var(--red)}
.b-neut{background:rgba(125,138,158,.14);color:var(--muted)}
.venue{display:flex;align-items:center;justify-content:space-between;gap:12px;
  padding:13px 15px;background:var(--panel-2);border:1px solid var(--border);border-radius:9px}
.venue .amt{font:600 20px/1 var(--mono)}
.venue .na{color:var(--dim);font-size:12px;font-style:italic}
.thread{border-left:2px solid var(--border);padding:2px 0 2px 14px;margin-bottom:14px}
.thread .who{font-weight:600;font-size:13px}
.thread .msg{color:var(--muted);font-size:13px;margin-top:3px;white-space:pre-wrap}
.warn{color:var(--amber);font-size:12px;margin-top:8px}
"""

_NAV = """
<header>
  <h1>&#9679; Autonomous Fund</h1>
  <nav>
    <a href="/" class="{on_home}">Overview</a>
    <a href="/positions" class="{on_pos}">Positions</a>
    <a href="/roundtable" class="{on_rt}">Round Table</a>
    <a href="/venues" class="{on_v}">Venues</a>
  </nav>
  <div class="right">
    <span id="bal-polymarket" class="pill">PM &mdash;</span>
    <span id="bal-robinhood" class="pill">RH &mdash;</span>
    <span id="state-pill" class="pill">stopped</span>
    <button id="go-btn" class="go">GO</button>
    <button id="stop-btn" class="stop" disabled>STOP</button>
  </div>
</header>
"""

_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css">
<script src="https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>{css}</style></head><body>
"""

_COMMON_JS = """
const $ = id => document.getElementById(id);
const money = v => (v==null?'--':(v<0?'-':'')+'$'+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}));
const signed = v => (v==null?'--':(v>0?'+':'')+money(v).replace('$-','-$'));
const cls = v => v==null?'flat':(v>0?'up':(v<0?'down':'flat'));
async function j(u){ try{ const r = await fetch(u); return r.ok ? await r.json() : null; }catch(e){ return null; } }

function renderBalances(b){
  const set=(el,label,v)=>{
    const n=$(el); if(!n) return;
    if(!v||!v.available){ n.textContent=label+' n/a'; n.title=(v&&v.reason)||'unavailable';
      n.style.color='var(--dim)'; return; }
    n.textContent=label+' '+money(v.cash_usd); n.title=label+' equity '+money(v.equity_usd);
    n.style.color='var(--fg)';
  };
  set('bal-polymarket','PM', b.polymarket_us||b.polymarket);
  set('bal-robinhood','RH', b.robinhood);
}
function renderState(s){
  const p=$('state-pill'); if(!p||!s) return;
  const st=s.state||'stopped';
  p.textContent = st + (s.session? ' \\u00b7 '+s.session : '');
  p.className='pill '+st;
  const run = st==='running'||st==='starting';
  if($('go-btn')) $('go-btn').disabled=run;
  if($('stop-btn')) $('stop-btn').disabled=!run;
}
function wireControls(refresh){
  const go=$('go-btn'), st=$('stop-btn');
  if(go) go.onclick=async()=>{ const r=await fetch('/api/start',{method:'POST'});
    if(!r.ok) alert('start failed: '+(await r.text())); refresh(); };
  if(st) st.onclick=async()=>{ await fetch('/api/stop',{method:'POST'}); refresh(); };
}
function spark(el, values, opts={}){
  if(!el) return null;
  if(!values || values.length<2){
    el.style.height='auto';
    el.innerHTML='<div class="empty">Not enough data to plot yet.<br>'
      +'<span style="font-size:12px">The curve draws one point per closed position.</span></div>';
    return null; }
  const xs = values.map((_,i)=>i);
  const up = values[values.length-1] >= values[0];
  const series = [{}, {stroke: up?'#26d97f':'#ff5f5f', width:2,
                       fill: up?'rgba(38,217,127,.10)':'rgba(255,95,95,.10)'}];
  const plot = new uPlot({width: el.clientWidth||600, height: el.clientHeight||190,
    padding:[10,8,4,8], cursor:{y:false},
    axes:[{stroke:'#4a5566',grid:{stroke:'#1b2430'}},{stroke:'#4a5566',grid:{stroke:'#1b2430'}}],
    scales:{x:{time:false}}, series}, [xs, values], el);
  (opts.lines||[]).forEach(l=>{ if(l.v==null) return;
    const d=document.createElement('div'); d.textContent=l.label+' '+money(l.v);
    d.style.cssText='position:absolute;right:8px;font:11px var(--mono);color:'+l.color+';pointer-events:none';
    d.style.top = (8 + (l.offset||0)*15) + 'px'; el.style.position='relative'; el.appendChild(d);
  });
  return plot;
}
"""


def page(title: str, body: str, script: str, active: str = "") -> str:
    nav = _NAV.format(
        on_home="on" if active == "home" else "",
        on_pos="on" if active == "positions" else "",
        on_rt="on" if active == "roundtable" else "",
        on_v="on" if active == "venues" else "",
    )
    return (_HEAD.format(title=title, css=SHELL_CSS) + nav
            + f"<main>{body}</main><script>{_COMMON_JS}\n{script}</script></body></html>")
