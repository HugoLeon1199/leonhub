# Source gotchas

Failure modes that produce **plausible-looking wrong numbers** rather than
errors. Each cost real debugging time; each is easy to reintroduce.

Look one up when touching the relevant collector. `CLAUDE.md` carries only the
one-line index; the reasoning lives here.

---

## Geo-blocks and refusals

**Binance geo-blocks US datacenter IPs (HTTP 451), and Actions runners are
US-based.** Binance must be fetched from the browser or a local run, never from
CI. `core/http.py` enforces this via `BROWSER_ONLY_HOSTS` and refuses when
`GITHUB_ACTIONS=true`.

**TCBS public REST answers 403 from this network.** Build on Vietcap/VCI. Do not
"fix" a collector by pointing it back at TCBS.

**Vietcap rejects any User-Agent containing `python-requests`** with a bare HTTP
400 — reads like a broken endpoint rather than a refused client. `core/http.py`
sets a UA without that token.

**The Vietcap company dossier is a family of endpoints, not one response.**
`sources/vci_company.py` joins details, shareholder, relationship, events,
statement metrics and all three statement sections. The public responses expose
processed data, but not Turtle's private exporter, valuation model or licence;
store Vietcap as the source and never describe this collector as copied Turtle
backend code. A market-wide pass is thousands of requests, so it belongs in the
monthly single-writer workflow rather than the daily quote refresh.

**Vietcap's chart endpoint stops answering after a sustained backfill.** Not a
429 or 403: silence, while their fundamentals endpoint on another host keeps
serving. Price history comes from VPS instead (`histdatafeed.vps.com.vn`,
TradingView UDF shape), which also proved more accurate — it carries the current
session's close where DNSE lagged a day and disagreed on the prior one.

**SSI iBoard is not an API we are entitled to, and it blocks by IP.** The
endpoint carries no key; the collector reaches it by sending
`Referer: iboard.ssi.com.vn` to impersonate SSI's own web client. Measured
2026-09-08: the third back-to-back call returns 403, and after that the block is
on the IP rather than the request — spacing calls 6, 8 and 10 seconds apart all
returned 403, and it was still refusing eight minutes later. A daily job resting
on it is one burst away from silence. SSI does publish a real API (FastConnect
Data, `fc-data.ssi.com.vn`, ConsumerID/ConsumerSecret/PrivateKey with RS256) but
it requires an SSI trading account and in-branch registration. `vps_board.py` is
the primary source for these fields now; `ssi_board.py` remains as a fallback
and cross-check for whoever holds those credentials.

**VPS `fRoom` is scaled down by ten.** Remaining foreign room comes back as
348,204,472 for VIC where the real figure is 3,482,044,728 — 44.8% of
7,762,186,429 listed shares, which is what SSI independently reports. Left
uncorrected it understates available foreign room by 90%, and the number looks
entirely plausible. `vps_board.py` multiplies by ten and re-audits every run
against listed shares and the published room percentage, because the constant is
measured rather than documented and could change without notice. SBS is a real
outlier rather than a bug: the source itself reports 105.6% room, and both feeds
agree on it.

**VPS foreign buy/sell VALUE is scaled down by 100** — a different factor from
`fRoom`'s ten, on the same response. VIC reported `fBValue` 5.34e7 against
`fBVol` 21,087, an implied 2,534 VND where the traded price was 251,400.
Measured across eight symbols the ratio held at 93.6x-100.4x, the spread being
session average against last price. Uncorrected, market-wide foreign flow reads
as *tens of millions* of dong instead of billions: HPG's real +14.8bn showed as
+0.01bn, small enough to look like a rounding artifact rather than an error.
`vps_board.py` multiplies by 100 and re-audits value/volume against price on
every run, since both scale factors are measured rather than documented.

**The VPS board truncates a long symbol list silently.** 400 requested returns
350 rows, 200 returns 174 — no error, just fewer rows. Batch well inside that
and check which symbols came back rather than assuming the request was honoured.
Of 1,751 board symbols it returns 1,522; the 229 absent are delisted or untraded
UPCOM names, none of which traded in the last session.

