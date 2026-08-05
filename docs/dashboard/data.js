// ===== E2 dashboard data — derived from log/*.jsonl, latest log 2026-08-05 =====
// Regenerate: python3 scripts/build_dashboard_data.py  (--check verifies byte-identity). NEVER hand-edit.
const AS_OF = "2026-08-05";
const SIG = [
{d:"2026-08-03",run:"2026-08-04",px:86.62,sma200:76.7374,sma20:87.023,vol:0.482231,hi:0.640699,volmax60:0.695808,trend:true,offpeak:true,alloc:1,sha:null,src:null,bars:2660},
{d:"2026-08-03",run:"2026-08-04",px:86.62,sma200:76.7374,sma20:87.023,vol:0.482231,hi:0.640699,volmax60:0.695808,trend:true,offpeak:true,alloc:1,sha:"40716df9d3ba5a333eee03d7a7491bb747e93812b34e9b57083feec1a165a410",src:"https://data.alpaca.markets/v2/stocks/QLD/bars?timeframe=1Day&adjustment=all&feed=sip&start=2006-06-01&end=2026-08-03T23%3A59%3A59Z&limit=10000&sort=asc",bars:2660},
{d:"2026-08-04",run:"2026-08-05",px:92.46,sma200:76.8566,sma20:87.1615,vol:0.525578,hi:0.640632,volmax60:0.695808,trend:true,offpeak:true,alloc:1,sha:"bde2f0ecc2e178cef04e49a08725aeda9ab3f10d0efab928de38e126e7082ad2",src:"https://data.alpaca.markets/v2/stocks/QLD/bars?timeframe=1Day&adjustment=all&feed=sip&start=2006-06-01&end=2026-08-04T23%3A59%3A59Z&limit=10000&sort=asc",bars:2661},
];
const TRD = [
{run:"2026-08-04T15:43:31+00:00",runEt:"2026-08-04",sig:"2026-08-03",alloc:1,action:"HALTED by HALT file — no order",orderId:null,dry:false,equity:null,refPx:null,curQty:null,tgtQty:null,lastActed:null,haltReason:"halted 2026-08-04: pre-go-live — routine plumbing test only, no orders until e2bot-08 (account reset to $100k pending)"},
{run:"2026-08-05T05:53:29+00:00",runEt:"2026-08-05",sig:"2026-08-04",alloc:1,action:"HALTED by HALT file — no order",orderId:null,dry:false,equity:null,refPx:null,curQty:null,tgtQty:null,lastActed:null,haltReason:"halted 2026-08-04: pre-go-live — routine plumbing test only, no orders until e2bot-08 (account reset to $100k pending)"},
];
const OPS = [
{at:"2026-08-04T12:17:23+00:00",d:"2026-08-04",ev:"note",note:"e2bot-07: audit convention (AUDIT.md) + runbook adopted; ops log initialized"},
{at:"2026-08-04T12:40:24+00:00",d:"2026-08-04",ev:"halt",note:"2026-08-04 pre-go-live halt: testing routine plumbing (clone, env, commit, push) with ordering suppressed"},
{at:"2026-08-04T12:54:14+00:00",d:"2026-08-04",ev:"manual",note:"Alpaca paper account switched to PA3KZ09KPX46 (created 2026-08-04, $100,000, 0 orders, 0 positions); old PA3105UW4264 retained with BOT2.0 history intact (snapshot: QLD-model assets/bot20-alpaca-order-history-2026-08-02.json). New account is the E2 forward-test witness."},
{at:"2026-08-04T14:15:09+00:00",d:"2026-08-04",ev:"failure",note:"2026-08-04 engine/signal_engine.py: stale data guard tripped - last bar 2026-08-04 (today, session still open) vs last completed session 2026-08-03; no signal, no order"},
{at:"2026-08-05T06:12:15+00:00",d:"2026-08-05",ev:"resume",note:"go-live: HALT lifted for e2bot-08 after --dry-run proved the execution path (equity 100000, 0 QLD, would submit MOC buy 1081 QLD @ ref 92.46, signal 2026-08-04 alloc=1.0); first real order expected on the 12:00 ET run 2026-08-05"},
];
const ANCHOR = null; // no live equity record yet — pre-first-run
const COVERAGE = [
{d:"2026-08-04",st:"log"},
{d:"2026-08-05",st:"log"},
];
