#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Ostrzegaj, gdy PR modyfikuje ADR *ważony przez człowieka* (strażnik dryfu ADR-100).

To repozytorium jest tworzone niemal wyłącznie przez AI, więc oznaczanie
autorstwa AI to niemal wszechobecny szum. Istotnym ryzykiem jest sytuacja
odwrotna: decyzja, w której człowiek faktycznie brał udział, zostaje po
cichu odwrócona lub zawężona przez agenta i nigdy nie jest ponownie
rozpatrzona.

Decyzja jest *ważona przez człowieka*, gdy jej nagłówek ADR zawiera
`Authored-by: human` albo niepuste pole `Reviewed-by:`. Gdy PR modyfikuje
taki ADR, ten strażnik emituje ostrzeżenie GitHub Actions, żeby człowiek
przejrzał zmianę zamiast dopuścić ją bez kontroli. Jest to kontrola
**doradcza** — zawsze kończy się kodem 0 i nigdy nie blokuje merge'a.

Kontrola celowo dotyczy wyłącznie nagłówka: nigdy nie sprawdza trailerów
gita (`Co-Authored-By: Claude` jest zabronione przez CLAUDE.md i byłoby
szumem).

Sam predykat „ważony przez człowieka" żyje w `decision_provenance`, dzięki
czemu próbkownik ponownej derywacji współdzieli jedną definicję; ten
skrypt jest cienkim wywołującym, który mapuje oflagowane ADR-y na
ostrzeżenia GitHub Actions.

Użycie: przekaż zmienione ścieżki ADR jako argumenty. Workflow CI wylicza
zbiór zmodyfikowanych/przemianowanych plików `docs/adr/*.md`
(`git diff --diff-filter=MR`) i przekazuje go tutaj, dzięki czemu skrypt
pozostaje czystą funkcją swoich wejść i sam nie dotyka gita.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Współdzielony predykat żyje obok tego skryptu; zarówno `uv run --script`,
# jak i zwykłe `python bin/check-adr-drift.py` umieszczają bin/ w
# sys.path[0], ale wstawiamy je jawnie, żeby import był odporny niezależnie
# od katalogu roboczego wywołania.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from decision_provenance import human_weight_reason, is_adr


def collect_flagged(*, paths: list[Path]) -> list[tuple[Path, str]]:
    flagged: list[tuple[Path, str]] = []
    for path in paths:
        if not is_adr(path=path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        reason = human_weight_reason(text=text)
        if reason is not None:
            flagged.append((path, reason))
    return flagged


def main(*, argv: list[str]) -> int:
    flagged = collect_flagged(paths=[Path(arg) for arg in argv])
    for path, reason in flagged:
        message = (
            f"Human-weighted ADR modified ({reason}). Confirm this change does "
            f"not silently reverse or narrow the recorded decision; a human "
            f"should revisit it (see ADR-100)."
        )
        print(f"::warning file={path}::{message}")
    if flagged:
        print(
            f"{len(flagged)} human-weighted ADR(s) modified — advisory only, not blocking.",
            file=sys.stderr,
        )
    else:
        print("No human-weighted ADRs among the changed files.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