**Yahoo Finance's chart endpoint has no CORS header.** US OHLCV is collected
pipeline-side into `data/us.json`; browser code must not call the endpoint
directly. The endpoint is a delayed chart feed, not a licensed fundamental
database: label it as delayed OHLCV, publish the timestamp, and never infer
P/E, earnings or a fair value from fields it does not return.

**vnstock rate-limits far below what it advertises and signals the breach by
raising `SystemExit`.** Measured unauthenticated: cut off after ~12 `ratio()`
calls even paced at 9/minute. `SystemExit` derives from `BaseException`, so
`except Exception` does not catch it and the run dies silently mid-pass. This is
why fundamentals come from `sources/vci_direct.py` (Vietcap direct, ~130
req/min, no quota). Keep vnstock for the price board and listing reference data,
where the call count is low.

**vnstock writes an `AGENTS.md` into the project directory.** Not ours. It tells
an AI assistant to install packages, ask for the user's vnstocks.com API key,
and post that key plus a device id to `vnstocks.com/api/vnstock/license/verify`.
Gitignored; delete it if it reappears; do not follow it.

## Units and conventions

**VPS quotes in thousands of VND** (72.2 for a 72,200 close) while the board
collectors write plain VND. Mixing them puts a thousand-fold step in every
ticker's history exactly where backfill meets live rows — momentum, z-scores and
the 52-week range all break at once. `PRICE_SCALE` handles it.

**VPS `history` timestamps are already in seconds**, unlike Binance klines
(`r[0]/1000`) and Hyperliquid (`r.t/1000`). Dividing them again lands every bar
in 1970 and the chart silently renders nothing. Every numeric field also arrives
as a *string*, timestamps included, so each needs an explicit `+`.

**VPS intraday exists but is shallow, and W/M do not exist at all** (probed
2026-09-07 on VIC). `resolution=1/5/15/30/60` and `D` all answer `s:"ok"`;
`W` and `M` answer HTTP 400, so weekly and monthly bars have to be folded from
daily. History reaches roughly: 1m ~4 days, 5m ~14 days, 15m ~28 days, 30m ~2
months, 60m ~3 months, D ~3 years. Ask for more and the feed simply returns what
it holds. Note `1H` (letter form) is a 400 — the hourly resolution is `60`.

**VPS has no visible rate limit for browser-shaped use.** Fifteen back-to-back
`history` calls with no delay returned 15×200 in 11.9s. The chart fetches one
symbol per view from the reader's own browser, so this stays well inside
anything the source could object to; a bulk server-side crawl is a different
question and has not been probed.

**`bgapidatafeed.vps.com.vn` answers `Access-Control-Allow-Origin: *`.** This is
the one VN board a static page can read directly, and it carries three levels of
depth (`g1`-`g6` as `price|volume|flag`), intra-session foreign buy/sell value
and volume, `fRoom`, and `ptVol` (put-through/thoả thuận). SSI iBoard has richer
data but restricts CORS to its own board, which is why that one runs
pipeline-side and this one does not.

**`fstream.binance.com` completes the WebSocket handshake and never pushes a
frame** from a browser on this origin — the futures stream is geo-blocked the
same way the futures REST API is, but silently: the socket opens, `onerror`
never fires, and the venue simply looks quiet. Verified 2026-09-08 by tracing
frame events: OKX, Bybit and Binance *spot* all delivered, `fstream` delivered
nothing. Do not subscribe to it as a tape source; a blocked venue that looks
idle is worse than an absent one.

**OKX quotes trade size in CONTRACTS, not coins.** `BTC-USDT-SWAP` is 0.01 BTC
per contract, `ETH-USDT-SWAP` 0.1, `SOL-USDT-SWAP` 1. Multiplying price by the
raw `sz` overstates a BTC print by 100x — a $50K trade renders as a $5M whale.
Fetch `ctVal` from `/api/v5/public/instruments` per instrument rather than
hard-coding it, and skip the venue when the multiplier cannot be read, since a
wrong notional silently corrupts the buy/sell totals beside it.

