# Handoff

Updated 2026-09-04 11:22 (Asia/Dubai). Read `CLAUDE.md` first. The active Claude
plan is `C:\Users\LEON_RM\.claude\plans\nh-gi-ho-n-calm-newell.md`; its round-3
status and this file are synchronized through G3.

## Outcome

The recovered TurtleTrading comparison plan A-E is complete. The site now has live
crypto charting and drawings, a per-ticker VN equity page, deterministic ticker
news, exposed SSI order-book depth, ETF issuer breakdown, clearer GEX and the
full signal track record. Round 3 has also completed F1/F2, G0-G3 and H1:
history coverage was backfilled, chart history pages left, Lightweight Charts is
on v5.2.0, drawing tools were expanded and indicators now use real panes.

The worktree contains the final G2/G3 refinement to `apps/chart/index.html` on top
of `7a826db`; the user did not ask for a commit. Do not reset it. There is no git
remote, so workflows have not run in GitHub Actions yet. Local server PID 3312
serves the repo on port 8811.

## Round 3 chart continuation (2026-09-04)

- `66c9572` completed F1, G0, the drawing registry/extra tools and H1. `4b56c6d`
  completed F2 lazy history. `7a826db` introduced the 16-indicator registry.
- The uncommitted follow-up finishes G2/G3 instead of changing direction. The ten
  drawing tools are `hline`, `hray`, `trend`, `vline`, `rect`, `fib`, `fibext`,
  `range`, `mark`, and `text`. Horizontal ray is correctly one-click. Drawings
  have OHLC magnet snap (Ctrl temporarily inverts it), endpoint editing, global
  Ctrl+Z/Ctrl+Y, move undo, per-symbol persistence, future-time extrapolation and
  a canvas constrained to the price pane.
- All 16 indicators remain registry-driven and multi-instance. Overlay series and
  oscillators now use actual LWC v5 panes, not multiple scales painted into one
  strip. The panel exposes every parameter (including MACD 3-tuples, Stochastic,
  Bollinger/Supertrend multipliers and Ichimoku 9/26/52), colour, per-instance
  visibility, reset to signal MA20/50, and limits of 16 indicators / 4 live panes.
- Formula/render fixes: flat RSI/MFI are 50, MACD histogram is direction-coloured,
  Supertrend splits green/red regimes, Ichimoku publishes Tenkan/Kijun/Span A/
  Span B/Chikou, and declared ranges/guides are rendered. WebSocket recalculation
  is throttled to 300 ms; older-history refresh reuses mounted series and panes.
- Drawing interaction is deliberately continuous: after the final required
  point, the selected tool remains armed for the next shape. It exits through
  `Pan / Esc`, right-click, Escape, or clicking the armed tool again. Every exit
  cancels partial points, so an unfinished Fibonacci/range cannot leak into the
  next click. Non-left pointer presses never create a drawing.
- The chart now has a Turtle-style right-click menu with working price alerts,
  price-scale fit/auto/log controls, free/magnet crosshair, volume and indicator
  title toggles, show/hide/delete actions, chart copy/download, theme and a jump
  to indicator settings. Alerts and preferences are localStorage-backed; alert
  crossing is checked only while the chart tab is open, with a one-shot toast and
  a manage dialog. PNG export composites the LWC screenshot and drawing canvas.
- Important truth: drawings are still a market-coordinate canvas overlay, **not**
  LWC Series Primitives. It is registry-backed, survives pan/zoom/future space and
  now tracks price-pane resizing. Do not describe G1 as a primitive migration.
- The reference chart source was audited directly. Its page publicly falls back
  from `multi.min.js` to an unminified `/multi.js` (HTTP 200, 1,011,167 bytes,
  10,799 lines); it is minified in production, not actually source-hidden. The
  application layer is custom vanilla JS: 35 drawing selectors, a DrawPrimitive
  adapter/unified renderer, and 62 registry indicators across seven categories.
  The reference itself is one-shot (`armTool(null)` after completion), unlike the
  user's requested continuous behavior. No application-source license notice was
  found; reproduce behavior/formulas, do not paste its implementation verbatim.
  See `docs/teardown-turtletrading.md` for the durable audit.

