# Chart decision implementation — 2026-09-21

## Delivered surface

`apps/chart/index.html` loads `decision-panel.js`, `decision-engine.js` and
`decision.css`. Three keyboard-accessible buttons expose MUA/BAN/CHO, select the
instrument and open a numerical evidence dialog. The UI uses accented Vietnamese.
The decision horizon is always H1 entries with H4 context, independently of the
chart's selected zoom/timeframe. No trading API, EA toggle or order is called.

BTC and ETH prices are explicitly Binance **Spot USDT**, not broker CFDs. There
is no silent fallback in the decision panel; a Binance outage yields WAIT even
if the separate chart can show Hyperliquid. XAU has an explicit broker-only
contract at `data/chart/xau.json`, currently `not_connected`. Selecting it clears
the previous candles and explains the absent broker chart. GC=F is macro context
only. A full live XAU chart/decision service remains outstanding.

## Locked research rule: trend-gate-1.0.0

- At least 280 closed bars per H1/H4 frame; fetch 500. Closed means the exclusive
  UTC close boundary is <= evaluation time. Duplicate, invalid or gapped crypto
  bars fail. EMA20/50 use SMA seeds; ATR14 and ADX14 use Wilder smoothing; ER20 is
  absolute 20-bar displacement divided by the sum of absolute close changes.
- Both H4 and H1 must agree: close above EMA20 above EMA50 and EMA20's five-bar
  slope >0.05 ATR for up; symmetric inequalities for down. ADX >=22 and ER >=0.35
  on both frames. This is a chosen experimental threshold, not an estimated win
  probability or a result from the user's EA backtests.
- Entry requires the latest H1 close to exceed the **previous** 20-bar high/low
  by >0.1 ATR; or a retest of the most recent breakout within 12 H1 bars. Freeze
  that breakout's level and ATR. A retest touches within 0.25 ATR and closes
  >0.1 ATR beyond it with a same-direction candle body. An intervening close
  back through -0.25 ATR invalidates that breakout.
- Do not chase: live price >1 breakout ATR beyond the level, >0.5 H1 ATR from
  the last H1 close, or >2 H1 ATR from EMA20 means WAIT. Price inside the 0.1 ATR
  margin also waits. Losing the level by 0.25 ATR invalidates permission; this
  is **not** a broker-aware stop-loss or a sizing recommendation.
- Bollinger(20,2) bandwidth at/below the 20th percentile of the prior 250 widths
  labels compression when H4 is not established. Compression alone never buys.
- A quote older than 90 seconds, an H1/H4 bar older than its complete interval,
  future timestamps, or an unverified crypto clock produces WAIT. At an hour
  boundary the old verdict expires before the next fetch arrives. Compare the
  client clock to Binance server time (maximum 30-second difference).

No hysteresis/PP-specific state filter or profitability estimate is shipped.
Changing these rules requires a new version and asset cache keys. Do not retune
against the live probe below.

## Freshness and evidence

Prices refresh about every 45 seconds while visible (15-second expiry checks).
Closed features are cached only when they match the current UTC H1/H4 buckets;
missing a just-closed candle triggers another fetch. Static context refreshes
every five minutes. Fetch failures remove eligibility, never fill with zero.

| Evidence | Age ceiling | Meaning |
|---|---|---|
| DXY / GC=F daily close | 4 calendar days | use actual final history point, not mixed live quote/change |
| ETF BTC / ETH | 5 calendar days | latest published observation and sum of last 5 rows; holidays can be zero rows |
| Deribit GEX | 8 hours | dealer sign is assumed; not a directional vote |
| Fear & Greed | 2 days | source observation timestamp required |
| BTC MVRV | 3 days | a new fetch cannot make an old observation current |

Both observation and artifact retrieval timestamps must predate evaluation and
meet the TTL. Missing/stale/future facts remain visible and excluded. ETF net
flow sign and DXY daily moves >=0.2% are displayed as potential counterevidence
against H4 direction. They do not override the technical rule. Calendar,
OI/funding and session-only tape are explicitly not part of the rule.

At most 240 state/bar changes are stored in this browser's localStorage with
all evaluator inputs and outputs. JSON export replays the rule from its input
features; it is not a centralized continuous candle archive or a full-EA replay.
Only time spent with the page running is covered. Storage failure is displayed.

