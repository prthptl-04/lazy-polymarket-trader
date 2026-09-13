"""The three dashboard pages: Overview, Positions, Venues.

Round Table lives in `dashboard/roundtable_view.py`.
"""

from dashboard.ui import page

# ---------------------------------------------------------------- Overview

_OVERVIEW_BODY = """
<div class="grid g3" style="margin-bottom:16px">
  <div class="panel"><h2>Equity</h2>
    <div id="equity" class="stat">--</div>
    <div id="equity-sub" class="sub">&nbsp;</div></div>
  <div class="panel"><h2>Realised P&amp;L</h2>
    <div id="realized" class="stat">--</div>
    <div id="record" class="sub">&nbsp;</div></div>
  <div class="panel"><h2>Win rate</h2>
    <div id="winrate" class="stat">--</div>
    <div id="pf" class="sub">&nbsp;</div></div>
  <div class="panel"><h2>Open risk</h2>
    <div id="openrisk" class="stat">--</div>
    <div id="openrisk-sub" class="sub">&nbsp;</div></div>
</div>

<div class="panel" style="margin-bottom:16px">
  <h2>Equity curve &mdash; realised, per closed position</h2>
  <div id="curve" class="chart" style="height:230px"></div>
</div>

<div class="grid g2" style="margin-bottom:16px">
  <div class="panel"><h2>Wallets</h2><div id="wallets" class="grid" style="gap:10px"></div></div>
  <div class="panel"><h2>The committee</h2>
    <div id="agents" class="agents"></div>
    <div class="sub" style="margin-top:10px">Hover any seat for its mandate.</div></div>
</div>

<div class="grid g2" style="margin-bottom:16px">
  <div class="panel"><h2>Last cycle</h2><div id="cycle"></div></div>
  <div class="panel"><h2>Recent decisions</h2><div id="audit"></div></div>
</div>

<div class="panel">
  <h2>What the fund has learned &mdash; written on every loss</h2>
  <div id="lessons"></div>
</div>
"""

