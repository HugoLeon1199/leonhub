// Pure, versioned research rule. No network, clock, storage or order side effects.
export const VERSION = 'trend-gate-1.0.0';
export const RULE = Object.freeze({adx:22, er:0.35, slope:0.05, breakout:0.1, retest:0.25, chase:1, quoteAge:90000});
const finite = Number.isFinite;
const last = a => a.at(-1);
const mean = a => a.reduce((s,x)=>s+x,0)/a.length;

function smooth(a, n, alpha){
  const out = Array(a.length).fill(null);
  if(a.length < n) return out;
  out[n-1] = mean(a.slice(0,n));
  for(let i=n;i<a.length;i++) out[i] = out[i-1]+alpha*(a[i]-out[i-1]);
  return out;
}

// Exchange closeTime is inclusive; normalize to an exclusive close boundary.
// A malformed or duplicated bar invalidates the input rather than being hidden.
export function closedBars(rows, seconds, now){
  if(!Array.isArray(rows)) throw Error('Thiếu chuỗi nến');
  let previous = -Infinity;
  return rows.filter(b=>{
    if(![b.time,b.open,b.high,b.low,b.close].every(finite) || b.time<=previous ||
       b.low<=0 || b.high<Math.max(b.open,b.close) || b.low>Math.min(b.open,b.close)) throw Error('Nến sai hoặc trùng thời gian');
    previous = b.time;
    return (b.closeTime ?? (b.time+seconds)*1000)<=now;
  });
}

export function features(rows, seconds, now, continuous=true){
  const bars = closedBars(rows,seconds,now);
  if(bars.length<280) throw Error('Cần ít nhất 280 nến đã đóng');
  if(continuous && bars.some((b,i)=>i && b.time-bars[i-1].time!==seconds)) throw Error('Chuỗi nến bị khuyết');
  const close = bars.map(b=>b.close), ema20=smooth(close,20,2/21), ema50=smooth(close,50,2/51);
  const tr=[], plus=[], minus=[];
  for(let i=1;i<bars.length;i++){
    const b=bars[i], p=bars[i-1], up=b.high-p.high, down=p.low-b.low;
    tr.push(Math.max(b.high-b.low,Math.abs(b.high-p.close),Math.abs(b.low-p.close)));
    plus.push(up>down && up>0?up:0); minus.push(down>up && down>0?down:0);
  }
  const atr=smooth(tr,14,1/14), p=smooth(plus,14,1/14), m=smooth(minus,14,1/14);
  const dx=atr.slice(13).map((a,i)=>p[i+13]+m[i+13] ? 100*Math.abs(p[i+13]-m[i+13])/(p[i+13]+m[i+13]) : 0);
  const adx=last(smooth(dx,14,1/14)), a=last(atr), b=last(bars), n=bars.length-1;
  const erDen=close.slice(-20).reduce((s,c,i)=>s+Math.abs(c-close[n-20+i]),0);
  const er=erDen?Math.abs(close[n]-close[n-20])/erDen:0;
  const widths=[];
  for(let i=19;i<bars.length;i++){
    const x=close.slice(i-19,i+1), avg=mean(x);
    widths.push(4*Math.sqrt(mean(x.map(v=>(v-avg)**2)))/avg);
  }
  const reference=widths.slice(-251,-1), widthPct=100*reference.filter(w=>w<=last(widths)).length/reference.length;
  const channel=i=>({high:Math.max(...bars.slice(i-20,i).map(x=>x.high)),low:Math.min(...bars.slice(i-20,i).map(x=>x.low))});
  const prior=channel(n), slope=(ema20[n]-ema20[n-5])/(a||1);
  const direction=b.close>ema20[n] && ema20[n]>ema50[n] && slope>RULE.slope?1:
    b.close<ema20[n] && ema20[n]<ema50[n] && slope<-RULE.slope?-1:0;
  const breakouts={up:null,down:null};
  for(const d of [1,-1]){
    // Freeze the most recent eligible breakout's prior-20-bar level and ATR.
    for(let i=n;i>=n-12;i--){
      const level=d===1?channel(i).high:channel(i).low, ai=atr[i-1];
      if(d*(bars[i].close-level)>RULE.breakout*ai){
        const invalid=bars.slice(i+1).some(x=>d*(x.close-level)<-RULE.retest*ai);
        const touch=d===1?b.low<=level+RULE.retest*ai:b.high>=level-RULE.retest*ai;
        const reclaim=d*(b.close-level)>RULE.breakout*ai && d*(b.close-b.open)>0;
        breakouts[d===1?'up':'down']={level,atr:ai,at:(bars[i].time+seconds)*1000,
          fresh:i===n,retest:i<n && touch && reclaim && !invalid,invalid};
        break;
      }
    }
  }
  return {count:bars.length,closedAt:(b.time+seconds)*1000,seconds,close:b.close,open:b.open,
    high:b.high,low:b.low,ema20:last(ema20),ema50:last(ema50),atr:a,adx,er,slope,direction,
    widthPct,upper:prior.high,lower:prior.low,breakouts};
}

