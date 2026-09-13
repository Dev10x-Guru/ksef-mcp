#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fail when two ADRs share the same NNN number in the merged tree.

ADR number collisions have happened twice (089/090, and a live 095
duplicate) because bot-authored ADR PRs race on the next free number
with no reservation. This guard scans the whole `docs/adr/` tree — the
state the branch would merge into — and exits non-zero on any duplicate,
so the collision is caught on the PR rather than after merge.

Run directly (`bin/check-adr-numbers.py`) or via the pre-commit hook /
CI workflow. Takes no arguments — it always scans the full corpus, not
just changed files, because a collision is a property of the tree.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"
ADR_FILENAME = re.compile(r"^(?P<number>\d{3})-.+\.md$")


def collect_collisions(*, adr_dir: Path) -> dict[str, list[str]]:
    by_number: dict[str, list[str]] = defaultdict(list)
    for path in sorted(adr_dir.rglob("*.md")):
        match = ADR_FILENAME.match(path.name)
        if match is None:
            continue
        by_number[match.group("number")].append(path.name)
    return {number: names for number, names in by_number.items() if len(names) > 1}


def main() -> int:
    if not ADR_DIR.is_dir():
        print(f"ADR directory not found: {ADR_DIR}", file=sys.stderr)
        return 1
    collisions = collect_collisions(adr_dir=ADR_DIR)
    if not collisions:
        return 0
    print("Duplicate ADR numbers detected:", file=sys.stderr)
    for number, names in sorted(collisions.items()):
        print(f"  ADR-{number}: {', '.join(names)}", file=sys.stderr)
    print(
        "\nRenumber one file to the next free number and fix its header "
        "and cross-references (see ADR-100 acceptance gate).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
