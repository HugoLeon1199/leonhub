# Inventory chênh lệch với TurtleTrading

Audit ngày 2026-09-04. Mục tiêu của tài liệu này là để phiên Claude/Codex tiếp
theo biết chính xác phần nào đã có, phần nào chỉ thiếu UI, phần nào cần collector
mới, và phần nào không nên sao chép nguyên trạng.

## Cập nhật triển khai 2026-09-04 (Codex, phần này ghi đè trạng thái cũ bên dưới)

- **G4 đã xong:** 14 timeframe chính thức cùng hỗ trợ bởi Binance/Hyperliquid
  (`1m` đến `1M`), tám khung thường dùng hiện trực tiếp và sáu khung ở menu
  “Khác”; symbol/timeframe nhớ qua reload và đồng bộ giữa tab. Sidebar có
  watchlist nhiều danh sách, tạo mới, chọn danh sách, nhập/xuất CSV, giá Binance
  live và ẩn/hiện panel.
- **H2/H3 đã xong:** sổ lệnh Binance 20 mức qua depth WebSocket 100 ms, imbalance
  bid/ask; Big Tape từ `aggTrade`, chỉ lưu giao dịch >=100.000 USD, ngưỡng hiển
  thị mặc định 500.000 USD và nhớ riêng theo symbol. Socket cũ được đóng khi đổi
  mã; REST depth 30 giây là fallback.
- **H4 đã xong ở mức có bằng chứng:** 10 GEX level gần spot được phân loại trên
  240 nến thành Break/Absorb/Fakeout/Respect. Bốn tỷ lệ đã làm tròn vẫn cộng đúng
  100%; Absorb chỉ được tính khi Big Tape thật gần level trong đúng candle. Vì
  client không có tape lịch sử, Absorb quá khứ không được suy đoán.
- **B2.1 đã xong:** `data/ticker/<SYM>.json` lazy-load ratio 8 năm theo quý/năm;
  trang ticker có peer rank/median/sample cùng ngành và chart giá 5 năm
  close/SMA200/volume lấy VPS ở browser, không commit hàng triệu candle.
- **B2.2/B2.3 đã triển khai:** schema append-only cho company, owners,
  relationships, events, statements; collector Vietcap trực tiếp; UI profile,
  nguồn/ngày analyst rating, cổ đông/người nội bộ, công ty con/liên kết, sự kiện
  và headline BCTC theo quý/năm. Quote hero được cập nhật bằng bar VPS mới nhất,
  nên không lặp lỗi giá hiện tại cao hơn 52-week high đã bake của trang mẫu.
- **Coverage thật sau full crawl:** 1.751 target, 0 request lỗi; warehouse thêm
  1.739 company, 28.316 owner, 4.878 relationship, 77.668 event và 10.068.118
  statement rows. Artifact lazy có 1.719 ticker/1.719 dossier/1.697 statement,
  1.044.029 ratio points, 46,39 MiB; validator chặn nếu dossier <95% hoặc
  statement <90% để nguồn hỏng không âm thầm publish trang rỗng.
- **Valuation guard đã xong, valuation engine chưa bịa:** BĐS/holding dùng
  NAV/SOTP khi đủ dữ liệu tài sản; bank/securities/insurance cần RIM; doanh nghiệp
  thường chỉ DCF khi có FCF. Thiếu input thì UI từ chối in fair value thay vì
  rơi về một con số vô lý.

Phần thật sự còn thiếu sau vòng này: valuation scenario engine có assumptions
đã kiểm tra, macro/narrative có fact references, chart workspace nhiều ô/loại
chart và timeframe tự do, cùng CVD/footprint/liquidation lịch sử. Không được đổi
nhãn các phần trên thành “đã xong” chỉ vì đã có card giao diện.

## Kết luận ngắn

- Chart đã ngang **số lượng**: 62 chỉ báo, 34 công cụ vẽ, 14 timeframe, named
  watchlists/CSV, order book, Big Tape và Level Behavior. Chưa ngang hệ thống
  workspace: 43 layout, tối đa 16 chart, 10 loại chart và timeframe tùy ý.
- Trang ticker LEON nay đã là dossier lazy-load rộng toàn thị trường: profile,
  BCTC, quản trị/sở hữu, sự kiện, chart giá, ratio history và peer comparison.
  Khoảng thiếu lớn còn lại là valuation scenario engine và narrative có dẫn
  facts, không còn là độ phủ dữ liệu/giao diện cơ bản.