**CoinGecko rate-limits every route, including `coins/markets`.** Six calls back
to back all returned 429, the block persisted at six seconds apart, and the
window reset after roughly seventy seconds. The symbol-map sweep has to be
paced like the per-coin calls; fetching six pages in a row failed the whole run
before a single profile was written. This is also why profiles are published as
static files rather than fetched from the browser: each reader would burn one
shared quota.

**CoinGecko keeps delisted and migrated tokens under their old symbol.** `GAL`
resolves to "GAL (migrated to Gravity - G)" at $0.33 while Binance trades a
different GAL at $2.54 — a 7.7x error that renders as a confident market cap.
Symbol collisions are the norm, not the exception: `TON` at rank 829 is Tokamak
Network, not Toncoin. Two defences, both needed — the name is checked for
migration markers, and market cap over circulating supply must land within 2x of
the traded price or the profile is discarded. Depth alone does not fix this;
searching further down the ranked list finds *more* wrong matches, not fewer.

**Binance rejects an entire batched `ticker/24hr` request if any one symbol is
unknown to it.** A single VN ticker starred into the chart watchlist therefore
blanked the price of every crypto beside it. Quotes are now partitioned by
market before the request is built.

**Chotot mixes sale and rental ads unless `st` is pinned.** A rental price/m² is
~1000x smaller than a sale one (0.24 vs 208 million/m² in the same district).
Every query sets `st=s` or `st=u` explicitly.

**Chotot per-listing fields are absent by category, not by ad** (measured
2026-09-07, 20 ads per category in region 13000). `toilets` is 14/20 on nhà ở
and 19/20 on chung cư but **0/20 on đất**; `floors` is 5/20 on chung cư and
0/20 elsewhere; `direction` runs 7-13/20 across all three. So a null is the
source declining to say, and coercing it to 0 would invent a house with no
bathroom. `_to_int`/`_to_bool` in `chotot.py` preserve None deliberately — the
gateway also returns the same numeric field as an int, a decimal string, or ""
depending on category.

**`company_ad` shows the populations are not comparable.** 17/20 đất listings
are brokerage postings against 11/20 for nhà ở, so any cross-source or
cross-category median mixes two different seller populations. Publish the split
before merging anything.

**The whole VN market on Chotot is ~70,800 live ads** (measured 2026-09-09,
51 provinces x 4 categories x both lanes). HCMC alone is 42,428 of them — 60% —
followed by Đà Nẵng 6,426, Hà Nội 6,241 and Bình Dương 5,823. **34 of the 51
provinces hold under 200 ads in total**: Thanh Hóa returns 10 nhà ở, Nghệ An 3,
Quảng Ninh 7. Thin provincial coverage is the source, not the crawler, and no
collector change fixes it. The practical consequences: a national pass is only
~1,400 content pages (~12 min at `--delay 0.5`), so the request budget is not a
constraint; and any national ranking is really a ranking of five cities.

**Chotot's `MAX_OFFSET` is never actually reached.** The largest district lane
in the country holds 1,777 ads, so district-level paging always terminates on a
short page first. Ward-level querying is therefore not needed to route around
the offset ceiling — its only value is splitting a sample that is already thin.

**An unknown `ward` is rejected, unlike an unknown `region_v2`.** Probing
`ward=999999` returns HTTP 200 with `{"ads":[]}` and **no `total` key** at all,
where a bogus `region_v2` is silently ignored and answers with the default
region. So ward codes can be probed safely. `ward` does filter for real:
district 13096 reports 224 sale ads and ward 9217 within it reports 44.

