"""Per-coin dossiers: what each asset actually is, not just what it costs.

`crypto_board.py` answers "what is moving". It cannot answer "what am I looking
at" -- a reader who does not already know what SUI or RENDER is gets a ticker
and a percentage. The equity side solved this with `data/ticker/*.json`; this is
its counterpart, and it follows the same shape: one small file per asset,
fetched lazily when a coin is opened.

CoinGecko is the source because it is the only free one that carries a written
description alongside supply and valuation. Two constraints shape the collector:

- **It rate-limits hard and answers 429 rather than throttling.** Six calls
  back to back returned six 429s, and the block persisted at six seconds apart;
  the window reset after roughly seventy seconds. That is why this runs
  pipeline-side on a slow loop and publishes static files, rather than letting
  each reader's browser call the API and burn a shared quota.
- **Symbols are not unique.** Several coins share a ticker, so the mapping from
  Binance's `BTC` to CoinGecko's `bitcoin` is resolved through the market-cap
  ranked list and the highest-ranked match wins. A symbol that resolves to
  nothing is skipped rather than guessed at.
"""

from __future__ import annotations

import argparse
import logging
import uuid
from typing import Any

from pipeline.core.http import HttpClient
from pipeline.publish.emit import read_json, write_json

log = logging.getLogger(__name__)

MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
COIN_URL = "https://api.coingecko.com/api/v3/coins/{id}"

# Measured: 6 rapid calls all 429, still refused at 6s apart, recovered after
# ~70s. 25s is comfortably inside that and keeps a full pass under an hour.
DELAY = 25.0

# The board publishes ~196 symbols. Profiles are for the ones a reader is
# plausibly looking up, and each costs 25 seconds, so the pass is capped.
DEFAULT_LIMIT = 120

# Description text is the bulkiest field and the browser only renders an
# excerpt. Truncated on a sentence boundary so the stored text never ends
# mid-word.
MAX_DESC = 1200


def _trim(text: str, limit: int = MAX_DESC) -> str | None:
    text = " ".join((text or "").split())
    if not text:
        return None
    # CoinGecko embeds anchor markup in the description; strip it rather than
    # publish tags into a page that renders as text.
    import re
    text = re.sub(r"<[^>]+>", "", text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: stop + 1] if stop > limit * 0.5 else cut.rstrip()) + " …"


def symbol_map(client: HttpClient, pages: int = 6) -> dict[str, str]:
    """Binance-style symbol -> CoinGecko id, highest market cap wins.

    Walking the ranked list in order means the first time a symbol is seen it
    is on the largest coin carrying it, so later duplicates are discarded.

    Six pages (1,500 coins) rather than three: the board lists names ranked in
    the 800s, and a shallower sweep left them unresolved. Depth alone does not
    make a match correct -- `TON` at rank 829 is Tokamak Network, not Toncoin --
    so the price cross-check in `collect` is what decides whether a resolved id
    is actually the traded asset.
    """
    mapping: dict[str, str] = {}
    for page in range(1, pages + 1):
        # The markets endpoint is rate-limited like every other CoinGecko route:
        # six pages fetched back to back exhausted the window and failed the
        # whole run before a single profile was written. Pace it like the
        # per-coin calls below.
        if page > 1:
            import time
            time.sleep(DELAY)
        try:
            rows = client.get_json(
                MARKETS_URL,
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                    "sparkline": "false",
                },
            )
        except Exception as exc:  # noqa: BLE001
            # A refused page costs coverage, not the run: whatever was mapped
            # before it is still usable, and the next pass fills the rest.
            log.warning("symbol map page %d failed: %s", page, exc)
            break
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            coin_id = row.get("id")
            if symbol and coin_id and symbol not in mapping:
                mapping[symbol] = coin_id
    return mapping


# CoinGecko keeps delisted and migrated tokens under their old symbol, so a
# ticker that a exchange has since reassigned resolves to the dead asset: GAL
# returned "GAL (migrated to Gravity - G)" at $0.33 against a traded $2.54.
# The name is where the migration is announced, so that is what is checked.
_DEAD_MARKERS = ("migrated to", "deprecated", "(old)", "[old]", "delisted", "sunset")


