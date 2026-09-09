# Session log

**Keep this file under 100 lines.** Newest entry at the top; git log is history.

## 2026-09-09 — navigation, chart axes, valuation disclosure, moat, news

**State.** Every destination is now openable in a new tab: hub tabs, the home
button, GEX chips and the chart's search results became anchors; rows that
cannot be anchors carry `data-href` and reproduce the browser's behaviour. The
shared block is inlined per app and guarded by `check_linkable` -- a shared
`<script src>` would fall outside `check_apps`, which skips scripts with a src.

Ticker mini-charts gained real axes, units and 45-degree period labels; bars are
anchored at zero and negative years drawn in the down colour. The valuation
panel now discloses its own method, parameters and limits. The moat panel gained
the five qualitative dimensions, each printing its rule and inputs, refusing
where nothing measures the dimension. Corporate events publish ~a decade behind
a collapsed year view. Big Tape opens at $500k with a 30-day window and states
what it actually observed.

**Data.** `data/ticker` 51 -> 56 MiB: five annual-only moat statement fields
(isa9, isa10, cfa19, bsa36, bsa9) and the event cap 30 -> 120, which publishes
every event held. `eq_company.meta` was populated for every symbol and read by
no pipeline -- free float, foreign cap, one-month liquidity and ICB codes now
reach the browser. `sec` (level-1 sector) added to stocks.json for cohorts.
New `vn_news` collector: CafeF + VnEconomy RSS through news_link's matcher took
ticker news from 6 symbols/11 links to 33/64.

**Fixed.** RIM terminal capitalised a non-growing residual as a growing
perpetuity (~1.5-2% overstatement on financials). Flat 25% payout replaced by
one derived from published dividends where computable; the market median is
~42%. `discountRate` keyed off `co.type` -- sixteen null-industry tickers were
silently taking the 12% default. `news_build` took only the newest `fetched_at`
batch, so a second collector displaced the first's stories entirely. Ticker
watchlist/jump navigated the iframe only, desyncing the hub URL. Wiki links
lacked `target=_top`. News nav used absolute paths broken on this deployment.

**Verified.** check_apps, check_linkable, validate (1,719 dossiers / 1,697
statements) all pass. Chrome checked hub tabs plain/ctrl/middle-click, the us,
crypto, bds and chart routes, ticker charts for VNM/A32 including negative-bar
zero baselines, valuation disclosure for VCB/VNM/VIC, moat for VNM/VCB/BVH/FPT,
and the tape's quota-halving path (4,000 events halve twice, 1,000 survive).
All 22 first-run news matches hand-reviewed: no false positives.

**Next.** Tape rollup is client-only and cannot backfill -- a collector would
need to run locally, since Binance geo-blocks CI. Moat cohort medians compare
within sector but peer dossiers are not loaded, so cross-company ratio medians
are still unavailable. NAV/SOTP for property/holding remains absent.

## 2026-09-04 — chart workspace + full ticker dossiers

**State.** Completed G4/H2/H3/H4 and B2 data/UI above `ba0c436`: continuous
drawing with clean exits, price-scale repaint fix, 14 intervals, named
watchlists/CSV, live order book, Big Tape and evidence-based Level Behavior.
Ticker route now has VPS five-year price, ratio/peer panels, profile, governance,
events, statement charts and industry valuation refusal rules.

**Data.** Direct Vietcap full crawl hit 1,751 targets with zero failures and
added 10,068,118 statement rows plus company/owner/relationship/event snapshots.
Published 1,719 ticker dossiers, 1,697 with statement series, 1,044,029 ratio
points, 46.39 MiB. Fixed browser-invalid NaN, bank CIR sign and all-zero cadences.

**Verified.** All 9 artifacts, Python compile, strict parse of 1,727 JSON files,
workflow YAML and diff-check pass. Chrome reviewed book/tape/levels, 2h/1M and
VIC/VCB/AAA. Repeated ticker build leaves an unchanged symbol byte-identical.

**Next.** Read `HANDOFF.md`. Remaining: guarded numeric valuation engine,
fact-cited macro/narrative, multi-chart/layout/chart types/arbitrary intervals,
and persisted CVD/footprint/liquidation microstructure. Server PID 3312:8811.