_OVERVIEW_JS = """
async function refresh(){
  const [rec,pnl,bal,agents,fund,audit,pos,vs,lessons,llm] = await Promise.all([
    j('/api/record'), j('/api/pnl'), j('/api/balances'), j('/api/agents'),
    j('/api/fund'), j('/api/audit?limit=12'), j('/api/positions'), j('/api/venue-sessions'),
    j('/api/lessons?limit=8'), j('/api/llm')]);
  renderLlm(llm);
  if(vs && renderVenuePills(vs.sessions)) wireVenueSwitches(refresh);

  if(pnl){ $('equity').textContent = money(pnl.equity_usd);
    $('equity-sub').textContent = 'started at '+money(pnl.starting_bankroll_usd); }

  if(rec){
    const r=$('realized'); r.textContent = signed(rec.realized_usd); r.className='stat '+cls(rec.realized_usd);
    $('record').textContent = rec.closed ? rec.wins+' won \\u00b7 '+rec.losses+' lost \\u00b7 best '
       +signed(rec.best_usd)+' \\u00b7 worst '+signed(rec.worst_usd)
      : 'no positions closed yet';
    $('winrate').textContent = rec.win_rate==null ? '--' : rec.win_rate.toFixed(0)+'%';
    // Profit factor matters more than win rate: 70% wins with tiny gains and
    // huge losses is a losing strategy that looks like a winning one.
    $('pf').textContent = rec.profit_factor==null
      ? 'profit factor needs a win and a loss' : 'profit factor '+rec.profit_factor;
    spark($('curve'), rec.equity_curve);
  }

  if(pos){
    const risk = pos.reduce((a,p)=>a + ((p.entry&&p.stop)? Math.abs(p.entry-p.stop)*p.quantity : 0), 0);
    $('openrisk').textContent = pos.length? money(risk) : '$0.00';
    $('openrisk-sub').textContent = pos.length
      ? pos.length+' position'+(pos.length>1?'s':'')+' \\u00b7 loss if every stop hits'
      : 'no open positions';
  }

  if(bal){
    renderBalances(bal);
    $('wallets').innerHTML = Object.entries(bal).map(([k,v])=>{
      const nm = k==='polymarket_us'?'Polymarket US':(k==='robinhood'?'Robinhood':k);
      const body = v.available ? '<div class="amt">'+money(v.cash_usd)+'</div>'
        : '<div class="na">'+(v.reason||'unavailable')+'</div>';
      return '<div class="venue"><div><div style="font-weight:600">'+nm+'</div>'
        +'<div class="sub">'+(v.available?'cash available':'not reachable')+'</div></div>'
        +'<div style="text-align:right">'+body
        +'<a href="/venues#'+k+'" style="font-size:12px">view activity &rarr;</a></div></div>';
    }).join('');
  }

  if(agents){
    $('agents').innerHTML = agents.map(a=>
      '<div class="agent"><span class="ico">'+a.icon+'</span><span class="nm">'+a.name+'</span>'
      +'<div class="card"><b>'+a.name+'</b>'+a.mandate
      +'<br><span style="color:var(--dim)">round '+a.round+'</span></div></div>').join('');
    gsap.from('.agent', {opacity:0, y:6, duration:.3, stagger:.04, ease:'power2.out'});
  }

  if(fund){
    renderState(fund.attached?fund:{state:'stopped'});
    const c = fund.last_cycle;
    $('cycle').innerHTML = !fund.attached
      ? '<div class="empty">No fund attached.<br><span style="font-size:12px">Set ANTHROPIC_API_KEY and a data provider, then restart.</span></div>'
      : (!c ? '<div class="empty">No cycle has run yet. Press GO.</div>'
        : '<table><tr><td>session</td><td class="num">'+c.session+'</td></tr>'
          +'<tr><td>screened</td><td class="num">'+c.universe+'</td></tr>'
          +'<tr><td>pre-screened out</td><td class="num">'+c.prescreened_out+'</td></tr>'
          +'<tr><td>debated</td><td class="num">'+c.deliberated+'</td></tr>'
          +'<tr><td>orders</td><td class="num">'+c.submitted+'</td></tr>'
          +'<tr><td>exits</td><td class="num">'+c.exits+'</td></tr></table>'
          +(c.halted_reason?'<div class="warn">HALTED: '+c.halted_reason+'</div>':''));
  }

  if(lessons){
    paint('lessons', lessons.length
      ? lessons.map(l=>'<div class="thread" style="border-left-color:'
          +(l.severity==='warning'?'var(--amber)':'var(--border)')+'">'
          +'<div class="who">'+(l.symbol||'')+' <span style="color:var(--dim);font-weight:400">'
          +l.code.replace(/_/g,' ')+'</span></div>'
          +'<div class="msg">'+l.lesson+'</div></div>').join('')
      : '<div class="empty">Nothing learned yet.<br><span style="font-size:12px">'
        +'A lesson is written every time a position closes at a loss &mdash; who dissented '
        +'and was right, whether the table was unanimous, whether the data was ever verified.'
        +'</span></div>');
  }

  if(audit){
    $('audit').innerHTML = audit.length
      ? '<table>'+audit.map(e=>'<tr><td>'+e.action+'</td><td style="color:var(--muted)">'
          +(e.target||'')+'</td><td class="num" style="color:var(--dim)">'
          +new Date(e.created*1000).toLocaleTimeString()+'</td></tr>').join('')+'</table>'
      : '<div class="empty">Nothing yet.</div>';
  }
}
wireControls(refresh); refresh(); setInterval(refresh, 5000);
"""

# --------------------------------------------------------------- Positions

_POSITIONS_BODY = """
<div class="panel" style="margin-bottom:16px">
  <h2>Open positions &mdash; what we hold and where we get out</h2>
  <div id="positions"></div>
</div>
<div id="charts" class="grid g2"></div>
<div class="panel" style="margin-top:16px">
  <h2>Closed &mdash; the results that count</h2><div id="closed"></div>
</div>
"""

