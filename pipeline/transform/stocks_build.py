"""Build stocks.json — the screener's data contract.

Two things distinguish this from a price snapshot:

- **Foreign flow is a first-class column**, at 1/5/20-day horizons. VN retail
  watches it daily and no free screener aggregates it.
- **Valuation is expressed against a ticker's own history**, not just its
  absolute level. A P/E of 12 means nothing until you know the ticker traded
  between 8 and 30 over the past eight years; the percentile says whether today
  is cheap *for this company*.

The warehouse is append-only, so every read here takes the latest observation
per key rather than assuming one row per ticker.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.publish.emit import write_json

log = logging.getLogger(__name__)

# Warehouse metric names (written by pipeline/sources/vci_direct.py) mapped to
# the short keys the browser receives.
METRIC_MAP = {
    # Valuation
    "pe": "pe",
    "pb": "pb",
    "ps": "ps",
    "ev_ebitda": "ev",
    "p_cf": "pcf",
    "dividend_yield": "dy",
    # Returns and margins
    "roe": "roe",
    "roa": "roa",
    "roic": "roic",
    "gross_margin": "gpm",
    "ebit_margin": "ebitm",
    "net_margin": "npm",
    # Balance sheet
    "debt_equity": "de",
    "current_ratio": "cr",
    "quick_ratio": "qr",
    "leverage": "lev",
    "asset_turnover": "at",
    # Bank-specific: absent for non-financials, which is expected.
    "nim": "nim",
    "npl": "npl",
    "car": "car",
    "casa": "casa",
    "ldr": "ldr",
    "cir": "cir",
}

# Ratios the source reports as fractions (0.2647) rather than percent (26.47).
AS_PERCENT = {
    "roe", "roa", "roic", "gpm", "ebitm", "npm", "dy",
    "nim", "npl", "car", "casa", "ldr", "cir",
}

# Own-history percentiles are the point of keeping an eight-year panel, but one
# per metric would nearly double the payload for columns nobody screens on.
# These are the ones where "cheap or expensive for this company" is the actual
# question a reader is asking.
PERCENTILE_KEYS = {"pe", "pb", "ps", "ev", "roe", "dy"}

# Reported as 0.0 rather than null for companies they do not apply to.
BANK_ONLY = {"nim", "npl", "car", "casa", "ldr", "cir"}

# The mirror image: metrics the source zero-fills where they are undefined.
# ROIC comes back as 0.0 for every bank because the concept does not apply to
# them, and publishing that states "this bank earns no return on capital".
# (Dividend yield has the same problem but a different cause and is handled in
# FUNDAMENTAL_SQL, which falls back to the last quarter that reported one.)
ZERO_MEANS_MISSING = {"roic", "de", "gpm"}

# Margins are a ratio to revenue, so a company with almost none produces
# arithmetically valid nonsense: PTC reports a 75,592% net margin, QBS -52,447%.
# The figures are real but carry no information, and left in they wreck every
# sort and axis they touch. Suppressed rather than clamped, because a clamped
# value would read as a genuine -500%.
SANE_RANGE = {
    "npm": (-500, 500),
    "ebitm": (-500, 500),
    "gpm": (-500, 500),
    "roe": (-500, 500),
    "roa": (-500, 500),
    "roic": (-500, 500),
    "ldr": (0, 500),
    "de": (0, 100),
}

# Field-wise "latest non-null", not row-wise "latest row". Two sources write to
# eq_quote and they do not carry the same columns: the SSI board has depth and
# foreign detail but no share count, Vietcap has the share count. Taking the
# newest row wholesale would blank out market cap for every ticker the moment an
# SSI collection ran last -- which is exactly what happened (128 of 1,729 kept a
# share count). Coalescing per column keeps each field's most recent real value.
#
# Restricted to one global session date used to read as "the price board", not
# "every ticker's own last trade" -- and most UPCOM names do not trade every
# session. That silently dropped price for 873 of 1,751 tickers (half of
# HOSE/HNX/UPCOM combined, and the large majority of UPCOM), even though 849 of
# those still carried fundamentals: real, listed companies simply missing from
# every screen that sorts or filters on price. A ten-session window is wide
# enough to catch a thinly traded name's last real print without reaching back
# so far the number stops meaning "current" -- and each symbol still contributes
# only its own newest quote in the window, via the per-column arg_max below.
# `as_of` in the result is that quote's own session date, published as `pd` so
# the UI can show a reader exactly how stale a given row's price is.
QUOTE_WINDOW_SESSIONS = 10

LATEST_QUOTE_SQL = f"""
SELECT
    symbol,
    arg_max(as_of, row(as_of, fetched_at))
        FILTER (WHERE price IS NOT NULL AND price > 0)           AS as_of,
    arg_max(price, row(as_of, fetched_at))
        FILTER (WHERE price IS NOT NULL AND price > 0)           AS price,
    arg_max(ref_price, row(as_of, fetched_at))
        FILTER (WHERE ref_price IS NOT NULL)                     AS ref_price,
    arg_max(volume, row(as_of, fetched_at))
        FILTER (WHERE volume IS NOT NULL)                        AS volume,
    arg_max(value, row(as_of, fetched_at))
        FILTER (WHERE value IS NOT NULL)                         AS value,
    arg_max(foreign_buy_value, row(as_of, fetched_at))
        FILTER (WHERE foreign_buy_value IS NOT NULL)            AS foreign_buy_value,
    arg_max(foreign_sell_value, row(as_of, fetched_at))
        FILTER (WHERE foreign_sell_value IS NOT NULL)           AS foreign_sell_value,
    arg_max(exchange, row(as_of, fetched_at))
        FILTER (WHERE exchange IS NOT NULL)                      AS exchange,
    max(fetched_at)                                             AS fetched_at
