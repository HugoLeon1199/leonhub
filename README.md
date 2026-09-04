# LEON Hub

An investment intelligence hub covering crypto, Vietnamese equities, real
estate and macro. Public data is collected from source, aggregated offline into
a point-in-time warehouse, and published as static JSON that single-file browser
apps read directly.

No framework, no bundler, no server. Hosting is static; the only moving part is
a set of scheduled collectors.

## Why it is built this way

Every number on the site should be traceable to a source, a fetch time, and a
sample size. That constraint drives the architecture more than any technology
choice:

- **The warehouse is append-only.** Observations carry `as_of` (when the fact
  was true) and `fetched_at` (when we learned it), and nothing is overwritten.
  A query filtered on `fetched_at <= t` reproduces exactly what was known at
  `t`, which is the only way a published track record can be honest rather than
  fitted after the fact.
- **Published JSON is a view, never the store.** Rebuilding a file is always
  safe, and a bad build can be rebuilt rather than recovered.
- **Aggregates expose their confidence.** BĐS publishes from five observations
  for geographic coverage, but labels 5–19 as exploratory and defaults the UI
  to the safer ≥20 view. `n` travels with every cell.

## Layout

```
hub/            Tab shell — routing, theme, PWA
apps/           One self-contained page per tab
  brief/        Daily cross-asset summary
  chart/        Crypto candles + perp context
  stocks/       VN equity screener
  ticker/       Per-ticker valuation, flows, signals, depth and news
  bds/          34-province map, trends, profiles and price screener
  signals/      Rules-based signals with a published track record
  gex/          Options gamma exposure
  flows/        ETF and institutional flows
  positioning/  Crypto funding/basis + VN breadth
  training/     Candle replay, paper orders, SL/TP and saved sessions
  quant/        No-look-ahead backtest, parameter sweep and exports
  us/           Curated US delayed-OHLCV dashboard
  wealth/       Local-only assets, debts, cash flow, goals and journal
  wiki/, blog/  Educational reference and reproducible research notes
pipeline/
  core/         http.py, warehouse.py, validate.py
  sources/      One module per external source
  transform/    Aggregation and derived metrics
  publish/      Writes data/*.json
data/           Published JSON — the contract the apps consume
warehouse/      DuckDB (gitignored; monthly .gz snapshot only)
```

## Getting started

```powershell
pip install -r requirements.txt

# 1. Ticker reference + daily prices + foreign flow (~12s for 1,751 tickers)
python -m pipeline.sources.vn_equity --what board

# 2. Quarterly fundamentals back to 2018 (~7 min for 1,722 tickers)
python -m pipeline.sources.vci_direct

# 2b. SSI three-level order book + remaining foreign room
python -m pipeline.sources.ssi_board --with-depth

# 3. Real-estate listings — start this early, history cannot be backfilled
python -m pipeline.sources.chotot --discover-regions
python -m pipeline.sources.chotot --regions 12000,13000 --max-pages 2

# 4. Cross-market flows
python -m pipeline.sources.etf_flows

# 5. Build and check the published artifacts
python -m pipeline.transform.stocks_build
python -m pipeline.transform.bds_aggregate
python -m pipeline.transform.flows_build
python -m pipeline.sources.news_link --qa-sample 20
python -m pipeline.transform.news_build
python -m pipeline.sources.us_equity
python -m pipeline.core.validate

# 6. Serve locally
python -m http.server 8000
# then open http://localhost:8000/hub/
```

Collectors write to the warehouse and build steps read from it, so run them in
sequence — DuckDB permits a single writer, and a build that starts mid-crawl
will refuse rather than publish from a partial read.

## Sources

| Domain | Source | Access | Notes |
|---|---|---|---|
| Crypto candles | Binance, Hyperliquid | free, no key | Fetched **in the browser** — Binance geo-blocks US datacenter IPs |
| Perp context | Binance futures, Coinbase | free, no key | Funding, open interest, long/short, Coinbase premium |
| VN prices + foreign flow | Vietcap via `vnstock` | free | Full market board in ~12s |
| VN fundamentals | Vietcap `iq-insight-service` | free | ~30 ratios per quarter, 2018→present, ~130 req/min |
| VN book depth + foreign room | SSI iBoard | free | Three levels of depth and remaining foreign ownership room; cross-checks Vietcap to 0.0007% on market-wide foreign net |
| VN price history | VPS (`histdatafeed`) | free | Daily OHLC, TradingView UDF shape. Quotes in thousands of VND |
| Real estate | Chotot public gateway | free | Asking prices with GPS, ward, and a dedupe key |
| ETF flows | Farside | free | Daily spot BTC/ETH creations and redemptions by issuer |
| Options | Deribit | free | Full chain in one request; gamma surface validated against a live third-party GEX feed |
| US delayed OHLCV | Yahoo Finance chart | free, pipeline-side | Curated 40-symbol universe; no fundamental/valuation fields |
| Administrative map | vietnam-map-34-provinces | MIT | Former province geometry grouped to the post-2025 34-province structure |

## Current coverage

| Artifact | Contents |
|---|---|
| `stocks.json` | 1,751 tickers; 1,725 priced; 1,719 with fundamentals; 1,735 with industry; 870 with momentum; 1,522 with foreign room; 1,292 with SSI depth |
| `bds.json` | 345 district/type series, 176 districts and 21/34 merged provinces; 192 high-confidence series at the default ≥20 filter; 47,343 raw observations / 31,614 unique listings |
| `flows.json` | 512 days each of BTC/ETH ETF flow, daily, cumulative and split across 12 BTC / 10 ETH issuers |
| `gex_btc.json`, `gex_eth.json` | Gamma surface, flip and max pain from 1,004 BTC / 850 ETH option contracts |
| `signals.json` | 220 closed trades with their full result history |
| `ticker/*.json` | 1,719 lazy dossiers; 1,697 include statement payloads |
| `us.json` | 40 liquid US equities/ETFs with one year of delayed daily close history |
| `news_ticker.json` | 7 audited rule-based links across 5 tickers; no AI ticker guessing |

The warehouse holds 1.44M quarterly fundamental observations (2018-Q1 to
2026-Q2) and 264 trading days of daily prices across 879 tickers.

## Scheduling

| Runner | Work | Why |
|---|---|---|
| GitHub Actions | VN equities, real estate | Free and unlimited on public repos; runs while the workstation is off |
| Local workstation | Heavy crawls, backfills, backtests | Not geo-blocked; more cores |
| Browser | Binance candles and perp context | Sidesteps the geo-block and costs nothing to serve |

## Disclaimers

Real-estate figures are **asking prices** from public listings, not transaction
prices. Nothing here is investment advice.

See [CLAUDE.md](CLAUDE.md) for the non-obvious source constraints — several cost
real debugging time to find and are easy to reintroduce.