## Completed plan sections

- **A1/A2/A3:** `apps/chart/index.html` uses Binance kline WebSocket updates,
  bounded 500-bar state, reconnect backoff and REST only as a stale/dead-socket
  fallback. Viewport is preserved. Theme changes recolor mounted volume bars.
  A 250-pair Binance datalist is cached 24h. MA20/MA50 has a persisted toggle.
- **A4:** canvas tools for horizontal level, trendline and Fibonacci. Drawings
  store time/price coordinates per symbol, redraw on pan/zoom/resize/theme,
  support hit-test/select/drag/Delete/clear and survive reload via localStorage.
- **B:** new `apps/ticker/index.html`; hidden `ticker` route in `hub/index.html`;
  stock symbols deep-link through the hub. Page shows `pr`/`pd` provenance,
  own-history valuation percentiles, 1/5/20 foreign flow when observed, room,
  momentum, SSI depth, open/closed signals, audited news and a stock chart link.
- **C:** new `news_link` warehouse table, `pipeline/sources/news_link.py`,
  `pipeline/transform/news_build.py`, validator, `data/news_ticker.json` and
  `.github/workflows/news.yml`. Matching is deterministic: normalized legal
  names, reviewed large-cap aliases and context-gated symbols; every row stores
  `matched_by`. It consumes `https://leonquant.com/content.json` without changing
  the NEWS repo or using AI.
- **D/E:** GEX explanation, signal exit reasons and all 214 closed trades;
  three-level SSI collection contract and 1,292 current rows with level-1 data;
  Farside breakdown for 12 BTC and 10 ETH ETF issuers.

## Important correctness decisions

- `_parse_date()` in `news_link.py` had lost its body: the `try/except` block
  belonged to it but sat after `article_text()`'s `return`, so it was dead code
  and the function returned `None` for every input. Every `published_at` written
  to `news_link` was therefore NULL, which no validator catches and nothing
  crashes on — the per-ticker "10 most recent" ordering was silently arbitrary.
  Fixed; new rows carry real timestamps. Older NULL rows remain by design, the
  table being append-only.

- NEWS summaries sometimes append unrelated-link headlines. Matching uses an
  excerpt when available, clips/splits stamped related-link tails, blocks venue
  names such as HNB/WCS, and builds only the newest full fetched-at snapshot.
  Matching requires the company to appear in the **headline**. Body text is used
  only to find candidates cheaply; the headline decides. This was tightened after
  a manual audit of all 14 links from the previous body-matching pass found ~6
  false positives that the earlier QA had missed — a bank-rate round-up naming
  seven banks attached to CTG/STB/VCB, an airport-congestion story to HVN/VJC,
  a market-wide story to VIC, and a football story to HHV. None was fixable by a
  better alias list, because the body prose was genuinely correct; only the
  headline separates a story about a company from one that merely lists it.
  Result: 7 links across 5 symbols, all manually verified correct. The rule
  deliberately drops real stories that mention a company only in passing.
- Historical VPS quote rows have no foreign flow. `stocks_build.py` no longer
  coalesces that absence to zero; f5/f20 appear only after a ticker has 5/20
  actual observations. Current artifact therefore honestly has f1 for 341
  tickers and no f5/f20 yet.
- `skip_existing` now checks `warehouse.latest_completed_quarter()` instead of
  whether a ticker has any fundamental row. In early-quarter reporting lag it
  deliberately retries missing prior-quarter data.
- GEX no longer publishes directly from its collector or creates future
  write-only scalar series. Each complete surface is one append-only
  `gex.{sym}.surface` observation (`meta` holds the payload); `gex_build.py`
  publishes the latest snapshot. Legacy scalar history remains untouched.
