// Same feature implementation as the browser; candles only pass through stdin.
import {VERSION,features} from '../../apps/chart/decision-engine.js';
const text=await new Response(Deno.stdin.readable).text(), d=JSON.parse(text);
if(d.asset!=='XAU' || d.source?.kind!=='broker-xauusd') throw Error('Expected broker XAUUSD');
const out={version:VERSION,asset:d.asset,source:d.source,quote:d.quote,
  h1:features(d.h1,3600,d.now,false),h4:features(d.h4,14400,d.now,false),
  updated_at:new Date(d.now).toISOString(),status:'snapshot'};
console.log(JSON.stringify(out));