export function evaluate(input){
  const {asset,now,source,quote,h1,h4,context=[]}=input;
  const out={version:VERSION,asset,at:now,action:'WAIT',direction:0,setup:'DATA',source,
    reason:'Thiếu giá và nến đã đóng',next:'Chờ nguồn giá đúng mã cập nhật lại.',h1,h4,quote,context,
    counter:[],invalidation:null};
  if(!['BTC','ETH','XAU'].includes(asset) || !finite(now)) return out;
  if(input.clockOK===false && asset!=='XAU'){
    out.reason='Chưa xác minh được giờ máy với nguồn giá';return out;
  }
  if(input.error){out.reason=input.error;return out;}
  if(asset==='XAU' && source?.kind!=='broker-xauusd'){
    out.reason='Thiếu nguồn XAUUSD của broker'; return out;
  }
  if(!source || ![h1,h4].every(f=>f && ['close','atr','adx','er','slope','ema20','ema50','closedAt','upper','lower'].every(k=>finite(f[k])) &&
    f.atr>0 && f.count>=280 && f.adx>=0 && f.adx<=100 && f.er>=0 && f.er<=1 && [-1,0,1].includes(f.direction))) return out;
  if(h1.seconds!==3600 || h4.seconds!==14400 || [h1,h4].some(f=>f.closedAt>now || now-f.closedAt>=f.seconds*1000)){
    out.reason='Nến H1/H4 cũ hoặc sai thời gian'; return out;
  }
  if(!quote || !finite(quote.price) || quote.price<=0 || !finite(quote.at) || quote.at>now+5000 || now-quote.at>RULE.quoteAge){
    out.reason='Giá hiện tại chưa cập nhật trong 90 giây'; return out;
  }
  out.direction=h4.direction;
  out.counter=context.filter(f=>f.status==='ok' && f.bias && f.bias===-out.direction);
  const d=h4.direction;
  out.next=`Chờ H4 đồng thuận EMA20/50, ADX ≥ ${RULE.adx}, ER ≥ ${RULE.er}; H1 phá biên hoặc hồi xác nhận.`;
  if(!d || h4.adx<RULE.adx || h4.er<RULE.er){
    out.setup=h1.widthPct<=20?'COMPRESSION':'RANGE';
    out.reason=h1.widthPct<=20?'Đang nén biên, chưa có xu hướng H4 đủ mạnh':'H4 chưa xác lập xu hướng đủ mạnh';
    return out;
  }
  if(h1.direction!==d || h1.adx<RULE.adx || h1.er<RULE.er){
    out.setup='ALIGNMENT'; out.reason='H1 chưa đồng thuận hoặc động lượng còn yếu'; return out;
  }
  const br=h1.breakouts?.[d===1?'up':'down'];
  out.setup='WAIT_BREAK';
  out.reason='Có xu hướng, chờ phá biên hoặc hồi xác nhận';
  out.next=`Chờ nến H1 đóng ${d===1?'trên':'dưới'} biên 20 nến ${d===1?h1.upper:h1.lower} ± 0,1 ATR, hoặc hồi về biên đã phá.`;
  if(!br || br.invalid || !(br.fresh || br.retest)) return out;
  if(![br.level,br.atr,br.at].every(finite) || br.atr<=0 || br.level<=0 || br.at>h1.closedAt){
    out.setup='DATA';out.reason='Mốc phá biên sai hoặc thiếu dữ liệu';return out;
  }
  const distance=d*(quote.price-br.level)/br.atr;
  out.level=br.level; out.invalidation=br.level-d*RULE.retest*br.atr;
  out.next=`Chờ hồi trong 0,25 ATR quanh biên ${br.level}, rồi đóng H1 xác nhận cùng chiều.`;
  if(d*(quote.price-out.invalidation)<=0){out.setup='INVALID';out.reason='Giá hiện tại đã mất biên xác nhận';return out;}
  if(distance>RULE.chase || Math.abs(quote.price-h1.close)>0.5*h1.atr || Math.abs(quote.price-h1.ema20)>2*h1.atr){
    out.setup='EXTENDED';out.reason='Giá đã chạy xa, chờ hồi để tránh đuổi giá';return out;
  }
  if(distance<RULE.breakout){out.setup='WAIT_RECLAIM';out.reason='Giá hiện tại chưa giữ được phía ngoài biên';return out;}
  out.action=d===1?'BUY':'SELL'; out.setup=br.fresh?'BREAKOUT':'PULLBACK';
  out.reason=br.fresh?'H1 phá biên, đồng thuận xu hướng H4':'H1 hồi và giữ biên đã phá, đồng thuận H4';
  out.next='Đánh giá lại khi H1 đóng; dừng xét lệnh mới nếu giá mất biên hoặc nguồn giá hết hạn.';
  return out;
}

