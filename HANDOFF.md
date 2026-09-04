# Handoff

Updated 2026-09-04. Read `CLAUDE.md` first. The active Claude plan is
`C:\Users\LEON_RM\.claude\plans\nh-gi-ho-n-calm-newell.md`.

## Chart tooling now matches the reference set

**62 indicators, 34 drawing tools** — the same counts as
`chart.turtletrading.vn`, reached by writing the formulas out rather than
copying an implementation. Verified in headless Chrome over 1000 real bars:
all 62 compute finite values and mount at once across 38 panes; all 34 draw
from synthetic mouse clicks, render, hit-test and survive reload.

Two indicators are deliberately named for what they are: the HMM regime is a
two-state classifier with a persistence filter (label says "xấp xỉ"), and
market structure is a swing-high comparison. Neither claims more than it does.

## Branch and remote

Repo is public at `https://github.com/HugoLeon1199/leonhub`, Pages serving
`hub-upgrades` at `https://hugoleon1199.github.io/leonhub/hub/`.

Commits on `hub-upgrades` above `f6c610b`:
- `b2a9900` rounds 1-2 (pipeline hardening, ticker page, per-ticker news)
- `66c9572` F1/G0/G1/G2/H1 — backfill, LWC v5, drawing registry, GEX tiles
- `4b56c6d` F2 lazy history paging
- `7a826db` 16-indicator registry
- `87b4c5d` drawing coordinates via logical index
- `eb11bc9` +15 drawing tools
- `47c9e34` +10 drawing tools (34 total)
- `b8d0707` +33 indicators (49 total)
- `8772619` +13 indicators (62 total)

## Reference source

`https://chart.turtletrading.vn/multi.js` serves **unminified** (1,011,167
bytes, 10,876 lines) — the production page loads `multi.min.js` but the plain
file is public. Cloudflare allows it with a browser User-Agent:

```bash
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
curl -sL -A "$UA" --compressed -o multi.js "https://chart.turtletrading.vn/multi.js"
```

Saved under `C:\Users\LEON_RM\Downloads\leon\site\` along with every app page.
No application-source licence notice was found; behaviour and formulas were
reproduced, implementation was not pasted.

## Coordinate handling — the fix that mattered

Drawing points landed slightly off the click and drifted with zoom. The cause
was extrapolating from the pixel spacing of the last two bars, which is exactly
what zooming changes. Now `coordinateToLogical` / `logicalToCoordinate` convert
through the chart's own bar index, which stays defined past the last bar and off
both edges. Measured: 0.0px vertical error, <=1.3px horizontal (bar-centre
rounding), 0.33px in future space.

Bar interval is the **median** of recent gaps, not one difference — a weekend
would otherwise report Monday's step as three days. Daily series hand back
`{year,month,day}` instead of a timestamp and are converted.

**Drawings remain a market-coordinate canvas overlay, not LWC Series
Primitives.** Do not describe them as a primitive migration.

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
