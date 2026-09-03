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
- **Aggregates hide themselves when thin.** A district median resting on eight
  listings is an anecdote; it is withheld, and `n` travels with every cell that
  is published.

## Layout

```
hub/            Tab shell — routing, theme, PWA
apps/           One self-contained page per tab
  chart/        Crypto candles + perp context
  stocks/       VN equity screener
  bds/          Real-estate price table
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

# 2. Quarterly fundamentals back to 2018 (~15 min for the whole market)
python -m pipeline.sources.vci_direct

# 3. Real-estate listings — start this early, history cannot be backfilled
python -m pipeline.sources.chotot --discover-regions
python -m pipeline.sources.chotot --regions 12000,13000

# 4. Build and check the published artifacts
python -m pipeline.transform.stocks_build
python -m pipeline.transform.bds_aggregate
python -m pipeline.core.validate

# 5. Serve locally
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
| Real estate | Chotot public gateway | free | Asking prices with GPS, ward, and a dedupe key |

## Scheduling

| Runner | Work | Why |
|---|---|---|
| GitHub Actions | VN equities, real estate | Free and unlimited on public repos; runs while the workstation is off |
| Local workstation | Heavy crawls, backfills, backtests | Not geo-blocked; more cores |
| Browser | Binance candles and depth | Sidesteps the geo-block and costs nothing to serve |

## Disclaimers

Real-estate figures are **asking prices** from public listings, not transaction
prices. Nothing here is investment advice.

See [CLAUDE.md](CLAUDE.md) for the non-obvious source constraints — several cost
real debugging time to find and are easy to reintroduce.
