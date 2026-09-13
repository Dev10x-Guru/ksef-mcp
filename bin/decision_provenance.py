"""Współdzielony predykat „decyzji ważonej przez człowieka" dla narzędzi proweniencji ADR-100.

Dwie symetryczne połówki procedury proweniencji ADR-100 korzystają z tego
modułu i nigdy nie mogą się różnić w tym, co uznają za *ważone przez
człowieka*:

- strażnik **dryfu** (`check-adr-drift.py`) — ostrzega, gdy PR modyfikuje
  ADR ważony przez człowieka;
- próbkownik **ponownej derywacji** (`rederive-sample.py`) — losuje
  próbkę decyzji ważonych przez człowieka do ponownego wyprowadzenia
  w bieżących okolicznościach.

Predykat ADR (`human_weight_reason`) celowo dotyczy wyłącznie nagłówka:
decyzja jest ważona przez człowieka, gdy jej nagłówek ADR zawiera
`Authored-by: human` **albo** niepuste pole `Reviewed-by:`. Nigdy nie
sprawdza trailerów gita (`Co-Authored-By: Claude` jest zabronione przez
CLAUDE.md i byłoby szumem).
"""

from __future__ import annotations

import re
from pathlib import Path

ADR_FILENAME = re.compile(r"^\d{3}-.+\.md$")

# Nagłówek to preambuła przed pierwszą sekcją `## `. Ograniczenie skanowania
# pól do niego zapobiega dopasowaniu tych samych nazw pól cytowanych w
# treści (np. ADR-100 objaśnia `Authored-by:` w prozie).
_HEADER_END = re.compile(r"^## ", re.MULTILINE)
_AUTHORED_BY = re.compile(r"^-\s*\*\*Authored-by:\*\*\s*(?P<value>.*?)\s*$", re.MULTILINE)
_REVIEWED_BY = re.compile(r"^-\s*\*\*Reviewed-by:\*\*\s*(?P<value>.*?)\s*$", re.MULTILINE)

# Wartości oznaczające „brak recenzenta" — puste pole `Reviewed-by:`.
# Trzy warianty myślnika są celowe, nie są literówką: człowiek wypełniający
# `Reviewed-by:` może wpisać dywiz, półpauzę albo pauzę, i wszystkie trzy
# oznaczają „nikt tego nie zrecenzował". RUF001 zgłasza dokładnie tę
# niejednoznaczność, której potrzebujemy.
# Warianty polskie dopisane wraz z przejściem projektu na polski: autor
# piszący ADR po polsku wpisze „brak" albo „nikt", a bez nich parser wziąłby
# to za nazwisko recenzenta i uznał decyzję za zrecenzowaną przez człowieka.
_EMPTY_MARKERS = {
    "",
    "—",
    "-",
    "–",  # noqa: RUF001 — półpauza to celowy wariant, nie literówka
    "none",
    "n/a",
    "tbd",
    "todo",
    "brak",
    "nikt",
    "do uzupełnienia",
}


def header_region(*, text: str) -> str:
    match = _HEADER_END.search(text)
    return text[: match.start()] if match else text


def _field(*, pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group("value").strip() if match else None


def is_reviewed(*, reviewed_by: str | None) -> bool:
    if reviewed_by is None:
        return False
    normalized = reviewed_by.strip().lower()
    if normalized in _EMPTY_MARKERS:
        return False
    # Niewypełniony placeholder z szablonu, np. "[named human(s) who read ...]".
    if normalized.startswith("[") and normalized.endswith("]"):
        return False
    return True


def is_human_authored(*, authored_by: str | None) -> bool:
    if authored_by is None:
        return False
    return authored_by.strip().lower().startswith("human")


def human_weight_reason(*, text: str) -> str | None:
    header = header_region(text=text)
    authored_by = _field(pattern=_AUTHORED_BY, text=header)
    reviewed_by = _field(pattern=_REVIEWED_BY, text=header)
    reasons: list[str] = []
    if is_human_authored(authored_by=authored_by):
        reasons.append(f"Authored-by: {authored_by}")
    if is_reviewed(reviewed_by=reviewed_by):
        reasons.append(f"Reviewed-by: {reviewed_by}")
    return " / ".join(reasons) if reasons else None


def is_adr(*, path: Path) -> bool:
    return ADR_FILENAME.match(path.name) is not None
