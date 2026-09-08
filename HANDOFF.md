# Handoff

Updated 2026-09-08 by Codex. Read `CLAUDE.md` and
`docs/source-gotchas.md` before touching collectors. The Claude plan is:

`C:\Users\LEON_RM\.claude\plans\nh-gi-ho-n-calm-newell.md`

## Current state

- Branch: `master`; overview changes and the preceding crypto context commit
  were pushed to the GitHub Pages source branch on 2026-09-08.
- Latest completed feature push before this handoff note is `8821072`: the VN
  valuation scenario engine. GitHub Pages deployment and App syntax both
  completed successfully; production route is
  `https://leonquant.com/hub/?tab=ticker&s=VIC`.
- Local server: PID 3312, `http://127.0.0.1:8811/hub/`.
- Chart: `http://127.0.0.1:8811/apps/chart/?sym=BTC&tf=30m`.
- Ticker dossier: `http://127.0.0.1:8811/apps/ticker/?s=VIC`.
- Do not reset the worktree. Use `git log -1` for the latest local commit; this
  handoff deliberately does not self-reference a commit hash.

## Next Claude: start here

1. Preserve the worktree. `data/crypto/index.json` and the many untracked
   `data/crypto/*.json` files belong to a separate concurrent crypto-profile
   run. Do not stage, delete or rewrite them while continuing VN ticker work.
2. Re-open VIC, FPT and VCB in production before changing valuation. The live
   model is entirely in `apps/ticker/index.html`: `discountRate`,
   `earningsScenario`, `residualIncomeScenario` and `valuationModel`.
3. Current model boundary: financials use RIM; non-financials use discounted
   normalized earnings. Property/holding output is explicitly low confidence
   and not NAV. Do not silently rename the VIC estimate to NAV/SOTP.
4. If the user continues valuation work, the next real inputs are `cfa19`
   capex plus interest-bearing debt for FCF DCF, project/segment assets for
   property NAV/SOTP, user-editable assumptions, and persisted valuation
   snapshots so forecast error can be backtested.
5. Check the scheduled `VN daily data` run after 16:30 ICT. The SSI fallback
   was committed in `69b39b5`, but its first post-close end-to-end CI success
   was still pending when this note was written. Ticker news now also runs
   independently every day at 17:15 ICT.
6. Before handing off or deploying, run:

   ```powershell
   python -m pipeline.core.check_apps
   python -m pipeline.core.validate stocks.json news_ticker.json signals.json ticker/manifest.json
   git diff --check
   ```

   The full validator currently fails only on the unrelated generated GAL
   crypto profile/price identity mismatch; do not “fix” it as part of VN scope.

## 2026-09-08 VN ticker navigation, freshness and evidence

- Valuation is now an explicit historical scenario engine instead of a refusal
  card. Financial firms use a 10-year residual-income model (current equity,
  normalized 5Y ROE, 25% payout default and ROE fade); other firms use a
  10-year discounted-earnings proxy based on median 5Y NPAT and robust
  profit/revenue growth. Both publish good/base/cautious results, cost of
  capital, terminal growth, current diluted share count, 30% margin of safety,
  confidence, input period and projected discounted flows. VIC renders a low-
  confidence earnings-power estimate plus an explicit warning that it is not
  project-level NAV/SOTP. Headless Chrome QA covered VIC, FPT and VCB; scenarios
  were monotonic and units rendered as VND/share.
- Each ticker dossier now has a prominent `Mở Chart LEON` action. It routes
  through `hub/?tab=chart&sym=<ticker>&tf=1d`, so the currently viewed VN code
  opens directly in LEON's own full chart workspace at the supported daily
  interval; the existing inline five-year chart and TradingView escape remain.
- `apps/stocks/index.html`: both the MÃ and full TÊN are now links to the same
  ticker dossier. The name column has an explicit readable width and wraps by
  words; the footer separates the market session date from the exact artifact
  build time, always formatted in ICT.
- `apps/ticker/index.html`: every data section now displays its own honest
  freshness badge. Market panels show the source session plus build time,
  Vietcap dossier panels show collector time plus build time, signals/news show
  their artifact times, and the VPS chart shows both its latest bar date and
  the exact browser check time. A failed VPS request is timestamped as a failed
  check rather than silently leaving “loading”.
- Added `Lợi thế cạnh tranh — bằng chứng định lượng`: industry-relative scale,
  margins/capital efficiency/leverage and multi-year profit durability. It is
  deliberately an evidence panel, not an AI moat verdict; brand, network
  effects, switching costs and intangible/regulatory advantages remain
  unscored until a cited factual input exists.