_POSITIONS_JS = """
async function refresh(){
  const [pos,fund,bal,rec,vs,llm] = await Promise.all([
    j('/api/positions'), j('/api/fund'), j('/api/balances'), j('/api/record'), j('/api/venue-sessions'), j('/api/llm')]);
  if(vs && renderVenuePills(vs.sessions)) wireVenueSwitches(refresh);
  renderLlm(llm);
  if(bal) renderBalances(bal);
  if(fund) renderState(fund.attached?fund:{state:'stopped'});

  if(!pos || !pos.length){
    $('positions').innerHTML = '<div class="empty">No open positions.<br>'
      +'<span style="font-size:12px">The fund opens one only when the round table reaches a '
      +'bullish consensus AND the grader approves the exit plan.</span></div>';
    $('charts').innerHTML=''; 
  } else {
    $('positions').innerHTML = '<table><tr><th>Symbol</th><th class="num">Qty</th>'
      +'<th class="num">Entry</th><th class="num">Stop</th><th class="num">Target</th>'
      +'<th class="num">R:R</th><th class="num">Risk</th><th class="num">Unrealised</th>'
      +'<th>Thesis</th></tr>'
      + pos.map(p=>{
          const risk = (p.entry&&p.stop)? Math.abs(p.entry-p.stop)*p.quantity : null;
          const rr = (p.entry&&p.stop&&p.target)
            ? (Math.abs(p.target-p.entry)/Math.abs(p.entry-p.stop)).toFixed(2) : '--';
          return '<tr><td><b>'+p.symbol+'</b></td><td class="num">'+p.quantity.toFixed(4)+'</td>'
            +'<td class="num">'+money(p.entry)+'</td>'
            +'<td class="num down">'+money(p.stop)+'</td>'
            +'<td class="num up">'+money(p.target)+'</td>'
            +'<td class="num">'+rr+'</td>'
            +'<td class="num">'+money(risk)+'</td>'
            +'<td class="num '+cls(p.unrealized_pct)+'">'
              +(p.unrealized_pct==null?'--':(p.unrealized_pct>0?'+':'')+p.unrealized_pct.toFixed(2)+'%')+'</td>'
            +'<td>'+(p.thesis_id?'<a href="/roundtable#'+p.thesis_id+'">debate &rarr;</a>':'--')+'</td></tr>';
        }).join('') + '</table>';

    $('charts').innerHTML = pos.map(p=>
      '<div class="panel"><h2>'+p.symbol+' &mdash; price vs plan</h2>'
      +'<div id="ch-'+p.symbol+'" class="chart"></div></div>').join('');
    for(const p of pos){
      const c = await j('/api/candles?symbol='+encodeURIComponent(p.symbol));
      const el = document.getElementById('ch-'+p.symbol);
      if(!c || !c.closes || !c.closes.length){
        el.innerHTML = '<div class="empty">'+((c&&c.reason)||'no price data')+'</div>'; continue;
      }
      spark(el, c.closes, {lines:[
        {label:'entry', v:c.entry, color:'var(--muted)', offset:0},
        {label:'stop',  v:c.stop,  color:'var(--red)',   offset:1},
        {label:'target',v:c.target,color:'var(--green)', offset:2}]});
    }
  }

  if(rec){
    $('closed').innerHTML = rec.closed
      ? '<div class="sub">'+rec.wins+' won \\u00b7 '+rec.losses+' lost \\u00b7 net '
        +signed(rec.realized_usd)+'</div>'
      : '<div class="empty">Nothing closed yet. Wins and losses appear here once a stop or target fires.</div>';
  }
}
wireControls(refresh); refresh(); setInterval(refresh, 5000);
"""

# ------------------------------------------------------------------ Venues

_VENUES_BODY = """
<div class="panel" style="margin-bottom:16px">
  <h2>Venues</h2><div id="venues" class="grid g2"></div>
</div>
<div class="grid g2">
  <div class="panel"><h2>Gates &mdash; what is allowed right now</h2><div id="gates"></div></div>
  <div class="panel"><h2>Seat calibration</h2><div id="score"></div></div>
</div>

<div class="panel" style="margin-top:16px">
  <h2>Live-trading checklist &mdash; CLAUDE.md #13</h2>
  <div id="livegate"></div>
</div>
"""

