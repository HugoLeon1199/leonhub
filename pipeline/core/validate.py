"""Deploy gate: refuse to publish data that fails a sanity check.

The failure this guards against is not a crash but a quiet one: a source starts
returning empty rows, a unit convention changes, half the market disappears —
and the site keeps serving numbers that look plausible and are wrong. A crash
is visible; a wrong median is not. So the checks below are deliberately blunt
and fail the build rather than warning into a log nobody reads.

Every threshold is a judgement call, and each is stated as a constant so it can
be argued with rather than buried in a condition.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse
from pathlib import Path

from pipeline.publish.emit import read_json, read_prev_json

log = logging.getLogger(__name__)

# A drop this large between runs means a source broke, not that the market moved.
MAX_ROW_DROP = 0.05
# VN asking prices, million VND per m2. Outside this band the unit changed.
BDS_PRICE_RANGE = (1.0, 3000.0)
# A published district cell must rest on at least this many listings.
BDS_MIN_SAMPLES = 20
# A US row must carry enough sessions to draw the one-year chart and compute a
# 126-session momentum. The collector publishes a 260-session trailing window;
# a short holiday year still clears this floor.
US_MIN_SESSIONS = 200
# Equity prices in VND. A stock at 5 VND or 5 million VND is a parsing error.
EQUITY_PRICE_RANGE = (100.0, 5_000_000.0)
# Ratios published as percent; outside this band the fraction/percent
# convention flipped somewhere.
PERCENT_RANGE = (-200.0, 500.0)
PERCENT_KEYS = {"roe", "roa", "roic", "gpm", "ebitm", "npm", "dy", "nim", "npl", "car", "casa", "ldr", "cir"}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def _fraction_outside(values: list[float], low: float, high: float) -> float:
    if not values:
        return 0.0
    bad = sum(1 for v in values if v < low or v > high)
    return bad / len(values)


def validate_stocks(rows: list[dict[str, Any]], previous: list[dict[str, Any]] | None) -> Report:
    rep = Report()
    if not isinstance(rows, list) or not rows:
        rep.error("stocks.json is empty")
        return rep
    if any(not isinstance(row, dict) for row in rows):
        rep.error("stocks.json contains a non-object row")
        return rep

    rep.stats["rows"] = len(rows)
    rep.stats["priced"] = sum(1 for r in rows if "p" in r)
    rep.stats["with_fundamentals"] = sum(1 for r in rows if "pe" in r or "pb" in r)

    missing_symbol = sum(1 for r in rows if not r.get("s"))
    if missing_symbol:
        rep.error(f"{missing_symbol} rows have no ticker")

    prices = [r["p"] for r in rows if isinstance(r.get("p"), (int, float))]
    lo, hi = EQUITY_PRICE_RANGE
    bad_price = _fraction_outside(prices, lo, hi)
    if bad_price > 0.01:
        rep.error(
            f"{bad_price:.1%} of prices fall outside {lo:,.0f}–{hi:,.0f} VND — "
            "the price unit likely changed"
        )

    for key in PERCENT_KEYS:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if not values:
            continue
        plo, phi = PERCENT_RANGE
        bad = _fraction_outside(values, plo, phi)
        # A unit flip moves the whole column, not a tail of it. The transform
        # already suppresses individually absurd margins, so what remains here
        # is a check on convention: 10% out of range means the column changed
        # meaning, while a few percent is just companies with odd financials.
        if bad > 0.10:
            rep.error(
                f"{bad:.1%} of `{key}` values fall outside {plo}–{phi} — "
                "a fraction is probably being published as a percent, or vice versa"
            )
        elif bad > 0.01:
            rep.warn(f"{bad:.1%} of `{key}` values sit outside {plo}–{phi}")

    if rep.stats["priced"] == 0:
        rep.error("no ticker carries a price")
    elif rep.stats["priced"] < 300:
        rep.warn(f"only {rep.stats['priced']} tickers priced — is the market closed?")

    if previous:
        drop = 1 - len(rows) / len(previous)
        rep.stats["row_change"] = round(-drop, 4)
        if drop > MAX_ROW_DROP:
            rep.error(
                f"ticker count fell {drop:.1%} versus the previous build "
                f"({len(previous)} → {len(rows)})"
            )

    return rep


def validate_bds(rows: list[dict[str, Any]], previous: list[dict[str, Any]] | None) -> Report:
    rep = Report()
    if not isinstance(rows, list) or not rows:
        rep.error("bds.json is empty")
        return rep
    if any(not isinstance(row, dict) for row in rows):
        rep.error("bds.json contains a non-object row")
        return rep

    rep.stats["rows"] = len(rows)
    rep.stats["districts"] = len({(r.get("r"), r.get("d")) for r in rows})
    rep.stats["with_yield"] = sum(1 for r in rows if "y" in r)

    thin = [r for r in rows if (r.get("n") or 0) < BDS_MIN_SAMPLES]
    if thin:
        rep.error(
            f"{len(thin)} rows published with fewer than {BDS_MIN_SAMPLES} samples "
            f"(first: {thin[0].get('r')}/{thin[0].get('d')})"
        )

    prices = [r["p"] for r in rows if isinstance(r.get("p"), (int, float))]
    lo, hi = BDS_PRICE_RANGE
    outside = [p for p in prices if p < lo or p > hi]
    if outside:
        rep.error(
            f"{len(outside)} district medians fall outside {lo}–{hi} million VND/m² "
            f"(e.g. {outside[0]:.2f}) — rental listings may be leaking into the "
            "sale aggregate"
        )

    missing_meta = sum(1 for r in rows if not r.get("u") or not r.get("n"))
    if missing_meta:
        rep.error(f"{missing_meta} rows lack `n` or `updated_at` provenance")

    # Panel metrics, when present. Each is a percentage or a day count, and a
    # value outside its range means the panel SQL changed shape rather than
    # that the market did something surprising.
    for key, lo, hi in (
        ("pc", 0, 100), ("gr", 0, 100), ("grx", 0, 100), ("dmc", 0, 100),
        ("ag", 0, 100), ("pcm", -100, 0),
    ):
        bad = [
            r for r in rows
            if isinstance(r.get(key), (int, float)) and not lo <= r[key] <= hi
        ]
        if bad:
            rep.error(
                f"{len(bad)} rows have `{key}` outside {lo}–{hi} "
                f"(e.g. {bad[0][key]} in {bad[0].get('r')}/{bad[0].get('d')})"
            )
    negative_dm = [
        r for r in rows if isinstance(r.get("dm"), (int, float)) and r["dm"] < 0
    ]
    if negative_dm:
        rep.error(f"{len(negative_dm)} rows report a negative time on market")

    # A published cut rate needs its own denominator beside it, or the reader
    # cannot tell 1-in-2 from 50-in-100.
    orphan_pc = [r for r in rows if r.get("pc") is not None and not r.get("pcn")]
    if orphan_pc:
        rep.error(f"{len(orphan_pc)} rows publish `pc` without the `pcn` sample")

    rep.stats["with_price_cut"] = sum(1 for r in rows if r.get("pc") is not None)
    rep.stats["with_gone_rate"] = sum(1 for r in rows if r.get("gr") is not None)
    rep.stats["with_agent_split"] = sum(1 for r in rows if r.get("ag") is not None)

    if previous:
        drop = 1 - len(rows) / len(previous)
        rep.stats["row_change"] = round(-drop, 4)
        if drop > MAX_ROW_DROP:
            rep.warn(
                f"district coverage fell {drop:.1%} versus the previous build "
                f"({len(previous)} → {len(rows)})"
            )

    return rep


def validate_us(rows: list[dict[str, Any]], previous: list[dict[str, Any]] | None) -> Report:
    """Guard the curated delayed-US snapshot and its one-year chart series."""
    rep = Report()
    if not isinstance(rows, list) or len(rows) < 30:
        rep.error(f"US snapshot has only {len(rows) if isinstance(rows, list) else 0} rows")
        return rep
    symbols = [row.get("s") for row in rows]
    if len(set(symbols)) != len(symbols) or any(not symbol for symbol in symbols):
        rep.error("US snapshot contains a missing or duplicate symbol")
    malformed = []
    stale_close = []
    for row in rows:
        history = row.get("hist")
        if not isinstance(history, list) or len(history) < US_MIN_SESSIONS:
            malformed.append(row.get("s"))
            continue
        # [date, close] is the legacy shape; [date, o, h, l, c, v] is what the
        # collector publishes now so a candlestick can be drawn. Accept both,
        # and read the close positionally from whichever arrived.
        if any(not isinstance(point, list) or len(point) not in (2, 6) for point in history):
            malformed.append(row.get("s"))
            continue
        dates = [point[0] for point in history]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            malformed.append(row.get("s"))
        # An OHLC bar whose high is below its low, or whose close sits outside
        # the bar, means the columns were mapped in the wrong order -- a fault
        # that produces a plausible-looking chart rather than an error.
        inverted = [
            point for point in history
            if len(point) == 6 and None not in point[1:5]
            and (point[2] < point[3] or not point[3] <= point[4] <= point[2])
        ]
        if inverted:
            malformed.append(row.get("s"))
        price = row.get("p")
        last_close = history[-1][-2] if len(history[-1]) == 6 else history[-1][1]
        if not isinstance(price, (int, float)) or price <= 0:
            malformed.append(row.get("s"))
        elif last_close and abs(price / last_close - 1) > 0.10:
            stale_close.append(row.get("s"))
    if malformed:
        rep.error(f"{len(malformed)} US rows have malformed price/history (first: {malformed[0]})")
    if stale_close:
        rep.warn(f"{len(stale_close)} US quotes differ >10% from last daily close")
    if previous:
        drop = 1 - len(rows) / len(previous)
        if drop > MAX_ROW_DROP:
            rep.error(f"US coverage fell {drop:.1%} ({len(previous)} → {len(rows)})")
    rep.stats.update(rows=len(rows), histories=sum(bool(r.get("hist")) for r in rows))
    return rep



def validate_fx(rows: list[dict[str, Any]], previous: list[dict[str, Any]] | None) -> Report:
    """Guard the delayed FX/macro snapshot.

    An exchange rate that silently changes scale is the failure this catches:
    a quote feed switching between 26,054 and 26.054 for USD/VND produces a
    plausible-looking number and a nonsense chart.
    """
    rep = Report()
    if not isinstance(rows, list) or not rows:
        rep.error("FX snapshot is empty")
        return rep

    keys = [row.get("s") for row in rows]
    if len(set(keys)) != len(keys) or any(not key for key in keys):
        rep.error("FX snapshot contains a missing or duplicate series key")

    malformed, jumped = [], []
    for row in rows:
        history = row.get("hist")
        price = row.get("p")
        if not isinstance(price, (int, float)) or price <= 0:
            malformed.append(row.get("s"))
            continue
        if not isinstance(history, list) or len(history) < 20:
            malformed.append(row.get("s"))
            continue
        dates = [point[0] for point in history]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            malformed.append(row.get("s"))
            continue
        last = history[-1][1]
        # A daily FX close moving more than a fifth against the live quote is a
        # unit change or a bad parse, not a market move.
        if last and abs(price / last - 1) > 0.20:
            jumped.append(row.get("s"))

    if malformed:
        rep.error(f"{len(malformed)} FX rows malformed (first: {malformed[0]})")
    if jumped:
        rep.error(f"{len(jumped)} FX quotes differ >20% from their last close (first: {jumped[0]})")
    if previous and len(rows) < len(previous):
        rep.warn(f"FX coverage fell ({len(previous)} → {len(rows)})")

    rep.stats.update(rows=len(rows), histories=sum(bool(r.get("hist")) for r in rows))
    return rep



def validate_crypto(rows: list[dict[str, Any]], previous: list[dict[str, Any]] | None) -> Report:
    """Guard the crypto board.

    The failure that matters is a feed returning stale or partial data while
    still looking well-formed: a board where nothing moved, or where the
    universe silently halved, reads as a quiet market rather than as a broken
    collector.
    """
    rep = Report()
    if not isinstance(rows, list) or len(rows) < 20:
        rep.error(f"Crypto board has only {len(rows) if isinstance(rows, list) else 0} rows")
        return rep

    symbols = [row.get("s") for row in rows]
    if len(set(symbols)) != len(symbols) or any(not s for s in symbols):
        rep.error("Crypto board contains a missing or duplicate symbol")

    bad = [r.get("s") for r in rows
           if not isinstance(r.get("p"), (int, float)) or (r.get("p") or 0) <= 0]
    if bad:
        rep.error(f"{len(bad)} crypto rows have a non-positive price (first: {bad[0]})")

    # Every major venue prints at least some movement across a whole day. A
    # board where nothing moved is a cached response, not a calm market.
    moved = sum(1 for r in rows if abs(r.get("chp") or 0) > 0.01)
    if moved < len(rows) * 0.5:
        rep.error(f"only {moved}/{len(rows)} crypto pairs moved; feed is likely stale")

    outside = [r.get("s") for r in rows
               if r.get("pos") is not None and not 0 <= r["pos"] <= 100]
    if outside:
        rep.error(f"{len(outside)} crypto rows have range position outside 0-100")

    if previous:
        drop = 1 - len(rows) / len(previous)
        if drop > MAX_ROW_DROP:
            rep.error(f"Crypto coverage fell {drop:.1%} ({len(previous)} → {len(rows)})")

    rep.stats.update(
        rows=len(rows),
        turnover_musd=round(sum(r.get("v") or 0 for r in rows)),
    )
    return rep



def validate_breadth(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    """Guard the VN breadth series.

    Percentages computed on a thin session look like a breadth collapse rather
    than like missing data, which is the failure this checks for.
    """
    rep = Report()
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, list) or len(series) < 20:
        rep.error(f"Breadth has only {len(series) if isinstance(series, list) else 0} sessions")
        return rep

    days = [p.get("d") for p in series]
    if days != sorted(days) or len(set(days)) != len(days):
        rep.error("Breadth sessions are unordered or duplicated")

    for key in ("a50", "a200", "uv"):
        outside = [p["d"] for p in series
                   if p.get(key) is not None and not 0 <= p[key] <= 100]
        if outside:
            rep.error(f"{len(outside)} breadth rows have {key} outside 0-100 (first: {outside[0]})")

    floor = (payload.get("median_universe") or 0) * 0.6
    thin = [p["d"] for p in series if floor and (p.get("n") or 0) < floor * 0.5]
    if thin:
        rep.warn(f"{len(thin)} published sessions cover under half the floor (first: {thin[0]})")

    if previous and isinstance(previous.get("series"), list):
        if len(series) < len(previous["series"]) - 5:
            rep.error(
                f"Breadth history shrank ({len(previous['series'])} → {len(series)})"
            )

    rep.stats.update(
        sessions=len(series),
        universe=payload.get("universe"),
        regime=(payload.get("regime") or {}).get("label"),
    )
    return rep


# ETF net flow in millions of USD. A day this large is a data error, not a
# real creation/redemption -- the biggest single-day BTC ETF flow on record is
# well under this.
FLOWS_DAY_RANGE = (-5000.0, 5000.0)


def validate_flows(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    rep = Report()
    etf = payload.get("etf") if isinstance(payload, dict) else None
    if not isinstance(etf, dict) or not etf:
        rep.error("flows.json has no `etf` series")
        return rep

    rep.stats["assets"] = sorted(etf)
    lo, hi = FLOWS_DAY_RANGE
    for asset, series in etf.items():
        if not isinstance(series, dict):
            rep.error(f"{asset}: flow series is not an object")
            continue
        values = series.get("v") or []
        days = series.get("d") or []
        if not isinstance(values, list) or not isinstance(days, list):
            rep.error(f"{asset}: dates and values must be arrays")
            continue
        rep.stats[f"{asset}_days"] = len(days)
        if not values:
            rep.error(f"{asset}: no daily flow values")
            continue
        if len(values) != len(days):
            rep.error(f"{asset}: {len(values)} values but {len(days)} dates — arrays desynced")
        bad = _fraction_outside([v for v in values if isinstance(v, (int, float))], lo, hi)
        if bad > 0:
            rep.error(f"{asset}: {bad:.1%} of daily flows fall outside ±{hi:,.0f}M USD")
        issuers = series.get("issuers")
        if not isinstance(issuers, dict) or not issuers:
            rep.error(f"{asset}: issuer-level ETF breakdown is missing")
        else:
            rep.stats[f"{asset}_issuers"] = len(issuers)
            for issuer, issuer_series in issuers.items():
                iv = issuer_series.get("v") if isinstance(issuer_series, dict) else None
                idates = issuer_series.get("d") if isinstance(issuer_series, dict) else None
                if not isinstance(iv, list) or not isinstance(idates, list) or len(iv) != len(idates):
                    rep.error(f"{asset}/{issuer}: issuer date/value arrays are malformed")

    if previous:
        prev_etf = previous.get("etf") or {}
        for asset, series in etf.items():
            prev_days = len((prev_etf.get(asset) or {}).get("d") or [])
            cur_days = len(series.get("d") or [])
            if prev_days and cur_days < prev_days:
                rep.warn(f"{asset}: day count fell {prev_days} → {cur_days} versus the previous build")

    return rep


def validate_signals(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    rep = Report()
    if not isinstance(payload, dict) or "stats" not in payload:
        rep.error("signals.json has no `stats`")
        return rep

    stats = payload["stats"]
    if not isinstance(stats, dict):
        rep.error("signals.json `stats` is not an object")
        return rep
    rep.stats["trades"] = stats.get("trades")
    rep.stats["universe"] = payload.get("universe")
    rep.stats["open_positions"] = len(payload.get("open") or [])

    # Deliberately no check on total_r, hit_rate, or drawdown: this rule is
    # published losing, on purpose (see CLAUDE.md). A validator that rejected a
    # bad track record would be exactly the pressure to tune it into looking
    # good that the page exists to resist. Only structure is checked here.
    if not payload.get("rules"):
        rep.error("signals.json has no `rules` block — a verdict without its rule")
    if not (payload.get("universe") or 0) > 0:
        rep.error("signals.json universe is empty")
    if payload.get("benchmark") is None:
        rep.warn("signals.json has no benchmark — a result without context")

    if previous:
        prev_trades = (previous.get("stats") or {}).get("trades") or 0
        cur_trades = stats.get("trades") or 0
        if prev_trades and cur_trades < prev_trades:
            rep.warn(f"closed trade count fell {prev_trades} → {cur_trades} versus the previous build")

    return rep


def validate_news(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    rep = Report()
    tickers = payload.get("tickers") if isinstance(payload, dict) else None
    if not isinstance(tickers, dict) or not tickers:
        rep.error("news_ticker.json has no ticker links")
        return rep

    stocks = read_json("stocks.json") or {}
    stock_rows = stocks if isinstance(stocks, list) else stocks.get("rows") or []
    universe = {r.get("s") for r in stock_rows if isinstance(r, dict) and r.get("s")}
    if not universe:
        rep.error("cannot validate news symbols because stocks.json has no universe")
        return rep

    links = 0
    today = datetime.now(timezone.utc).date()
    for symbol, items in tickers.items():
        if symbol not in universe:
            rep.error(f"news ticker `{symbol}` is not present in stocks.json")
        if not isinstance(items, list) or not items:
            rep.error(f"{symbol}: news list is empty or malformed")
            continue
        if len(items) > 10:
            rep.error(f"{symbol}: publishes {len(items)} links (maximum is 10)")
        for item in items:
            links += 1
            if not isinstance(item, dict) or not item.get("t"):
                rep.error(f"{symbol}: link has no title")
                continue
            parsed = urlparse(str(item.get("u") or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                rep.error(f"{symbol}: invalid URL `{item.get('u')}`")
            if item.get("m") not in {"name", "alias", "symbol"}:
                rep.error(f"{symbol}: unknown match method `{item.get('m')}`")
            if item.get("d"):
                try:
                    published = date.fromisoformat(str(item["d"]))
                except ValueError:
                    rep.error(f"{symbol}: invalid date `{item['d']}`")
                else:
                    if published > today:
                        rep.error(f"{symbol}: publication date {published} is in the future")

    rep.stats.update(symbols=len(tickers), links=links)
    if not links:
        rep.error("news_ticker.json contains zero links")
    return rep


# Deribit BTC/ETH surfaces. Spot ranges are wide on purpose -- they exist to
# catch a parsing error (an index price of 0, or of 7700 instead of 77000),
# not to flag a real market move.
GEX_SPOT_RANGE = {"btc": (1_000.0, 1_000_000.0), "eth": (50.0, 50_000.0)}


def validate_gex(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    rep = Report()
    if not isinstance(payload, dict) or not payload:
        rep.error("gex file is empty")
        return rep

    sym = str(payload.get("sym") or "").lower()
    rep.stats["sym"] = sym
    rep.stats["contracts"] = payload.get("contracts")

    if sym not in GEX_SPOT_RANGE:
        rep.error(f"unsupported or missing gex symbol `{sym or '?'}`")

    for key in ("spot", "net_gex", "gamma_flip", "max_pain"):
        if not isinstance(payload.get(key), (int, float)):
            rep.error(f"gex file missing numeric `{key}`")

    spot = payload.get("spot")
    bounds = GEX_SPOT_RANGE.get(sym)
    if isinstance(spot, (int, float)) and bounds and not (bounds[0] <= spot <= bounds[1]):
        lo, hi = bounds
        rep.error(f"spot {spot:,.2f} for {sym or '?'} falls outside the sane band {lo:,.0f}–{hi:,.0f}")

    contracts = payload.get("contracts")
    if not isinstance(contracts, int) or isinstance(contracts, bool) or contracts <= 0:
        rep.error("gex file reports zero option contracts")

    return rep


def validate_ticker_manifest(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    """Check the lazy per-ticker history contract without loading every file."""
    rep = Report()
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    count = payload.get("count") if isinstance(payload, dict) else None
    if not isinstance(symbols, list) or not symbols:
        rep.error("ticker manifest has no symbols")
        return rep
    if count != len(symbols):
        rep.error(f"ticker manifest count {count} != {len(symbols)} symbols")
    if len(symbols) < 1_500:
        rep.error(f"only {len(symbols)} ticker detail files — expected broad-market coverage")
    dossiers = payload.get("dossiers", 0)
    statements = payload.get("statements", 0)
    for label, value in (("dossiers", dossiers), ("statements", statements)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > len(symbols):
            rep.error(f"ticker manifest has invalid {label} count `{value}`")
    if isinstance(dossiers, int) and dossiers < len(symbols) * 0.95:
        rep.error(f"only {dossiers}/{len(symbols)} ticker dossiers — expected at least 95% coverage")
    if isinstance(statements, int) and statements < len(symbols) * 0.90:
        rep.error(f"only {statements}/{len(symbols)} statement payloads — expected at least 90% coverage")
    data_dir = Path(__file__).resolve().parents[2] / "data" / "ticker"
    missing = [symbol for symbol in symbols if not (data_dir / f"{symbol}.json").exists()]
    if missing:
        rep.error(f"{len(missing)} ticker detail files missing (first: {missing[0]})")
    for symbol in [s for s in ("VIC", "VCB", "SSI", symbols[0]) if s in symbols]:
        try:
            item = json.loads((data_dir / f"{symbol}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rep.error(f"{symbol}: unreadable ticker detail: {exc}")
            continue
        if item.get("s") != symbol or not isinstance(item.get("m"), dict) or not item["m"]:
            rep.error(f"{symbol}: malformed identity or metric map")
        for key, groups in (item.get("m") or {}).items():
            for cadence, points in groups.items():
                if cadence not in {"q", "y"} or not isinstance(points, list):
                    rep.error(f"{symbol}/{key}: invalid cadence `{cadence}`")
                elif any(not isinstance(p, list) or len(p) != 2 for p in points):
                    rep.error(f"{symbol}/{key}/{cadence}: malformed point")
        if item.get("co") is not None:
            if not isinstance(item["co"], dict) or not item["co"].get("profile"):
                rep.error(f"{symbol}: company dossier has no profile")
        if item.get("st") is not None:
            if not isinstance(item["st"], dict) or not item["st"]:
                rep.error(f"{symbol}: statement map is empty")
            for field, statement in item.get("st", {}).items():
                if not statement.get("label") or not any(k in statement for k in ("q", "y")):
                    rep.error(f"{symbol}/{field}: malformed statement series")
    rep.stats.update(
        tickers=len(symbols), missing_files=len(missing),
        dossiers=dossiers, statements=statements,
    )
    return rep


def validate_bds_listings(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    """Guard the per-district listing shards.

    Unlike bds.json this one deliberately has no minimum sample: a district
    with eight plots is published, because that is exactly where a buyer looks.
    So the checks here are about referential integrity and units, not weight of
    evidence -- a shard the index promises but that does not exist is a broken
    link in the UI, and a price/m2 outside the sane band means rental listings
    leaked into the sale lane again.
    """
    rep = Report()
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        rep.error("bds listings index is empty")
        return rep

    keys = [row.get("k") for row in rows]
    if len(set(keys)) != len(keys) or any(not key for key in keys):
        rep.error("bds listings index has a missing or duplicate district key")

    missing, malformed, bad_price = [], [], []
    sampled = 0
    for row in rows:
        key = row.get("k")
        # Reading 388 shards on every run is affordable and is the only way the
        # index's promises are actually checked.
        shard = read_json(f"bds/listings/{key}.json")
        if shard is None:
            missing.append(key)
            continue
        sampled += 1
        items = shard.get("rows")
        if not isinstance(items, list) or len(items) != row.get("n"):
            malformed.append(key)
            continue
        for item in items:
            price = item.get("p")
            if not isinstance(price, (int, float)) or not (BDS_PRICE_RANGE[0] <= price <= BDS_PRICE_RANGE[1]):
                bad_price.append(key)
                break
            # Total price in billions against per-m2 in millions and size in m2.
            # These three are published independently, so a unit slip in any one
            # shows up as a mismatch here rather than as a plausible wrong number.
            total, size = item.get("tp"), item.get("sz")
            if isinstance(total, (int, float)) and isinstance(size, (int, float)) and size > 0:
                implied = total * 1000 / size
                if implied > 0 and abs(implied / price - 1) > 0.05:
                    bad_price.append(key)
                    break

    if missing:
        rep.error(f"{len(missing)} districts in the index have no shard file (first: {missing[0]})")
    if malformed:
        rep.error(f"{len(malformed)} listing shards are unreadable or disagree with the index (first: {malformed[0]})")
    if bad_price:
        rep.error(f"{len(bad_price)} shards carry an out-of-band or inconsistent price (first: {bad_price[0]})")
    if previous and isinstance(previous.get("rows"), list):
        drop = 1 - len(rows) / max(1, len(previous["rows"]))
        if drop > MAX_ROW_DROP:
            rep.error(f"listing coverage fell {drop:.1%} ({len(previous['rows'])} → {len(rows)})")

    rep.stats.update(
        districts=len(rows),
        listings=sum(row.get("n") or 0 for row in rows),
        thin_districts=sum(1 for row in rows if row.get("thin")),
        shards_checked=sampled,
    )
    return rep


def validate_crypto_profiles(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    """Guard the per-coin dossiers.

    The failure worth catching here is a supply figure that contradicts itself:
    circulating above max supply, or a fully-diluted valuation below market cap.
    Both are arithmetically impossible and both render as a confident number on
    the page rather than as an error.
    """
    rep = Report()
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        rep.error("crypto profile index is empty")
        return rep

    symbols = [row.get("s") for row in rows]
    if len(set(symbols)) != len(symbols) or any(not s for s in symbols):
        rep.error("crypto profile index has a missing or duplicate symbol")

    # Several coins share a ticker, so the symbol -> CoinGecko id mapping can
    # silently attach the wrong asset's description and market cap to a row.
    # Market cap divided by circulating supply must land near the traded price;
    # when it does not, the profile belongs to a different coin.
    board = {r["s"]: r for r in ((read_json("crypto.json") or {}).get("rows") or []) if r.get("s")}

    missing, impossible, mismatched, described = [], [], [], 0
    for row in rows:
        symbol = row.get("s")
        profile = read_json(f"crypto/{symbol}.json")
        if profile is None:
            missing.append(symbol)
            continue
        if profile.get("desc"):
            described += 1

        quote = board.get(symbol)
        mc, supply = profile.get("mc"), profile.get("supply")
        if quote and isinstance(quote.get("p"), (int, float)) and quote["p"] > 0 \
           and isinstance(mc, (int, float)) and isinstance(supply, (int, float)) and supply > 0:
            implied = mc / supply
            # A factor of two either way absorbs the timing gap between the two
            # sources; a wrong coin is normally out by orders of magnitude.
            if not (0.5 < implied / quote["p"] < 2):
                mismatched.append(symbol)
        supply, max_supply = profile.get("supply"), profile.get("max_supply")
        if isinstance(supply, (int, float)) and isinstance(max_supply, (int, float)) and max_supply > 0:
            # A 1% tolerance: CoinGecko's two figures are snapshotted at
            # slightly different moments and can disagree at the margin.
            if supply > max_supply * 1.01:
                impossible.append(symbol)
                continue
        mc, fdv = profile.get("mc"), profile.get("fdv")
        if isinstance(mc, (int, float)) and isinstance(fdv, (int, float)) and fdv > 0 and mc > fdv * 1.01:
            impossible.append(symbol)

    if missing:
        rep.error(f"{len(missing)} coins in the index have no profile file (first: {missing[0]})")
    if impossible:
        rep.error(f"{len(impossible)} profiles carry impossible supply/valuation (first: {impossible[0]})")
    if mismatched:
        rep.error(
            f"{len(mismatched)} profiles imply a price unlike the traded one "
            f"(first: {mismatched[0]}) — the symbol probably resolved to a different coin"
        )
    if described < len(rows) * 0.5:
        rep.warn(f"only {described}/{len(rows)} coin profiles carry a description")

    rep.stats.update(coins=len(rows), with_description=described)
    return rep


def validate_crypto_context(payload: dict[str, Any], previous: dict[str, Any] | None) -> Report:
    """Guard the market-context artifact.

    Each figure comes from a different API, so the check is per-metric bounds
    rather than a shape test: a source that changes unit (TH/s to EH/s, a
    percentage to a fraction) keeps returning valid JSON and starts publishing a
    number three orders of magnitude wrong.
    """
    rep = Report()
    if not isinstance(payload, dict) or len(payload) < 3:
        rep.error("crypto context is empty or missing most sections")
        return rep

    # (path, low, high) -- wide enough to survive real market moves, narrow
    # enough that a unit change cannot pass.
    bounds = [
        ("market.dom_btc", 20.0, 90.0),
        ("market.dom_eth", 2.0, 40.0),
        ("mvrv.value", 0.2, 10.0),
        ("fng.value", 0.0, 100.0),
        # TH/s, per the endpoint's own `unit` field: 880,650,223 TH/s is
        # 881 EH/s, which matches the network's published hash rate. The band
        # spans roughly 100 to 100,000 EH/s.
        ("network.hashrate", 1e8, 1e11),
        ("network.tx", 50_000.0, 2_000_000.0),
        ("tvl.value", 1e9, 1e13),
        ("market.mcap", 1e11, 1e14),
    ]
    outside = []
    for path, low, high in bounds:
        section, key = path.split(".")
        value = (payload.get(section) or {}).get(key)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or not (low <= value <= high):
            outside.append(f"{path}={value}")

    if outside:
        rep.error(
            f"{len(outside)} context metrics outside their plausible range "
            f"({', '.join(outside[:3])}) — a source unit probably changed"
        )
    if payload.get("failed"):
        rep.warn(f"context sources failed: {', '.join(payload['failed'][:4])}")

    present = [k for k in ("network", "market", "mvrv", "tvl", "fng") if payload.get(k)]
    if len(present) < 3:
        rep.error(f"only {len(present)} of 5 context sections present: {present}")
    rep.stats.update(sections=len(present), checked=len(bounds) - len(outside))
    return rep


# Decision numbers as the Vietnamese gazette writes them. Planning decisions are
# Prime-Ministerial (`1569/QĐ-TTg`). A land-price table is usually a provincial
# People's Committee decision (`79/2024/QĐ-UBND`) but some provinces issue it as
# a People's Council resolution instead -- Bình Dương's is 20/2024/NQ-HĐND -- so
# both forms are legitimate and the check accepts either rather than forcing a
# citation into the shape we expected.
_PLANNING_DECISION = re.compile(r"^\d{1,4}/QĐ-TTg$")
_LAND_PRICE_DECISION = re.compile(r"^\d{1,4}/\d{4}/(QĐ-UBND|NQ-HĐND)$")


def validate_province_profiles(
    payload: dict[str, Any], previous: dict[str, Any] | None
) -> Report:
    """Check the hand-maintained province write-ups.

    This file is edited by a person, not produced by a pipeline, which is
    exactly why it needs a validator: a mistyped decision number or a date that
    does not exist would be published as a citation and read as fact. The page
    already refuses to print a citation it does not have; this refuses to print
    one that is malformed.
    """
    rep = Report()
    provinces = payload.get("provinces") if isinstance(payload, dict) else None
    if not isinstance(provinces, dict) or not provinces:
        rep.error("province_profiles.json has no `provinces` object")
        return rep

    rep.stats["provinces"] = len(provinces)
    if len(provinces) != 34:
        rep.error(
            f"{len(provinces)} provinces described, expected the 34 of the "
            "post-2025 structure"
        )

    today = date.today()
    cited = 0
    land_cited = 0
    for name, profile in provinces.items():
        if not isinstance(profile, dict):
            rep.error(f"{name}: profile is not an object")
            continue

        planning = profile.get("planning")
        if planning is not None:
            if not isinstance(planning, dict):
                rep.error(f"{name}: `planning` is not an object")
            else:
                cited += 1
                decision = planning.get("decision")
                if not isinstance(decision, str) or not _PLANNING_DECISION.match(decision):
                    rep.error(f"{name}: planning decision {decision!r} is not a `N/QĐ-TTg` number")
                _check_citation_date(rep, name, "planning", planning.get("date"), today)
                if not planning.get("title"):
                    rep.error(f"{name}: planning citation has no title")
                if not planning.get("source"):
                    rep.error(f"{name}: planning citation names no source")

        land = profile.get("land_price_table")
        if land is not None:
            if not isinstance(land, dict):
                rep.error(f"{name}: `land_price_table` is not an object")
            else:
                land_cited += 1
                decision = land.get("decision")
                if not isinstance(decision, str) or not _LAND_PRICE_DECISION.match(decision):
                    rep.error(
                        f"{name}: land-price decision {decision!r} is not a "
                        "`N/YYYY/QĐ-UBND` or `N/YYYY/NQ-HĐND` number"
                    )
                _check_citation_date(rep, name, "land_price_table", land.get("date"), today)
                if not land.get("issuer"):
                    rep.error(f"{name}: land-price citation names no issuing committee")
                start, end = land.get("effective_from"), land.get("effective_to")
                if start and end and str(start) > str(end):
                    rep.error(f"{name}: land-price effective_from {start} is after {end}")

                # The check that would have caught the real failure: three
                # citations sat on the page nine months after they expired,
                # because nothing compared their end date to the calendar. A
                # land-price table is superseded annually, and now sometimes
                # mid-year, so an expired one is not merely stale -- it states
                # the wrong prices are in force.
                if end and str(end) < today.isoformat():
                    rep.error(
                        f"{name}: land-price citation {decision} expired on {end}; "
                        "a superseding document exists and must be cited instead"
                    )

                # How far the citation was actually checked. Publishing a
                # gazette-verified number and a legal-database number in the
                # same shape claims more confidence than we have for one of them.
                verified = land.get("verified")
                if verified not in {"gazette", "secondary"}:
                    rep.error(
                        f"{name}: land-price `verified` is {verified!r}, expected "
                        "\"gazette\" (primary source read) or \"secondary\""
                    )
                elif verified == "gazette" and not land.get("url"):
                    rep.error(
                        f"{name}: claims gazette verification but cites no URL"
                    )

    rep.stats["with_planning"] = cited
    rep.stats["with_land_price"] = land_cited
    if not cited:
        rep.error("no province cites a planning decision at all")
    return rep


def _check_citation_date(
    rep: Report, name: str, field: str, value: Any, today: date
) -> None:
    """A citation date must be a real ISO date, and cannot be in the future."""
    if not value:
        rep.error(f"{name}: {field} citation has no date")
        return
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        rep.error(f"{name}: {field} date {value!r} is not an ISO date")
        return
    if parsed > today:
        rep.error(f"{name}: {field} is dated {value}, which is in the future")


# A state-price ratio needs at least this many matched streets behind it.
LAND_PRICE_MIN_STREETS = 8
# State prices are a fee base and sit below market, but not arbitrarily so.
# Outside this band a street was mis-joined or a unit moved.
LAND_PRICE_RATIO_RANGE = (0.5, 100.0)


def validate_land_price(
    payload: dict[str, Any], previous: dict[str, Any] | None
) -> Report:
    """Check the asking-vs-state comparison.

    The failure this guards against is a mis-joined street. Vietnamese street
    names repeat across districts -- 202 of HCMC's 3,098 do -- so pairing "Lê
    Lợi" in Gò Vấp with "Lê Lợi" in District 1 produces a 400x ratio that reads
    as a spectacular market signal rather than as the bug it is.
    """
    rep = Report()
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        rep.error("land_price.json has no rows")
        return rep

    rep.stats["districts"] = len(rows)
    rep.stats["matched_streets"] = sum(r.get("n") or 0 for r in rows)
    rep.stats["provinces"] = len({r.get("rs") for r in rows})

    thin = [r for r in rows if (r.get("n") or 0) < LAND_PRICE_MIN_STREETS]
    if thin:
        rep.error(
            f"{len(thin)} districts rest on fewer than {LAND_PRICE_MIN_STREETS} "
            f"matched streets (first: {thin[0].get('d')})"
        )

    lo, hi = LAND_PRICE_RATIO_RANGE
    outside = [r for r in rows if not lo <= (r.get("rr") or 0) <= hi]
    if outside:
        rep.error(
            f"{len(outside)} ratios fall outside {lo}-{hi}x "
            f"(e.g. {outside[0].get('rr')} in {outside[0].get('d')}) — "
            "a street was probably matched across districts"
        )

    undocumented = [r for r in rows if not r.get("doc")]
    if undocumented:
        rep.error(
            f"{len(undocumented)} districts publish a ratio without naming the "
            "land-price decision it rests on"
        )

    # The mirror lags the gazette. That is tolerable, but it has to be visible:
    # a ratio computed against a superseded table is answering last year's
    # question, and the page can only say so if this check surfaces the gap.
    profiles = read_json("province_profiles.json")
    if isinstance(profiles, dict):
        current = {
            name: (prof.get("land_price_table") or {}).get("decision")
            for name, prof in (profiles.get("provinces") or {}).items()
            if isinstance(prof, dict)
        }
        stale: list[str] = []
        for row in rows:
            slug = _province_slug(row.get("rs"), profiles)
            expected = current.get(slug) if slug else None
            if expected and row.get("doc") and row["doc"] != expected:
                stale.append(f"{row.get('d')}: {row['doc']} vs {expected}")
        if stale:
            rep.warn(
                f"{len(stale)} districts price against a document that is not the "
                f"one cited for their province (e.g. {stale[0]}) — the mirror is "
                "behind the gazette"
            )
            rep.stats["stale_doc_districts"] = len(stale)

    return rep


def _province_slug(name: str | None, profiles: dict[str, Any]) -> str | None:
    """Map a published province name back to its profile slug."""
    if not name:
        return None
    for slug, prof in (profiles.get("provinces") or {}).items():
        if not isinstance(prof, dict):
            continue
        if slug == name or prof.get("name") == name:
            return slug
    # Profiles are keyed by slug and the published `rs` is a display name, so
    # fold it the same way the publisher does -- a naive space-to-dash misses
    # every province with a diacritic, which is nearly all of them.
    from pipeline.transform.bds_aggregate import slugify

    folded = slugify(name)
    return folded if folded in (profiles.get("provinces") or {}) else None


VALIDATORS = {
    "stocks.json": validate_stocks,
    "crypto/index.json": validate_crypto_profiles,
    "crypto/context.json": validate_crypto_context,
    "bds.json": validate_bds,
    "bds/listings/index.json": validate_bds_listings,
    "province_profiles.json": validate_province_profiles,
    "land_price.json": validate_land_price,
    "us.json": validate_us,
    "fx.json": validate_fx,
    "crypto.json": validate_crypto,
    "breadth.json": validate_breadth,
    "flows.json": validate_flows,
    "signals.json": validate_signals,
    "news_ticker.json": validate_news,
    "gex_btc.json": validate_gex,
    "gex_eth.json": validate_gex,
    "ticker/manifest.json": validate_ticker_manifest,
}

# Validators in this set take the raw payload (dict) rather than the unwrapped
# `rows` list -- their artifacts have no single dominant array (flows keys by
# asset, gex is a flat scalar surface, signals mixes rules/stats/trades).
_TAKES_PAYLOAD = {
    "flows.json", "signals.json", "news_ticker.json", "gex_btc.json",
    "gex_eth.json", "ticker/manifest.json", "breadth.json",
    "bds/listings/index.json",
    "province_profiles.json",
    "land_price.json",
    "crypto/index.json",
    "crypto/context.json",
}


def run(names: list[str] | None = None) -> int:
    targets = names or list(VALIDATORS)
    failed = False

    for name in targets:
        validator = VALIDATORS.get(name)
        if not validator:
            print(f"skip {name}: no validator registered")
            continue

        payload = read_json(name)
        if payload is None:
            # A missing artifact is a failed build, not a reason to wave the
            # deploy through -- silently passing here is exactly the gap that
            # let stale or absent files reach the site with no warning.
            print(f"{name}: FAIL — not built (no such file in data/)")
            failed = True
            continue

        prev_payload = read_prev_json(name)

        if name in _TAKES_PAYLOAD:
            rep = validator(payload, prev_payload)
        else:
            # Artifacts carry provenance around their rows; older builds were a
            # bare array.
            rows = payload if isinstance(payload, list) else payload.get("rows")
            if rows is None:
                print(f"{name}: FAIL — no `rows` key")
                failed = True
                continue
            prev_rows = None
            if isinstance(prev_payload, list):
                prev_rows = prev_payload
            elif isinstance(prev_payload, dict):
                prev_rows = prev_payload.get("rows")
            rep = validator(rows, prev_rows)

        expected_gex_sym = {"gex_btc.json": "btc", "gex_eth.json": "eth"}.get(name)
        if expected_gex_sym and isinstance(payload, dict):
            actual_gex_sym = str(payload.get("sym") or "").lower()
            if actual_gex_sym != expected_gex_sym:
                rep.error(
                    f"artifact name expects `{expected_gex_sym}` but payload says "
                    f"`{actual_gex_sym or '?'}`"
                )

        status = "ok" if rep.ok else "FAIL"
        print(f"\n{name}: {status}")
        for key, value in rep.stats.items():
            print(f"  {key}: {value}")
        for msg in rep.warnings:
            print(f"  warning: {msg}")
        for msg in rep.errors:
            print(f"  error: {msg}")
        failed = failed or not rep.ok

    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate published JSON before deploy")
    parser.add_argument("names", nargs="*", help="Files to check (default: all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(run(args.names or None))


if __name__ == "__main__":
    main()
