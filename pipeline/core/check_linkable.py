"""Fail when the shared link-behaviour block drifts between apps.

Every app reproduces the browser's own link handling for elements that cannot
be anchors -- table rows, SVG groups, list items. That code is pasted into each
single-file app rather than shared through <script src>, because check_apps.py
skips scripts carrying a src attribute: a shared file would sit outside the only
JavaScript gate this repo has, and sw.js caches same-origin assets by path with
no cache busting. Both are failure modes this project has already been bitten by.

Copy-paste is only acceptable with something watching the copies, so this is
that something. apps/brief/index.html holds the canonical block; every other
copy must match it once whitespace is normalised.

    python -m pipeline.core.check_linkable
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

CANONICAL = REPO_ROOT / "apps" / "brief" / "index.html"
BLOCK_RE = re.compile(
    r"//\s*LEON-LINKABLE-BEGIN\s+(?P<version>\S+)\s*\n(?P<body>.*?)//\s*LEON-LINKABLE-END",
    re.S,
)


def html_files() -> list[Path]:
    files = sorted(REPO_ROOT.glob("apps/*/index.html"))
    hub = REPO_ROOT / "hub" / "index.html"
    if hub.exists():
        files.append(hub)
    return files


def extract(path: Path) -> tuple[str, str] | None:
    """Return (version, normalised body) for the block, or None when absent."""
    match = BLOCK_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return None
    # Indentation and blank lines are free to differ; the code must not.
    body = "\n".join(
        line.strip() for line in match.group("body").splitlines() if line.strip()
    )
    return match.group("version"), body


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def check(verbose: bool = False) -> int:
    reference = extract(CANONICAL)
    if reference is None:
        print(
            f"FAIL {CANONICAL.relative_to(REPO_ROOT).as_posix()} has no "
            "LEON-LINKABLE block, so there is nothing to compare against."
        )
        return 1

    version, canonical_body = reference
    want = digest(canonical_body)
    failures = 0
    carriers = 0

    for path in html_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        found = extract(path)
        # Not every app needs the block: an app whose every destination is a
        # real anchor is the better outcome, not a missing copy.
        if found is None:
            continue
        carriers += 1
        their_version, body = found
        got = digest(body)
        if their_version != version:
            failures += 1
            print(f"FAIL {rel}: block version {their_version}, canonical is {version}")
        elif got != want:
            failures += 1
            print(f"FAIL {rel}: block differs from canonical ({got} != {want})")
        elif verbose:
            print(f"  ok  {rel}")

    if failures:
        print(
            f"\ncheck_linkable: {failures} of {carriers} copies drifted from "
            f"{CANONICAL.relative_to(REPO_ROOT).as_posix()}.\n"
            "  Copy the canonical block over the drifted one rather than "
            "hand-merging: the copies exist to be identical."
        )
        return 1
    print(f"check_linkable: {carriers} copies of block {version} match canonical {want}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    sys.exit(check(args.verbose))


if __name__ == "__main__":
    main()