_VENUES_JS = """
async function refresh(){
  const [bal,fund,score,vs,llm] = await Promise.all([
    j('/api/balances'), j('/api/fund'), j('/api/scorecard'), j('/api/venue-sessions'), j('/api/llm')]);
  if(vs && renderVenuePills(vs.sessions)) wireVenueSwitches(refresh);
  renderLlm(llm);
  if(bal) renderBalances(bal);
  if(fund) renderState(fund.attached?fund:{state:'stopped'});

  if(bal){
    const sess = (vs && vs.sessions) || {};
    const changed = paint('venues', Object.entries(bal).map(([k,v])=>{
      const on = sess[k] !== false;
      const nm = k==='polymarket_us'?'Polymarket US':(k==='robinhood'?'Robinhood':k);
      return '<div class="panel" id="'+k+'" style="background:var(--panel-2)">'
        +'<h2>'+nm+'</h2>'
        +(v.available
          ? '<div class="stat">'+money(v.cash_usd)+'</div><div class="sub">cash \\u00b7 equity '
            +money(v.equity_usd)+'</div>'
          : '<div class="na" style="color:var(--dim)">'+(v.reason||'unavailable')+'</div>')
        +'<div style="margin-top:12px;display:flex;align-items:center;gap:10px">'
          +'<span class="sub">trading</span>'+venueSwitch(k, nm, on)+'</div>'
        +(on ? '' : '<div class="warn">Switched OFF &mdash; no new positions here. '
            +'Exits still allowed so nothing gets trapped.</div>')
        +'</div>';
    }).join(''));
    if(changed) wireVenueSwitches(refresh);
  }

  if(fund && fund.attached){
    const ks=fund.kill_switch, pdt=fund.pdt;
    let h='<table>';
    h+='<tr><td>session</td><td class="num">'+fund.session+'</td></tr>';
    h+='<tr><td>equities</td><td class="num">'+(fund.equities_open?'open':'closed')+'</td></tr>';
    if(ks) h+='<tr><td>daily loss headroom</td><td class="num '+(ks.tripped?'down':'')+'">'
      +(ks.armed? money(ks.remaining_usd)+' of '+money(ks.limit_usd) : 'not armed')+'</td></tr>';
    if(pdt) h+='<tr><td>day trades left</td><td class="num">'
      +(pdt.pdt_applies? pdt.day_trades_remaining+' of 3' : 'unrestricted')+'</td></tr>';
    h+='<tr><td>cycles run</td><td class="num">'+fund.metrics.cycles+'</td></tr>';
    h+='</table>';
    // The three states where the screen must shout rather than inform.
    let warns=[];
    if(ks&&ks.tripped) warns.push('DAILY LOSS LIMIT TRIPPED &mdash; no new risk today. Exits still allowed.');
    if(ks&&!ks.armed) warns.push('Kill-switch UNARMED &mdash; no equity observed yet, so the daily loss limit cannot fire.');
    if(pdt&&pdt.pdt_applies&&pdt.day_trades_remaining<=0)
      warns.push('Day-trade budget exhausted &mdash; equity round trips are blocked until the window rolls.');
    h += warns.map(w=>'<div class="warn">'+w+'</div>').join('');
    $('gates').innerHTML=h;
  } else {
    $('gates').innerHTML='<div class="empty">No fund attached.</div>';
  }

  const lg = fund && fund.attached && fund.router_live_gate;
  if(lg){
    const label = {
      env_paper_trading_false: 'PAPER_TRADING=false in .env',
      venue_authenticated: 'venue authenticated',
      risk_caps_live_appropriate: 'risk caps sized to the bankroll',
      paper_trades_recorded: 'graded paper trades on record',
      operator_approval_lesson: 'operator approval recorded',
    };
    paint('livegate',
      '<div class="' + (lg.live_possible ? 'sub' : 'warn') + '">'
      + (lg.live_possible
          ? 'All conditions met &mdash; a live venue would be permitted to trade.'
          : 'LIVE TRADING BLOCKED. The fund is paper-only until every box is ticked.')
      + '</div><table>'
      + Object.entries(lg.checks).map(([k,v])=>
          '<tr><td>'+(label[k]||k)+'</td><td class="num '+(v?'up':'down')+'">'
          +(v?'yes':'no')+'</td></tr>').join('')
      + '<tr><td>graded paper trades</td><td class="num">'
      + lg.graded_paper_trades + ' of ' + lg.required_paper_trades
      + '</td></tr></table>');
  } else {
    paint('livegate', '<div class="empty">No fund attached.</div>');
  }

  if(score){
    $('score').innerHTML = score.resolved
      ? '<table><tr><th>Seat</th><th class="num">Calls</th><th class="num">Hit</th>'
        +'<th class="num">Brier</th><th class="num">Over-confident by</th></tr>'
        + score.seats.map(s=>'<tr><td>'+s.seat_name+'</td><td class="num">'+s.samples+'</td>'
          +'<td class="num">'+(s.hit_rate*100).toFixed(0)+'%</td>'
          +'<td class="num">'+s.brier.toFixed(3)+'</td>'
          +'<td class="num '+(Math.abs(s.overconfidence)>10?'down':'')+'">'
          +s.overconfidence.toFixed(0)+'</td></tr>').join('')+'</table>'
      : '<div class="empty">No seat has been scored yet.<br><span style="font-size:12px">'
        +(score.fit? score.fit.reason : 'Scores appear once positions close.')+'</span></div>';
  }
}
wireControls(refresh); refresh(); setInterval(refresh, 5000);
"""


OVERVIEW_HTML = page("Fund · Overview", _OVERVIEW_BODY, _OVERVIEW_JS, "home")
POSITIONS_HTML = page("Fund · Positions", _POSITIONS_BODY, _POSITIONS_JS, "positions")
VENUES_HTML = page("Fund · Venues", _VENUES_BODY, _VENUES_JS, "venues")
