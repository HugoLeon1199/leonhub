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
        if any(not isinstance(point, list) or len(point) != 2 for point in history):
            malformed.append(row.get("s"))
            continue
        dates = [point[0] for point in history]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            malformed.append(row.get("s"))
        price = row.get("p")
        if not isinstance(price, (int, float)) or price <= 0:
            malformed.append(row.get("s"))
        elif history[-1][1] and abs(price / history[-1][1] - 1) > 0.10:
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


VALIDATORS = {
    "stocks.json": validate_stocks,
    "bds.json": validate_bds,
    "us.json": validate_us,
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
_TAKES_PAYLOAD = {"flows.json", "signals.json", "news_ticker.json", "gex_btc.json", "gex_eth.json", "ticker/manifest.json"}


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
