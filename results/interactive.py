"""Self-contained interactive HTML chart for one strategy's backtest run.

Renders ``<strategy_dir>/charts/<strategy>_interactive.html`` — a single offline
file (no CDN, no dependency) that overlays the ``nofee`` vs ``fee_5bps`` equity
curves, a drawdown sub-panel, and every trade as a bubble (size = traded volume,
colour = win/loss). Hovering reads, at the nearest bar: both equities, realized
PnL, drawdown, position, cumulative commission (= no-fee equity − fee equity),
and the nearest trade's time / side / volume / entry→exit price / PnL. A segmented
control toggles no-fee / fee / both.

Data comes from the run's saved artifacts only (``equity_curve.csv``,
``trades.csv``, ``metrics.json``) — a pure function of the PnL data, so it is a
backfill (no re-run). matplotlib is NOT required (this is HTML/SVG, not a PNG).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from results.charts import _load_series, _pick

_EQ_TARGET = 1500      # equity points to plot
_TRADE_CAP = 12000     # max trades embedded (stride-sampled beyond this)


def _downsample_idx(n: int, target: int) -> list[int]:
    if n <= target:
        return list(range(n))
    step = n // target
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def _to_secs(v: float) -> int:
    return int(v / 1e9) if v > 1e17 else int(v)


def _scenario(run_dir: Path) -> dict | None:
    csv_path = run_dir / "equity_curve.csv"
    if not csv_path.is_file():
        return None
    s = _load_series(csv_path)
    n = len(s["t"])
    if not n:
        return None
    idx = _downsample_idx(n, _EQ_TARGET)
    metrics = {}
    mp = run_dir / "metrics.json"
    if mp.is_file():
        try:
            metrics = json.loads(mp.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            metrics = {}
    return {
        "t": [_to_secs(s["t"][i]) for i in idx],
        "equity": [round(s["equity"][i], 2) for i in idx],
        "pnl": [round(s["pnl"][i], 2) for i in idx],
        "position": [round(s["position"][i], 3) for i in idx],
        "ret": metrics.get("total_return"),
        "final": metrics.get("final_equity"),
        "trades": metrics.get("trade_count"),
        "maxdd": metrics.get("max_drawdown"),
        "commission": metrics.get("total_commission"),
    }


def _trades(strategy_dir: Path) -> dict:
    # gross trade log is identical across fee scenarios; read from nofee.
    tp = strategy_dir / "nofee" / "trades.csv"
    if not tp.is_file():
        tp = strategy_dir / "fee_5bps" / "trades.csv"
    empty = {"t": [], "xt": [], "side": [], "qty": [], "ep": [], "xp": [],
             "pnl": [], "win": [], "shown": 0, "total": 0}
    if not tp.is_file():
        return empty
    with tp.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    total = len(rows)
    if not total:
        return empty
    idx = _downsample_idx(total, _TRADE_CAP)
    hdr = list(rows[0].keys())
    et = _pick(hdr, ("entry_time_ns", "entry_time"))
    xt = _pick(hdr, ("exit_time_ns", "exit_time"))
    out = {"t": [], "xt": [], "side": [], "qty": [], "ep": [], "xp": [],
           "pnl": [], "win": [], "shown": len(idx), "total": total}
    for i in idx:
        r = rows[i]
        out["t"].append(_to_secs(float(r.get(et, 0) or 0)))
        out["xt"].append(_to_secs(float(r.get(xt, 0) or 0)))
        out["side"].append(str(r.get("side", "")))
        out["qty"].append(float(r.get("quantity", 0) or 0))
        out["ep"].append(round(float(r.get("entry_price", 0) or 0), 1))
        out["xp"].append(round(float(r.get("exit_price", 0) or 0), 1))
        out["pnl"].append(round(float(r.get("realized_pnl", 0) or 0), 2))
        out["win"].append(1 if str(r.get("win", "")).lower() in ("true", "1") else 0)
    return out


def render_interactive(strategy_dir: str | Path) -> str | None:
    """Write ``<strategy_dir>/charts/<name>_interactive.html``; return rel path or None."""
    strategy_dir = Path(strategy_dir)
    nofee = _scenario(strategy_dir / "nofee")
    fee = _scenario(strategy_dir / "fee_5bps")
    if nofee is None and fee is None:
        return None
    # if one scenario is missing, mirror the other so the page still renders.
    nofee = nofee or fee
    fee = fee or nofee
    data = {
        "strategy": strategy_dir.name,
        "nofee": nofee,
        "fee": fee,
        "trades_list": _trades(strategy_dir),
    }
    html = _TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    charts_dir = strategy_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    out = charts_dir / f"{strategy_dir.name}_interactive.html"
    out.write_text(html, encoding="utf-8")
    return str(out.relative_to(strategy_dir))


# --------------------------------------------------------------------------- #
# Self-contained page template (no external assets). ``__DATA__`` is replaced
# with the run's JSON. Keep the {curly braces} — this is a plain string, not an
# f-string / .format template.
# --------------------------------------------------------------------------- #
_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>backtest — equity / drawdown / trades</title>
<style>
  :root{--bg:#eef1f5;--surface:#fff;--surface-2:#f4f6f9;--ink:#101720;--muted:#5a6674;
    --hair:#dbe0e7;--grid:#e7ebf0;--nofee:#2563eb;--fee:#dd7a0c;--zero:#94a3b8;
    --win:#1a9d6b;--loss:#d23b3b;--dd:#d23b3b;--accent:#2563eb;
    --shadow:0 1px 2px rgba(16,23,32,.06),0 8px 24px rgba(16,23,32,.08);}
  @media (prefers-color-scheme:dark){:root{--bg:#0c1016;--surface:#131a23;--surface-2:#0f151d;
    --ink:#e7edf4;--muted:#8a97a6;--hair:#222c38;--grid:#1c2530;--nofee:#5aa0ff;--fee:#f0a53a;
    --zero:#5b6b7d;--win:#38b381;--loss:#ef6b6b;--dd:#ef6b6b;--accent:#5aa0ff;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.5);}}
  :root[data-theme="light"]{--bg:#eef1f5;--surface:#fff;--surface-2:#f4f6f9;--ink:#101720;
    --muted:#5a6674;--hair:#dbe0e7;--grid:#e7ebf0;--nofee:#2563eb;--fee:#dd7a0c;--zero:#94a3b8;
    --win:#1a9d6b;--loss:#d23b3b;--dd:#d23b3b;--accent:#2563eb;
    --shadow:0 1px 2px rgba(16,23,32,.06),0 8px 24px rgba(16,23,32,.08);}
  :root[data-theme="dark"]{--bg:#0c1016;--surface:#131a23;--surface-2:#0f151d;--ink:#e7edf4;
    --muted:#8a97a6;--hair:#222c38;--grid:#1c2530;--nofee:#5aa0ff;--fee:#f0a53a;--zero:#5b6b7d;
    --win:#38b381;--loss:#ef6b6b;--dd:#ef6b6b;--accent:#5aa0ff;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.5);}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;line-height:1.4;}
  .wrap{max-width:1100px;margin:0 auto;padding:26px 18px 40px;}
  .card{background:var(--surface);border:1px solid var(--hair);border-radius:14px;
    box-shadow:var(--shadow);overflow:hidden;}
  .head{padding:20px 22px 14px;border-bottom:1px solid var(--hair);}
  .eyebrow{font:600 11px/1.4 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.12em;
    text-transform:uppercase;color:var(--muted);}
  h1{margin:6px 0 2px;font-size:23px;font-weight:680;letter-spacing:-.01em;text-wrap:balance;}
  .sub{color:var(--muted);font-size:13px;}
  .toprow{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;flex-wrap:wrap;}
  .seg{display:inline-flex;border:1px solid var(--hair);border-radius:9px;overflow:hidden;flex:none;}
  .seg button{appearance:none;border:0;background:var(--surface-2);color:var(--muted);
    font:600 12px ui-monospace,monospace;padding:7px 13px;cursor:pointer;border-right:1px solid var(--hair);}
  .seg button:last-child{border-right:0;}
  .seg button[aria-pressed="true"]{background:var(--accent);color:#fff;}
  .seg button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px;}
  .stats{display:flex;flex-wrap:wrap;gap:9px;margin-top:14px;}
  .chip{flex:1 1 118px;min-width:112px;background:var(--surface-2);border:1px solid var(--hair);
    border-radius:10px;padding:9px 12px;}
  .chip .k{font:600 10px/1.4 ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;
    color:var(--muted);display:flex;align-items:center;gap:6px;}
  .chip .v{font:640 18px/1.25 ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums;margin-top:3px;}
  .swatch{width:9px;height:9px;border-radius:2px;display:inline-block;flex:none;}
  .neg{color:var(--loss);} .pos{color:var(--win);}
  .charts{position:relative;padding:12px 12px 2px;}
  svg{display:block;width:100%;height:auto;touch-action:none;}
  .axis{fill:var(--muted);font:500 10.5px ui-monospace,monospace;font-variant-numeric:tabular-nums;}
  .plab{fill:var(--muted);font:600 10px ui-monospace,monospace;letter-spacing:.09em;text-transform:uppercase;}
  .gridline{stroke:var(--grid);stroke-width:1;}
  .zeroline{stroke:var(--zero);stroke-width:1;stroke-dasharray:4 4;opacity:.85;}
  .serie{fill:none;stroke-width:1.6;stroke-linejoin:round;stroke-linecap:round;}
  .s-nofee{stroke:var(--nofee);} .s-fee{stroke:var(--fee);}
  .hidden{display:none;}
  .ddarea{fill:var(--dd);opacity:.16;} .ddline{fill:none;stroke:var(--dd);stroke-width:1.2;opacity:.9;}
  .trade{stroke:none;} .cross{stroke:var(--muted);stroke-width:1;opacity:0;pointer-events:none;}
  .dot{opacity:0;stroke:var(--surface);stroke-width:1.4;pointer-events:none;}
  .legend{display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:4px 22px 16px;}
  .lg{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--muted);}
  .lg b{color:var(--ink);font-weight:600;}
  .tip{position:absolute;pointer-events:none;opacity:0;transform:translate(-50%,0);background:var(--surface);
    border:1px solid var(--hair);border-radius:10px;box-shadow:var(--shadow);padding:10px 12px;
    min-width:236px;z-index:5;transition:opacity .07s;}
  .tip .date{font:600 11px ui-monospace,monospace;color:var(--muted);letter-spacing:.03em;
    margin-bottom:7px;padding-bottom:6px;border-bottom:1px solid var(--hair);}
  .tip .row{display:flex;justify-content:space-between;gap:14px;align-items:baseline;
    font-variant-numeric:tabular-nums;padding:2.5px 0;}
  .tip .lab{font-size:11.5px;color:var(--muted);display:flex;align-items:center;gap:6px;}
  .tip .val{font:600 12.5px ui-monospace,monospace;}
  .tip .sub2{font:500 10.5px ui-monospace,monospace;color:var(--muted);}
  .tip .tsec{margin-top:7px;padding-top:7px;border-top:1px solid var(--hair);}
  .tip .thead{font:600 10px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;
    color:var(--muted);margin-bottom:4px;display:flex;justify-content:space-between;}
  .foot{padding:0 22px 20px;color:var(--muted);font-size:11.5px;}
  .foot code{font-family:ui-monospace,monospace;background:var(--surface-2);border:1px solid var(--hair);
    border-radius:5px;padding:1px 5px;}
</style></head><body>
<div class="wrap"><div class="card">
  <div class="head">
    <div class="toprow">
      <div>
        <div class="eyebrow" id="eyebrow"></div>
        <h1 id="title"></h1>
        <div class="sub">Equity, drawdown &amp; trades — no-fee vs 5 bps fee on one signal path</div>
      </div>
      <div class="seg" id="seg" role="group" aria-label="scenario">
        <button data-mode="both" aria-pressed="true">Both</button>
        <button data-mode="nofee" aria-pressed="false">No fee</button>
        <button data-mode="fee" aria-pressed="false">Fee 5bps</button>
      </div>
    </div>
    <div class="stats" id="stats"></div>
  </div>
  <div class="charts" id="charts">
    <svg id="eq" viewBox="0 0 1000 320" preserveAspectRatio="none" role="img" aria-label="Equity curve">
      <g id="eqgrid"></g><text class="plab" x="6" y="16">EQUITY (USDT)</text>
      <line class="cross" id="cx-eq" y1="10" y2="292"></line>
      <path class="serie s-nofee" id="p-nofee"></path>
      <path class="serie s-fee" id="p-fee"></path>
      <g id="tmarks"></g>
      <circle class="dot" id="d-nofee" r="3.6" fill="var(--nofee)"></circle>
      <circle class="dot" id="d-fee" r="3.6" fill="var(--fee)"></circle>
    </svg>
    <svg id="ddsvg" viewBox="0 0 1000 150" preserveAspectRatio="none" role="img" aria-label="Drawdown">
      <g id="ddgrid"></g><text class="plab" x="6" y="16">DRAWDOWN (%)</text>
      <path class="ddarea" id="ddarea"></path><path class="ddline" id="ddline"></path>
      <line class="cross" id="cx-dd" y1="4" y2="112"></line>
      <circle class="dot" id="d-dd" r="3.2" fill="var(--dd)"></circle>
      <g id="ddx"></g>
    </svg>
    <div class="tip" id="tip"></div>
  </div>
  <div class="legend">
    <span class="lg"><span class="swatch" style="background:var(--nofee)"></span><b>Equity — no fee</b></span>
    <span class="lg"><span class="swatch" style="background:var(--fee)"></span><b>Equity — fee 5bps</b></span>
    <span class="lg"><span class="swatch" style="background:var(--win);border-radius:50%"></span>win</span>
    <span class="lg"><span class="swatch" style="background:var(--loss);border-radius:50%"></span>loss (bubble size = volume)</span>
    <span class="lg" style="margin-left:auto">hover to inspect ↑</span>
  </div>
  <div class="foot" id="foot"></div>
</div></div>
<script>
const DATA=__DATA__;
const A=DATA.nofee,B=DATA.fee,TR=DATA.trades_list;
const N=A.t.length,tmin=A.t[0],tmax=A.t[N-1];
const $=id=>document.getElementById(id);
const W=1000,ML=64,MR=14,EQT=10,EQB=28,EQH=320,EPH=EQH-EQT-EQB,PW=W-ML-MR;
const DDH=150,DDT=10,DDB=38,DPH=DDH-DDT-DDB;
let ymin=Infinity,ymax=-Infinity;
for(const s of [A,B])for(const v of s.equity){if(v<ymin)ymin=v;if(v>ymax)ymax=v;}
ymin=Math.min(ymin,0);ymax=Math.max(ymax,100000);const pd=(ymax-ymin)*.05;ymin-=pd;ymax+=pd;
const X=t=>ML+(t-tmin)/(tmax-tmin)*PW, Y=v=>EQT+(ymax-v)/(ymax-ymin)*EPH;
const ddOf=s=>{const o=[];let pk=-Infinity;for(const v of s.equity){pk=Math.max(pk,v);o.push(pk>0?(v-pk)/pk*100:0);}return o;};
const ddN=ddOf(A),ddF=ddOf(B);
const com=A.equity.map((v,i)=>v-B.equity[i]);   // cumulative commission = nofee - fee
let ddmin=0;for(const v of ddN.concat(ddF))if(v<ddmin)ddmin=v;ddmin=Math.min(ddmin,-1);
const YD=v=>DDT+(0-v)/(0-ddmin)*DPH;
const fUSD=v=>{const a=Math.abs(v),s=v<0?"-":"";if(a>=1e6)return s+"$"+(a/1e6).toFixed(2)+"M";
  if(a>=1e3)return s+"$"+(a/1e3).toFixed(1)+"k";return s+"$"+a.toFixed(0);};
const fPct=v=>v==null?"—":(v>=0?"+":"")+(v*100).toFixed(1)+"%";
const fDate=s=>{const d=new Date(s*1000),p=n=>String(n).padStart(2,"0");
  return d.getUTCFullYear()+"-"+p(d.getUTCMonth()+1)+"-"+p(d.getUTCDate())+" "+p(d.getUTCHours())+":"+p(d.getUTCMinutes());};

$("eyebrow").textContent="BACKTEST · BTCUSDT PERP · 1m · 2024-07 → 2026-06";
$("title").textContent=DATA.strategy;
const winRate=TR.total?(TR.win.reduce((a,b)=>a+b,0)/TR.win.length*100):null;
const chip=(k,sw,v,cls)=>`<div class="chip"><div class="k">${sw?`<span class="swatch" style="background:${sw}"></span>`:""}${k}</div><div class="v ${cls||''}">${v}</div></div>`;
$("stats").innerHTML=
  chip("No-fee return","var(--nofee)",fPct(A.ret),A.ret<0?"neg":"pos")+
  chip("Fee 5bps return","var(--fee)",fPct(B.ret),B.ret<0?"neg":"pos")+
  chip("Total commission","",fUSD(B.commission||0),"neg")+
  chip("Max drawdown","",A.maxdd!=null?"-"+(A.maxdd*100).toFixed(1)+"%":"—","neg")+
  chip("Trades","",(A.trades??TR.total).toLocaleString())+
  chip("Win rate","",winRate!=null?winRate.toFixed(1)+"%":"—");

const TICKS=5;
for(let i=0;i<=TICKS;i++){const v=ymin+(ymax-ymin)*i/TICKS,y=Y(v);
  const cls=Math.abs(v)<(ymax-ymin)*.006?"zeroline":"gridline";
  $("eqgrid").insertAdjacentHTML("beforeend",
   `<line class="${cls}" x1="${ML}" x2="${W-MR}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"></line>`+
   `<text class="axis" x="${ML-8}" y="${(y+3.5).toFixed(1)}" text-anchor="end">${fUSD(v)}</text>`);}
function ddgrid(){$("ddgrid").innerHTML="";for(const v of [0,ddmin/2,ddmin]){const y=YD(v);
  $("ddgrid").insertAdjacentHTML("beforeend",
   `<line class="gridline" x1="${ML}" x2="${W-MR}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"></line>`+
   `<text class="axis" x="${ML-8}" y="${(y+3.5).toFixed(1)}" text-anchor="end">${v.toFixed(0)}%</text>`);}}
ddgrid();
for(let i=0;i<=6;i++){const t=tmin+(tmax-tmin)*i/6,x=X(t),d=new Date(t*1000);
  $("ddx").insertAdjacentHTML("beforeend",
   `<text class="axis" x="${x.toFixed(1)}" y="${DDH-14}" text-anchor="middle">${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,"0")}</text>`);}

const pathEq=s=>{let d="";for(let i=0;i<N;i++)d+=(i?"L":"M")+X(s.t[i]).toFixed(1)+" "+Y(s.equity[i]).toFixed(1);return d;};
$("p-nofee").setAttribute("d",pathEq(A));$("p-fee").setAttribute("d",pathEq(B));

function eqIdx(t){let lo=0,hi=N-1;while(lo<hi){const m=(lo+hi)>>1;if(A.t[m]<t)lo=m+1;else hi=m;}
  if(lo>0&&Math.abs(A.t[lo-1]-t)<Math.abs(A.t[lo]-t))lo--;return lo;}
// bubble radius from volume
let qmax=0;for(const q of TR.qty)if(q>qmax)qmax=q;qmax=qmax||1;
const rOf=q=>1.5+3.3*Math.sqrt(Math.max(q,0)/qmax);
let tm="";for(let i=0;i<TR.t.length;i++){const j=eqIdx(TR.t[i]);
  tm+=`<circle class="trade" cx="${X(A.t[j]).toFixed(1)}" cy="${Y(A.equity[j]).toFixed(1)}" r="${rOf(TR.qty[i]).toFixed(2)}" fill="${TR.win[i]?'var(--win)':'var(--loss)'}" opacity="0.5"></circle>`;}
$("tmarks").innerHTML=tm;

function drawDD(mode){const s=mode==="fee"?ddF:ddN;
  let da="M"+X(A.t[0]).toFixed(1)+" "+YD(0).toFixed(1),dl="";
  for(let i=0;i<N;i++){da+="L"+X(A.t[i]).toFixed(1)+" "+YD(s[i]).toFixed(1);dl+=(i?"L":"M")+X(A.t[i]).toFixed(1)+" "+YD(s[i]).toFixed(1);}
  da+="L"+X(A.t[N-1]).toFixed(1)+" "+YD(0).toFixed(1)+"Z";
  $("ddarea").setAttribute("d",da);$("ddline").setAttribute("d",dl);}
drawDD("nofee");

const capNote=TR.shown<TR.total?` (showing ${TR.shown.toLocaleString()} of ${TR.total.toLocaleString()} — even-sampled)`:"";
$("foot").innerHTML=`Both fee scenarios replay the <b>same</b> signal path, so trades &amp; gross PnL are identical — only commission differs. `+
  `Equity at <code>${N.toLocaleString()}</code> points (from <code>1,049,760</code> 1m bars); trades plotted${capNote}. Cumulative commission = no-fee − fee equity.`;

function trIdx(t){if(!TR.t.length)return -1;let lo=0,hi=TR.t.length-1;while(lo<hi){const m=(lo+hi)>>1;if(TR.t[m]<t)lo=m+1;else hi=m;}
  if(lo>0&&Math.abs(TR.t[lo-1]-t)<Math.abs(TR.t[lo]-t))lo--;return lo;}

// scenario toggle
let MODE="both";
function applyMode(m){MODE=m;
  $("p-nofee").classList.toggle("hidden",m==="fee");
  $("p-fee").classList.toggle("hidden",m==="nofee");
  drawDD(m);
  for(const b of $("seg").children)b.setAttribute("aria-pressed",b.dataset.mode===m);}
$("seg").addEventListener("click",e=>{const b=e.target.closest("button");if(b)applyMode(b.dataset.mode);});

const charts=$("charts"),eqsvg=$("eq"),tip=$("tip");
const cxEq=$("cx-eq"),cxDd=$("cx-dd"),dN=$("d-nofee"),dF=$("d-fee"),dD=$("d-dd");
function move(evt){const r=eqsvg.getBoundingClientRect();
  const cx=evt.clientX!==undefined?evt.clientX:(evt.touches&&evt.touches[0]?evt.touches[0].clientX:undefined);
  if(cx===undefined)return;
  let vx=Math.max(ML,Math.min(W-MR,(cx-r.left)/r.width*W));
  const t=tmin+(vx-ML)/PW*(tmax-tmin),i=eqIdx(t),gx=X(A.t[i]);
  for(const c of [cxEq,cxDd]){c.setAttribute("x1",gx);c.setAttribute("x2",gx);c.style.opacity=1;}
  const yN=Y(A.equity[i]),yF=Y(B.equity[i]),ddv=(MODE==="fee"?ddF:ddN)[i];
  dN.setAttribute("cx",gx);dN.setAttribute("cy",yN);dN.style.opacity=MODE==="fee"?0:1;
  dF.setAttribute("cx",gx);dF.setAttribute("cy",yF);dF.style.opacity=MODE==="nofee"?0:1;
  dD.setAttribute("cx",gx);dD.setAttribute("cy",YD(ddv));dD.style.opacity=1;
  const k=trIdx(A.t[i]);let tHtml="";
  if(k>=0){const wl=TR.win[k]?'pos':'neg';
    tHtml=`<div class="tsec"><div class="thead"><span>nearest trade #${k+1}</span>`+
      `<span class="${wl}">${TR.win[k]?'WIN':'LOSS'}</span></div>`+
      `<div class="row"><span class="lab">${TR.side[k]} · vol ${TR.qty[k]}</span>`+
        `<span class="val ${wl}">${fUSD(TR.pnl[k])}</span></div>`+
      `<div class="row"><span class="sub2">${fDate(TR.t[k])} → ${fDate(TR.xt[k])}</span></div>`+
      `<div class="row"><span class="sub2">entry ${fUSD(TR.ep[k])} → exit ${fUSD(TR.xp[k])}</span></div></div>`;}
  const rowN=`<div class="row"><span class="lab"><span class="swatch" style="background:var(--nofee)"></span>equity · no fee</span><span class="val">${fUSD(A.equity[i])}</span></div>`;
  const rowF=`<div class="row"><span class="lab"><span class="swatch" style="background:var(--fee)"></span>equity · fee 5bps</span><span class="val">${fUSD(B.equity[i])}</span></div>`;
  tip.innerHTML=`<div class="date">${fDate(A.t[i])} UTC</div>`+
    (MODE!=="fee"?rowN:"")+(MODE!=="nofee"?rowF:"")+
    `<div class="row"><span class="lab">realized pnl</span><span class="val">${fUSD(A.pnl[i])}</span></div>`+
    `<div class="row"><span class="lab">cum. commission</span><span class="val neg">${fUSD(com[i])}</span></div>`+
    `<div class="row"><span class="lab">drawdown</span><span class="val neg">${ddv.toFixed(2)}%</span></div>`+
    `<div class="row"><span class="lab">position</span><span class="val">${A.position[i]}</span></div>`+tHtml;
  const pr=charts.getBoundingClientRect(),relX=gx/W*r.width;let left=r.left-pr.left+relX;
  const tw=tip.offsetWidth;left=Math.max(tw/2+6,Math.min(pr.width-tw/2-6,left));
  tip.style.left=left+"px";
  const top=(r.top-pr.top)+(Math.min(yN,yF)/EQH)*r.height-tip.offsetHeight-12;
  tip.style.top=Math.max(4,top)+"px";tip.style.opacity=1;}
function leave(){for(const e of [cxEq,cxDd,dN,dF,dD])e.style.opacity=0;tip.style.opacity=0;}
charts.addEventListener("mousemove",move);
charts.addEventListener("mouseleave",leave);
charts.addEventListener("touchmove",e=>{move(e);e.preventDefault();},{passive:false});
charts.addEventListener("touchend",leave);
</script></body></html>"""


def render_strategy_charts(strategy_dir: str | Path) -> dict:
    """One call to build every artifact for a strategy from its saved PnL data.

    Per-run PNG panels (equity/drawdown/pnl/position) for each of ``nofee`` /
    ``fee_5bps``, a fee-vs-nofee overlay PNG, and the interactive HTML. Intended as
    the pipeline hook a batch/backtest driver calls once both fee scenarios are
    written, e.g. ``from results import render_strategy_charts``.
    """
    from results.charts import render_fee_compare, render_run_charts  # noqa: PLC0415

    strategy_dir = Path(strategy_dir)
    out: dict = {}
    for sub in ("nofee", "fee_5bps"):
        if (strategy_dir / sub / "equity_curve.csv").is_file():
            out[sub] = render_run_charts(strategy_dir / sub)
    out["fee_compare"] = render_fee_compare(strategy_dir)
    out["interactive"] = render_interactive(strategy_dir)
    return out


__all__ = ["render_interactive", "render_strategy_charts"]