- LEON có lợi thế thật ở 1.751 mã, percentile tám năm theo quý, foreign flow,
  room ngoại, SSI book, tín hiệu công khai và provenance giá. Không được bỏ các
  phần này chỉ để giống giao diện mẫu.
- Front-end và output của Turtle đọc được; pipeline tạo BCTC, valuation và AI
  không được public. Ta có thể tái tạo contract dữ liệu, không thể nói là đã có
  source collector/valuation gốc của họ.

## Cấu trúc trang ticker mẫu, theo đúng thứ tự hiển thị

1. Header chung: điều hướng cổ phiếu/crypto/Mỹ/vĩ mô/so sánh, tìm kiếm gợi ý,
   watchlist và theme.
2. Identity: mã, tên, sàn, ngành, link sang tín hiệu kỹ thuật, nút yêu thích.
3. Company profile có thu gọn/mở rộng.
4. Quote strip: giá, thay đổi, volume, mức cao nhất và thấp nhất trong database.
5. Macro VN: regime, confidence, summary, key factors, risks, outlook, timestamp.
6. Fundamental charts theo ngành, dữ liệu năm và YTD.
7. Valuation: phương pháp, cảnh báo chất lượng input, fair value, safe price,
   market comparison, tham số. DCF còn có ba scenario và projection 10 năm.
8. AI overview: chất lượng doanh nghiệp, BCTC, triển vọng ngành, moat năm chiều,
   rủi ro, catalyst, timestamp và confidence.
9. Quản trị: officers, top shareholders, subsidiaries/affiliates.
10. Corporate events.
11. Technical chart năm năm: close, SMA200 và volume.
12. Hai bảng ratio bốn năm: key ratios và expanded ratios.
13. News theo ticker.

Trang VIC tải về có 19 lần `Plotly.newPlot` và hai bảng. Mỗi chart nhúng lại
Plotly dark template nên HTML phình tới 258 KB; LEON nên dùng một renderer chung
và JSON per ticker, không lặp lại cách đóng gói này.

## Fundamental chart families của mẫu

### Phi tài chính — 18 chart

`revenue`, `net_profit`, `eps`, `fcf`, `market_cap`, `gross_margin`,
`interest_coverage`, `roe`, `roic`, `equity`, `shares_outstanding`,
`debt_equity`, `dividend_payout`, `dividend_yield`, `dso`, `dio`,
`current_ratio`, `asset_turnover`.

### Ngân hàng — 15 chart trong mẫu STB

`revenue`, `net_profit`, `eps`, `bvps`, `nim`, `market_cap`, `cir`, `roe`,
`npl`, `provision_cover`, `ldr`, `equity`, `shares_outstanding`,
`dividend_payout`, `dividend_yield`.

### Chứng khoán — 15 chart trong mẫu SSI

`revenue`, `net_profit`, `eps`, `bvps`, `margin_lending`, `market_cap`,
`net_margin`, `roe`, `cost_to_income`, `margin_to_equity`, `debt_equity`,
`equity`, `shares_outstanding`, `dividend_payout`, `dividend_yield`.

### Bảo hiểm — 14 chart trong mẫu BVH

`revenue`, `net_profit`, `eps`, `bvps`, `loss_ratio`, `market_cap`,
`expense_ratio`, `combined_ratio`, `roe`, `equity`, `debt_equity`,
`shares_outstanding`, `dividend_payout`, `dividend_yield`.

Đây là điểm quan trọng: không thể dùng một bộ card chung cho mọi ngành rồi gọi
là parity. Banking, securities và insurance cần schema/render riêng.

Hai bảng bốn năm cũng đổi theo ngành. Mẫu VIC có 10 key + 48 expanded ratios;
STB có 7 + 25 bank ratios; SSI có 10 + 39; BVH có 10 + 42. Nhóm expanded gồm
cash-flow quality/accruals, growth, leverage/liquidity, efficiency/turnover và
industry metrics như NIM/CIR/LDR/provision cover hoặc loss/combined ratio.

## Contract dữ liệu public của Turtle

### Screener `/data/stocks.json`

1.522 rows, 24 short keys:

| Key | Nghĩa |
|---|---|
| `s,n,e,i` | symbol, name, exchange, industry |
| `p,v,mc` | price, volume, market cap |
| `pe,pb,ps,ev` | P/E, P/B, P/S, EV/EBITDA |
| `roe,roic,roa` | profitability ratios |
| `dy,de,nm` | dividend yield, debt/equity, net margin |
| `rg,pg` | revenue growth, profit growth |
| `fcf` | flag FCF usable/positive |
| `yd` | số năm dữ liệu |
| `iv,disc` | intrinsic value và discount/premium bounded |
| `mt` | moat label |