- `write_json()` keeps ignored pre-build copies in `data/.prev`; all validators
  fail on missing artifacts. VN/BDS/flows/news share `warehouse-writer`; GEX
  keeps its independent cache lineage.

## Current artifact truth

- stocks: 1,751 rows; 1,725 priced; 1,719 fundamentals; 870 momentum; 1,522
  foreign room; 1,292 depth; 341 with one observed foreign-flow day.
- signals: 220 closed, 35 open, total -79.9R (unchanged by design).
- news: 7 rule-based links across 5 symbols in the current 608-article digest,
  all manually audited. Headline-only matching; see the correctness note above.
- flows: 512 days per asset; 12 BTC and 10 ETH issuer series.
- BDS: 192 rows / 102 districts; warehouse has 47,343 listings.
- GEX refresh: BTC 1,004 contracts / 70 profile points; ETH 850 / 63.

## Verification completed

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m compileall -q pipeline
python -m pipeline.core.validate
```

All 8 artifacts pass. Expected warnings only: ~1% tails of `npm`/`ebitm`.
Every data JSON and workflow YAML parses; `git diff --check` passes. Synthetic
regressions cover quote session ordering, partial Chotot checkpoints, previous
artifact snapshots, completed-quarter selection, and news false-code blocking.

Browser QA:

- Rendered the hidden hub route and VIC/SSI ticker pages; valuation bars, price
  provenance, depth, signal history and news layout are correct.
- Rendered issuer tables and live chart. Chart showed Binance live state,
  funding/OI/long-short/Coinbase premium and MA20/50.
- CDP interaction reported 500 bars, live socket, 250 coin options and five
  drawing modes. Synthetic pointer input created hline/trend/fib; hit-test found
  the trend; storage count survived reload; theme mutation called volume
  `setData()` immediately. Test drawings/localStorage were cleaned afterward.
- Round-3 CDP QA at 1440x900 reported LWC 5.2.0, a live Binance socket, 1,000
  bars after automatic paging, true RSI/MACD panes, and a canvas height exactly
  matching pane 0. Invalid MACD 30/20/5 was rejected; valid MACD 8/21/5, RSI and
  five-line Ichimoku mounted; hiding/showing RSI removed/recreated its pane.
- Real mouse events created a trend, selected and moved endpoint A, then global
  Ctrl+Z/Ctrl+Y restored/reapplied it. Magnet snapped to the nearest OHLC and Ctrl
  preserved the raw price. Formula checks passed flat RSI/MFI=50, symmetric
  Bollinger bands, projected Ichimoku spans and split Supertrend output. The only
  browser console error was the pre-existing missing favicon 404.
- Follow-up CDP QA first verified one-shot completion, then the user chose
  continuous drawing. Final QA created two consecutive trends without rearming
  (mode stayed `trend`), then three consecutive horizontal lines. Clicking the
  armed tool, right-click and `Esc` all returned to pan; unfinished Fibonacci
  exited with zero pending points. At 1440x900 the menu was visually inspected
  and clamped
  inside the viewport. Volume, magnet, logarithmic scale, drawing visibility,
  add/manage alert and screenshot composition all changed real chart state; the
  composed PNG was 97,488 bytes. A clean reload had 1,000 bars and no runtime
  exceptions; favicon 404 remains the only console error.

## Remaining work

- Add/configure a git remote before expecting Pages or scheduled workflows to
  run. This is outside the requested local implementation and requires the
  user's repository destination/authority.
- Round 3 after the chart scope: G4 timeframe/watchlist, H2/H3 big tape and order
  book, H4 Level Behavior, then B2 ticker-page valuation/peer improvements.
- Deferred by the original plan: US equities, replay/training, backtester,
  wealth tracker, wiki and PWA. Do not treat those as unfinished A-E work.
