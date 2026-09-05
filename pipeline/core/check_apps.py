"""Parse every app's inline JavaScript and fail on a syntax error.

This exists because of a real outage. A fetch call was inserted into the middle
of a `Promise.all([...])` array and terminated with a semicolon, which closed
the array literal early. The whole script failed to parse, so the real-estate
tab rendered nothing at all -- no map, no table, no prices -- and shipped that
way. Brace and paren counting missed it completely: the counts stayed balanced,
because the bug was a wrong separator rather than a missing bracket.

Only a real parser catches that class of mistake, and the apps have no build
step to catch it for them. Deno is used when present since it is already
installed here and needs no project setup; without it the check reports what it
skipped rather than passing silently, because a check that quietly does nothing
is worse than no check.

    python -m pipeline.core.check_apps
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Scripts with a `src` are external and have nothing inline to parse. Very short
# blocks are the pre-paint theme setter, not app logic worth extracting.
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
MIN_LENGTH = 40


def html_files() -> list[Path]:
    files = sorted(REPO_ROOT.glob("apps/*/index.html"))
    hub = REPO_ROOT / "hub" / "index.html"
    if hub.exists():
        files.append(hub)
    return files


def inline_scripts(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    return [b for b in SCRIPT_RE.findall(source) if len(b.strip()) >= MIN_LENGTH]


def check(verbose: bool = False) -> int:
    deno = shutil.which("deno")
    if not deno:
        print(
            "check_apps: deno not found, no JavaScript was parsed.\n"
            "  Install it (https://deno.land) or run the equivalent check in CI.\n"
            "  Reporting this rather than passing: a skipped check that prints "
            "nothing is indistinguishable from a passing one."
        )
        return 0

    failures = 0
    checked = 0
    with tempfile.TemporaryDirectory() as tmp:
        for path in html_files():
            label = path.parent.name or path.stem
            for index, block in enumerate(inline_scripts(path)):
                target = Path(tmp) / f"{label}_{index}.js"
                target.write_text(block, encoding="utf-8")
                checked += 1
                result = subprocess.run(
                    [deno, "check", "--no-lock", str(target)],
                    capture_output=True, text=True,
                )
                # `deno check` also type-checks; only syntax errors are fatal
                # here, since these files are plain browser JS with no types
                # and no module graph to resolve.
                stderr = result.stderr or ""
                syntax = [
                    line.strip() for line in stderr.splitlines()
                    if "SyntaxError" in line or "Expected" in line
                ]
                if syntax:
                    failures += 1
                    rel = path.relative_to(REPO_ROOT).as_posix()
                    print(f"FAIL {rel} (script {index}): {syntax[0]}")
                elif verbose:
                    print(f"  ok  {label}_{index}")

    if failures:
        print(f"\ncheck_apps: {failures} of {checked} inline scripts failed to parse")
        return 1
    print(f"check_apps: {checked} inline scripts parse cleanly")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    sys.exit(check(args.verbose))


if __name__ == "__main__":
    main()