Độ phủ cao ở snapshot cơ bản; `iv` có 1.226/1.522 mã và `disc` có 1.197. File
này không chứa chuỗi BCTC, governance hay narrative; các phần đó được bake vào
từng HTML ticker.

### Giá và chart

- `/data/prices.json`: `updated_at`, rồi `stocks[symbol] = {p,ch,ch_pct,vol}`.
- `/api/stock/live.json`: khoảng nhóm blue-chip, đơn vị nghìn đồng,
  `{price,high,low,ref}`; `live-prices.js` poll 15 giây trong giờ giao dịch.
- `/data/history/<SYM>.json`: `{s,u,b}`, với mỗi bar
  `[date,open,high,low,close,volume]`; VIC có 1.247 bars.
- `live-chart.js` render client-side close area + SMA200 + volume. Comment trong
  source nói JSON được tạo bởi private `export_history.py`; file generator đó
  không được server public.

### News và macro

- `/data/news/<SYM>.json`: `{ticker,items[]}`; item gồm
  `title,url,preview,source,lang,ts`. VIC có 20 items ở thời điểm audit.
- `/data/macro_vn_sentiment.json`: `state.regime`, `confidence`, `summary`,
  `key_factors[]`, `risks[]`, `outlook`, `timestamp`, `model`, `news_count`,
  `window_days`.

### Embedded per-ticker data

HTML bake sẵn annual statement/ratio series, valuation outputs/parameters,
company description, AI analysis, officers, shareholders, subsidiaries and
events. Đây là output đã xử lý chứ không phải raw data API hay pipeline source.

## LEON đang có gì

### Public `data/stocks.json`

1.751 rows và 49 possible keys. Ngoài identity/quote/valuation ratios, LEON có:

- `pd`, `pr`: ngày giá và cờ giá tham chiếu — provenance tốt hơn mẫu.
- `pe_p`, `pb_p`, `ps_p`, `ev_p`, `roe_p`, `dy_p`: percentile lịch sử chính mã.
- `f1`, `fr`: foreign flow và foreign room; `f5/f20` chỉ nên xuất hiện khi đã
  tích đủ số ngày thật.
- `m1,m3,m6,pos52,z`: momentum, 52-week position và standardized move.
- `a1,a1v,b1,b1v`: SSI level 1. UI hỗ trợ ba mức nhưng artifact hiện mới có một.
- `at,cr,de,dy,ebitm,ev,gpm,lev,npm,pcf,pb,pe,ps,qr,roa,roe,roic` và metric
  banking `casa,cir,ldr,nim,npl`.

### Warehouse hiện tại

- `eq_quote`: 353.356 rows; có open/high/low/volume/value, `listed_share`, foreign
  buy/sell value và volume. Nhiều field hữu ích chưa publish sang ticker JSON.
- `eq_fundamental`: 1.443.259 rows, 1.722 symbols, 42 periods, 83 metric names.
  Bộ collector chính có dữ liệu 2018-Q1 đến 2026-Q2.
- VIC: 41 periods, 28 ratio metrics. Có market cap, owners equity, EBITDA và các
  ratio; chưa có annual statement lines như revenue/net income/FCF/EPS.
- Một nhóm metric statement cũ chỉ tồn tại ở ba mã YBM/YEG/YTC và vài kỳ 2018–19;
  không được coi là coverage usable.
- `news_link`: source/timestamp/matched_by có provenance, nhưng output hiện chỉ
  7 links/5 symbols do rule headline-only có chủ ý.

### UI ticker hiện tại

Chỉ fetch ba file: `stocks.json`, `signals.json`, `news_ticker.json`. Nó render:
identity/price, six own-history percentile cards, foreign flow/room, momentum,
SSI book, SMA20×SMA50 trade history và deterministic news. Không có per-ticker
history JSON, statement charts, peer panel, profile, valuation model,
governance/events, macro hay AI dossier.

## Khoảng thiếu 1-1 và hướng xử lý

