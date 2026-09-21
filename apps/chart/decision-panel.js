import {VERSION, features, evaluate, contextFacts} from './decision-engine.js?v=1';

const ASSETS=['BTC','ETH','XAU'], labels={BUY:'MUA',SELL:'BÁN',WAIT:'CHỜ'};
const strip=document.getElementById('decision-strip'), cards=document.getElementById('decision-cards');
const dialog=document.getElementById('decision-dialog'), body=document.getElementById('decision-body');
const refreshButton=document.getElementById('decision-refresh');
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=x=>Number.isFinite(x)?x.toLocaleString('vi-VN',{maximumFractionDigits:2}):'—';
const stamp=x=>x && Number.isFinite(new Date(x).getTime())?new Date(x).toLocaleString('vi-VN',{timeZoneName:'short'}):'chưa có';
const KEY='leon_chart_decisions_v1';
let inputs={}, decisions={}, raw={}, selected=null, busy=false, lastRefresh=0, macroAt=0, clockOK=false, storageOK=true;
const priceCache=new Map();

async function json(url){
  const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(12000)});
  if(!r.ok) throw Error(`HTTP ${r.status}`);
  return r.json();
}
async function crypto(asset,now){
  const pair=asset+'USDT', cache=priceCache.get(asset), hour=Math.floor(now/3600000);
  const parse=rows=>{
    if(!Array.isArray(rows)) throw Error('Thiếu nến Binance');
    return rows.map(r=>({time:r[0]/1000,closeTime:r[6]+1,open:+r[1],high:+r[2],low:+r[3],close:+r[4]}));
  };
  const quotePromise=json(`https://api.binance.com/api/v3/ticker/24hr?symbol=${pair}`);
  const current=cache?.hour===hour && cache.h1.closedAt===hour*3600000 && cache.h4.closedAt===Math.floor(now/14400000)*14400000;
  const barPromise=current?Promise.resolve(cache):Promise.all(['1h','4h'].map(tf=>json(
    `https://api.binance.com/api/v3/klines?symbol=${pair}&interval=${tf}&limit=500`))).then(([h1,h4])=>{
      const value={hour,h1:features(parse(h1),3600,now),h4:features(parse(h4),14400,now)};
      priceCache.set(asset,value);return value;
    });
  const [q,f]=await Promise.all([quotePromise,barPromise]);
  if(q.symbol!==pair) throw Error('Nguồn trả sai mã');
  return {asset,source:{name:'Binance Spot · '+pair,kind:'binance-spot',url:'https://www.binance.com/en/trade/'+asset+'_USDT'},
    quote:{price:+q.lastPrice,at:+q.closeTime},h1:f.h1,h4:f.h4};
}

async function xau(){
  // The compact broker snapshot contains features, never account identifiers,
  // candles, positions or a replacement gold instrument. No fallback to GC=F.
  const d=await json('../../data/chart/xau.json');
  if(d.version!==VERSION || d.asset!=='XAU' || d.source?.kind!=='broker-xauusd' || !d.quote) throw Error('Chưa có bản chụp XAUUSD đúng phiên bản');
  return d;
}
async function context(){
  const paths={fx:'fx.json',flows:'flows.json',context:'crypto/context.json',gex_btc:'gex_btc.json',gex_eth:'gex_eth.json'};
  await Promise.all(Object.entries(paths).map(async([key,path])=>{
    try{raw[key]=await json('../../data/'+path);}catch{raw[key]=null;}
  }));
  macroAt=Date.now();
}

