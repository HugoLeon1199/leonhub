"""Vendor a compact Vietnam province map for the static BDS app.

The upstream feature collection is MIT licensed.  It contains the former 63
province shapes; the client groups them with the same 63-to-34 mapping used by
`bds_aggregate.py`.  Keeping source shapes separate means we preserve familiar
district/province provenance while rendering the current merged provinces.

Run only when refreshing the map asset:
    python -m pipeline.transform.build_vietnam_map
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

from pipeline.publish.emit import DATA_DIR

SOURCE = (
    "https://raw.githubusercontent.com/hoccungduy/"
    "vietnam-map-34-provinces/main/src/core/assets/vn-all.geo.json"
)


def build() -> Path:
    request = Request(SOURCE, headers={"User-Agent": "LEON-Hub map asset builder"})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed HTTPS source
        source = json.load(response)

    features = []
    for feature in source.get("features", []):
        props = feature.get("properties") or {}
        name = props.get("name") or props.get("woe-name")
        geometry = feature.get("geometry")
        if name and geometry:
            features.append({"n": name, "g": geometry})
    if len(features) < 60:
        raise RuntimeError(f"map source unexpectedly contains only {len(features)} provinces")

    payload = {
        "source": SOURCE,
        "project": "hoccungduy/vietnam-map-34-provinces",
        "license": "MIT",
        "features": features,
    }
    path = DATA_DIR / "vn-map.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {path} ({path.stat().st_size / 1024:.1f} KiB, {len(features)} shapes)")
    return path


if __name__ == "__main__":
    build()
