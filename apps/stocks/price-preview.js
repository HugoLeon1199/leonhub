// VPS UDF prices are thousands of VND; timestamps are seconds, not milliseconds.
export function parseHistory(payload){
  if(payload?.s !== "ok" || !Array.isArray(payload.t)) throw Error("no_data");
  const bars = new Map();
  payload.t.forEach((value,i) => {
    const t = Number(value);
    const values = ["o","h","l","c","v"].map(k => {
      const raw = payload[k]?.[i];
      return raw == null || raw === "" ? NaN : Number(raw);
    });
    const [o,h,l,c,v] = values;
    if(!Number.isFinite(t) || t < 946684800 || !values.every(Number.isFinite) ||
       Math.min(o,h,l,c) <= 0 || v < 0 || l > Math.min(o,c) || h < Math.max(o,c) || l > h) return;
    bars.set(t,{t,o:o*1000,h:h*1000,l:l*1000,c:c*1000,v});
  });
  const result = [...bars.values()].sort((a,b) => a.t-b.t);
  if(result.length < 2) throw Error("no_data");
  return result;
}

const fmt = n => Number.isFinite(n) ? n.toLocaleString("vi-VN",{maximumFractionDigits:0}) : "—";
const date = t => new Date(t*1000).toLocaleDateString("vi-VN",{timeZone:"Asia/Ho_Chi_Minh"});
const stamp = () => new Date().toLocaleString("vi-VN",{timeZone:"Asia/Ho_Chi_Minh",hour12:false}) + " ICT";
const escape = text => String(text ?? "").replace(/[&<>"']/g,c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

export function createPricePreview(root){
  let symbol = "", bars = [], range = 63, controller, generation = 0;
  const node = name => root.querySelector(`[data-preview="${name}"]`);
  function draw(){
    const data = bars.slice(-range), W=960,H=190,L=8,R=74,T=10,B=34;
    const low=Math.min(...data.map(b=>b.l)),high=Math.max(...data.map(b=>b.h));
    const spread=high-low || Math.max(high*.01,1), bottom=H-B-30;
    const x=i=>L+(i+.5)*(W-L-R)/data.length, y=p=>T+(high-p)/spread*(bottom-T);
    const width=Math.max(1,Math.min(9,(W-L-R)/data.length*.65));
    const volume=Math.max(1,...data.map(b=>b.v));
    let shapes="";
    for(let i=0;i<4;i++){
      const p=high-spread*i/3,yy=y(p);
      shapes+=`<path d="M${L} ${yy}H${W-R}" stroke="var(--line)"/><text x="${W-R+6}" y="${yy+4}" fill="var(--muted)" font-size="10">${fmt(p)}</text>`;
    }
    data.forEach((b,i)=>{
      const color=b.c>=b.o?"var(--up)":"var(--down)",xx=x(i),vh=b.v/volume*24;
      shapes+=`<path d="M${xx} ${y(b.h)}V${y(b.l)}" stroke="${color}"/><rect x="${xx-width/2}" y="${Math.min(y(b.o),y(b.c))}" width="${width}" height="${Math.max(1,Math.abs(y(b.o)-y(b.c)))}" fill="${color}"/><rect x="${xx-width/2}" y="${H-B-vh}" width="${width}" height="${vh}" fill="${color}" opacity=".4"/>`;
    });
    shapes+=`<text x="${L}" y="${H-9}" fill="var(--muted)" font-size="10">${date(data[0].t)}</text><text x="${W-R}" y="${H-9}" text-anchor="end" fill="var(--muted)" font-size="10">${date(data.at(-1).t)}</text>`;
    node("chart").innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Biểu đồ nến ngày và khối lượng ${escape(symbol)}"><title>${escape(symbol)}: ${date(data[0].t)} đến ${date(data.at(-1).t)}, ${data.length} phiên</title>${shapes}</svg>`;
    const read = b => {node("readout").textContent=`${date(b.t)} · Mở ${fmt(b.o)} · Cao ${fmt(b.h)} · Thấp ${fmt(b.l)} · Giá cuối ${fmt(b.c)} VND · KL ${fmt(b.v)} CP`;};
    read(data.at(-1));
    const svg=node("chart").firstElementChild;
    svg.addEventListener("pointermove",e=>{const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*W;read(data[Math.max(0,Math.min(data.length-1,Math.floor((px-L)/(W-L-R)*data.length)))]);});
    svg.addEventListener("pointerleave",()=>read(data.at(-1)));
  }
  async function refresh(){
    controller?.abort();controller=new AbortController();const request=++generation, active=controller;
    const timeout=setTimeout(()=>active.abort(),15000);
    node("stamp").textContent="Đang kiểm tra dữ liệu VPS…";
    node("refresh").disabled=true;
    try{
      const now=Math.floor(Date.now()/1000);
      const response=await fetch(`https://histdatafeed.vps.com.vn/tradingview/history?symbol=${encodeURIComponent(symbol)}&resolution=D&from=${now-400*86400}&to=${now}`,{signal:controller.signal,cache:"no-store"});
      if(!response.ok)throw Error(response.status);
      const result=parseHistory(await response.json());
      if(request!==generation)return;
      bars=result;const last=bars.at(-1),prev=bars.at(-2),change=(last.c/prev.c-1)*100;
      node("price").textContent=fmt(last.c)+" VND";
      node("change").textContent=(change>=0?"+":"")+change.toLocaleString("vi-VN",{maximumFractionDigits:2})+"%";
      node("change").className=change>=0?"up":"down";
      const stale=now-last.t>4*86400;
      node("stamp").textContent=`VPS · phiên ${date(last.t)} · kiểm tra ${stamp()}${stale?" · DỮ LIỆU TRỄ":""}. Giá cuối nến ngày; trong phiên chưa phải giá đóng cửa. Có thể trễ.`;
      node("stamp").classList.toggle("warn",stale);
      draw();
    }catch(e){
      if(request!==generation)return;
      node("stamp").textContent=`Không lấy được dữ liệu mới từ VPS · kiểm tra ${stamp()}. ${bars.length?"Giữ biểu đồ của lần kiểm tra trước.":"Giá dưới đây thuộc ảnh chụp bảng tổng hợp."}`;
      if(!bars.length)node("chart").textContent="Nguồn giá tạm thời không phản hồi. Bấm Làm mới để thử lại hoặc mở Chart LEON.";
    }finally{clearTimeout(timeout);if(request===generation)node("refresh").disabled=false;}
  }
  return {select(row,meta){
    const next=row?.s||"";
    if(next===symbol)return;
    controller?.abort();generation++;symbol=next;bars=[];root.hidden=!row;
    if(!row)return;
    root.innerHTML=`<header><div><h2>${escape(symbol)}</h2><div class="company-name">${escape(row.n)}</div></div><div><span class="preview-price" data-preview="price">${fmt(row.p)} VND</span> <span data-preview="change"></span></div></header><nav aria-label="Khoảng biểu đồ">${[[22,"1T"],[63,"3T"],[126,"6T"],[260,"1N"]].map(([n,label])=>`<button class="btn" data-range="${n}" aria-pressed="${n===range}">${label}</button>`).join("")}<button class="btn" data-preview="refresh">Làm mới</button><span class="spacer"></span><a href="../../hub/?tab=chart&amp;sym=${encodeURIComponent(symbol)}&amp;tf=1d" target="_top">Chart LEON ↗</a><a href="../../hub/?tab=ticker&amp;s=${encodeURIComponent(symbol)}" target="_top">Hồ sơ & báo cáo ↗</a></nav><div class="preview-stamp" data-preview="stamp" role="status">Ảnh chụp bảng: ${escape(row.pd||meta.as_of||"chưa rõ phiên")}</div><div class="preview-chart" data-preview="chart">Đang tải biểu đồ…</div><div class="preview-readout" data-preview="readout"></div>`;
    root.querySelectorAll("[data-range]").forEach(button=>button.addEventListener("click",()=>{
      range=Number(button.dataset.range);root.querySelectorAll("[data-range]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));if(bars.length)draw();
    }));
    node("refresh").addEventListener("click",refresh);refresh();
  }};
}