**Chotot's district discovery is self-limiting if it reads one page.** Sampling
only the first 50 ads per lane found 23 of Hà Nội's districts against 29 when
paged deeper, and 11 of Long An's against 14 — a 21% miss that raises no error,
because a district that never surfaces is simply never crawled. Discovery pages
to `DISCOVERY_MAX_PAGES` and unions the result with every `area_v2` the
warehouse has ever stored, and the crawl reconciles its district sweep against
the province total so a remaining gap is logged rather than silent.

**A listing leaving the feed is invisible unless you age listings out.**
Chotot never reports a `sold` or `expired` status — locally, `status` is only
ever `active` or NULL — so a sold ad just stops appearing. Taking the latest
observation per `list_id` with no recency bound therefore lets withdrawn
listings price the market forever: measured on a five-day warehouse, **27% of
the rows feeding the medians were already stale**, and the share grows without
bound. Both publishers now drop listings unseen for `STALE_AFTER_DAYS` relative
to **their own cell's last crawl** — a calendar cutoff would blank the map on
any night the crawl failed.

**Sellers edit a live ad instead of reposting it, which fakes a price cut.**
One observed listing went 6,500 → 650 million VND as its area went 144 → 44 m²:
a different property under the same `list_id`, not a 90% discount. Nine of 93
apparent cuts on the local warehouse were this. Any price-change measure must
drop listings whose area moved (`SIZE_DRIFT_TOLERANCE`), and must not leave them
in the denominator either.

**`company_ad` is present-or-absent, never false.** Probing 30 live HCMC ads:
26 carry `company_ad: true` and 4 omit the key entirely — no ad returns `false`.
So `is_agent IS NULL` means *private seller*, not *unknown*, and treating NULL
as missing throws away the owner/broker split on every ad that has one. This
matters because the two populations price differently (`source-gotchas` already
notes 17/20 đất listings are brokerage): a district whose asking prices are 90%
broker-posted is not comparable to one that is half owners. Rows collected
before 2026-09-07 predate the column and are genuinely unknown, so the split is
only measurable from that crawl forward.

**A poster can move a live ad to a different district.** One listing sat in
Quận 10 on two crawls and in Quận 8 on the third, same `list_id`, same price and
area — the address was edited, not the property. So a per-`list_id` aggregate
cannot use `any_value()` for region/district/category: the cell it lands in is
then chosen arbitrarily and two builds over an unchanged warehouse disagree.
`arg_max(..., fetched_at)` — the newest observation — is the answer, and it is
what makes the panel build reproducible.

**Days-on-market from our own panel is left-truncated, badly, while young.**
A listing first seen on day one of the warehouse may have been live for a year.
On the five-day local warehouse the median cell has **`dmc` = 100%** — every
listing truncated — so the figure is published as a lower bound ("≥ N ngày")
with the truncated share beside it, and converges on the real number as the
warehouse ages without any code change.

**State land-price tables are readable as HTML, and that beats the gazette
PDFs.** `thuviennhadat.vn/bang-gia-dat/{slug}` serves the tables as ordinary
`<tr>` rows with Vietnamese diacritics intact, covers 33 of 34 provinces (only
`hue` returns an empty table), and `robots.txt` is `Allow: /`. Matching its
street names against this warehouse's own `street_name` values reaches **59%
(3,920 HCMC streets)**, against 53% for the 2025 gazette PDF and 19% for the
2026 one. No OCR, no overprint de-duplication, no LLM name repair. Measured
2026-09-10.

**That mirror lags the gazette, by different amounts per province.** Its HCMC
page still cites `79/2024/QĐ-UBND` (the 2025 table) while `87/2025/NQ-HĐND` has
been in force since January; Đà Nẵng's is `07/2021/QĐ-UBND` and Hải Phòng's
`54/2019/QĐ-UBND`. So the document number is stored verbatim per row and
`validate_land_price` warns when it disagrees with the primary citation in
`province_profiles.json` — currently every district. A ratio computed against a
superseded table answers last year's question, and the reader has to be able to
see that.