- The public Turtle VIC page was used only to compare visible methodology:
  historical parameters, discounted-NPAT fallback and three scenarios. LEON's
  implementation is original and more explicit about the VIC result being an
  earnings-power proxy rather than project-level NAV/SOTP; Turtle's private
  pipeline/model was not copied.
- Refreshed ticker news locally from 500 current digest articles: 11 strict
  headline matches across 6 symbols. VIC now has three latest articles dated
  2026-09-07. `.github/workflows/news.yml` also has an independent daily 17:15
  ICT schedule, so news no longer waits for the larger VN workflow to succeed.
- Verification: Chrome headless visual QA at 1440px covered the stocks table
  and the VIC dossier; names/timestamps/moat/valuation/chart rendered cleanly.
  Targeted validation passes for stocks, news, signals and ticker manifest;
  `git diff --check` passes. The all-artifact gate is currently red only on the
  unrelated concurrently generated `data/crypto/index.json` GAL profile/price
  mismatch; those crypto files were not staged or changed by this work.

## 2026-09-08 overview names and deep links

- `apps/brief/index.html` no longer truncates long company names with an
  ellipsis. Names wrap so the full published `stocks.json` company name remains
  readable.
- Every actionable overview row is now a keyboard-accessible link. Equity rows
  open the matching ticker dossier, BDS rows open the exact district/province/
  category filters, and the crypto, ETF, GEX, breadth and foreign-flow tiles
  route to their corresponding hub tabs.
- `apps/bds/index.html` now consumes overview deep-link parameters (`q`,
  `province`, `region`, `category`, `cat`) after its facets load. Normal direct
  visits keep the prior default-province behavior.
- Verification: all 35 inline app scripts parse, the full published-data gate
  passes, and `git diff --check` passes. Windows Computer Use was unavailable
  in this session, so no new screenshot claim is made.
- Live schedule audit on 2026-09-08: BDS, US, GEX and intraday depth workflows
  have recent successful runs. The VN daily workflow remains scheduled for
  16:30 ICT on weekdays but its latest run failed in `vn_equity` while decoding
  an upstream API response; committed `stocks.json` is therefore still dated
  2026-09-04. Cross-market flows also failed because Farside returned HTTP 403
  to the US GitHub runner. A local collector pass succeeded and republished
  `flows.json` through 2026-09-04 (the latest available session; 2026-09-07 was
  the US Labor Day holiday). Restoring unattended ETF refresh still needs a
  permitted source reachable from CI or a local scheduler.
- GitHub Pages deployment and App syntax checks both passed for the overview
  push. Production probes returned HTTP 200 and valid JSON for stocks, BDS,
  flows, GEX and `data/crypto/context.json`.
- `.github/workflows/daily-vn.yml` now lets the proven SSI quote collector act
  as a fallback when the VNStock/Vietcap wrapper fails before board collection.
  A guard still fails the workflow if both quote sources fail, so this does not
  turn a total source outage into a green stale-data build. Do not manually run
  the close workflow during a live VN session; the next scheduled post-close
  run is the first honest end-to-end confirmation.

## 2026-09-05 Market Structure live-flow upgrade

- `apps/gex/index.html` now combines two explicitly separated layers:
  Deribit option positioning for BTC/ETH, and Binance USDⓈ-M Futures live
  order flow for BTC/ETH/SOL. The new CVD is taker-buy notional minus
  taker-sell notional and intentionally resets whenever the tab/symbol opens;
  it is not presented as historical or all-day CVD.
- The same panel lists actual `forceOrder` events seen since the tab opened and
  separates long liquidations (forced SELL) from short liquidations (forced
  BUY). It says plainly that these are Binance-only executions, not inferred
  liquidation zones and not the whole market.
- Solana has its own Market Structure mode with Binance candles, CVD and
  liquidations. Do **not** add `gex_sol.json` yet: a live Deribit dry-run on
  2026-09-05 returned a valid SOL index price but zero option contracts. The UI
  explains the missing GEX rather than publishing a neutral-looking zero
  surface.
- `pipeline/sources/deribit_gex.py` now raises when Deribit returns an empty
  option chain. This prevents a supported symbol outage or unsupported symbol
  from being persisted as genuine zero GEX. BTC dry-run remained healthy with
  978 contracts after the guard was added.
- Binance migrated `aggTrade` and `forceOrder` to the routed
  `/market/stream` endpoint and retired push delivery through the legacy route
  after 2026-04-23. Browser QA caught the silent-open legacy socket; the page
  now uses the new route. Live QA on BTC received 17 aggregate trades in seven
  seconds, changed CVD to 182.5K, drew the CVD SVG and passed a deterministic
  forced-SELL parser check (2 × 100 = 200 long liquidation).
