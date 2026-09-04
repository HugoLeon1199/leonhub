# CLAUDE.md — LEON Hub

Investment hub: crypto, VN equities, real estate, macro. Public data collected
offline into a point-in-time warehouse, published as static JSON, read by
single-file browser apps. Python 3.11 + vanilla JS. No framework, no build step.

**Docs and code comments in English. UI copy in Vietnamese.**

Sister repo `HugoLeon1199/leonquant` (`D:\CODE\WEB\NEWS`) supplies the News tab
and is the reference for anything Playwright-based.

## Architecture

```
sources → DuckDB warehouse (append-only, point-in-time) → transform → data/*.json → apps
```

The warehouse is the source of truth; published JSON is a view over it.

```
hub/            Tab shell
apps/           One self-contained page per tab
                brief chart stocks ticker bds signals gex flows
pipeline/
  core/         http.py, warehouse.py, validate.py
  sources/      One module per external source
  transform/    Aggregation and derived metrics
  publish/      emit.py — writes data/*.json
data/           Published JSON (committed — the contract)
warehouse/      DuckDB (gitignored; monthly .gz snapshot only)
docs/           Reference, read on demand — see below
```

## Commands

```powershell
# Real estate — the moat. Daily; history cannot be backfilled.
python -m pipeline.sources.chotot --discover-regions      # 48 province codes
python -m pipeline.sources.chotot --regions 12000,13000 --max-pages 2

# VN equities
python -m pipeline.sources.vn_equity --what board         # ~14s, 1,751 tickers
python -m pipeline.sources.ssi_board --with-depth         # depth + foreign room
python -m pipeline.sources.vn_history                     # daily OHLC backfill
python -m pipeline.sources.vci_direct                     # ~7 min full market
python -m pipeline.sources.vci_direct --skip-existing     # resume
python -m pipeline.sources.vci_company --delay 0.2        # slow monthly dossier/BCTC snapshot
python -m pipeline.sources.vci_company --scope profile,statements --skip-existing
python -m pipeline.sources.news_link --qa-sample 20       # deterministic, no AI

# Crypto
python -m pipeline.sources.crypto_board                   # local only: Binance geo-blocks CI
python -m pipeline.sources.etf_flows
python -m pipeline.sources.deribit_gex --symbol BTC
python -m pipeline.sources.deribit_gex --symbol ETH

# Build, then gate
python -m pipeline.transform.stocks_build
python -m pipeline.transform.ticker_details_build
python -m pipeline.sources.vci_company --symbols VIC,VCB,SSI --dry-run
python -m pipeline.transform.bds_aggregate
python -m pipeline.transform.flows_build
python -m pipeline.transform.signals_build
python -m pipeline.transform.news_build
python -m pipeline.transform.gex_build
python -m pipeline.sources.fx_rates
python -m pipeline.core.validate

python -m http.server 8811    # http://localhost:8811/hub/
```

Collectors and build steps must run in sequence — DuckDB allows one writer.
Every collector takes `--dry-run`.

## Rules that shape the code

- **Append-only, point-in-time.** Every row carries `fetched_at`; nothing is
  updated in place. Reads take the latest observation per key. This is what
  keeps backtests and any published track record free of look-ahead.
- **Short keys in published JSON** (`p`, `mc`, `pe`) — the browser downloads the
  whole table to filter locally, so key length is a per-pageload cost.
- **Median, never mean** for asking prices; clip p5–p95 first.
- **Hide cells with `n < 20`**; publish `n` beside every aggregate.
- **Label asking prices as such** ("giá rao — tham khảo"). Publish aggregates,
  never verbatim listings.
- **State the rule, not just the verdict.** Any derived judgement (regime,
  valuation, signal) shows its inputs, or the page is an opaque dashboard.
- **Publish losing results unchanged.** The signals page currently shows -76R.
  Tuning that into looking good is the failure mode it exists to prevent.
- ISO timestamps with explicit offset. VN session 09:00–11:30, 13:00–14:45 ICT.
- "Not investment advice" on every analytical page.

## Scheduling

| Runner | Work | Why |
|---|---|---|
| GitHub Actions | VN equities, real estate, ETF, GEX | Free and unlimited on public repos; runs while the workstation is off |
| Local workstation | Heavy crawls, backfills, backtests | Not geo-blocked, more cores |
| Browser | Binance candles and perp context | Sidesteps the geo-block, costs nothing to serve |

Actions cron drifts 5–30 min and can be skipped under load. Every job must be
idempotent and resumable; market-close jobs need a buffer.

`leonquant` is already ~495MB against GitHub's ~1GB soft cap. Keep the warehouse
out of git, commit compact JSON only, never images or candle history.

## Reference — read on demand, not every session

| File | Read it when |
|---|---|
| `docs/source-gotchas.md` | Touching any collector. Failure modes that produce plausible wrong numbers rather than errors — geo-blocks, unit conventions, zero-fill, pagination limits, computation traps |
| `docs/teardown-turtletrading.md` | Deciding what to build next, or why something is built this way |
| `docs/session-log.md` | Picking up unfinished work. Latest entry only |

Before changing a collector, check `docs/source-gotchas.md` for that source —
several constraints there are invisible from the code and re-derivable only by
repeating the debugging that found them.