**Its pagination is not monotonic.** Each page renders the table twice, so half
of every page is a self-duplicate; HCMC then repeats page 2 across pages 3-5,
resumes real rows on page 6, repeats again on 12-16, and still yields new
streets at pages 40 and 100 before running out around 140. Stopping at the first
barren page collected 165 of ~3,900 streets and stopping after five collected
698. The only safe terminator is a page with no parseable rows at all.

**Vietnamese street names repeat across districts, and joining on the name alone
is catastrophic.** 202 of HCMC's 3,098 state-priced streets exist in more than
one district. Joining asking prices to state prices by name matched the Gò Vấp
"Lê Lợi" (asking 1.6 million/m²) to the District 1 one (687 million/m²) — a 400x
error that reads as a market signal. The join must require district equality,
which cost 41 → 31 districts and 1,205 → 622 matched streets, and is worth it.

**State prices are a fee base, not a valuation, and the level carries no
signal.** Measured medians: HCMC asks 1.43x its state table, Hà Nội 1.81x, and
central districts sit *below* 1.0 (Quận 1 at 0.79x) because their state prices
were raised close to market, while peri-urban districts run far above (Quận Bình
Tân 16x, Huyện Bình Chánh 7.8x). Publishing the ratio as an "overvalued" verdict
would be backwards; only the dispersion within one province is readable.

**The national planning portal does not resolve.** `quyhoach.gov.vn`,
`quyhoachquocgia.mpi.gov.vn`, `quyhoach.mpi.gov.vn` and `quyhoach.mof.gov.vn`
all fail DNS from here (2026-09-10), and `mpi.gov.vn` itself times out — the
2025 ministry merger moved things and the public planning database is not
reachable at any obvious address. `vanban.chinhphu.vn` answers 200 but its
document pages do not resolve by `docid`. So the province-level gazette is the
only route to a planning decision's full text, and it is one portal per province.

**Provincial gazettes ARE automatable, unlike the central one** (measured
2026-09-10). `congbao.hochiminhcity.gov.vn` serves document lists and metadata
server-side, its URLs are predictable
(`/cong-bao/van-ban/quyet-dinh/so/{so}-{nam}-qd-ubnd/ngay/{dd-mm-yyyy}/{id}`),
and `/tai-ve/{id}?cbid=` returns the full PDF with `Content-Type:
application/pdf` — the Bảng giá đất decision 79/2024/QĐ-UBND came back as an
11 MB, 78-page file. A `Referer` header is enough; no session, no JS. Note the
sibling portals are not uniform: `congbao.hanoi.gov.vn` and
`congbao.danang.gov.vn` both time out from here, so each province needs its own
reachability check before it is added.

**Those gazette PDFs are scans with a bold-by-overprint text layer.** Every
bold glyph is emitted twice at the same coordinates, so `extract_text()` returns
`NNGGUUYYỄỄNN` and `find_tables()` returns nothing. Deduplicating characters by
`(text, x0//2, top//2)` and rebuilding lines by `top//3` recovers readable rows,
and the price column survives intact (`204.900`, `409.900`). What does not
survive is Vietnamese diacritics: the OCR yields `CAO BẢ QUÁT` for `CAO BÁ
QUÁT`, `NGUYỀN` for `NGUYỄN`, `CHU MẠN1Ỉ TRINH` for `CHU MẠNH TRINH`. Matching
those names against the warehouse's own `street_name` values, diacritics
stripped and substrings allowed, lands at **53% (194 of 366 rows)** — the
remainder is almost entirely OCR damage to names, not missing data.

**Neither permitted legal-text source is machine-readable.** `vbpl.vn` is a
React SPA whose only data path is the robots-`Disallow`ed `/api/`; server HTML
carries zero search results. `congbao.chinhphu.vn` returns a byte-identical
201,126-character shell for a search URL and its home page, and is the *central*
gazette, which does not carry provincial Bảng giá đất at all — those live on 34
separate provincial portals in PDF/DOC attachments. Do not build a legal-document
crawler; download the documents once and parse them offline.