- Incidental interrupted-work cleanup: the duplicate Charm metric tile was
  removed. Fresh desktop visual QA at 1440×1500 found the SOL layout readable
  with no overlap or horizontal overflow. Static checks: all 35 inline scripts
  parse, Python pipeline compiles, and `git diff --check` passes.
- Remaining honest gap versus Turtle: there is still no persisted historical
  futures trade/footprint/liquidation store. Building that needs an append-only
  collector and retention policy; do not relabel this browser session stream as
  historical data.

## 2026-09-05 map repair and interrupted-work check

- `apps/bds/index.html` now renders the 63 legacy source geometries as exactly
  34 interactive post-merger provinces. Non-canonical Highcharts labels
  (`Southeast`, `Haiphong`, `Ho Chi Minh city`) are normalized, and shared
  legacy edges inside a merged province are removed from its visible outline.
- The map is top-aligned in its grid cell. A long province narrative no longer
  vertically centers the fixed-height SVG and pushes the country silhouette
  below the fold. Each province group also has keyboard activation and an
  accessible value label.
- Fresh Chrome QA at 1900x1000 reviewed the real hub route `?tab=bds`. Runtime
  inspection found 34 unique groups, zero unmapped legacy labels, 34 fill paths
  and 34 outline paths; Hồ Chí Minh was the selected merged group.
- The pre-existing uncommitted `hub/index.html` + `sw.js` change is Claude's
  cache-staleness repair, not part of the map patch. It was inspected and left
  intact. A clean Chrome profile registered `/sw.js` at root scope, activated
  `leon-v5`, controlled the page and created only the `leon-v5` cache.
- Verification: all 35 inline app scripts parse, all published-data validators
  pass (expected `npm`/`ebitm` tail warnings only), and `git diff --check`
  passes. Uncommitted files are `apps/bds/index.html`, `hub/index.html`,
  `sw.js`, and this handoff.

## Completed in this round

### Chart interaction and workspace

- Still **62 indicators and 34 drawing tools**. The tools are formula/behavior
  reimplementations over Lightweight Charts 5.2.0, not pasted Turtle source.
- Drawing placement is now continuous as the user requested: after finishing a
  shape the same tool remains armed. Exit with Pan, click the active tool again,
  right-click or `Esc`; every exit clears incomplete vertices.
- Market coordinates remain logical-index/price, never screen pixels. Existing
  `watchDrawingTransform()` now repaints the external canvas when the right
  price scale, logical range or price-pane height changes, fixing drift/lag
  while zooming or dragging the price axis.
- 14 intervals supported by both Binance and Hyperliquid:
  `1m,3m,5m,15m,30m,1h,2h,4h,8h,12h,1d,3d,1w,1M`. Eight common intervals are
  buttons; six are in `Khác`. Symbol/timeframe persist and sync across tabs via
  localStorage + `BroadcastChannel("leon_market_context_v1")`.
- Right sidebar now matches the reference's working shape: named watchlists,
  create/select, CSV import/export, live quote/change, add/remove current coin,
  and persistent show/hide state.
- `Sổ lệnh`: Binance depth snapshot plus `@depth20@100ms`, 10 asks + 10 bids,
  quantity bars and bid/ask imbalance. REST refresh every 30 seconds is fallback.
- `Big Tape`: Binance `@aggTrade`, records events >=100,000 USD; display
  threshold defaults to 500,000 USD, is editable and persists per symbol.
- `Levels`: ten GEX strikes nearest spot, classified across the latest 240
  candles as Break/Absorb/Fakeout/Respect. Rounded labels sum to exactly 100%
  whenever there are touches. Absorb requires actual Big Tape near the strike
  in the candle; historical tape is not invented. Old sockets close on symbol
  changes. Sidebar tab can be deep-linked with `&side=book|tape|levels`.

### VN ticker dossier and data contract

- `data/stocks.json`: 1,751 listings, 1,725 priced, 1,719 with fundamentals.
  It now includes open/high/low, shares, 52-week high/low and trading-day count.
- `pipeline/publish/emit.py` recursively converts NaN/Infinity to `null` and
  uses `allow_nan=False`. This fixed a real browser-wide failure: Python's JSON
  accepted four literal `NaN` tokens in `stocks.json`, but `JSON.parse` did not.
- New lazy `data/ticker/<SYM>.json` contract keeps the screener small. Published
  result: 1,719 files, 1,719 dossiers, 1,697 statement payloads, 1,044,029 ratio
  points, 46.39 MiB; all referenced files exist.
- Full direct-Vietcap crawl: 1,751 targets, **0 failed requests**, 1,739 company
  rows, 28,316 new owner rows, 4,878 new relationships, 77,668 events and
  10,068,118 statement rows. Elapsed 2,531 seconds locally.
