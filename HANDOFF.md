# Handoff

Updated 2026-09-04 by Codex. Read `CLAUDE.md` and
`docs/source-gotchas.md` before touching collectors. The Claude plan is:

`C:\Users\LEON_RM\.claude\plans\nh-gi-ho-n-calm-newell.md`

## Current state

- Branch: `hub-upgrades`; this round starts above `ba0c436`.
- Local server: PID 3312, `http://127.0.0.1:8811/hub/`.
- Chart: `http://127.0.0.1:8811/apps/chart/?sym=BTC&tf=30m`.
- Ticker dossier: `http://127.0.0.1:8811/apps/ticker/?s=VIC`.
- Do not reset the worktree. Use `git log -1` for the latest local commit; this
  handoff deliberately does not self-reference a commit hash.

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
- Valuation router is intentionally a guard, not a fake calculator: property/
  holding companies require NAV/SOTP asset inputs; banks/securities/insurance
  require RIM inputs; other companies require usable FCF for DCF. Without those
  inputs it refuses to print fair value. Vietcap analyst target is shown as a
  source opinion, never presented as LEON intrinsic value.

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
- Ticker QA at 1440x3000 covered VIC, VCB and ordinary company AAA. Price charts,
  statement families, ratio cards, peer rows and valuation refusal rendered.
  The VCB pass caught and verified the CIR sign fix.

## What is genuinely still missing

Do not reopen completed G4/H2/H3/H4/B2 data/UI work. Remaining parity work is:

1. A valuation engine with validated inputs, industry-specific RIM/NAV/SOTP/DCF,
   scenarios, assumption provenance and snapshot history. The guard/refusal
   layer exists; the numeric engine does not.
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
