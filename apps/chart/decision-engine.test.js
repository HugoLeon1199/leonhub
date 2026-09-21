import {VERSION, features, evaluate, contextFacts} from './decision-engine.js';
function equal(a,b){if(JSON.stringify(a)!==JSON.stringify(b)) throw Error(`Expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);}
function near(a,b){if(Math.abs(a-b)>1e-8) throw Error(`${a} != ${b}`);}
function assert(a){if(!a) throw Error('Assertion failed');}
const now=Date.UTC(2026,8,21,12,1), hour=3600000;
function bars(n=500){return Array.from({length:n},(_,i)=>({time:(now-60000-(n-i)*hour)/1000,open:100+i,high:102+i,low:99+i,close:101+i}));}
function fixture(d=1){
  const f={count:500,seconds:3600,closedAt:now-60000,close:100+d*0.4,open:100,high:101,low:99,
    atr:1,adx:30,er:0.6,slope:d*0.6,ema20:100,ema50:99,direction:d,widthPct:50,upper:100,lower:100,
    breakouts:{[d===1?'up':'down']:{level:100,atr:1,at:now-60000,fresh:true,retest:false,invalid:false}}};
  return {asset:'BTC',now,source:{name:'fixture',kind:'binance-spot'},quote:{price:100+d*0.4,at:now},h1:f,h4:{...f,seconds:14400},context:[]};
}
Deno.test('Wilder trend, ATR and ER reference on monotonic bars',()=>{
  const f=features(bars(),3600,now);near(f.adx,100);near(f.atr,3);near(f.er,1);equal(f.direction,1);equal(f.upper,600);
});
Deno.test('Unfinished/future candle cannot move features or a verdict',()=>{
  const rows=bars(), f=features(rows,3600,now);
  const forming={time:(now-60000)/1000,open:10000,high:10001,low:1,close:2};
  equal(features([...rows,forming],3600,now),f);
  const input=fixture();equal(evaluate(input),evaluate(JSON.parse(JSON.stringify(input))));
});
Deno.test('Duplicates, gaps and corrupt OHLC rejected',()=>{
  for(const modify of [b=>b.splice(490,1),b=>b.push(b.at(-1)),b=>{b.at(-1).low=-1;}]){
    const b=bars();modify(b);let thrown=false;try{features(b,3600,now);}catch{thrown=true;}assert(thrown);
  }
});
Deno.test('Flat market is WAIT, not a low-ADX directional vote',()=>{
  const f=features(bars().map(b=>({...b,open:100,close:100,high:101,low:99})),3600,now);
  equal(f.adx,0);equal(f.er,0);equal(f.direction,0);
  const x=fixture();x.h1=f;x.h4={...f,seconds:14400};equal(evaluate(x).action,'WAIT');
});
Deno.test('BUY and SELL are symmetric and require an actual entry setup',()=>{
  equal(evaluate(fixture()).action,'BUY');equal(evaluate(fixture(-1)).action,'SELL');
  const x=fixture();x.h1.breakouts.up.fresh=false;equal(evaluate(x).action,'WAIT');
  x.h1.breakouts.up.retest=true;equal(evaluate(x).setup,'PULLBACK');equal(evaluate(x).action,'BUY');
});
Deno.test('Do not chase, trade an invalidated level or a weak/contradictory H4',()=>{
  for(const change of [x=>{x.quote.price=103;},x=>{x.quote.price=99;},x=>{x.h4.adx=5;},
    x=>{x.h4.er=.1;},x=>{x.h4.direction=-1;},x=>{x.h1.breakouts.up.invalid=true;}]){
    const x=fixture();change(x);equal(evaluate(x).action,'WAIT');
  }
});
Deno.test('Stale/missing/future mandatory inputs always WAIT',()=>{
  for(const change of [x=>{x.quote.at-=90001;},x=>{x.quote=null;},x=>{x.h1.closedAt=now+1;},
    x=>{x.h4.closedAt-=5*hour;},x=>{x.h1.atr=0;},x=>{x.h1.count=12;},x=>{x.h1.seconds=7200;},
    x=>{x.h1.closedAt=now-hour;},x=>{x.clockOK=false;},x=>{x.error='Source unavailable';},
    x=>{x.h1.breakouts.up.atr=NaN;},x=>{x.h1.breakouts.up.at=now+1;}]){
    const x=fixture();change(x);equal(evaluate(x).action,'WAIT');
  }
});
Deno.test('XAU must be explicit broker XAUUSD; GC=F never satisfies it',()=>{
  const x=fixture();x.asset='XAU';x.source.kind='comex';equal(evaluate(x).action,'WAIT');
  x.source.kind='broker-xauusd';equal(evaluate(x).action,'BUY');
});
Deno.test('Stale, future, missing and zero context remain distinguishable',()=>{
  const data={flows:{updated_at:new Date(now).toISOString(),stats:{btc:{latest:'2026-09-18',sum_5d:0,latest_musd:0}}}};
  const etf=d=>contextFacts('BTC',d,now).find(x=>x.label==='ETF BTC');
  equal(etf(data).status,'ok');assert(etf(data).value.includes('0 triệu'));
  data.flows.stats.btc.latest='2026-09-04';equal(etf(data).status,'stale');
  data.flows.stats.btc.latest='2026-09-22';equal(etf(data).status,'future');
  equal(etf({}).status,'missing');
  const x=fixture();x.context=contextFacts('BTC',{},now);equal(evaluate(x).action,'BUY');
});
Deno.test('New artifact timestamp cannot launder stale observation dates',()=>{
  const data={context:{updated_at:new Date(now).toISOString(),mvrv:{value:1.5,as_of:'2026-09-14'},fng:{value:70}}};
  const f=contextFacts('BTC',data,now);equal(f.find(x=>x.label==='BTC MVRV').status,'stale');
  equal(f.find(x=>x.label==='Fear & Greed').status,'missing');
});
Deno.test('Serialized rule inputs replay the complete verdict',()=>{
  const x=fixture();const stored=JSON.parse(JSON.stringify({input:x,result:evaluate(x)}));
  equal(evaluate(stored.input),stored.result);equal(stored.result.version,VERSION);
});
