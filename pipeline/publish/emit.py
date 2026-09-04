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
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
# The pre-build copy of each artifact, kept only so validate.py can compare a
# fresh build against what it is about to replace (row-count regressions).
# Gitignored -- this is a same-machine handoff between build and validate, not
# part of the published contract.
PREV_DIR = DATA_DIR / ".prev"


def _json_safe(value: Any) -> Any:
    """Replace non-finite floats with null before publishing browser JSON.

    Python's JSON encoder accepts NaN by default, but JSON.parse in every
    browser rejects it. DuckDB can legitimately return float NaN for a missing
    quote field, so the publication boundary has to normalize it explicitly.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def write_json(
    name: str,
    payload: Any,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write a compact JSON artifact into data/ and report its size."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        previous = PREV_DIR / name
        previous.parent.mkdir(parents=True, exist_ok=True)
        previous.write_bytes(path.read_bytes())

    if meta is not None or isinstance(payload, dict):
        body: Any = payload if isinstance(payload, dict) else {"rows": payload}
        if meta:
            body = {**body, **meta}
        body.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    else:
        body = payload

    body = _json_safe(body)
    text = json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), default=str,
        allow_nan=False,
    )

    # `updated_at` changes on every run by definition, so a byte comparison
    # would call every file new even when the data is identical -- and each of
    # these is a single line, so git stores a whole fresh copy rather than a
    # diff. Across 1,719 ticker dossiers that is ~51MB of history per refresh
    # for, usually, no change at all. Compare everything except the timestamp
    # and keep the existing file when only that moved.
    if path.exists() and _same_but_for_timestamp(path, body):
        log.debug("unchanged %s", path.name)
        return path

    path.write_text(text, encoding="utf-8")
    log.info("wrote %s (%.1f KB)", path.name, len(text.encode()) / 1024)
    return path


def _same_but_for_timestamp(path: Path, body: Any) -> bool:
    """True when the file on disk matches `body` apart from `updated_at`.

    Any parse or type problem answers False: rewriting a file needlessly costs
    disk, but skipping a write that was actually needed would publish stale
    numbers, so the uncertain case has to fall through to the write.
    """
    if not isinstance(body, dict):
        return False
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(existing, dict):
        return False
    return {k: v for k, v in existing.items() if k != "updated_at"} == \
           {k: v for k, v in body.items() if k != "updated_at"}


def read_json(name: str) -> Any:
    path = DATA_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_prev_json(name: str) -> Any:
    """Read the pre-build snapshot write_json() kept before its latest overwrite.

    None if the artifact has never been built before -- the caller should treat
    that as "no baseline to compare against" rather than a regression.
    """
    path = PREV_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