| Khối | Tình trạng | Việc phải làm |
|---|---|---|
| Quote high/low/open/value | Có trong warehouse | Publish field và hiển thị; ghi rõ intraday/52W/all-time |
| Listed shares | Có trong `eq_quote` | Publish, kiểm adjusted shares/split |
| Technical chart 5Y | Có OHLCV | Xuất `data/ticker/<SYM>.json` hoặc history file, render LWC thay Plotly |
| Ratio history 8Y quý | Có | Xuất series, chart annual/quarter toggle |
| Industry peer percentile | Có snapshot + industry | Tính cohort, sample count, rank và median |
| Company profile/listing info | Chưa lưu | Collector `Company.overview()` |
| Revenue/net profit/EPS/BVPS | Chưa usable | Collect income + balance statement |
| FCF/net cash/debt/capex | Chưa usable | Collect cash flow + balance statement, chuẩn hóa dấu |
| DSO/DIO/interest cover/payout | Chưa đủ | Tính từ statements; không thay missing bằng 0 |
| Bank metrics | Có một phần ratio | Thêm provision cover, YOEA/COF và statement-specific values |
| Securities metrics | Chưa đủ | Margin lending, margin/equity, cost/income |
| Insurance metrics | Chưa đủ | Loss/expense/combined ratios |
| Officers | Chưa lưu | Collector `Company.officers()` |
| Shareholders | Chưa lưu | Collector `Company.shareholders()` |
| Subsidiaries | Chưa lưu | Collector `Company.subsidiaries()` |
| Corporate events | Chưa lưu | Collector `Company.events()` |
| Macro state | Chưa có ticker mount | Có thể tái dùng brief/news inputs nhưng cần artifact riêng |
| Valuation DCF/RIM/NAV | Chưa có | Chỉ làm sau khi statement inputs qua validation |
| AI overview/moat | Chưa có | Sinh có citations/facts/timestamp/confidence; không bake hallucination |
| Ticker news breadth | Có nhưng rất hẹp | Giữ precision; thêm aliases/sources có kiểm thử thay vì body matching |
| Search/watchlist | Chưa có trên ticker | Dùng chung component với chart/stocks, không copy ba bản state |

## Nguồn collector khả thi đã xác minh

Package `vnstock` hiện cài trong môi trường đã expose đúng các hàm còn thiếu:

- `Finance.income_statement()`, `balance_sheet()`, `cash_flow()`.
- `Company.overview()`, `officers()`, `shareholders()`, `subsidiaries()`,
  `events()`; ngoài ra có `ownership`, `affiliate`, `capital_history`, `news`.

Một probe read-only duy nhất với `source='VCI', symbol='VIC'` trả về:

- income statement: 25 lines × 4 năm;
- balance sheet: 122 lines × 4 năm;
- cash flow: 41 lines × 4 năm;
- overview: 42 columns, có company profile, listing date, 1Y high/low, issue
  shares, free float, target price/rating;
- 9 officers, 44 shareholders, 159 subsidiaries và 50 events.

Profile và governance trả về rất gần nội dung đã bake ở Turtle, nên VCI là
nguồn có khả năng cao hoặc ít nhất cùng upstream; đây là inference, vì trang mẫu
chỉ công bố chung là “dữ liệu công khai”.

Giới hạn quan trọng: community edition báo rõ statements chỉ trả tối đa bốn kỳ,
trong khi mẫu có 15 năm. Trước full crawl phải kiểm license/rate limit và chọn
chiến lược: paid history, KBS fallback, hoặc tích lũy snapshots; không được ghi
plan “15 năm đã có” khi mới probe được bốn năm.

## Thiết kế data contract nên làm tốt hơn mẫu

Không sinh 1.500 HTML nặng. Tạo generic ticker app + JSON per symbol:

```text
data/ticker/VIC.json
  meta          identity, profile, exchange, industry, listing, provenance
  quote         price/ref/open/high/low/volume/value/date/freshness
  statements    annual/quarter series with unit/source/restated flag
  ratios        annual/quarter series
  peers         cohort, median, percentile, sample count
  valuation     method, suitability, inputs, scenarios, result or refusal
  governance    officers/shareholders/subsidiaries
  events        normalized corporate events
  analysis      facts, risks, catalysts, moat, generated_at, sources
```

Raw warehouse tables nên append-only: `eq_statement`, `eq_company`,
`eq_officer`, `eq_shareholder`, `eq_subsidiary`, `eq_event`. Publish file bằng
atomic write theo pattern hiện có. Không nhét narrative/HTML vào
`data/stocks.json` vì làm screener tải chậm.

## Guardrails định giá bắt buộc

- Bank, securities, insurance: RIM/residual income với BVPS, normalized ROE,
  payout, cost of equity và terminal growth.
- Doanh nghiệp phi tài chính: DCF chỉ khi FCF dương/đủ ổn định và có net debt,
  diluted shares, capex/working-capital consistency.
