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
from typing import Any

from pipeline.publish.emit import read_json

log = logging.getLogger(__name__)

# A drop this large between runs means a source broke, not that the market moved.
MAX_ROW_DROP = 0.05
# VN asking prices, million VND per m2. Outside this band the unit changed.
BDS_PRICE_RANGE = (1.0, 3000.0)
# A published district cell must rest on at least this many listings.
BDS_MIN_SAMPLES = 20
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
    if not rows:
        rep.error("stocks.json is empty")
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
        if bad > 0.02:
            rep.error(
                f"{bad:.1%} of `{key}` values fall outside {plo}–{phi} — "
                "a fraction is probably being published as a percent, or vice versa"
            )

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
    if not rows:
        rep.error("bds.json is empty")
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


VALIDATORS = {
    "stocks.json": validate_stocks,
    "bds.json": validate_bds,
}


def run(names: list[str] | None = None) -> int:
    targets = names or list(VALIDATORS)
    failed = False

    for name in targets:
        validator = VALIDATORS.get(name)
        if not validator:
            print(f"skip {name}: no validator registered")
            continue

        rows = read_json(name)
        if rows is None:
            print(f"skip {name}: not built yet")
            continue

        rep = validator(rows, None)
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