## Source repair and dated integration probe

Farside ETH returned HTTP 200 but zero parsed rows because `Total` was rejected
by the all-uppercase ticker-header detector. The parser now ignores Total when
recognizing tickers, uses the published total, preserves real zero, refuses
partial-market sums and rejects an empty net-flow result. Sources were collected
sequentially into the append-only warehouse and `flows.json` rebuilt.

- ETF through 2026-09-18: BTC +433.0m USD, five published rows +6.1m; ETH +143.7m,
  five rows -140.6m. The rolling 730-day publication contains 511 rows per asset
  versus 512 in the older Sep 4 snapshot: the window rolls; this is below the
  validator's regression threshold. ETH issuer coverage is now 11.
- Crypto context refreshed Sep 21: Fear & Greed 70 dated Sep 21; MVRV 1.484 still
  dated Sep 14 and correctly excluded as stale. A daily context workflow was
  added; a successful request is not treated as source freshness.
- Live read-only probe at **2026-09-21 09:14 UTC**: BTC WAIT (extended), H1 ADX
  24.74 / ER 0.572, H4 ADX 38.59 / ER 0.612; ETH WAIT (no fresh entry setup), H1
  ADX 37.89 / ER 0.482, H4 ADX 36.90 / ER 0.535. H1 closed 09:00 UTC; H4 closed
  08:00 UTC. This is a dated correctness example, not a current recommendation.

Primary references: [Binance market endpoints](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market),
[Farside BTC](https://farside.co.uk/bitcoin-etf-flow-all-data/),
[Farside ETH](https://farside.co.uk/ethereum-etf-flow-all-data/),
[MT5 closed-bar positions](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py).

## Verification and continuation

```powershell
deno test apps/chart/decision-engine.test.js
deno check apps/chart/decision-engine.js apps/chart/decision-panel.js
python -m unittest pipeline.sources.test_etf_flows
python -m pipeline.core.check_apps
python -m pipeline.core.check_linkable
python -m pipeline.core.validate
deno run --allow-net=api.binance.com --allow-read=data pipeline/tools/chart_decision_probe.js
```

11 rule tests and 3 ETF parser regressions pass. The live probe passes serialized
input replay. All 35 inline scripts, external modules, shared link blocks and
18 artifact validators pass. Existing fundamental/land-price/US-quote warnings
remain; there is no visual browser QA claim. Local Chart HTTP returned 200.

`pipeline/tools/mt5_chart_snapshot.py` provides a read-only extraction path using
the **same** JavaScript formulas via `mt5_chart_features.js`. It refuses to start
a terminal, change login, enable trading, or attach to a different data directory.
It publishes no account identifier/positions and stores no candles. Default
output is gitignored `.tmp_xau_snapshot.json`; `--dry-run` writes nothing:

```powershell
& D:\MT5\mcp\.venv\Scripts\python.exe -m pipeline.tools.mt5_chart_snapshot `
  --terminal D:\MT5\research\gold_5k_20260915\runtime\terminal64.exe --dry-run
```

The existing isolated research runtime was started hidden with trading disabled,
but three connection attempts failed (`-10004 No IPC connection` or missing
terminal info). Only that research runtime was stopped afterward. No successful
broker export is claimed; no EA source, preset, account login or orders changed.

Next work in order:

1. Restore the isolated research terminal's IPC/data connection; validate broker
   identity, bid/ask spread and native UTC bar alignment. Finish a continuously
   refreshed HTTPS XAU feed/chart. A GitHub Pages snapshot is insufficient for
   90-second quote freshness; do not relabel a static file as a live feed.
2. Collect a dated CPI/NFP/FOMC calendar with timezone conversion and provenance.
   Current UI links to official BLS/Fed calendars; no automatic news blackout.
3. Pin current EA/EX5/set/data provenance; add native H3/H6/H8/H12 and family-aware
   entry gates preserving position/pending-order management. Compare full-EA
   baseline vs filters with the same costs/sizing and out-of-sample windows.
4. Record forward/demo outcomes, denied winners and risk. No profitability or
   universal PP-gating claim can be made from this UI release.
