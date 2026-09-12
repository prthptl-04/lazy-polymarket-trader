"""The round-table monitoring page.

Separate from `server.py` because that file already carries the main
dashboard's markup and this is another few hundred lines of it.

Design intent: **show the disagreement.** The point of five independent seats
is that they can differ, so the UI leads with the vote split and the
abstentions rather than with a single headline number. A page that showed only
"BULLISH 78%" would hide exactly the information that makes the design worth
its token cost.

Unanimity gets a warning banner for the same reason — in a five-seat LLM panel
agreement usually means shared framing, not a safe trade.
"""

from __future__ import annotations

ROUNDTABLE_HTML = """<!-- round table -->
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --bull: #3fb950; --bear: #f85149; --neutral: #d29922;
    --accent: #58a6ff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  a { color: var(--accent); text-decoration: none; }
  header {
    display: flex; align-items: center; gap: 16px;
    padding: 14px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
    position: sticky; top: 0; z-index: 10;
  }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; }
  main { display: grid; grid-template-columns: 320px 1fr; gap: 0; height: calc(100vh - 53px); }
  .list { border-right: 1px solid var(--border); overflow-y: auto; }
  .detail { overflow-y: auto; padding: 20px; }
  .row {
    padding: 12px 16px; border-bottom: 1px solid var(--border); cursor: pointer;
  }
  .row:hover { background: #1c2128; }
  .row.active { background: #1f6feb22; border-left: 3px solid var(--accent); }
  .row .sym { font-weight: 600; }
  .row .meta { color: var(--muted); font-size: 12px; margin-top: 3px; }
  .pill {
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600; text-transform: uppercase;
  }
  .bullish { background: #23863644; color: var(--bull); }
  .bearish { background: #da363344; color: var(--bear); }
  .neutral { background: #9e6a0344; color: var(--neutral); }
  .in_progress { background: #1f6feb44; color: var(--accent); }
  .seat {
    border: 1px solid var(--border); border-radius: 8px;
    padding: 14px; margin-bottom: 12px; background: var(--panel);
  }
  .seat.abstained { opacity: .55; border-style: dashed; }
  .seat h3 { margin: 0 0 6px; font-size: 14px; display: flex; gap: 10px; align-items: center; }
  .seat .conf { color: var(--muted); font-weight: 400; font-size: 12px; }
  .seat p { margin: 6px 0; }
  .seat ul { margin: 6px 0 0; padding-left: 18px; color: var(--muted); font-size: 13px; }
  .banner {
    border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 13px;
  }
  .warn { background: #9e6a0322; border: 1px solid #9e6a03; color: #e3b341; }
  .transcript {
    background: #010409; border: 1px solid var(--border); border-radius: 8px;
    padding: 14px; white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular,
    Menlo, monospace; font-size: 12.5px; line-height: 1.65; overflow-x: auto;
  }
  .tally { display: flex; gap: 8px; margin: 10px 0 18px; }
  .empty { color: var(--muted); padding: 40px; text-align: center; }
  h2 { font-size: 18px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
  section { margin-bottom: 26px; }
  section > h4 {
    font-size: 12px; text-transform: uppercase; letter-spacing: .5px;
    color: var(--muted); margin: 0 0 10px; font-weight: 600;
  }
</style>

<header>
  <h1>Round Table</h1>
  <a href="/">&larr; Dashboard</a>
  <span id="live" style="color:var(--muted);font-size:12px;margin-left:auto"></span>
</header>

<main>
  <div class="list" id="list"><div class="empty">no deliberations yet</div></div>
  <div class="detail" id="detail"><div class="empty">select a deliberation</div></div>
</main>

<script>
let active = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const when = ts => ts ? new Date(ts * 1000).toLocaleString() : '';

const pill = (text, cls) => `<span class="pill ${cls}">${esc(text)}</span>`;

async function loadList() {
  const rows = await (await fetch('/api/deliberations')).json();
  const el = document.getElementById('list');
  if (!rows.length) { el.innerHTML = '<div class="empty">no deliberations yet</div>'; return; }
  el.innerHTML = rows.map(r => {
    const cls = r.status === 'in_progress' ? 'in_progress' : (r.signal || 'neutral');
    const label = r.status === 'in_progress' ? 'debating' : (r.signal || '—');
    const conf = r.confidence != null ? ` ${Math.round(r.confidence)}%` : '';
    return `<div class="row ${r.thesis_id === active ? 'active' : ''}"
                 onclick="select('${r.thesis_id}')">
      <div class="sym">${esc(r.symbol)} ${pill(label + conf, cls)}</div>
      <div class="meta">${r.seats} seats &middot; ${when(r.created)}</div>
    </div>`;
  }).join('');
}

async function select(id) {
  active = id;
  await loadList();
  const d = await (await fetch('/api/deliberations/' + encodeURIComponent(id))).json();
  if (d.error) { document.getElementById('detail').innerHTML =
    `<div class="empty">${esc(d.error)}</div>`; return; }

  const t = d.tally || {};
  const tally = ['bullish','bearish','neutral']
    .filter(k => t[k]).map(k => pill(`${k} ${t[k]}`, k)).join('');

  const banners = [];
  if (d.unanimous) banners.push(`<div class="banner warn">
    <strong>No dissent.</strong> Every responding seat agreed. In a five-seat panel
    that usually means the seats shared a framing rather than that the trade is safe —
    treat the confidence figure with suspicion.</div>`);
  if (d.abstentions && d.abstentions.length) banners.push(`<div class="banner warn">
    <strong>Abstained:</strong> ${esc(d.abstentions.join(', '))}. These seats did not
    vote and were excluded from the tally.</div>`);
  if (d.consensus && d.consensus.synthesized_by_llm === false) banners.push(
    `<div class="banner warn"><strong>Fallback tally.</strong> The chair was
     unavailable, so this is a vote count rather than a synthesis and its confidence
     is capped.</div>`);

  const seats = (d.opinions || []).map(o => {
    if (o.failed) return `<div class="seat abstained">
      <h3>${esc(o.seat_name)} ${pill('abstained','neutral')}</h3>
      <p>${esc(o.error || 'unavailable')}</p></div>`;
    const pts = (o.key_points || []).map(p => `<li>${esc(p)}</li>`).join('');
    const cns = (o.concerns || []).map(c => `<li>${esc(c)}</li>`).join('');
    return `<div class="seat">
      <h3>${esc(o.seat_name)} ${pill(o.signal, o.signal)}
        <span class="conf">confidence ${Math.round(o.confidence)}</span></h3>
      <p>${esc(o.reasoning)}</p>
      ${pts ? `<ul>${pts}</ul>` : ''}
      ${cns ? `<div class="conf" style="margin-top:8px">Would be wrong if:</div><ul>${cns}</ul>` : ''}
    </div>`;
  }).join('');

  const c = d.consensus || {};
  document.getElementById('detail').innerHTML = `
    <h2>${esc(d.symbol)}
      ${c.signal ? pill(c.signal + ' ' + Math.round(c.confidence || 0) + '%', c.signal)
                 : pill(d.status, 'in_progress')}</h2>
    <div class="sub">${esc(d.asset_class)} &middot; ${when(d.created)} &middot;
      <code>${esc(d.thesis_id).slice(0,8)}</code></div>
    <div class="tally">${tally}</div>
    ${banners.join('')}
    ${c.summary ? `<section><h4>Resolution</h4><p>${esc(c.summary)}</p></section>` : ''}
    ${c.dissent ? `<section><h4>Surviving objection</h4><p>${esc(c.dissent)}</p></section>` : ''}
    <section><h4>Seat positions</h4>${seats}</section>
    ${c.transcript ? `<section><h4>Debate</h4>
      <div class="transcript">${esc(c.transcript)}</div></section>` : ''}
  `;
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  const live = document.getElementById('live');
  ws.onopen = () => live.textContent = 'live';
  ws.onclose = () => { live.textContent = 'reconnecting…'; setTimeout(connect, 2000); };
  ws.onmessage = ev => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.event === 'deliberation' || m.event === 'opinion') {
      loadList();
      if (active) select(active);
    }
  };
}

loadList();
connect();
setInterval(loadList, 5000);
</script>
"""
