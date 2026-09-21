# BTC / ETH / XAU: market-state research for the existing EA

Research date: 2026-09-21. Production probe: 06:44 UTC / 10:44 Dubai / 13:44 ICT.

User follow-up: the final Chart must show **MUA / BÁN / CHỜ** for BTC, ETH and
XAU with numerical technical and macro explanations. The current product
requirements and continuation sequence are in
[Chart decision panel specification](chart-decision-panel-spec.md). An observer
is an intermediate stage, not the final requested outcome.

## Decision

Build a small, auditable market-state monitor and evaluate an entry permission
layer per strategy family. Start with broker price structure, compression,
expansion and execution conditions. The existing Hub provides much of the
display and indicator groundwork, but it does not yet provide a tested EA gate.

The useful distinction is **choppy range, compressed range preparing a break,
new breakout, established trend, and extended move awaiting a pullback**.
Suppressing every ranging period would suppress the preparation phase of
several existing strategies. Waiting for strong ADX on every entry would also
delay or exclude some initial breakouts.

This is a research/design deliverable, not a live enable/disable instruction.
No EA, preset, account, production page or scheduled collector was changed.
No gated-EA backtest was performed, so there is no measured improvement claim.

## Evidence and scope

- Inspected `CLAUDE.md`, `HANDOFF.md`, the chart/GEX apps, source collectors,
  warehouse schema, signal builder and relevant workflows.
- The requested `D:\MT5\10pair\_edit` does not exist. The inspected source is
  `D:\MT5\10pair_edit`, containing the three named FINAL EA files.
- Downloaded the public Hub/chart HTML and six public JSON artifacts. Normalized
  chart and Hub HTML hashes match the local source exactly.
- Read the actual EA entry, management and dispatch paths. The input inventory
  is **source defaults**, not verification of a live VPS preset or EX5 binary.
- Read existing report headers and XAU cache metadata; did not rerun or certify
  those historical tests. The report hashes do not link their binaries to the
  currently inspected source.
- A connected browser was unavailable. HTTP/content checks succeeded, but this
  session did not visually validate the page or verify live WebSocket counters.
- Reproducible audit: [script](research/trend-gate-2026-09-21/audit.py),
  [timestamped evidence](research/trend-gate-2026-09-21/evidence.json).

## What is already available

| Component | Current implementation | Use for this project | Boundary |
|---|---|---|---|
| BTC/ETH OHLCV | Binance spot REST and kline stream; Hyperliquid candle fallback in `apps/chart/index.html` | Structure, ATR, direction, compression, breakout visualization | Exchange spot/perpetual data are not the MT5 broker CFD execution feed; fallback venue must remain explicit |
| Indicators | EMA/SMA, ATR, ADX, Choppiness, Bollinger, Keltner, squeeze, Donchian, Supertrend | Reuse formulas/UI after parity checks | Multiple transforms of the same prices are not independent confirmations |
| Multi-timeframe panel | H1/H4/D1 for crypto, SMA50 direction + Supertrend direction, scaled by ADX | Context display | Not a validated trend-strength filter; see defects below |
| Book / Big Tape | Binance spot depth/tape and additional OKX/Bybit tape paths | Optional breakout context | Tape is selectively observed and thresholded, not a complete trade history |
| Tape persistence | Browser localStorage, up to 12,000 events / 30 days, with observation windows | Honest local session review | No collection while the browser is absent; quotas trim history; not a continuous research database |
| Market Structure flow | Binance USD-M `aggTrade` / `forceOrder`, session CVD | Optional participation/positioning context | CVD resets on symbol/tab session; observed liquidation stream is partial venue data |
| Futures context | Funding, OI, long/short ratios | Later ablation features | Current snapshots do not establish OI changes over a historical horizon |
| GEX | Deribit BTC/ETH options, modelled gamma/flip/strikes; hourly scheduled refresh | Optional level context | Dealer sign is assumed, not observed; not a direct trend classifier |
| ETF flows / macro context | Daily/static JSON artifacts | Slow context if fresh | Too slow for entry timing, and some artifacts are stale |
| Gold / DXY | Yahoo daily closing series via `pipeline/sources/fx_rates.py` | Macro context | `GC=F` is COMEX futures; not broker XAUUSD intraday OHLC/bid/ask |
| Signal backtest conventions | `signals_build.py` with explicit rules, results and costs | Reuse reporting principles | It is a VN equity daily strategy, not a test of the MT5 EAs |
| Storage | Append-only DuckDB observations with `fetched_at` | Reuse provenance and as-of patterns | Inspected schema has no dedicated continuous broker bars/state/EA-decision history |

