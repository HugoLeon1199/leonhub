"""Write the published JSON contract.

Two conventions, both borrowed from the teardown and both load-bearing:

- **Short keys.** `p` not `price_per_m2`. Turtle fits 1,522 tickers in 466KB
  that way; a screener that filters in the browser has to ship the whole table,
  so key length is a real cost, paid on every page load.
- **Every file carries provenance.** `updated_at`, the source, and per-row `n`
  travel with the data so the UI can show where a number came from without a
  second request. A figure whose origin the page cannot state should not be on
  the page.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def write_json(
    name: str,
    payload: Any,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write a compact JSON artifact into data/ and report its size."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name

    if meta is not None or isinstance(payload, dict):
        body: Any = payload if isinstance(payload, dict) else {"rows": payload}
        if meta:
            body = {**body, **meta}
        body.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    else:
        body = payload

    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)
    path.write_text(text, encoding="utf-8")
    log.info("wrote %s (%.1f KB)", path.name, len(text.encode()) / 1024)
    return path


def read_json(name: str) -> Any:
    path = DATA_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
