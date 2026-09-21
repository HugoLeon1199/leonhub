"""Read-only source/freshness audit. No MT5 connection, orders or pipeline writes.

Uses only the standard library. Writes evidence.json beside this script.
Run from any directory with an existing Python >=3.10 environment.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import html
import re
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).with_name("evidence.json")
EA_ROOT = Path(r"D:\MT5\10pair_edit")
NOW = datetime.now(timezone.utc)
PATHS = [
    "hub/?tab=chart", "apps/chart/", "data/gex_btc.json",
    "data/gex_eth.json", "data/flows.json", "data/crypto/context.json",
    "data/fx.json", "data/signals.json",
]


def digest(text):
    return sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def probe(path):
    url = "https://leonquant.com/" + path
    row = {"url": url, "checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        request = Request(url, headers={"User-Agent": "LEON-Hub-research-audit/1.0"})
        with urlopen(request, timeout=25) as response:
            raw = response.read()
            row["status"] = response.status
        text = raw.decode("utf-8-sig")
        row.update(bytes=len(raw), normalized_text_sha256=digest(text))
        if path.endswith(".json"):
            data = json.loads(text)
            row["keys"] = list(data)
            row["updated_at"] = data.get("updated_at")
            if row["updated_at"]:
                stamp = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
                row["age_hours_at_check"] = round((NOW - stamp).total_seconds() / 3600, 2)
            if "gex_" in path:
                row["summary"] = {k: data.get(k) for k in (
                    "sym", "spot", "contracts", "regime", "nearest_expiry")}
            elif path.endswith("flows.json"):
                row["series"] = {k: {"observations": len(v.get("d", [])),
                    "last_observation": v.get("d", [None])[-1] if v.get("d") else None}
                    for k, v in data.get("etf", {}).items()}
            elif path.endswith("context.json"):
                row["summary"] = {"mvrv_as_of": data.get("mvrv", {}).get("as_of"),
                                  "failed": data.get("failed")}
            elif path.endswith("fx.json"):
                row["series"] = data
            local = ROOT / path
            if local.exists():
                local_data = json.loads(local.read_text(encoding="utf-8-sig"))
                row["local_updated_at"] = local_data.get("updated_at")
        else:
            row["title"] = re.search(r"<title>(.*?)</title>", text).group(1)
            local = ROOT / ("apps/chart/index.html" if path == "apps/chart/" else "hub/index.html")
            row["matches_local_normalized_text"] = digest(local.read_text(encoding="utf-8-sig")) == digest(text)
        return row
    except Exception as error:
        row["error"] = f"{type(error).__name__}: {error}"
        return row


def ea_inventory():
    rows = []
    for symbol, name in (
        ("BTCUSD", "Leon_LIVE_BTCUSD_FINAL.mq5"),
        ("ETHUSD", "Leon_EA_LIVE_ETHUSD_FINAL.mq5"),
        ("XAUUSD", "Leon_EA_LIVE_XAUUSD_FINAL.mq5"),
    ):
        path = EA_ROOT / name
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        active = [int(p) for p in re.findall(r"input bool Use_PP(\d+)\s*=\s*true;", text)]
        inputs = re.findall(r"^input[^\r\n]+", text, re.M)
        rows.append({"symbol": symbol, "path": str(path), "sha256": sha256(raw).hexdigest(),
            "active_pp_source_defaults": active,
            "source_defaults_not_verified_live": True,
            "selected_inputs": [line for line in inputs
                if "CustomBalance" in line or any(re.search(rf"_PP{p}\b", line) for p in active)],
            "external_regime_identifiers_found": re.findall(
                r"WebRequest|GlobalVariable|iADX|Choppiness|CalendarValue|Regime", text)})
    return rows


def historical_inventory():
    root = Path(r"D:\MT5\research\gold_5k_20260915\data")
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8-sig"))
    cache = {"path": str(root), "metadata_rows": sum(y["n"] for y in metadata["years"]),
        "first": metadata["years"][0]["first"], "last": metadata["years"][-1]["last"],
        "verification": "Metadata read and file existence/size checked; candles not reloaded or validated",
        "files": [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(root.glob("m1_*.pkl"))]}
    reports = []
    for symbol in ("BTCUSD", "ETHUSD", "XAUUSD"):
        path = EA_ROOT / "BACKTEST" / "CBMOI" / f"{symbol}_moi.html"
        raw = path.read_bytes()
        text = raw.decode("utf-16")
        cells = [html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
                 for s in re.findall(r"<td[^>]*>(.*?)</td>", text, re.S)]
        keys = ("Expert:", "Symbol:", "Period:", "History Quality:", "Initial Deposit:",
                "Total Net Profit:", "Profit Factor:", "Total Trades:",
                "Equity Drawdown Relative:", "Balance Drawdown Relative:")
        fields = {key: cells[cells.index(key) + 1] for key in keys if key in cells}
        deal_section = text[text.rfind("<b>Deals</b>"):]
        stamps = re.findall(r"\b20\d{2}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}\b", deal_section)
        reports.append({"path": str(path), "sha256": sha256(raw).hexdigest(), "summary_as_reported": fields,
            "deal_time_first": min(stamps) if stamps else None,
            "deal_time_last": max(stamps) if stamps else None,
            "sizing_inputs_as_reported": [c for c in cells if re.match(r"^(CustomBalance|RiskPercent_PP|riskPercent_PP)", c)],
            "verification": "Header extraction only; no rerun or attribution of report binary to current source"})
    return {"xau_m1_cache": cache, "existing_reports": reports}


def main():
    with ThreadPoolExecutor(max_workers=4) as pool:
        production = list(pool.map(probe, PATHS))
    # Retain only compact FX provenance, not a second copy of its history.
    for row in production:
        if row["url"].endswith("fx.json") and "series" in row:
            data = row.pop("series")
            row["fx_structure"] = {k: type(v).__name__ for k, v in data.items()}
            row["fx_rows"] = []
            for key, value in data.items():
                candidates = value if isinstance(value, list) else list(value.values()) if isinstance(value, dict) else []
                for item in candidates:
                    if isinstance(item, dict):
                        row["fx_rows"].append({k: v for k, v in item.items()
                            if not isinstance(v, (list, dict))})
    record = {
        "checked_at": NOW.isoformat(), "scope": "Research audit; no trading decisions or backtest",
        "root": str(ROOT), "production": production, "ea": ea_inventory(),
        "historical": historical_inventory(),
        "mtf_counterexample": {
            "description": "Algebraic counterexample to using the existing gauge as a trend-strength gate",
            "direction_votes": [1, 1], "adx": 5,
            "score": max(.35, min(1, 5 / 40)), "up_label_threshold": .15,
            "result": "up despite ADX=5; this is not a measured market observation",
        },
        "limitations": ["No connected browser was available for visual/live-stream QA.",
            "No MT5 terminal, live preset or account was accessed.",
            "No gated-EA performance test was run.",
            "Source hashes identify inspected files, not historical report binaries."],
    }
    OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in production:
        print(json.dumps({k: v for k, v in row.items() if k not in ("keys", "fx_rows")}, ensure_ascii=False))
    print(f"Evidence: {OUT}")


if __name__ == "__main__":
    main()