FROM eq_quote
WHERE as_of >= (
    SELECT min(d) FROM (
        SELECT DISTINCT as_of AS d FROM eq_quote ORDER BY d DESC LIMIT {QUOTE_WINDOW_SESSIONS}
    )
)
GROUP BY symbol
"""

LATEST_LISTING_SQL = """
SELECT DISTINCT ON (symbol) symbol, organ_name, exchange, industry
FROM eq_listing
ORDER BY symbol, fetched_at DESC
"""

# Share count changes only on a corporate action, so unlike price it is safe --
# and necessary -- to carry forward from whichever day last reported it. The SSI
# board does not carry it at all.
SHARE_COUNT_SQL = """
SELECT DISTINCT ON (symbol) symbol, listed_share
FROM eq_quote
WHERE listed_share IS NOT NULL
ORDER BY symbol, fetched_at DESC
"""

# Rolling foreign net over the last N trading dates present in the warehouse.
FOREIGN_FLOW_SQL = """
WITH daily AS (
    SELECT symbol, as_of,
        coalesce(arg_max(foreign_buy_value, fetched_at)
            FILTER (WHERE foreign_buy_value IS NOT NULL), 0)
        - coalesce(arg_max(foreign_sell_value, fetched_at)
            FILTER (WHERE foreign_sell_value IS NOT NULL), 0) AS net
    FROM eq_quote
    GROUP BY symbol, as_of
    HAVING count(*) FILTER (
        WHERE foreign_buy_value IS NOT NULL OR foreign_sell_value IS NOT NULL
    ) > 0
),
ranked AS (
    SELECT symbol, as_of, net,
           row_number() OVER (PARTITION BY symbol ORDER BY as_of DESC) AS day_rank
    FROM daily
)
SELECT symbol,
       sum(net) FILTER (WHERE day_rank = 1)  AS net_1d,
       sum(net) FILTER (WHERE day_rank <= 5) AS net_5d,
       sum(net) FILTER (WHERE day_rank <= 20) AS net_20d,
       count(*) AS observed_days
FROM ranked
GROUP BY symbol
"""

# Price-derived measures that only exist once the warehouse holds history.
# Momentum and the 52-week position are what let the screener rank on trend
# rather than on a single day's close, and the z-score is how "unusual move"
# gets defined without picking an arbitrary percentage: 3% is a shrug for a
# small cap and an event for a blue chip.
PRICE_HISTORY_SQL = """
WITH daily AS (
    SELECT DISTINCT ON (symbol, as_of)
        symbol, as_of, price, volume
    FROM eq_quote
    WHERE price IS NOT NULL AND price > 0
    ORDER BY symbol, as_of, fetched_at DESC
),
ranked AS (
    SELECT symbol, as_of, price, volume,
           row_number() OVER (PARTITION BY symbol ORDER BY as_of DESC) AS back
    FROM daily
),
returns AS (
    SELECT symbol,
           price / lag(price) OVER (PARTITION BY symbol ORDER BY as_of) - 1 AS ret,
           as_of - lag(as_of) OVER (PARTITION BY symbol ORDER BY as_of) AS gap_days,
           row_number() OVER (PARTITION BY symbol ORDER BY as_of DESC) AS back
    FROM daily
),
vol AS (
    -- Returns spanning a trading halt are not one-day moves. IDP resumed after
    -- nearly two months at -38.9%, which a naive z-score reported as an
    -- 8.8-sigma day. The threshold has to clear VN's public holidays, though:
    -- the 2 September National Day put a six-calendar-day gap between the last
    -- two sessions for 765 tickers, so anything tighter than ten days would
    -- discard the entire market's most recent move.
    SELECT symbol,
           stddev_samp(ret) AS ret_sd,
           max(ret) FILTER (WHERE back = 1) AS last_ret
    FROM returns
    WHERE ret IS NOT NULL AND gap_days <= 10
    GROUP BY symbol
)
SELECT
    r.symbol,
    count(*)                                    AS days,
    max(r.price) FILTER (WHERE r.back = 1)      AS last_price,
    max(r.as_of) FILTER (WHERE r.back = 1)      AS last_price_date,
    max(r.price) FILTER (WHERE r.back = 22)     AS price_1m,
    max(r.price) FILTER (WHERE r.back = 64)     AS price_3m,
    max(r.price) FILTER (WHERE r.back = 127)    AS price_6m,
    max(r.price)                                AS high_52w,
    min(r.price)                                AS low_52w,
    median(r.volume)                            AS median_volume,
    any_value(v.ret_sd)                         AS ret_sd,
    any_value(v.last_ret)                       AS last_ret
