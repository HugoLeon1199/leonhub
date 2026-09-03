"""Gamma exposure from the Deribit option chain.

Dealers who sell options hedge the gamma they take on, and that hedging flow is
mechanical: in positive net gamma they buy dips and sell rallies, damping
moves; in negative gamma they do the opposite, amplifying them. So the sign and
location of aggregate gamma says something about how price is likely to behave
that price history alone cannot.

The whole surface comes from one 435 KB request — the chain carries open
interest, mark IV, strike and expiry per contract, which is everything the
Black-Scholes gamma needs.

Two conventions decide whether the output is meaningful:

- **Dealer sign.** The standard assumption is that dealers are long calls and
  short puts against customer flow, so call gamma is added and put gamma
  subtracted. Getting this backwards inverts every regime call the page makes.
- **Deribit quotes in BTC, settles in USD.** `open_interest` is in contracts of
  1 BTC, and gamma per 1% move is what desks actually quote, so the conversion
  is `gamma x OI x spot^2 x 0.01`. Skipping the spot-squared term produces a
  number that looks plausible and is off by five orders of magnitude.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pipeline.core.http import HttpClient
from pipeline.core import warehouse as wh

log = logging.getLogger(__name__)

API = "https://www.deribit.com/api/v2/public"
# Contracts this far out contribute almost nothing to near-term hedging flow but
# add a long tail of noise to the profile.
MAX_DAYS = 400
# Strikes beyond this distance from spot cannot realistically be hedged into.
STRIKE_BAND = 0.6
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

INSTRUMENT_RE = re.compile(r"^(?P<sym>[A-Z]+)-(?P<exp>\d{1,2}[A-Z]{3}\d{2})-(?P<strike>[\d.]+)-(?P<kind>[CP])$")


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_greeks(spot: float, strike: float, t: float, iv: float) -> tuple[float, float, float]:
    """Black-Scholes gamma, vanna and charm for one contract.

    Rates are taken as zero: Deribit's options are inverse and crypto carry is
    already inside the forward the market quotes, so adding a rate term would
    double-count it.
    """
    if spot <= 0 or strike <= 0 or t <= 0 or iv <= 0:
        return 0.0, 0.0, 0.0

    vol_t = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t) / vol_t
    d2 = d1 - vol_t
    pdf = norm_pdf(d1)

    gamma = pdf / (spot * vol_t)
    # Vanna = d(delta)/d(vol); charm = d(delta)/d(time), converted to per-day.
    # With r = q = 0 the charm numerator collapses to d2, and an earlier
    # expansion of it here carried a stray factor of three.
    vanna = -pdf * d2 / iv
    charm = pdf * d2 / (2 * t) / 365.0
    return gamma, vanna, charm


def parse_instrument(name: str) -> tuple[float, str, datetime] | None:
    m = INSTRUMENT_RE.match(name)
    if not m:
        return None
    exp = m.group("exp")
    try:
        day = int(exp[:-5])
        month = MONTHS[exp[-5:-2]]
        year = 2000 + int(exp[-2:])
        # Deribit expiries settle at 08:00 UTC.
        expiry = datetime(year, month, day, 8, tzinfo=timezone.utc)
    except (ValueError, KeyError):
        return None
    return float(m.group("strike")), m.group("kind"), expiry


def compute(chain: list[dict[str, Any]], spot: float, now: datetime) -> dict[str, Any]:
    """Aggregate the chain into the gamma surface."""
    by_strike: dict[float, dict[str, float]] = {}
    call_gex = put_gex = 0.0
    gex_0dte = 0.0
    vanna_total = charm_total = 0.0
    call_oi = put_oi = 0.0
    atm_iv_candidates: list[tuple[float, float]] = []
    nearest_expiry: datetime | None = None
    kept: list[dict[str, Any]] = []

    for row in chain:
        parsed = parse_instrument(row.get("instrument_name", ""))
        if not parsed:
            continue
        strike, kind, expiry = parsed

        oi = row.get("open_interest") or 0.0
        iv = (row.get("mark_iv") or 0.0) / 100.0     # quoted in percent
        if oi <= 0 or iv <= 0:
            continue

        years = (expiry - now).total_seconds() / (365.25 * 24 * 3600)
        if years <= 0 or years * 365.25 > MAX_DAYS:
            continue
        if abs(strike / spot - 1) > STRIKE_BAND:
            continue

        kept.append({"strike": strike, "kind": kind, "oi": oi, "iv": iv, "expiry": expiry})

        gamma, vanna, charm = bs_greeks(spot, strike, years, iv)
        # Gamma notional per 1% move, in USD.
        notional = gamma * oi * spot * spot * 0.01

        if kind == "C":
            call_gex += notional
            call_oi += oi
        else:
            put_gex += notional
            put_oi += oi

        # Dealers long calls, short puts: puts enter with the opposite sign.
        signed = notional if kind == "C" else -notional
        bucket = by_strike.setdefault(strike, {"gex": 0.0, "call_oi": 0.0, "put_oi": 0.0})
        bucket["gex"] += signed
        bucket["call_oi" if kind == "C" else "put_oi"] += oi

        vanna_total += vanna * oi * spot * (1 if kind == "C" else -1)
        charm_total += charm * oi * spot * (1 if kind == "C" else -1)

        days = (expiry - now).total_seconds() / 86400
        if days <= 1:
            gex_0dte += signed
        if nearest_expiry is None or expiry < nearest_expiry:
            nearest_expiry = expiry
        atm_iv_candidates.append((abs(strike - spot), iv))

    net_gex = call_gex - put_gex
    atm_iv = min(atm_iv_candidates)[1] if atm_iv_candidates else 0.0

    return {
        "spot": round(spot, 2),
        "net_gex": round(net_gex, 1),
        "net_gex_0dte": round(gex_0dte, 1),
        "call_gex": round(call_gex, 1),
        "put_gex": round(put_gex, 1),
        "pc_ratio": round(put_oi / call_oi, 3) if call_oi else None,
        "vanna": round(vanna_total, 1),
        "charm": round(charm_total, 1),
        "atm_iv": round(atm_iv, 4),
        "regime": "positive" if net_gex >= 0 else "negative",
        "gamma_flip": gamma_flip(kept, spot, now),
        "max_pain": max_pain(by_strike),
        "nearest_expiry": nearest_expiry.date().isoformat() if nearest_expiry else None,
        "profile": [
            {"strike": k, "gex": round(v["gex"], 1)}
            for k, v in sorted(by_strike.items())
        ],
        "levels": top_levels(by_strike, spot),
    }


def gamma_flip(contracts: list[dict[str, Any]], spot: float, now: datetime) -> float | None:
    """The spot price at which total dealer gamma would flip sign.

    Not a cumulative sum over strikes: gamma is a function of where spot is, so
    the flip has to be found by revaluing the whole book at candidate prices and
    locating the crossing. A cumulative walk answers a different question
    (which strike tips the running total) and lands well away from the real
    level — it put the flip above spot on a book that is long gamma at spot,
    which is self-contradictory.
    """
    def total_gamma_at(price: float) -> float:
        total = 0.0
        for c in contracts:
            years = (c["expiry"] - now).total_seconds() / (365.25 * 24 * 3600)
            if years <= 0:
                continue
            gamma, _, _ = bs_greeks(price, c["strike"], years, c["iv"])
            notional = gamma * c["oi"] * price * price * 0.01
            total += notional if c["kind"] == "C" else -notional
        return total

    lo, hi = spot * 0.5, spot * 1.5
    g_lo, g_hi = total_gamma_at(lo), total_gamma_at(hi)
    if (g_lo < 0) == (g_hi < 0):
        return None  # no crossing inside a plausible band

    # Bisection: 40 evaluations of the book is cheap and lands within a dollar.
    for _ in range(40):
        mid = (lo + hi) / 2
        if (total_gamma_at(mid) < 0) == (g_lo < 0):
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)


def max_pain(by_strike: dict[float, dict[str, float]]) -> float | None:
    """Strike where the most open interest expires worthless.

    Computed on total pay-out across all strikes, which is the conventional
    definition, rather than on gamma.
    """
    strikes = sorted(by_strike)
    if not strikes:
        return None
    best, best_pain = None, None
    for candidate in strikes:
        pain = 0.0
        for strike in strikes:
            bucket = by_strike[strike]
            if candidate > strike:
                pain += (candidate - strike) * bucket["call_oi"]
            elif candidate < strike:
                pain += (strike - candidate) * bucket["put_oi"]
        if best_pain is None or pain < best_pain:
            best, best_pain = candidate, pain
    return best


def top_levels(by_strike: dict[float, dict[str, float]], spot: float, count: int = 12) -> list[dict[str, Any]]:
    """The strikes carrying the most gamma — where hedging flow concentrates."""
    ranked = sorted(by_strike.items(), key=lambda kv: -abs(kv[1]["gex"]))[:count]
    out = []
    for strike, bucket in ranked:
        out.append({
            "strike": strike,
            "gex": round(bucket["gex"], 1),
            "side": "call" if bucket["gex"] >= 0 else "put",
            "rel": "above" if strike > spot else "below",
            "dist": round((strike / spot - 1) * 100, 2),
            "call_oi": round(bucket["call_oi"], 1),
            "put_oi": round(bucket["put_oi"], 1),
        })
    return sorted(out, key=lambda r: r["strike"])


def collect(symbol: str = "BTC", dry_run: bool = False) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = wh.utcnow()
    client = HttpClient(delay=1.0)

    chain = client.get_json(
        f"{API}/get_book_summary_by_currency",
        params={"currency": symbol, "kind": "option"},
    )["result"]
    index = client.get_json(
        f"{API}/get_index_price", params={"index_name": f"{symbol.lower()}_usd"}
    )["result"]["index_price"]

    surface = compute(chain, float(index), started)
    surface["sym"] = symbol
    surface["contracts"] = len(chain)
    surface["updated_at"] = started.isoformat()

    if dry_run:
        return surface

    rows = [
        {
            "series": f"gex.{symbol.lower()}.{key}",
            "as_of": started,
            "value": float(surface[key]),
            "source": "deribit",
            "fetched_at": started,
            "meta": None,
        }
        for key in ("net_gex", "net_gex_0dte", "call_gex", "put_gex",
                    "gamma_flip", "max_pain", "vanna", "charm", "atm_iv", "spot")
        if isinstance(surface.get(key), (int, float))
    ]

    con = wh.connect()
    try:
        wh.append(con, "metric_ts", rows)
        wh.log_run(
            con, run_id, f"deribit_gex:{symbol}", started, "ok",
            rows_in=len(rows), rows_new=len(rows),
            detail={k: v for k, v in surface.items() if not isinstance(v, list)},
        )
    finally:
        con.close()

    from pipeline.publish.emit import write_json
    path = write_json(f"gex_{symbol.lower()}.json", surface)
    surface["path"] = str(path)
    return surface


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute gamma exposure from Deribit")
    parser.add_argument("--symbol", default="BTC", help="BTC or ETH")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = collect(args.symbol.upper(), args.dry_run)
    summary = {k: v for k, v in out.items() if not isinstance(v, list)}
    summary["profile_points"] = len(out.get("profile", []))
    summary["levels"] = len(out.get("levels", []))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