def to_profile(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    market = payload.get("market_data") or {}

    def usd(key: str) -> float | None:
        value = (market.get(key) or {}).get("usd")
        return float(value) if isinstance(value, (int, float)) else None

    def num(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    name = payload.get("name")
    if not name:
        return None
    lowered = name.lower()
    if any(marker in lowered for marker in _DEAD_MARKERS):
        log.info("%s: skipping %r -- superseded token", symbol, name)
        return None

    return {
        "s": symbol,
        "n": name,
        "id": payload.get("id"),
        "rank": payload.get("market_cap_rank"),
        "desc": _trim((payload.get("description") or {}).get("en", "")),
        # Categories are how a reader places an unfamiliar coin: "Layer 1" or
        # "DeFi" says more in two words than a paragraph.
        "cat": [c for c in (payload.get("categories") or []) if c][:5],
        "home": ((payload.get("links") or {}).get("homepage") or [None])[0] or None,
        "mc": usd("market_cap"),
        "fdv": usd("fully_diluted_valuation"),
        "vol": usd("total_volume"),
        # Circulating against max supply is the dilution question, and it is the
        # one number a price chart cannot show.
        "supply": num(market.get("circulating_supply")),
        "max_supply": num(market.get("max_supply")),
        "total_supply": num(market.get("total_supply")),
        "ath": usd("ath"),
        "ath_pct": num((market.get("ath_change_percentage") or {}).get("usd")),
        "ath_date": ((market.get("ath_date") or {}).get("usd") or "")[:10] or None,
        "atl": usd("atl"),
        "atl_date": ((market.get("atl_date") or {}).get("usd") or "")[:10] or None,
        "d7": num(market.get("price_change_percentage_7d")),
        "d30": num(market.get("price_change_percentage_30d")),
        "d1y": num(market.get("price_change_percentage_1y")),
        "genesis": payload.get("genesis_date"),
        "src": "coingecko",
    }


def collect(
    symbols: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = False,
    delay: float = DELAY,
    skip_existing: bool = False,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    client = HttpClient(delay=delay, retries=2, timeout=25)

    board = read_json("crypto.json") or {}
    board_prices = {
        r["s"]: r["p"] for r in (board.get("rows") or [])
        if r.get("s") and isinstance(r.get("p"), (int, float)) and r["p"] > 0
    }
    if not symbols:
        rows = board.get("rows") or []
        # The board is already ordered by turnover, so taking the head means the
        # coins anyone is likely to open are the ones that get a profile.
        symbols = [r["s"] for r in rows if r.get("s")][:limit]

    log.info("resolving %d symbols against the CoinGecko id list", len(symbols))
    mapping = symbol_map(client)

    written, skipped, missing, failed, mismatched = 0, 0, [], [], []
    index: list[dict[str, Any]] = []

    for symbol in symbols:
        coin_id = mapping.get(symbol.upper())
        if not coin_id:
            missing.append(symbol)
            continue
        existing = read_json(f"crypto/{symbol}.json") if skip_existing else None
        if existing:
            # Skipping the fetch must not skip the index entry, or a resumed run
            # publishes an index naming only the coins it happened to refresh
            # and the UI reports every earlier profile as missing.
            skipped += 1
            index.append({"s": existing.get("s", symbol), "n": existing.get("n"),
                          "rank": existing.get("rank")})
            continue
        try:
            payload = client.get_json(
                COIN_URL.format(id=coin_id),
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "false",
                    "developer_data": "false",
                    "sparkline": "false",
                },
            )
        except Exception as exc:  # noqa: BLE001 - one bad coin must not end the pass
            log.warning("%s (%s): %s", symbol, coin_id, exc)
            failed.append(symbol)
            continue

        profile = to_profile(payload, symbol.upper())
        if not profile:
            failed.append(symbol)
            continue

        # Last line of defence against a wrong-but-plausible match. Market cap
        # over circulating supply must land near the traded price; a different
        # coin wearing the same ticker is normally out by a large factor. Catch
        # it here rather than publishing a file the validator will reject.
        quoted = board_prices.get(symbol.upper())
        if quoted and profile.get("mc") and profile.get("supply"):
            implied = profile["mc"] / profile["supply"]
            if not (0.5 < implied / quoted < 2):
                log.warning(
                    "%s: implied %.6f vs traded %.6f -- symbol resolved to a different coin",
                    symbol, implied, quoted,
                )
                mismatched.append(symbol)
                continue

        index.append({"s": profile["s"], "n": profile["n"], "rank": profile["rank"]})
        if not dry_run:
            write_json(f"crypto/{symbol.upper()}.json", profile)
            written += 1

    if not dry_run and index:
        write_json("crypto/index.json", {
            "rows": sorted(index, key=lambda r: (r["rank"] is None, r["rank"] or 0)),
            "count": len(index),
            "source": "coingecko",
        })

    return {
        "run_id": run_id,
        "targets": len(symbols),
        "written": written,
        "skipped": skipped,
        "unresolved": missing[:10],
        "unresolved_count": len(missing),
        "failed": failed[:10],
        "mismatched": mismatched,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Build data/crypto/*.json coin dossiers")
    parser.add_argument("--symbols", help="Comma-separated list; defaults to the board head")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--delay", type=float, default=DELAY)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import json
    print(json.dumps(collect(
        symbols=[s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None,
        limit=args.limit,
        dry_run=args.dry_run,
        delay=args.delay,
        skip_existing=args.skip_existing,
    ), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