FROM ranked r
LEFT JOIN vol v USING (symbol)
GROUP BY r.symbol
HAVING count(*) >= 30
"""

# Latest value per metric, plus the percentile of that value within the
# ticker's own history of the same metric.
FUNDAMENTAL_SQL = """
WITH observed AS (
    SELECT DISTINCT ON (symbol, period, metric)
        symbol, period, metric, value
    FROM eq_fundamental
    ORDER BY symbol, period, metric, fetched_at DESC
),
latest AS (
    -- For most metrics the newest quarter wins outright. Dividend yield is the
    -- exception: it is 0.0 until a payment is declared, so the newest quarter
    -- reads as "pays nothing" for companies that paid in every prior quarter.
    -- There alone, fall back to the most recent quarter that carried a value.
    SELECT DISTINCT ON (symbol, metric)
        symbol, metric, value AS latest_value, period AS latest_period
    FROM observed
    ORDER BY symbol, metric,
             (metric = 'dividend_yield' AND (value IS NULL OR value = 0)),
             period DESC
),
pct AS (
    SELECT o.symbol, o.metric,
           avg(CASE WHEN o.value <= l.latest_value THEN 1.0 ELSE 0.0 END) AS percentile,
           count(*) AS history_n
    FROM observed o
    JOIN latest l USING (symbol, metric)
    GROUP BY 1, 2
)
SELECT l.symbol, l.metric, l.latest_value, l.latest_period,
       p.percentile, p.history_n
FROM latest l JOIN pct p USING (symbol, metric)
"""


# Foreign ownership room, from the SSI board. Room is what decides whether
# foreign demand can be expressed at all: a ticker at its cap cannot be bought
# by non-residents however strong the interest, so a low room figure changes how
# a foreign-flow reading should be interpreted.
FOREIGN_ROOM_SQL = """
SELECT DISTINCT ON (series)
    split_part(series, '.', 3) AS symbol,
    value
FROM metric_ts
WHERE series LIKE 'vn.board.%.foreign_room'
ORDER BY series, fetched_at DESC
"""

# Latest three-level order book from SSI. Compact keys keep stocks.json small:
# b1/b1v are bid-1 price/volume, a1/a1v ask-1, and likewise for levels 2-3.
BOARD_DEPTH_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (series)
        split_part(series, '.', 3) AS symbol,
        split_part(series, '.', 4) AS field,
        value
    FROM metric_ts
    WHERE series LIKE 'vn.board.%'
      AND split_part(series, '.', 4) IN (
        'bid1','bid1_vol','bid2','bid2_vol','bid3','bid3_vol',
        'ask1','ask1_vol','ask2','ask2_vol','ask3','ask3_vol'
      )
    ORDER BY series, as_of DESC, fetched_at DESC
)
SELECT symbol, field, value FROM latest
"""

DEPTH_KEYS = {
    **{f"bid{i}": f"b{i}" for i in (1, 2, 3)},
    **{f"bid{i}_vol": f"b{i}v" for i in (1, 2, 3)},
    **{f"ask{i}": f"a{i}" for i in (1, 2, 3)},
    **{f"ask{i}_vol": f"a{i}v" for i in (1, 2, 3)},
}


