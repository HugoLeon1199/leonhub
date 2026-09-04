# Teardown: app.turtletrading.vn

Analysed 2026-09-03 by downloading the hub shell, all ten subdomain apps, and
the data files they fetch. This records what is worth copying, what to improve
on, and the measurements behind both.

## Architecture: hub shell plus micro-apps

Every route — `/chart`, `/gex`, `/wiki`, `/bds/tinh-ha-noi` — returns the same
286 KB HTML file. It is vanilla JavaScript with no framework, no bundler, and no
build step. The shell is a tab bar plus an iframe container; each tab is an
independent application deployed on its own subdomain.

| Tab | Subdomain | HTML | Role |
|---|---|---|---|
| Chart | chart.* | 142 KB | Crypto candles, TradingView Lightweight Charts |
| Signal | signals.*/chart/btc | 222 KB | Trade signals; Cloudflare Turnstile |
| Structure | signals.*/gex | 160 KB | Gamma exposure from options |
| Phân tích | phantichcoban.* | 73 KB | 1,522-ticker equity screener |
| News | news.* | 57 KB | News digest |
| Training | replay.* | 440 KB | Chart replay, paper trading |
| Quant | quant.* | 222 KB | Client-side backtester |
| Tài chính | wealth.* | 55 KB | Net-worth tracker |
| BĐS | bds.* | 244 KB | 303-district real-estate table |
| Wiki | wiki.* | — | Quartz v4.5.2 (Obsidian → static) |
| Blog | blog.* | — | Static blog |

Details worth noting in the shell: full PWA manifest, dark theme applied before
first paint to avoid a flash, `dns-prefetch`/`preconnect` per tab (saving
100–300 ms on first tab tap), self-hosted analytics, a client-side error
collector feeding a bug reporter, Telegram Mini App support, and shareable URLs
of the form `?tab=<key>&u=<page>`.

The AI assistant is **bring-your-own-key**: the user supplies an API key or
points it at a local Ollama / LM Studio instance. The operator pays nothing for
inference.

## Chart source audit (2026-09-04)

The chart implementation is not meaningfully hidden. Production preloads the
self-hosted Lightweight Charts v5.2.0 build and `multi.min.js`, but the page's
own error fallback publicly requests `/multi.js`. That unminified file returned
HTTP 200 and measured 1,011,167 bytes / 10,799 lines. The minified file is
574,951 bytes across three lines and has no source-map reference.

Most of the product layer is custom vanilla JavaScript written on top of
Lightweight Charts. The source describes a greenfield multi-instance chart and
parts ported from an older in-page implementation. Its drawing stack contains a
registry, a unified `DrawRenderer`, a `DrawPrimitive` Series Primitive adapter,
hit testing/edit handles, per-pane state and controller-level tool arming. There
are 35 unique `data-tool` values, including the cursor, and fixed-point polygon
definitions for channels, pitchfork variants, Elliott waves, XABCD/ABCD,
head-and-shoulders, Fibonacci channel and related tools.

The indicator stack is also custom and registry-driven: 62 definitions in seven
categories. Each definition carries defaults, parameter schema, line metadata,
range/fill behavior and pane type; separate helpers compute values and attach or
detach LWC series/primitives. Multi-instance state and `paneWith` allow compatible
oscillators to share a pane. Standard formulas are independently reproducible;
cloud fills, volume profiles, SMC and regime backgrounds use custom primitives.

The reference app itself disarms a drawing after completion with
`armTool(null)`. LEON intentionally differs: the selected tool remains active for
repeated drawing and exits via right-click, Escape, Pan, or clicking the active
tool again.

No copyright/license/SPDX notice was found in the public application source.
Public readability is not an open-source grant, so reproduce behavior and
standard formulas without importing their application code verbatim. The
underlying Lightweight Charts library is separately Apache-2.0 licensed and has
an attribution requirement.

## Data model: mostly precomputed, a little live

