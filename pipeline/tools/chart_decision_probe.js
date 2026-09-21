// Bounded, read-only integration probe. No candle history is written to git.
// deno run --allow-net=api.binance.com --allow-read=data pipeline/tools/chart_decision_probe.js
import {features,evaluate,contextFacts} from '../../apps/chart/decision-engine.js';
const raw={};
for(const [key,path] of Object.entries({fx:'fx.json',flows:'flows.json',context:'crypto/context.json',gex_btc:'gex_btc.json',gex_eth:'gex_eth.json'})){
  raw[key]=JSON.parse(await Deno.readTextFile('data/'+path));
}
async function get(path){
  const r=await fetch('https://api.binance.com/api/v3/'+path,{signal:AbortSignal.timeout(15000)});
  if(!r.ok) throw Error(`${path}: HTTP ${r.status}`);return r.json();
}
const server=await get('time'), now=Date.now(), clockOK=Math.abs(server.serverTime-now)<30000;
if(!clockOK) throw Error('Clock differs from Binance by more than 30 seconds');
for(const asset of ['BTC','ETH']){
  const [a,b,q]=await Promise.all(['klines?symbol='+asset+'USDT&interval=1h&limit=500',
    'klines?symbol='+asset+'USDT&interval=4h&limit=500','ticker/24hr?symbol='+asset+'USDT'].map(get));
  const parse=r=>r.map(x=>({time:x[0]/1000,closeTime:x[6]+1,open:+x[1],high:+x[2],low:+x[3],close:+x[4]}));
  const input={asset,now:Date.now(),clockOK,source:{kind:'binance-spot',name:asset+'USDT Binance Spot'},
    quote:{price:+q.lastPrice,at:+q.closeTime},h1:features(parse(a),3600,now),h4:features(parse(b),14400,now),
    context:contextFacts(asset,raw,now)};
  const result=evaluate(input);
  if(JSON.stringify(evaluate(JSON.parse(JSON.stringify(input))))!==JSON.stringify(result)) throw Error('Replay mismatch');
  console.log(JSON.stringify({asset,action:result.action,reason:result.reason,version:result.version,
    at:new Date(input.now).toISOString(),h1:{close:input.h1.close,adx:input.h1.adx,er:input.h1.er,closedAt:input.h1.closedAt},
    h4:{close:input.h4.close,adx:input.h4.adx,er:input.h4.er,closedAt:input.h4.closedAt},quote:input.quote,
    context:result.context.map(f=>({label:f.label,value:f.value,status:f.status,observed:f.observed}))}));
}
