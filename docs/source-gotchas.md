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

**SSI iBoard pins CORS to `iboard.ssi.com.vn`** and refuses three exchange calls
made back to back. Pipeline-side only, with a few seconds between calls.

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

**Chotot mixes sale and rental ads unless `st` is pinned.** A rental price/m² is
~1000x smaller than a sale one (0.24 vs 208 million/m² in the same district).
Every query sets `st=s` or `st=u` explicitly.

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
