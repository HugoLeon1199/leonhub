# Session log

**Keep this file under 100 lines.** Newest entry at the top; git log is history.

## 2026-09-09 (2) — real estate: honest medians, and the panel layer

**Why the map looked empty.** Measured the whole source: **70,798 live ads
nationally**, HCMC 42,428 of them (60%), and **34 of 51 provinces under 200 ads
in total**. Thin provincial coverage is Chotot, not the crawler — no collector
change fixes it, and `MIN_SAMPLES=20` stays. One real bug did exist: district
discovery read only the first 50 ads per lane and so missed 21% of Hà Nội's
districts (23 of 29). It now pages deeper, unions with every `area_v2` the
warehouse holds, and reconciles its sweep against the province total.

**The medians were wrong.** Nothing aged listings out, and Chotot never reports
a sold status — a withdrawn ad priced the market forever. 27% of the rows behind
the medians were already stale on a five-day warehouse. Both publishers now drop
listings unseen for 3 days *relative to their own cell's last crawl*, so a failed
night degrades instead of blanking the map; the page prints the size of the
correction. `dom` was renamed `age`: it measured ad age at observation, never
days-on-market. The rent leg now gets the same repost-collapse and p5–p95 clip as
the sale leg (median yield moved 2.3%, worst cell 18.8%).

**The moat, finally built.** `bds_panel_build` reads the observation history the
`(list_id, fetched_at)` key was for: price cuts, time on market, disappearance
rate — no new requests. Sellers edit a live ad rather than repost, so a listing
that went 6,500 → 650 million as its area went 144 → 44 m² is a different
property, not a discount; area drift is excluded from both numerator and
denominator. DOM is left-truncated (median cell `dmc` = 100% today) so it
publishes as "≥ N ngày" with the truncated share beside it and converges as the
warehouse ages. Disappearance is conditioned on the cell's own crawl history,
labelled "tin biến mất", never "đã bán", and published with reposts subtracted.

**Quy hoạch.** `province_profiles.json` gained a validator (it is hand-edited,
so a malformed citation would publish as fact) and a `land_price_table` block:
HCMC 79/2024/QĐ-UBND, Hà Nội 71/2024/QĐ-UBND, Đà Nẵng 59/2024/QĐ-UBND, each
checked against its gazette. Citation only — never a copy of the table.
`vbpl.vn` and `congbao.chinhphu.vn` are both SPAs whose data paths are
robots-disallowed or client-rendered, so no legal-document crawler exists.

**Verified.** validate (incl. new panel bounds and profile checks), check_apps,
check_linkable, compileall all pass. Chrome checked 14/14 column alignment, the
drawer panel block, the no-shard message, and the land-price paragraph for cited
and uncited provinces. Shards fell 7.3 → 5.5 MB (`MAX_PER_DISTRICT` 400 → 200)
and `bds_listings_build` finally runs in CI, where it had never been wired.

**Next.** Ward-level aggregation (`ward_v2` is now collected; the queryable code
was previously discarded) is worth ~5-15 cells nationally — small. A flag table
for "where to invest" needs `pc` to age first. Parsing an actual Bảng giá đất PDF
is the open question: do HCMC first, publish the street-match rate, and stop if
it lands under 30%.

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