**Live, fetched in the browser.** Free, keyless, CORS-open exchange endpoints:
Binance spot and futures, Hyperliquid, Bybit, OKX. Order-book depth is pulled
client-side at limits of 1000 / 500 / 400 respectively. The operator's servers
never touch this traffic.

**Static JSON produced by offline pipelines.** Measured directly:

| File | Size | Contents |
|---|---|---|
| `bds.*/data/bds.json` | 117 KB | 554 rows of district × property type: median asking price/m², CAGR, span in years, sample count, rental yield. Labelled "giá rao, chỉ để tham khảo" (asking prices, for reference) |
| `phantichcoban.*/data/stocks.json` | 466 KB | 1,522 tickers with exchange, industry, price, market cap, P/E, P/B, P/S, EV, ROE, ROIC, ROA |
| `phantichcoban.*/data/prices.json` | 96 KB | Intraday price and volume for the same tickers, plus 213 coins keyed by CoinGecko id. Carries `updated_at` and `elapsed_sec: 12.5` |
| `data.signals.*/api/gex/btc/today.json` | 10 KB | Computed from Deribit options: net GEX, 0DTE split, call/put GEX, put-call ratio, vanna, charm, gamma flip, max pain, regime label. Cache-busted every 45 seconds |

**One real backend.** `data.signals.turtletrading.vn` computes GEX, serves push
notifications, and receives telemetry. Everything else is static hosting.

## What is worth copying

1. **Cost structure.** Static hosting, free exchange APIs, BYOK inference. A
   server exists only where the work genuinely cannot happen client-side.
2. **One file per module, deployed independently.** A broken tab cannot take the
   site down, and each can ship on its own schedule.
3. **Short JSON keys** (`p`, `mc`, `pe`). A browser-side screener downloads the
   whole table to filter it, so key length is a cost paid on every page load.
4. **Provenance on every aggregate.** Sample counts, spans, timestamps, and an
   explicit note that real-estate figures are asking prices.
5. **Signals carry entry, stop, target, and PnL in R** rather than a bare
   direction.
6. **Quartz for the wiki** — notes are written in Obsidian and built to a static
   site, so documentation costs no CMS.

## Where we go further

Each of these is a gap in their product, not a difference of taste.

- **Fundamentals depth.** They publish a current snapshot. Vietcap's endpoint
  returns ~30 ratios per quarter back to 2018, which supports factor ranking and
  lets a valuation be stated as a percentile of the ticker's own history — "P/E
  is in the cheapest quartile of its last eight years" — rather than an absolute
  number the reader has no baseline for.
- **Foreign flow.** `foreign_buy_value` / `foreign_sell_value` per ticker is
  available and they do not use it, though it is among the most-watched figures
  in the VN market.
- **Real-estate history and velocity.** They publish one CAGR number. Chotot
  returns GPS coordinates, ward, a listing id, and a listing timestamp, which
  supports a price panel plus listing velocity, days-on-market, and repost rate
  — leading indicators a price-only view cannot show.
- **Cross-market flows.** ETF flows (Farside), stablecoin supply (DefiLlama),
  cross-venue open interest and funding, Coinbase premium, rates. They have
  none of this layer.
- **A daily brief.** There is no single screen answering "what changed today".

## Measurements taken during the teardown

- Vietcap fundamentals endpoint: 25/25 tickers at a sustained 127 requests per
  minute, no quota encountered.
- `vnstock` wrapper: cut off after ~12 calls even when paced at 9 requests per
  minute, signalled by raising `SystemExit`.
- Full VN market price board: 1,751 tickers in 9.5–12.5 seconds — matching the
  `elapsed_sec: 12.5` recorded in their own `prices.json`, which confirms the
  method.
- Chotot: pagination returns HTTP 400 past roughly 20,000 offset; province-level
  `total` is a rounded placeholder (always 10,000) while district-level `total`
  is real.
- Deribit: 980 live BTC option contracts with open interest, mark IV, strike and
  expiry in a single 437 KB request — enough to compute the full GEX surface.
