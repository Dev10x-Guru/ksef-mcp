#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Zakończ błędem, gdy dwa ADR-y mają ten sam numer NNN w scalonym drzewie.

Kolizje numerów ADR zdarzyły się już dwukrotnie (089/090 oraz aktywny
duplikat 095), ponieważ PR-y z ADR-ami tworzone przez boty ścigają się
o kolejny wolny numer bez żadnej rezerwacji. Ten strażnik skanuje całe
drzewo `docs/adr/` — czyli stan, do którego scaliłby się dany branch —
i kończy się kodem niezerowym przy każdym duplikacie, dzięki czemu
kolizję wyłapuje się na etapie PR-a, a nie po merge'u.

Uruchamiaj bezpośrednio (`bin/check-adr-numbers.py`) albo przez hook
pre-commit / workflow CI. Nie przyjmuje żadnych argumentów — zawsze
skanuje pełny zbiór, a nie tylko zmienione pliki, bo kolizja jest
właściwością całego drzewa, nie pojedynczego pliku.
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
        "\nZmień numerację jednego pliku na kolejny wolny numer i popraw "
        "jego nagłówek oraz odwołania (zob. bramkę akceptacji w ADR-100).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
