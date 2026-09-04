"""Publish the latest complete BTC/ETH gamma surfaces from the warehouse."""

from __future__ import annotations

import argparse
import json
from typing import Any

from pipeline.core import warehouse as wh
from pipeline.publish.emit import write_json


def build(symbols: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    symbols = [s.lower() for s in (symbols or ["btc", "eth"])]
    con = wh.connect_reader()
    try:
        result: dict[str, Any] = {}
        for symbol in symbols:
            row = con.execute(
                """SELECT meta FROM metric_ts
                   WHERE series = ?
                   ORDER BY as_of DESC, fetched_at DESC LIMIT 1""",
                [f"gex.{symbol}.surface"],
            ).fetchone()
            if not row or not row[0]:
                raise RuntimeError(
                    f"No complete {symbol.upper()} GEX surface in the warehouse. "
                    f"Run `python -m pipeline.sources.deribit_gex --symbol {symbol.upper()}` first."
                )
            surface = json.loads(str(row[0]))
            result[symbol] = {
                "contracts": surface.get("contracts"),
                "updated_at": surface.get("updated_at"),
                "profile_points": len(surface.get("profile") or []),
            }
            if not dry_run:
                path = write_json(f"gex_{symbol}.json", surface)
                result[symbol]["path"] = str(path)
    finally:
        con.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build GEX JSON artifacts")
    parser.add_argument("--symbols", default="btc,eth")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(json.dumps(build(symbols, args.dry_run), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
