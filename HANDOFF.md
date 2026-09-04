# Handoff

Updated 2026-09-03 23:30 (Asia/Dubai). Read `CLAUDE.md` first. The recovered
Claude plan is `C:\Users\LEON_RM\.claude\plans\nh-gi-ho-n-calm-newell.md`;
its A-E status table and final note are synchronized with this file.

## Outcome

The recovered TurtleTrading comparison plan is complete. The site now has live
crypto charting and drawings, a per-ticker VN equity page, deterministic ticker
news, exposed SSI order-book depth, ETF issuer breakdown, clearer GEX and the
full signal track record. Structural hardening from the prior pass is preserved.

The worktree is intentionally uncommitted on top of `f6c610b`; the user did not
ask for a commit. Do not reset it. There is no git remote, so workflows have not
run in GitHub Actions yet. Local server PID 28552 still serves the repo on 8811.

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
- signals: 214 closed, 35 open, total -76R (unchanged by design).
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

## Remaining external work only

- Add/configure a git remote before expecting Pages or scheduled workflows to
  run. This is outside the requested local implementation and requires the
  user's repository destination/authority.
- Deferred by the original plan: US equities, replay/training, backtester,
  wealth tracker, wiki and PWA. Do not treat those as unfinished A-E work.