- Real estate holding/conglomerate như VIC: NAV/SOTP hoặc **không xuất IV** nếu
  thiếu segment/asset inputs. Không fallback mù từ FCF âm sang NPAT.
- Cyclical/commodity: normalized earnings or peer multiples, không extrapolate
  một năm đỉnh.
- Luôn công bố method, period, source, base value, growth stages, discount rate,
  terminal growth, net cash/debt, shares, margin of safety và three scenarios.
- Sanity gate: unit, sign, split/restatement, terminal growth < discount rate,
  scenario monotonicity, implied multiple, market-price ratio and stale inputs.
- Nếu gate fail, render lý do “chưa thể định giá”, không render số rồi dán cảnh báo.
- Lưu snapshots để sau này backtest sai số IV thay vì chỉ có current verdict.

## Lỗi mẫu không được sao chép

- `live-prices.js` cập nhật giá VIC lên 256.100 nhưng không cập nhật card high
  đã bake là 236.000, làm current > “cao nhất database”.
- VIC FCF âm cộng dồn 5 năm nhưng DCF fallback sang normalized NPAT và cho fair
  value 2.803, safe price 1.962; good scenario vẫn thấp khoảng 98,8% so market.
- Các page có ratio 0.000 hàng loạt (ví dụ ROA/ROE TTM), cần phân biệt missing với
  zero thật.
- 19 Plotly calls lặp full theme trong mỗi page gây bloat.
- AI text có disclaimer nhưng không có inline source citations cho từng fact.

## Thứ tự triển khai đề xuất

### B2.1 — dùng dữ liệu đã có, không chờ collector

1. Xuất per-ticker quote + OHLCV + quarterly ratio series.
2. Thêm technical chart năm năm và annual/quarter ratio charts.
3. Thêm peer median/percentile/sample count.
4. Thêm educational notes và giữ nguyên provenance/foreign flow/signal/news.

Gate: VIC không hiện IV; all missing là `null`; route 1.751 mã không 404; payload
lazy-load; price/card labels nói rõ period.

### B2.2 — company/governance/events

1. Thiết kế năm bảng warehouse append-only.
2. Probe 25 mã đại diện bốn ngành và ba sàn trước.
3. Chốt rate limit/community coverage rồi mới full crawl.
4. Publish profile, officers, ownership, subsidiaries, events có `source` và
   `fetched_at`.

### B2.3 — statements và valuation

1. Ingest income/balance/cash-flow, normalize item IDs/unit/sign/annual vs Q.
2. Derive chart families theo ngành và verify bằng ít nhất 10 mã/nhóm.
3. Implement suitability router + DCF/RIM/NAV refusal rules.
4. Add scenarios, assumptions and valuation snapshot history.

### B2.4 — narrative

Sinh quality/industry/moat/risk/catalyst sau cùng. Prompt chỉ nhận facts đã
validate; output phải lưu fact references, model, generated_at và confidence.

## Khoảng thiếu toàn sản phẩm ngoài ticker

| Module | Đã có | Còn thiếu để gần mẫu |
|---|---|---|
| Chart | 62 indicators, 34 drawings, paging, 14 timeframe, named watchlists/CSV | arbitrary timeframe, 43 layouts, multi-chart 16 panes, alternate chart types |
| Market Structure/GEX | GEX surface/profile/tiles, Big Tape, spot book, evidence-based Level Behavior | perp/CVD/footprint and historical absorption/liquidation/positioning layers |
| Signals | transparent SMA20×50 backtest | multi-timeframe state, macro/liquidation/positioning/GEX/taker context |
| BĐS | aggregate table, 47k raw listings | map/GeoJSON, listing velocity, DOM/repost/history panels |
| News | rich static digest + strict ticker links | live ticker breadth and shared macro state |
| Training | chưa có | replay, orders, costs, session save/stats, HTF indicators |
| Quant | chưa có | editor, backtest, sweep, walk-forward, robustness matrix, portfolio synthesis |
| Wealth | chưa có | assets/debts/goals/transactions/on-chain wallet |
| Hub infra | basic tabs/Pages | PWA/offline, LRU iframe state, shared search/watchlist/sync |

B2.1/B2.2 và phần data/UI của B2.3 đã hoàn tất trong vòng này. Nếu mục tiêu tiếp
theo là feature parity toàn site, khối Market Structure lịch sử và multi-chart
workspace mới là phần công sức lớn nhất; replay/quant/wealth là các sản phẩm
riêng, không nên trộn vào một sprint ticker.
