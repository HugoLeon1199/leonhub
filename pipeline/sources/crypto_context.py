"""Market-wide crypto context: the measurable half of a daily read.

A coin dossier says what an asset is. This says what the market around it is
doing — network activity, valuation against realised cost, liquidity, and
concentration — so a price move can be read against something other than
another price.

Everything here is a measured figure with a named source and a timestamp. There
is deliberately no written narrative: a paragraph generated from headlines reads
as authoritative and cannot be checked, which is the failure `CLAUDE.md` calls
out ("state the rule, not just the verdict"). The page derives its labels from
stated thresholds instead, and shows the threshold beside the label.

Sources, all free and unauthenticated:
  - blockchain.info    hash rate, transactions, unique addresses, difficulty
  - bitcoin-data.com   MVRV (market value against realised value)
  - CoinGecko          total market cap, dominance, 24h change
  - DefiLlama          total value locked across chains
  - alternative.me     Fear & Greed
"""

from __future__ import annotations

import argparse
import logging
import uuid
from typing import Any

from pipeline.core.http import HttpClient
from pipeline.publish.emit import write_json

log = logging.getLogger(__name__)

CHART = "https://api.blockchain.info/charts/{name}"
MVRV_URL = "https://bitcoin-data.com/v1/mvrv/last"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
TVL_URL = "https://api.llama.fi/v2/historicalChainTvl"
FNG_URL = "https://api.alternative.me/fng/"


def _series(client: HttpClient, name: str, days: int = 30) -> list[dict[str, float]]:
    payload = client.get_json(
        CHART.format(name=name),
        params={"timespan": f"{days}days", "format": "json", "cors": "true"},
    )
    values = payload.get("values") if isinstance(payload, dict) else None
    return values if isinstance(values, list) else []


def _latest_and_change(values: list[dict[str, float]], back: int = 7) -> tuple[float | None, float | None]:
    """Current reading plus its percentage change `back` points ago.

    A level alone rarely means anything -- hash rate at 880 EH/s is only
    informative against where it was a week ago.
    """
    if not values:
        return None, None
    latest = values[-1].get("y")
    if not isinstance(latest, (int, float)):
        return None, None
    if len(values) <= back:
        return float(latest), None
    prior = values[-1 - back].get("y")
    if not isinstance(prior, (int, float)) or prior == 0:
        return float(latest), None
    return float(latest), round((latest / prior - 1) * 100, 2)


def collect(dry_run: bool = False, delay: float = 1.5) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    client = HttpClient(delay=delay, retries=2, timeout=25)
    out: dict[str, Any] = {"run_id": run_id}
    failed: list[str] = []

    def attempt(label: str, fn):
        # One dead source must not empty the whole artifact: a partial context
        # is still worth publishing, and the missing key says so by its absence.
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: %s", label, exc)
            failed.append(label)
            return None

    # --- Bitcoin network ---
    network: dict[str, Any] = {}
    for key, chart in (("hashrate", "hash-rate"), ("tx", "n-transactions"),
                       ("addresses", "n-unique-addresses"), ("difficulty", "difficulty")):
        values = attempt(chart, lambda c=chart: _series(client, c))
        if values:
            level, change = _latest_and_change(values)
            if level is not None:
                network[key] = level
                if change is not None:
                    network[f"{key}_7d"] = change
    if network:
        out["network"] = network

    # --- Valuation against realised cost ---
    mvrv = attempt("mvrv", lambda: client.get_json(MVRV_URL))
    if isinstance(mvrv, dict) and isinstance(mvrv.get("mvrv"), (int, float)):
        out["mvrv"] = {"value": round(float(mvrv["mvrv"]), 3), "as_of": mvrv.get("d")}

    # --- Market size and concentration ---
    glob = attempt("coingecko global", lambda: client.get_json(GLOBAL_URL))
    data = (glob or {}).get("data") if isinstance(glob, dict) else None
    if isinstance(data, dict):
        caps = data.get("total_market_cap") or {}
        dom = data.get("market_cap_percentage") or {}
        market: dict[str, Any] = {}
        if isinstance(caps.get("usd"), (int, float)):
            market["mcap"] = round(float(caps["usd"]))
        if isinstance(data.get("market_cap_change_percentage_24h_usd"), (int, float)):
            market["mcap_24h"] = round(float(data["market_cap_change_percentage_24h_usd"]), 2)
        for coin in ("btc", "eth"):
            if isinstance(dom.get(coin), (int, float)):
                market[f"dom_{coin}"] = round(float(dom[coin]), 2)
        if isinstance(data.get("active_cryptocurrencies"), int):
            market["coins"] = data["active_cryptocurrencies"]
        if market:
            out["market"] = market

    # --- On-chain liquidity outside exchanges ---
    tvl = attempt("defillama tvl", lambda: client.get_json(TVL_URL))
    if isinstance(tvl, list) and tvl:
        latest = tvl[-1]
        if isinstance(latest.get("tvl"), (int, float)):
            entry: dict[str, Any] = {"value": round(float(latest["tvl"]))}
            # 7 daily points back, when the series is long enough to have them.
            if len(tvl) > 7 and isinstance(tvl[-8].get("tvl"), (int, float)) and tvl[-8]["tvl"]:
                entry["chg_7d"] = round((latest["tvl"] / tvl[-8]["tvl"] - 1) * 100, 2)
            out["tvl"] = entry

    # --- Sentiment ---
    fng = attempt("fear & greed", lambda: client.get_json(FNG_URL, params={"limit": 8}))
    rows = (fng or {}).get("data") if isinstance(fng, dict) else None
    if isinstance(rows, list) and rows:
        try:
            values = [int(r["value"]) for r in rows if str(r.get("value", "")).isdigit()]
            out["fng"] = {
                "value": values[0],
                "label": rows[0].get("value_classification"),
                # The seven-day average separates a spike from a standing mood.
                "avg_7d": round(sum(values[:7]) / len(values[:7])) if values else None,
            }
        except (KeyError, ValueError, ZeroDivisionError):
            failed.append("fear & greed parse")

    out["sources"] = ["blockchain.info", "bitcoin-data.com", "coingecko", "defillama", "alternative.me"]
    out["failed"] = failed

    if not dry_run and len(out) > 3:
        write_json("crypto/context.json", {k: v for k, v in out.items() if k != "run_id"})
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Build data/crypto/context.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()
    import json
    print(json.dumps(collect(dry_run=args.dry_run, delay=args.delay), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