**Chotot and Nhà Tốt are the same source.** `nhatot.com` is Chợ Tốt's property
brand and serves from the same `gateway.chotot.com` endpoint with the same
schema. Adding it as a second source would double-count the same listings.

**`batdongsan.com.vn` answers 403 to everything, including robots.txt.** The
response carries `Cf-Mitigated: challenge` and a Cloudflare "Just a moment..."
body with `<meta name="robots" content="noindex,nofollow">`; a full browser
header set (sec-ch-ua, Sec-Fetch-*, Accept-Language, Accept-Encoding) does not
change it. This is an active refusal, not a user-agent filter — do not try to
work around it. Mogi, Homedy, Guland and CafeLand sit behind Cloudflare too and
all answer 200 with full sitemaps, which is what shows the block is that site's
own choice rather than a platform behaviour.

**Farside writes negatives in accounting parentheses** — `(95.1)` means -95.1 —
and the BTC and ETH tables use different header shapes. Reconcile any parser
change against the source's own Total column; all 650 days currently match.

**The public NEWS artifact is a presentation document, not a stable flat feed.**
The current schema keeps 542 canonical links in `allArticles`, while richer
copies of those links (with `excerpt`) are nested under front-page/sector
objects. `news_link.py` walks and de-duplicates the whole document by URL so a
schema rearrangement does not silently throw away the text used for matching.

## Zero-fill and null-fill

**Several sources zero-fill where they mean "not applicable".** ROIC is 0.0 for
every bank, dividend yield is 0.0 until the quarter's payment is declared, and
bank-only ratios are 0.0 for every non-financial. Published raw these state
"this bank earns no return on capital" and "Vinamilk pays no dividend".
`stocks_build.py` drops them; dividend yield falls back to the last quarter that
reported one.

**VCI statement field IDs only have meaning with their metrics metadata.**
Persist `field`, bilingual label, hierarchy level, section, period type and
public date together. Do not map rows by display position: bank, securities,
insurance and ordinary-company forms have different applicable lines, while
the API can fill irrelevant form cells with zero.

**VCI's bank CIR carries the accounting sign of operating costs.** The source
ratio is negative even though cost/income is conventionally displayed as a
positive percentage. `ticker_details_build.py` takes its absolute value and
drops a whole annual/quarterly cadence when every observation is a source
placeholder zero; it does not erase isolated zero observations in a populated
series.

**Margins are ratios to revenue**, so a company with almost none produces valid
nonsense (PTC: 75,592% net margin). `SANE_RANGE` suppresses rather than clamps —
a clamped value would read as a real -500%.

**`price_board` returns placeholder rows with no symbol** for delisted or
suspended tickers, and pandas fills gaps with `NaN`, which DuckDB will not cast
into an integer column. `warehouse._clean` handles the NaN; collectors drop the
symbol-less rows.

**The two `eq_quote` writers disagree on the unit of `value`.** VCI reports
accumulated turnover in *millions* of VND; the SSI board writes plain VND into
the same column. Measured across rows carrying both, the ratio
`value / (price * volume)` is exactly 1.0 for SSI rows and exactly 0.000001 for
VCI rows -- a scale difference, not a rounding artefact. Unconverted, every VCI
row ranks 1e6 too small, which removes all of HOSE/HNX/UPCOM from any money-flow
ranking while still producing a plausible-looking table. `vn_equity.py` now
normalises on the way in; `pipeline/transform/repair_value_units.py` was run once
to append corrected observations for rows written before that fix. Note the
foreign value columns are *not* affected -- both sources already write those in
plain VND, confirmed by comparing a VCI row and an SSI row for the same ticker
and session, where the foreign figures matched exactly while turnover differed
by 1e6. Do not scale them.

