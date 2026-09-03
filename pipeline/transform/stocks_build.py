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

LATEST_QUOTE_SQL = """
SELECT DISTINCT ON (symbol)
    symbol, as_of, price, ref_price, volume, value, listed_share,
    foreign_buy_value, foreign_sell_value, exchange, fetched_at
FROM eq_quote
ORDER BY symbol, fetched_at DESC
"""

LATEST_LISTING_SQL = """
SELECT DISTINCT ON (symbol) symbol, organ_name, exchange, industry
FROM eq_listing
ORDER BY symbol, fetched_at DESC
"""

# Rolling foreign net over the last N trading dates present in the warehouse.
FOREIGN_FLOW_SQL = """
WITH daily AS (
    SELECT DISTINCT ON (symbol, as_of)
        symbol, as_of,
        coalesce(foreign_buy_value, 0) - coalesce(foreign_sell_value, 0) AS net
    FROM eq_quote
    ORDER BY symbol, as_of, fetched_at DESC
),
ranked AS (
    SELECT symbol, as_of, net,
           dense_rank() OVER (ORDER BY as_of DESC) AS day_rank
    FROM daily
)
SELECT symbol,
       sum(net) FILTER (WHERE day_rank = 1)  AS net_1d,
       sum(net) FILTER (WHERE day_rank <= 5) AS net_5d,
       sum(net) FILTER (WHERE day_rank <= 20) AS net_20d
FROM ranked
GROUP BY symbol
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
        trading_days = con.execute(
            "SELECT count(DISTINCT as_of) FROM eq_quote"
        ).fetchone()[0]
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
        price = quote.get("price")
        ref = quote.get("ref_price")

        row: dict[str, Any] = {
            "s": symbol,
            "n": listing.get("organ_name"),
            "e": quote.get("exchange") or listing.get("exchange"),
            "i": listing.get("industry"),
        }
        if price:
            row["p"] = round(price, 1)
            if ref:
                row["ch"] = round((price - ref) / ref * 100, 2)
        if quote.get("volume"):
            row["v"] = int(quote["volume"])
        if price and quote.get("listed_share"):
            # Market cap in billion VND, matching the fundamental panel's unit.
            row["mc"] = round(price * quote["listed_share"] / 1e9, 1)

        flow = flows.get(symbol)
        if flow:
            # A 5- or 20-day window is only meaningful once the warehouse holds
            # that many trading days. Until then the rolling sums equal the
            # daily figure, and publishing three identical columns would imply
            # a history that does not exist.
            for src, dst, needed in (
                ("net_1d", "f1", 1), ("net_5d", "f5", 5), ("net_20d", "f20", 20)
            ):
                if flow.get(src) and trading_days >= needed:
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
            if key in PERCENTILE_KEYS and item["n"] >= 8:
                row[f"{key}_p"] = round(item["pct"] * 100)

        out.append(row)

    stats: dict[str, Any] = {
        "rows": len(out),
        "priced": sum(1 for r in out if "p" in r),
        "with_fundamentals": sum(1 for r in out if "pe" in r or "pb" in r),
        "with_foreign_flow": sum(1 for r in out if "f1" in r),
        "with_industry": sum(1 for r in out if r.get("i")),
        "trading_days_held": trading_days,
    }

    if not dry_run:
        path = write_json("stocks.json", out)
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