- New append-only warehouse tables: `eq_company`, `eq_owner`,
  `eq_relationship`, `eq_event`, `eq_statement`. The full statement field set
  stays in DuckDB; static files carry only headline fields needed by readers.
- `pipeline/sources/vci_company.py` joins profile, shareholders/officers,
  subsidiaries/affiliates, events, statement metrics and income/balance/cashflow
  sections. It is sequential, paced, resumable by symbol/scope and separate from
  the daily job. Monthly CI lives in `.github/workflows/company-refresh.yml`.
- Daily/monthly workflows self-bootstrap listing/fundamental state and validate
  dossier coverage. Validation fails below 95% dossier or 90% statement coverage.
  Stable per-ticker timestamps prevent no-op builds creating a 1,700-file Git
  diff; `vci_direct --skip-existing` avoids pointless daily refetches.
- VCI bank CIR uses the negative accounting sign of operating expenses. The
  ticker transform publishes conventional positive CIR and drops an entire
  cadence if the source filled every point with a non-applicable zero.

### Ticker UI

- Search/datalist, local VN watchlist, sticky section navigation and TradingView
  escape link.
- Hero/quote strip: latest VPS daily bar refreshes price/change/open/high/low/
  volume/market cap in the browser, with source and date. This avoids Turtle's
  captured VIC contradiction where live price exceeded its baked high.
- Five-year close + SMA200 + volume SVG, rendered client-side from VPS; no huge
  candle history is committed.
- Company profile, ownership, analyst rating/target with Vietcap attribution and
  source date, officers/shareholders, subsidiaries/affiliates and events.
- Industry-aware annual/quarterly statement headline charts, eight-year ratio
  charts, and industry peer median/rank/sample-size comparison.
- Existing LEON advantages remain: foreign flow/room, SSI level-1 depth,
  momentum, disclosed signals and strict headline-only news matching.
- Valuation now has a transparent numeric scenario layer: RIM for financials
  and discounted historical earnings for other firms. Property/holding output
  is deliberately labelled a low-confidence earnings proxy, not NAV/SOTP.
  Vietcap analyst target remains a separately attributed source opinion.

## Source boundary

Turtle's unminified browser app was public and its output/data contracts could
be inspected. Its collector, generated ticker HTML pipeline, valuation engine
and AI prompts are not public. The close field match points to a shared Vietcap
upstream, but that is an inference. LEON uses direct public Vietcap/VPS/Binance/
Hyperliquid data and its own implementation. Do not claim private backend code
was copied or that the reference's licence transfers to us.

## Verification completed

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m compileall -q pipeline
python -m pipeline.core.validate
```

- All 9 artifacts pass. Expected warnings only: about 1% `npm`/`ebitm` tails.
- 1,727 published JSON files parsed with strict rejection of non-finite tokens.
- Every workflow YAML parses; `git diff --check` passes.
- Rebuilding ticker payloads twice left `VIC.json` SHA-256 unchanged, proving a
  no-op build does not manufacture per-symbol changes.
- Headless Chrome visual QA at 1600x1000 covered watchlist, order book, Big Tape,
  Levels, `2h` and `1M`. Order book moved live; Level percentages summed to 100.
- Historical pre-engine ticker QA at 1440x3000 covered VIC, VCB and ordinary
  company AAA. Price charts, statement families, ratio cards, peer rows and the
  then-current valuation refusal rendered. The newer scenario-engine QA is
  recorded near the top of this file. The VCB pass caught and verified the CIR
  sign fix.

## What is genuinely still missing

Do not reopen completed G4/H2/H3/H4/B2 data/UI work. Remaining parity work is:

1. Full asset/FCF valuation depth: project-level NAV/SOTP for property/holdings,
   capex-based FCF DCF for ordinary firms, assumption editing and persisted
   valuation snapshots/backtests. The current historical earnings/RIM scenario
   engine is live, but those deeper input families are not.
2. Macro and company narrative with fact references, model/generated timestamp
   and confidence. Do not generate prose from unvalidated facts.
3. Multi-chart workspace (reference has many layouts/up to 16 panes), alternate
   chart types and arbitrary user-defined candle aggregation. Fourteen official
   intervals are done; arbitrary intervals are not.
4. Historical microstructure: CVD, footprint, liquidation/positioning layers
   and persisted tape/order-book history. Current book/tape are live-session
   evidence only.
5. Larger separately scoped products: replay/training, quant/backtester, wealth,
   US equities, PWA/offline. These were explicitly deferred, not bugs in B2.

The detailed reference inventory and field families are in
`docs/turtle-gap-inventory.md`; source traps are in `docs/source-gotchas.md`.