PAXG/XAUT dossier files are tokenized-gold assets, not substitutes for XAUUSD.
There is no inspected broker XAU chart adapter in this app. The chart advertises
14 intervals but omits H3 and H6, although the EA uses both. Binance itself
supports H6; the restriction here is the app's interval list. Native MT5 bars
are preferable to inventing an H3 alignment from an exchange feed.

### Production freshness observed on 2026-09-21

| Public artifact | Build timestamp UTC | Underlying observation | Assessment |
|---|---|---|---|
| `gex_btc.json` | Sep 21 04:46:48 | 968 option contracts in snapshot | About 1h57m old at probe; useful only with an explicit age limit |
| `gex_eth.json` | Sep 21 04:46:50 | 890 option contracts in snapshot | Same limitation |
| `flows.json` | Sep 8 06:44:47 | BTC/ETH last session Sep 4 | Stale for a current market verdict |
| `crypto/context.json` | Sep 8 06:05:15 | MVRV as of Sep 7 | Stale; `failed=[]` is not proof of current freshness |
| `fx.json` | Sep 19 01:03:07 | Gold/DXY as of Sep 18 | Prior daily session across a weekend, not live execution data |
| `signals.json` | Sep 9 03:40:28 | VN signal artifact | Not this strategy universe |

Local GEX JSON was dated Sep 11 while production was Sep 21. Always distinguish
checkout artifacts from production. Do not infer a current BTC/ETH trend from
the sign of the above GEX snapshot or a current XAU trend from a daily close.

### Specific chart issues to resolve before using it as a gate

1. `scoreBars` clamps ADX conviction to at least 0.35, while the UI calls scores
   above 0.15 bullish. If price/SMA and Supertrend both vote up, **ADX=5 still
   gives score=0.35 and an up label**. This is a deterministic formula example,
   not a market observation. Directional agreement does not establish tradable
   trend strength. The score is not a calibrated probability.
2. REST candle conversion discards Binance close time. `scoreBars` takes the last
   candle, and stream conversion does not preserve the kline closed flag for
   gating. The current candle is appropriate for drawing but can change before
   its close. Historical permission must use only fully known observations.
3. `refreshMtf` is called on panel/context changes; inspected call sites do not
   run it automatically at each higher-timeframe close. A moving chart does not
   prove that the multi-timeframe verdict has been refreshed.
4. GEX, spot OHLC, perp positioning and broker quotes have different timestamps,
   venues and price bases. A composite score must not hide these differences.
5. An hourly GitHub Actions artifact or a browser-only calculation cannot act
   as a reliable continuously running execution service.

## What the EA actually trades

All 11 active branches below have a default risk budget of 0.2% of the sizing
capital. All three inspected sources default to `CustomBalance=50000`.
This does not override user-selected live presets or establish economic capital.

| Symbol | Active branch and native timeframe | Entry behavior read from source |
|---|---|---|
| BTCUSD | PP3 H3 | Fractal-level stop orders, EMA-based management |
| BTCUSD | PP5 H4 | Three-bar momentum pattern; market entry |
| BTCUSD | PP7 H3 | EMA direction with stochastic pullback conditions |
| BTCUSD | PP9 H6 | After a high exceeds a fractal, place a SELL STOP below that bar; reverse for a low sweep |
| ETHUSD | PP7 H8 | EMA direction with stochastic pullback conditions |
| ETHUSD | PP9 H3 | Same sweep/reversal entry family as BTC PP9 |
| ETHUSD | PP10 H2 | Stable Donchian boundary; stop orders to break the range |
| XAUUSD | PP3 H3 | Fractal-level stop orders |
| XAUUSD | PP4 H1 | Extended compression followed by a Keltner breakout |
| XAUUSD | PP6 H1 | Session range breakout via pending orders; timed management |
| XAUUSD | PP12 H12/H3 | Prior breach of a large-frame fractal and small-frame break in the opposite direction |