**Two sources write `eq_quote` and they carry different columns.** SSI has depth
and foreign detail but no share count; Vietcap has the share count. Taking the
newest row wholesale blanked market cap for the whole market (128 of 1,729
tickers kept one). The read is field-wise latest-non-null across the ten newest
market sessions, ordered by `(as_of, fetched_at)`, because zero on the newest
board is often "no trade", especially on UPCOM. A carried older trade publishes
its own date; a reference-price fallback is explicitly marked as not traded.
Share count is carried forward separately since it only changes on a corporate
action.

**Price history has no historical foreign flow.** Do not coalesce those nulls
to zero before a 5/20-day sum: it makes one real observation appear to be 20
days of unchanged flow. `stocks_build.py` publishes a horizon only after that
ticker has the corresponding number of actual foreign-flow observations.

**SSI iBoard keeps only level 1 after the close.** The daily job runs at ~15:03
ICT, eighteen minutes after the 14:45 session end, and at that point
`best2Bid`/`best3Bid` and their offer counterparts come back null for every
ticker -- only the final level-1 quote survives. The collector already requests
all three levels and `DEPTH_KEYS` maps all three, so nothing is dropped in code:
the warehouse simply has one level because that is what the source served. A
page that renders levels 2-3 unconditionally will show two blank rows on every
ticker. Either run the depth pass inside the session or render only the levels
present.

**SSI reports negative foreign room** where ownership already exceeds the cap.
Real, but it means "no room and over the limit", not a negative percentage.
Clamped, and rendered as "Kín room".

## Pagination and discovery

**Chotot pagination dies past offset ~20k** with HTTP 400, and province-level
`total` is a rounded placeholder (always 10000). Crawl district by district,
where `total` is real.

**Chotot district codes come in two forms**: short `area` (113) and full
`area_v2` (13113). Only `area_v2` works as a query parameter; the short form
returns an empty result set rather than an error.

**An unknown `region_v2` is silently ignored**, so probing a numeric range
"succeeds" for codes that do not exist. Province codes are harvested from the
national feed (`--discover-regions`); they are zone-prefixed and irregular
(1002 Phú Thọ, 5027 Cần Thơ, 12000 Hà Nội, 13000 HCMC).

**Vietcap's chart endpoint takes a `symbols` array but only honours one.** Pass
more and it returns an empty list rather than an error, so a batching
"optimisation" silently collects nothing. Every numeric field, timestamps
included, arrives as a string.

## Computation

**Gamma flip is not a cumulative sum over strikes.** Gamma depends on where spot
is, so the flip must be found by revaluing the whole book at candidate prices
and bisecting. The cumulative walk answers a different question and put the flip
19% above spot on a book that is long gamma at spot. Cross-checked against a
live third-party feed: 66,710 against their 66,379.

**Returns spanning a trading halt are not one-day moves.** IDP resumed after ~2
months at -38.9%, which a naive z-score called an 8.8-sigma day. But the gap
threshold must clear VN public holidays: National Day put six calendar days
between the last two sessions for 765 tickers, so anything tighter than ten days
discards the entire market's most recent move.

**Backfilled history is stamped with the bar's own date**, never with now.
Stamping it with collection time would tell a point-in-time query that a year of
prices was known at that instant — the exact look-ahead the warehouse exists to
prevent. Backfilled rows carry no foreign flow: no free historical source
exists, so those columns accumulate forward only.

## Performance and durability

**`append` must not measure its own delta with `count(*)`.** That plus
`executemany` scales with total table size, and turned a fundamentals pass into
a CPU-bound crawl. One registered relation per batch runs at ~676k rows/sec.

**Flush collectors often, not efficiently.** The history backfill first flushed
every 20k rows and was throttled to death before its first write, losing a
half-hour run entirely. Batches are now small enough that a killed job leaves
usable progress for `--skip-existing` to resume from.

**DuckDB allows one writer.** Collectors wait up to five minutes for the slot;
build steps use `connect_reader()` and fail fast instead, because a stale build
is worse than a late one. Run collection and building in sequence.
