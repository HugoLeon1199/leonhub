import {parseHistory} from "./price-preview.js";
const check=(condition,message)=>{if(!condition)throw Error(message);};
Deno.test("VPS strings, seconds, VND conversion, ordering and duplicates",()=>{
  const d={s:"ok",t:["1790208000","1790121600","1790208000"],o:[32,31,32],h:[34,33,34],l:[31,30,31],c:[33,32,33.5],v:[100,200,300]};
  const bars=parseHistory(d);
  check(bars.length===2,"deduplicate timestamps");
  check(bars[0].t===1790121600,"seconds stay seconds and sorted");
  check(bars[1].c===33500 && bars[1].v===300,"VND conversion without volume scaling");
});
Deno.test("Malformed OHLC and missing values never become chart prices",()=>{
  const d={s:"ok",t:[1790035200,1790121600,1790208000,1790294400],o:[32,32,32,32],h:[34,34,31,34],l:[31,31,30,31],c:[33,32,33,null],v:[100,200,100,100]};
  check(parseHistory(d).length===2,"reject invalid range and missing close");
  for(const input of [{s:"no_data"},{s:"ok",t:[]},{...d,c:[null,null,null,null]}]){
    let failed=false;try{parseHistory(input);}catch{failed=true;}check(failed,"reject unusable response");
  }
});