Source anchors:

- BTC `Leon_LIVE_BTCUSD_FINAL.mq5`: inputs 2-15; PP3 at 825; PP5 at 1067;
  PP7 at 1303; PP9 opposite-side stop orders at 1530-1540; dispatch at 1898.
- ETH `Leon_EA_LIVE_ETHUSD_FINAL.mq5`: inputs 2-15; same PP9 order-side pattern;
  PP10 `CheckStableUpper_PP10` / `OnTick_PP10`.
- XAU `Leon_EA_LIVE_XAUUSD_FINAL.mq5`: PP4 at 1117/1174; PP6 at 1418;
  `OnDeinit_PP6` at 1444; PP12 input/entry at 1967/2069; dispatch at 2121.

The system may benefit from persistent moves after entry, but **its entry
families are mixed**. PP9 and PP12 are not generic trend-continuation entries.
A bullish higher-timeframe gate could exclude their profitable bearish turns;
the opposite could be true too. Measure this by branch and direction.

### Entry permission is different from switching the whole EA off

- Current `Use_PP` flags dispatch entire branch handlers, including management.
  Adding `if (!trend) return` at the top of `OnTick` would also skip management.
- Existing pending orders can still trigger after permission is withdrawn.
  Define cancellation/expiry by symbol, magic and branch; verify cancellation
  acknowledgement and handle a fill racing with cancellation.
- XAU `OnDeinit_PP6` attempts to close PP6 positions and cancel pending orders.
  Detaching/reinitializing an EA is therefore not a neutral pause.
- Several branches use 900-second windows with state initialized on attachment.
  Restart behavior, stale setup re-entry and duplicate prevention need replay.
- Some fractal routines inspect bar index 0. Reproducing the original strategy
  requires its actual detection time, not putting a known pivot on its historical
  center bar. A closed-bar refactor would be a separate strategy change.