function log(input,result){
  try{
    const old=JSON.parse(localStorage.getItem(KEY)||'[]');
    const rows=Array.isArray(old)?old:[];
    const previous=rows.findLast(r=>r.result?.asset===result.asset);
    const key=r=>[r.action,r.setup,r.h1?.closedAt,r.h4?.closedAt,r.reason,JSON.stringify(r.context)].join('|');
    if(previous && key(previous.result)===key(result)) return;
    rows.push({input,result});
    localStorage.setItem(KEY,JSON.stringify(rows.slice(-240)));
    storageOK=true;
  }catch{storageOK=false;}
}
function compute(){
  const now=Date.now();
  for(const asset of ASSETS){
    const input={...inputs[asset],asset,now,clockOK,context:contextFacts(asset,raw,now)};
    const result=evaluate(input);
    decisions[asset]=result;
    log(input,result);
  }
  render();
}
function render(){
  for(const asset of ASSETS){
    const r=decisions[asset], card=cards.querySelector(`[data-decision="${asset}"]`);
    if(!r || !card) continue;
    card.setAttribute('aria-current',String(selected===asset));
    card.setAttribute('aria-label',`${asset}: ${labels[r.action]}. ${r.reason}. Xem chart và lý do.`);
    card.innerHTML=`<span class="decision-card-top"><b>${asset}</b><span class="decision-action ${r.action.toLowerCase()}">${labels[r.action]}</span></span>`+
      `<span class="decision-reason">${esc(r.reason)}</span>`+
      `<span class="decision-meta">${r.h1?`H1 đóng ${esc(new Date(r.h1.closedAt).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'}))} · ADX ${num(r.h1.adx)}`:'Đang chờ dữ liệu đúng nguồn'} · Xem lý do ↗</span>`;
  }
  if(dialog.open && selected) renderDetails(decisions[selected]);
}
function renderDetails(r){
  const focused=document.activeElement?.id;
  document.getElementById('decision-title').textContent=`${r.asset} · ${labels[r.action]} · H1 theo xu hướng H4`;
  const rows=[['Giá đóng','close'],['EMA20','ema20'],['EMA50','ema50'],['ATR14','atr'],['ADX14 ≥ 22','adx'],['ER20 ≥ 0,35','er'],['Độ dốc EMA20 / ATR (5 nến)','slope'],['Biên trên 20 nến trước','upper'],['Biên dưới 20 nến trước','lower']];
  body.innerHTML=`<p><strong>${esc(r.reason)}</strong></p><p>Giá hiện tại: <b>${num(r.quote?.price)}</b> · ${esc(r.source?.name||'Chưa có nguồn giá')}<br><small>Giá lúc ${esc(stamp(r.quote?.at))}; đánh giá ${esc(stamp(r.at))}. Chỉ xét nến đã đóng.</small></p>`+
    `<h3>Kỹ thuật: điều kiện vào lệnh mới</h3>`+
    (r.h1 && r.h4?`<table class="decision-metrics"><thead><tr><th>Chỉ tiêu</th><th>H1</th><th>H4</th></tr></thead><tbody>${rows.map(([label,key])=>`<tr><th>${label}</th><td>${num(r.h1[key])}</td><td>${num(r.h4[key])}</td></tr>`).join('')}</tbody></table>`+
      `<p><small>H1 đóng ${esc(stamp(r.h1.closedAt))} · H4 đóng ${esc(stamp(r.h4.closedAt))}. Độ rộng Bollinger H1 ở phân vị ${num(r.h1.widthPct)}% của 250 nến trước; ≤20% là nén biên.</small></p>`:
      '<p>Chưa đủ nến H1/H4 đúng mã để kết luận. XAU cần nguồn XAUUSD của broker; giá COMEX chỉ là bối cảnh.</p>')+
    `<p><b>Điều kiện tiếp theo:</b> ${esc(r.next)}</p>`+
    (r.level?`<p>Biên đã phá: <b>${num(r.level)}</b>. Mất điều kiện khi giá ${r.direction===1?'≤':'≥'} <b>${num(r.invalidation)}</b>. Đây là mức hủy tín hiệu, chưa phải stop-loss cho EA.</p>`:'')+
    `<h3>Vĩ mô và dòng tiền đã thu thập</h3><p><small>Giải thích bối cảnh; các yếu tố này chưa được kiểm chứng để cộng điểm hoặc tự đảo chiều tín hiệu kỹ thuật. Dữ liệu cũ/thiếu bị loại khỏi phần nhận định.</small></p>`+
    `<ul class="decision-facts">${r.context.map(f=>`<li class="${f.status==='ok'?'':'unavailable'}"><b>${esc(f.label)}</b>: ${esc(f.value)} <strong>${({ok:'',stale:' · CŨ — không dùng',missing:' · THIẾU — không dùng',future:' · SAI THỜI GIAN — không dùng'})[f.status]}</strong><small>Quan sát ${esc(stamp(f.observed))} · thu thập ${esc(stamp(f.fetched))} · <a href="${esc(f.url)}" target="_blank" rel="noopener">Nguồn</a></small><small>${esc(f.note)}</small></li>`).join('')}</ul>`+
    `<h3>Điểm trái chiều và dữ liệu còn thiếu</h3><p>${r.counter.length?r.counter.map(f=>`${esc(f.label)} đang ngược chiều xu hướng H4 (${esc(f.value)}).`).join(' '):'Chưa thấy yếu tố ngược chiều trong các dòng ETF/DXY đủ mới; không có nghĩa rủi ro thấp.'}</p>`+
    '<p>Chưa đưa OI/funding và dòng lệnh quan sát trong phiên vào bộ lọc vì chưa có lịch sử đồng bộ. Lịch tin chưa tự khóa lệnh; xem <a href="https://www.bls.gov/schedule/" target="_blank" rel="noopener">BLS</a> và <a href="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm" target="_blank" rel="noopener">Fed</a>.</p>'+
    `<p class="decision-warning">Bộ quy tắc thử nghiệm ${VERSION}; chưa kiểm chứng lợi nhuận với toàn bộ EA. MUA/BÁN là điều kiện kỹ thuật tham khảo, không tự bật EA hay đặt lệnh. EA có cả nhánh hồi/đảo chiều; cần kiểm thử từng PP. Không phải khuyến nghị đầu tư.</p>`+
    `<p><small>Lịch sử tối đa 240 lần đổi trạng thái/nến, chỉ khi mở trang và chỉ trên trình duyệt này. ${storageOK?'Đang lưu.':'Không lưu được; trình duyệt đã chặn hoặc hết dung lượng.'}</small></p>`+
    '<button type="button" id="decision-export">Tải lịch sử và số liệu đánh giá</button> <a href="../../docs/chart-decision-panel-spec.md" target="_blank" rel="noopener">Quy tắc &amp; ghi chú</a>';
  document.getElementById('decision-export').onclick=()=>{
    let stored='[]';try{stored=localStorage.getItem(KEY)||'[]';}catch{stored=JSON.stringify(Object.values(decisions));}
    const url=URL.createObjectURL(new Blob([stored],{type:'application/json'}));
    const a=document.createElement('a'); a.href=url;a.download='leon-chart-decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  if(focused==='decision-export') document.getElementById(focused).focus({preventScroll:true});
}

async function refresh(){
  if(busy) return;
  busy=true;refreshButton.disabled=true;
  try{
    const now=Date.now();
    const tasks=ASSETS.map(async asset=>{
      try{inputs[asset]=asset==='XAU'?await xau():await crypto(asset,now);}
      catch{inputs[asset]={error:asset==='XAU'?'Thiếu nguồn XAUUSD của broker':'Không tải được giá/nến Binance'};}
    });
    tasks.push(json('https://api.binance.com/api/v3/time').then(d=>{clockOK=Math.abs(d.serverTime-Date.now())<30000;}).catch(()=>{clockOK=false;}));
    if(now-macroAt>300000) tasks.push(context());
    await Promise.all(tasks);
    lastRefresh=Date.now();compute();
  }finally{busy=false;refreshButton.disabled=false;}
}
cards.addEventListener('click',e=>{
  const card=e.target.closest('[data-decision]');if(!card) return;
  selected=card.dataset.decision;
  window.leonSelectDecisionAsset?.(selected);
  renderDetails(decisions[selected]);render();dialog.showModal();
});
document.getElementById('decision-close').onclick=()=>dialog.close();
refreshButton.onclick=()=>{priceCache.clear();refresh();};
document.addEventListener('visibilitychange',()=>{if(!document.hidden){compute();refresh();}});
setInterval(()=>{compute();if(!document.hidden && Date.now()-lastRefresh>=45000) refresh();},15000);
strip.hidden=false;compute();refresh();