# The two price sources spell exchanges differently — SSI writes "hose"/"upcom",
# Vietcap "HSX"/"UPCOM" — and a row's value depends on which collector last
# touched it. Left alone the screener's exchange filter offers seven options for
# three exchanges and splits HOSE across two of them.
EXCHANGE_CANON = {
    "hose": "HOSE", "hsx": "HOSE",
    "hnx": "HNX",
    "upcom": "UPCOM",
    "delisted": "Đã hủy niêm yết",
}


def canon_exchange(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return EXCHANGE_CANON.get(value.strip().lower(), value.strip().upper())


def _rows(con, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def build(dry_run: bool = False) -> dict[str, Any]:
    con = wh.connect_reader()
    try:
        quotes = {r["symbol"]: r for r in _rows(con, LATEST_QUOTE_SQL)}
        listings = {r["symbol"]: r for r in _rows(con, LATEST_LISTING_SQL)}
        flows = {r["symbol"]: r for r in _rows(con, FOREIGN_FLOW_SQL)}
        rooms = {r["symbol"]: r["value"] for r in _rows(con, FOREIGN_ROOM_SQL)}
        depth: dict[str, dict[str, float]] = {}
        for item in _rows(con, BOARD_DEPTH_SQL):
            key = DEPTH_KEYS.get(item["field"])
            if key and item["value"] is not None:
                depth.setdefault(item["symbol"], {})[key] = item["value"]
        shares = {r["symbol"]: r["listed_share"] for r in _rows(con, SHARE_COUNT_SQL)}
        history = {r["symbol"]: r for r in _rows(con, PRICE_HISTORY_SQL)}
        trading_days, session_date = con.execute(
            "SELECT count(DISTINCT as_of), max(as_of) FROM eq_quote"
        ).fetchone()
        fundamentals: dict[str, dict[str, Any]] = {}
        for r in _rows(con, FUNDAMENTAL_SQL):
            key = METRIC_MAP.get(r["metric"])
            if not key:
                continue
            fundamentals.setdefault(r["symbol"], {})[key] = {
                "v": r["latest_value"],
                "pct": r["percentile"],
                "n": r["history_n"],
            }
    finally:
        con.close()

    out: list[dict[str, Any]] = []
    for symbol, listing in sorted(listings.items()):
        quote = quotes.get(symbol) or {}
        hist = history.get(symbol) or {}
        price = quote.get("price")
        ref = quote.get("ref_price")
        price_date = quote.get("as_of")

        # No trade on the latest session reports price as 0, not null. That is
        # correct as a statement about today, but naively dropping it dropped
        # 850 of 1,751 tickers -- almost entirely UPCOM/HNX names the warehouse
        # has exactly one observation for, so PRICE_HISTORY_SQL's >=30-day
        # requirement never sees them either: these are not thinly traded
        # stocks, they are ones this pipeline has not watched trade at all yet.
        # Fall back in two steps, and mark which happened so the UI never
        # states a stand-in as a live quote:
        #   1. a genuine last trade, if PRICE_HISTORY_SQL has one (>=30 days of
        #      the symbol actually trading somewhere in its window);
        #   2. else the exchange's own reference price for the session --
        #      administratively set, not market-discovered, but the only
        #      figure that exists for a ticker with a single snapshot on
        #      record. Never treated as a % change against itself.
        is_reference_only = False
        if not price:
            if hist.get("last_price"):
                price = hist["last_price"]
                price_date = hist.get("last_price_date")
            elif ref:
                price = ref
                is_reference_only = True
            ref = None  # ref_price pairs with the session that quoted it, not a carried-forward price

        row: dict[str, Any] = {
            "s": symbol,
            "n": listing.get("organ_name"),
            "e": canon_exchange(quote.get("exchange") or listing.get("exchange")),
            "i": listing.get("industry"),
        }
        if price:
            row["p"] = round(price, 1)
            if ref:
                row["ch"] = round((price - ref) / ref * 100, 2)
            if is_reference_only:
                # Not a traded price at all -- state the rule, don't let it
                # read like a quote. Short key: "reference, not a trade".
                row["pr"] = 1
            elif price_date and session_date and price_date < session_date:
                # Publish the session a real trade is from only when it lags
                # the market's latest session -- the common case needs no
                # flag, and a reader should never mistake an old print for a
                # live price.
                row["pd"] = str(price_date)
        if quote.get("volume"):
            row["v"] = int(quote["volume"])
        for key, value in (depth.get(symbol) or {}).items():
            row[key] = int(value) if key.endswith("v") else round(value, 1)
        share_count = shares.get(symbol)
        if price and share_count:
            # Market cap in billion VND, matching the fundamental panel's unit.
            row["mc"] = round(price * share_count / 1e9, 1)

        # Room as a share of the float, not the raw count: "362 million shares"
        # means nothing without knowing the company's size, while "24% of the
        # float is still open to foreigners" is directly readable.
        room = rooms.get(symbol)
        # SSI reports a negative room where foreign ownership already exceeds the
        # current cap — real, but it means "no room, and over the limit", not a
        # negative percentage. Clamped to zero so the column reads as headroom.
        if room is not None and share_count:
            row["fr"] = round(max(room, 0) / share_count * 100, 1)

        hist = history.get(symbol)
        if hist and hist.get("last_price"):
            last = hist["last_price"]
            for src, dst in (("price_1m", "m1"), ("price_3m", "m3"), ("price_6m", "m6")):
                past = hist.get(src)
                if past:
                    row[dst] = round((last / past - 1) * 100, 1)

            high, low = hist.get("high_52w"), hist.get("low_52w")
            if high and low and high > low:
                # Position in the 52-week range: 100 means at the high, 0 at the
                # low. More useful than the two prices, which need mental
                # arithmetic before they say anything.
                row["pos52"] = round((last - low) / (high - low) * 100)

            # Today's move in standard deviations of this ticker's own daily
            # returns. A 3% day is noise for a small cap and an event for VCB;
            # the z-score is what makes those comparable.
            sd, ret = hist.get("ret_sd"), hist.get("last_ret")
            if sd and ret is not None and sd > 0:
                row["z"] = round(ret / sd, 1)

        flow = flows.get(symbol)
        if flow:
            # A 5- or 20-day window is only meaningful once the warehouse holds
            # that many trading days. Until then the rolling sums equal the
            # daily figure, and publishing three identical columns would imply
            # a history that does not exist.
            for src, dst, needed in (
                ("net_1d", "f1", 1), ("net_5d", "f5", 5), ("net_20d", "f20", 20)
            ):
                # Do not turn missing foreign-flow history into zeros. The
                # warehouse has years of prices but may only have one SSI/
                # Vietcap foreign snapshot; 5d/20d appear only after that many
                # actual observations have accumulated for this ticker.
                if flow.get(src) and (flow.get("observed_days") or 0) >= needed:
                    row[dst] = round(flow[src] / 1e9, 2)  # billion VND

        for key, item in (fundamentals.get(symbol) or {}).items():
            if item["v"] is None:
                continue
            # Bank-only metrics come back as a literal 0.0 for every
            # non-financial rather than as null. Publishing "NIM 0.00%" against
            # a shoe manufacturer states something false, so drop them.
            if not item["v"] and (key in BANK_ONLY or key in ZERO_MEANS_MISSING):
                continue
            # The source reports rates as fractions (VCB ROE arrives as 0.179,
            # NIM as 0.0275); publish them as percent so the UI never has to
            # know which convention a given column follows.
            value = item["v"] * 100 if key in AS_PERCENT else item["v"]
            bounds = SANE_RANGE.get(key)
            if bounds and not (bounds[0] <= value <= bounds[1]):
                continue
            row[key] = round(value, 2)
            # A percentile against two quarters of history is noise, not signal.
            # An all-zero dividend history has no meaningful percentile: zero
            # ranks at 100% only because every observation ties it, which the
            # UI would misleadingly describe as an unusually high yield.
            if key in PERCENTILE_KEYS and item["n"] >= 8 and not (key == "dy" and value == 0):
                row[f"{key}_p"] = round(item["pct"] * 100)

        out.append(row)

    stats: dict[str, Any] = {
        "rows": len(out),
        "priced": sum(1 for r in out if "p" in r),
        "with_fundamentals": sum(1 for r in out if "pe" in r or "pb" in r),
        "with_foreign_flow": sum(1 for r in out if "f1" in r),
        "with_industry": sum(1 for r in out if r.get("i")),
        "with_momentum": sum(1 for r in out if "m3" in r),
        "with_depth": sum(1 for r in out if "b1" in r or "a1" in r),
        "trading_days_held": trading_days,
    }

    if not dry_run:
        # Wrapped rather than a bare array: without a top-level `updated_at` the
        # page cannot tell a reader how old the numbers are, and a stale file
        # looks identical to a fresh one.
        path = write_json("stocks.json", {
            "rows": out,
            "as_of": str(session_date) if session_date else None,
            "trading_days": trading_days,
        })
        stats["path"] = str(path)
        stats["size_kb"] = round(path.stat().st_size / 1024, 1)

    stats["sample"] = out[:2]
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build stocks.json from the warehouse")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(build(args.dry_run), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