Recommended future behavior: continuously run management, and gate only **new
entry requests**. Broker-held stops remain; original trailing/exit management
continues at its original cadence. Disabled terminal AutoTrading still runs the
EA code but bans EA trade requests, so it also prevents requested modifications
and exits; it is not an entry-only control. [MetaQuotes OnTick documentation](https://www.mql5.com/en/docs/event_handlers/ontick).

## Existing historical evidence: useful starting material, not a new validation

Three `10pair_edit/BACKTEST/CBMOI/*_moi.html` files were inspected:

| Report | Expert name | CustomBalance in report | Trades | Profit factor | Reported max relative equity DD |
|---|---|---:|---:|---:|---:|
| BTCUSD | P11_BTCUSD | 75,000 | 1,760 | 1.53 | 6.78% |
| ETHUSD | P11_ETHUSD | 75,000 | 1,587 | 1.59 | 5.75% |
| XAUUSD | P11_XAUUSD | 100,000 | 2,825 | 1.28 | 7.04% |

These are values extracted from existing report headers, **not results rerun
in this research**. Initial deposit is 50,000 in each individual report, so the
DD percentages are not a forecast for a combined 10,000 account. Expert names
also differ from the current FINAL filenames. History quality says 100%, 100%,
98%; that field alone does not establish real-tick coverage or a testing mode.
The configured end date is Dec 31, 2026, but the last timestamp in each report's
deal section is Sep 16. Do not claim future history through December.

The results justify studying these assets, but they do not establish that they
are the best three across the original portfolio, or that filtering improves
them. Audit exact source/EX5/set/INI/log provenance and recover per-PP entries,
exits, costs, positions and equity before comparing variants.

There is also an existing XAU M1 cache at
`D:\MT5\research\gold_5k_20260915\data`. Its metadata reports 2,369,156 rows from
2020-01-02 through 2026-09-14; all seven annual pickle files exist. This session
checked metadata/files, not every candle. It is useful for feature exploration,
not a replacement for real bid/ask tick replay of pending fills and SLs.

`D:\MT5\backtest\volfilter.csv` and its launcher show that an older XAU
ATR-percent filter experiment exists. It uses a different test EA, PP mix and
sizing. Treat it as prior exploratory work, not evidence for this proposed gate.
Do not run that launcher unchanged: it deletes shared tester output and uses
the normal terminal installation.

## Proposed first model to test

Keep **state, direction, setup and permission** separate. A market can have a
bullish context and still be too extended for a new long, or be compressed and
ready for either-direction breakout without an established trend.

| State shown to the user | Observable condition | Candidate entry permission |
|---|---|---|
| WAIT — choppy / unclear | Low directional efficiency, frequent overlap, no qualifying compression or break | No new continuation entries |
| ARMED — compressed range | Narrow range/bandwidth relative to its own history; valid structural boundaries | Selected breakout branches may prepare stop orders; no automatic market chase |
| BREAKOUT UP/DOWN | Closed bar clears the previously known boundary with a buffer | Selected breakout/momentum branches may enter in that direction under their own rules |
| TREND UP/DOWN | Direction persists with adequate strength/efficiency | Selected continuation branches may seek their existing setups |
| WAIT PULLBACK | Trend direction exists but entry is too extended | Withhold new chase entries; restore permission only after a qualifying reset |
| BLOCKED / UNKNOWN | Missing/stale core data, excessive costs, event restriction, risk limit | No new entries; continue position management |

These states describe evidence at the decision time. ARMED does not predict
which way price will break or guarantee that it will break. BREAKOUT does not
guarantee continuation. Earlier participation accepts more false breaks; later
confirmation accepts more missed profit and worse entry distance.

### Minimal features and one explicit exploratory seed

Use H4 as a shared display context and calculate setups on each branch's actual
native timeframe. H1 can provide earlier alerts; H2/H3/H6/H8/H12 branch bars
must retain broker boundaries. D1 is context, not a mandatory veto for every
H1 setup. M15 is optional observation, not an untested replacement entry engine.

The following numerical values are **research seeds**, not optimized or approved
trading thresholds. Register them before measuring outcomes:

- Direction: EMA20 versus EMA50 and the sign of the EMA20 slope over five
  completed bars. Normalize slope by ATR for comparisons between assets.
- Trend strength: ADX14 >=22 plus efficiency ratio ER20 >=0.35 as one candidate.
  ER20 = `abs(C[t]-C[t-20]) / sum(abs(diff(C)), 20)`. Zero denominator is unknown,
  not strong trend. Compare with simpler ADX-only and ER-only alternatives;
  Choppiness can substitute for ER rather than becoming another mandatory vote.
- Compression: Bollinger bandwidth20 at/below the 20th percentile of the prior
  250 completed bandwidth observations. Maintain a separate ARMED branch even
  when ADX is low. Do not require every squeeze to match the EA's PP4 formula;
  reproduce its original definition when testing that branch.
- New breakout: close above the maximum high of the **previous** 20 completed
  bars plus 0.1 ATR, or below the previous minimum minus 0.1 ATR. Exclude the
  signal bar from its own boundary. Freeze the crossed boundary for subsequent
  extension/retest calculations. This generic definition annotates state;
  native fractal/session/Donchian setup conditions still govern actual EA entry.
- Extension: initially flag distance greater than 1 ATR beyond that frozen
  boundary as WAIT PULLBACK. A candidate reset is a return within 0.25 ATR of
  the boundary followed by a closed-bar reclaim, while structure remains valid.
  Retest permission does not itself create a trade if the EA has no entry.
- State retention: test a wider off threshold (ADX<18 / ER<0.25 for two native
  closes) to reduce rapid switching; structural invalidation, stale data and
  execution-risk blocks take priority. Fix exact transition priority and state
  expiry before the first replay. Do not tune these values on the final holdout.

State priority for the first specification should be: core-data/risk block,
structural invalidation, valid new breakout, established trend plus extension,
compression, then WAIT. A low-ADX breakout is allowed to reach BREAKOUT without
first passing the established-trend test. This exception is essential to the
user's intended early participation and must be evaluated explicitly.

### Branch policy to compare, not deploy as an assumption

- PP3/PP6/PP10: test preparation in ARMED plus valid native breakout setups.
- PP4: test compression-release permission; strong ADX need not precede release.
- PP5: test momentum permission and the cost of excluding extended entries.
- PP7: test direction/strength permission while preserving its pullback entry.
- PP9/PP12: evaluate sweep/reversal conditions separately. Initially preserve
  their original behavior in the family-gate comparison, apart from common
  data/execution blocks; log what a universal trend gate would have excluded.

Version the permission policy. No aggregate 0-100 score should conceal which
condition or which PP blocked a trade.

## Which extra data is worth adding

**Required first:** broker-native completed OHLC for all three assets, accurate
bar close times, bid/ask spread and quote age, trading sessions, symbol contract
specifications, and per-PP entry/position records. Export/record without orders.
Backfill BTC/ETH broker history with explicit coverage checks; their continuous
M1/tick coverage was not established in this audit.

**Useful later:** persistent exchange OI, funding, taker flow and options
snapshots, with venue/time provenance. Binance's standard OI-history endpoint
documents only the latest month. A browser limit=1 request cannot measure an OI
trend, and current snapshots cannot be joined into old tests as if known then.
[Binance OI documentation](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics).

Spot/perp taker volumes from klines offer a simpler persisted flow proxy than
recording a full order book. Binance supplies quote volume and taker-buy quote
volume; their difference can define per-bar signed taker notional, with explicit
venue/market labels. It is not identical to the current thresholded Big Tape.
[Binance candle fields](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints).

**GEX remains optional.** Deribit's endpoint supplies OI/IV, not the inventory
identity/direction of dealers. The repo signs calls positive and puts negative
as a model convention. Hence "negative GEX implies trend" is a hypothesis, and
"positive GEX means disable" is not justified without testing. Missing chains
remain UNKNOWN. [Deribit public chain fields](https://docs.deribit.com/api-reference/market-data/public-get_book_summary_by_currency).

**CPI and other events:** if CPI in the request means the inflation release,
treat it as a scheduled event-risk layer. The event can produce expansion but
does not determine a reliable long/short direction. Start by logging CPI,
employment releases and FOMC, then compare a predeclared short entry embargo
with no embargo; post-event spread normalization and a completed confirmation
bar matter more than a fixed timer alone. A 15-minute-before/30-minute-after
window is only an experiment, not an established optimum.

Use [BLS CPI schedule](https://www.bls.gov/schedule/news_release/cpi.htm),
[BLS employment schedule](https://www.bls.gov/schedule/news_release/empsit.htm)
and [Federal Reserve FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
or MT5's calendar with explicit provenance. MT5 calendar timestamps use trade
server time. Archived calendar replay needs DST correction and must not reveal
actual/revised values before publication; a calendar cache is needed in the
tester. [MT5 calendar](https://www.mql5.com/en/docs/calendar),
[calendar tester limitations](https://www.mql5.com/en/book/advanced/calendar/calendar_cache_tester).

Do not require ETF flows, MVRV or expensive full-depth history for version one.
The first question is whether simple, reproducible price-based permissions add
value to the actual EA after costs.

## Validation: the filter must earn its place

1. Pin source, EX5, exact inputs, broker symbol specification, data coverage and
   tester mode. Reproduce the continuous-run baseline. Test on a separate
   runtime; do not stop the user's terminal or overwrite shared tester files.
2. Annotate the baseline's decisions with features known at the decision time.
   Initial diagnostic: per-PP P/L in chop/compression/breakout/trend, long/short,
   news windows and cost buckets. Log denied opportunities and eventual outcomes.
   Do not label a period "trending" using its future profitability.
3. Compare a deliberately small set: A existing EA, B simple ADX gate,
   C family-aware price gate, D C plus event/execution blocks. Add flow/GEX only
   if incremental out-of-sample evidence supports the added data dependency.
4. Rerun the complete EA, not just delete losing trades from an existing list.
   Denied entries change stacking, cooldown/state, pending orders, capital and
   later trades. Keep sizing/exits the same when isolating gate effects.
5. Use chronological walk-forward folds and purge/handle open positions across
   boundaries. Previously studied 2025-26 data are not an untouched holdout just
   because a new report calls them one. Reserve a genuinely unused period or
   collect locked-rule forward demo evidence; disclose prior asset selection.
6. Pending-order breakout tests need broker bid/ask and tick-order resolution.
   Confirm real-tick coverage and inspect tester logs for generated fallback.
   Include commission, spread, swap, slippage/gaps and partial execution rules.
   Apply costs to lot/contract size, not a universal dollar charge per trade.
   [MetaQuotes execution and spread rules](https://www.metatrader5.com/en/terminal/help/algotrading/testing_features).
7. Report net expectancy in R, PF, equity DD including floating P/L, recovery
   time, turnover/exposure, entry delay, false-break frequency, denied winners,
   lost top-decile winners and retained profit. Report sample counts by PP/state.
   Use time-block uncertainty estimates; thin cells are inconclusive.
8. Measure combined portfolio equity, concurrent stop/pending risk, margin and
   P/L correlation under stress. Price correlation alone does not establish EA
   P/L correlation. Concentrating on three assets does not authorize raising
   their lot sizes or transferring the full ten-asset risk budget to them.

For a concrete predeclared screening target, compare whether C reduces equity
DD by at least 20% while retaining at least 85% of A's net profit at identical
sizing in the evaluation sample. These are proposed project tradeoffs, not
measured results or universal standards. Also report the full tradeoff curve:
a gate that meets a ratio by leaving almost no trades is not automatically good.
Require consistency across folds/assets, cost stress and nearby parameters,
then a locked-rule forward demo with enough independent setups. If no robust
improvement survives, keep the original entry logic and use the monitor only
for observation/risk context.

Broader evidence supports trend following as a research family, but its scope
must stay clear: the cited AQR study examines 58 futures/forwards and much longer
return horizons. It does not validate H1-H12 crypto/gold EAs or this gate.
[Moskowitz, Ooi and Pedersen, Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum).

## Implementation sequence after this research

**First deliverable: three-symbol observer.** Add a compact panel to Chart with
symbol/source, data age, direction, state, compression, strength, frozen break
levels, ATR extension, next important event and reason codes. Show the native
PP timeframe and distinguish UNKNOWN from sideways. Store decision snapshots
so a user can inspect what was actually visible before a move. This first
observer does not issue orders or advertise a tested permission verdict.

**Second deliverable: reproducible gate experiment.** Keep market data and
snapshots in a private/local store outside committed `data/` candle history.
Suggested records: `market_bar`, `regime_snapshot`, `event_snapshot`,
`ea_decision`, with source, instrument, timeframe, `bar_close_at`, `observed_at`,
version and data-quality flags. Keep DuckDB writes serialized. Replay feature
snapshots and EA decisions using exactly the same bar boundaries.

**Third deliverable: gated demo clone, only if justified.** Compute the execution
gate beside the EA from broker data; expose a sanitized read-only snapshot to
the Hub. Website/network loss must not stop existing-position management.
Use a separate demo build/magic namespace, explicit restart reconciliation,
pending cancellation/expiry and reason logs. Do not expose account identifiers,
credentials or private positions through the public static site.

Suggested ownership of future changes:

| Area | Concrete work |
|---|---|
| `apps/chart/index.html` | Observer, freshness, closed-bar MTF refresh, H3/H6/broker identity support |
| New pure feature module | Reusable formulas, deterministic state transitions, parity fixtures |
| Local MT5 data/export layer | Broker bars/spread/specifications and provenance; no order capability needed |
| Separate research runner | Walk-forward comparison, denied-opportunity log, full equity/cost outputs |
| EA demo clone | Entry-only permission checks plus pending lifecycle; preserve management |

No new external paid feed is needed to answer the first price-based research
question. The main missing work is broker data alignment, branch-aware testing
and an auditable continuously updated state history.

## Reproduce this audit

The audit uses Python's standard library and performs bounded public HTTP reads
plus local file reads. It rewrites only its own `evidence.json`:

```powershell
& 'D:\MT5\mcp\.venv\Scripts\python.exe' docs/research/trend-gate-2026-09-21/audit.py
```

The existing interpreter is called directly because PowerShell's activation
script was blocked by the machine's execution policy. No execution policy,
packages, shared environment, account or terminal setting was changed.