// Optional context never fills missing prices or becomes a spurious zero.
// Both retrieval time and observation time must predate this evaluation.
export function contextFacts(asset, data, now){
  const facts=[];
  const add=(label,value,observed,fetched,ttl,url,bias=0,note='')=>{
    const o=Date.parse(observed), f=Date.parse(fetched);
    const status=!finite(o)||!finite(f)||value==null?'missing':o>now || f>now?'future':now-o>ttl || now-f>ttl?'stale':'ok';
    facts.push({label,value:value??null,observed:observed??null,fetched:fetched??null,status,url,bias:status==='ok'?bias:0,note});
  };
  const day=86400000, fx=data.fx;
  for(const code of ['dxy',...(asset==='XAU'?['gold']:[])]){
    const r=Array.isArray(fx?.rows)?fx.rows.find(x=>x?.s===code):null;
    const hist=Array.isArray(r?.hist)?r.hist:[], end=hist.at(-1), prev=hist.at(-2);
    const pct=end && prev?(end[1]/prev[1]-1)*100:null;
    add(code==='dxy'?'DXY • đóng cửa':'COMEX GC=F • tham chiếu',end && finite(pct)?`${end[1]} (${pct.toFixed(2)}%/phiên)`:null,
      end?.[0],fx?.updated_at,4*day,code==='dxy'?'https://finance.yahoo.com/quote/DX-Y.NYB/':'https://finance.yahoo.com/quote/GC=F/',
      code==='dxy'&&Math.abs(pct)>=0.2?-Math.sign(pct):0,
      code==='dxy'?'USD tăng thường gây sức ép; tương quan có thể đổi.':'Vàng tương lai, không phải giá XAUUSD để vào lệnh.');
  }
  if(asset!=='XAU'){
    const f=data.flows?.stats?.[asset.toLowerCase()];
    add(`ETF ${asset}`,finite(f?.sum_5d)&&finite(f?.latest_musd)?`${f.latest_musd} triệu USD/ngày; 5 ngày nguồn công bố ${f.sum_5d} triệu USD`:null,
      f?.latest,data.flows?.updated_at,5*day,`https://farside.co.uk/${asset==='BTC'?'bitcoin':'ethereum'}-etf-flow-all-data/`,Math.sign(f?.sum_5d||0),'Dòng vốn đã công bố, không dự báo điểm phá biên.');
    const g=data[`gex_${asset.toLowerCase()}`];
    add('GEX Deribit',g?.contracts>0 && finite(g?.net_gex)?`${(g.net_gex/1e6).toFixed(1)} triệu USD / 1%; ${g.contracts} hợp đồng; flip ${g.gamma_flip??'—'}`:null,
      g?.updated_at,g?.updated_at,8*3600000,'https://www.deribit.com/',0,'Dấu dealer là giả định mô hình; không dùng để quyết định chiều.');
    const c=data.context;
    add('Fear & Greed',finite(c?.fng?.value)?`${c.fng.value}/100`:null,c?.fng?.as_of,c?.updated_at,2*day,
      'https://alternative.me/crypto/fear-and-greed-index/',0,'Tâm lý crypto; không thay thế tín hiệu H1.');
    if(asset==='BTC') add('BTC MVRV',c?.mvrv?.value,c?.mvrv?.as_of,c?.updated_at,3*day,'https://bitcoin-data.com/',0,'Định giá on-chain; không phải bộ định thời vào lệnh.');
  }
  add('Lịch CPI / NFP / FOMC',null,null,null,0,'https://www.bls.gov/schedule/','',
    'Chưa có lịch tin tự động. Kiểm tra lịch BLS và Fed trước khi cho EA vào lệnh.');
  return facts;
}
