#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Wybierz próbkę decyzji ważonych przez człowieka do adwersarialnej ponownej derywacji.

Połowa ADR-100 odpowiadająca za ponowną derywację powtórnie rozpatruje
decyzje *ludzkie* w świetle bieżących okoliczności. Ten próbkownik buduje
zbiór kandydatów, wybiera małą rotującą próbkę i emituje ją jako JSON do
skonsumowania przez agenta ponownej derywacji.

Jedno źródło kandydatów na dziś: **korpus ADR** (`docs/adr/*.md`) — ADR-y,
które predykat „ważony przez człowieka" (z `decision_provenance`, wspólnej
biblioteki) oznacza (`Authored-by: human` albo niepuste `Reviewed-by:`).
Dziś puste; rośnie wraz z przyrostem znaczników proweniencji.

Wybór, w kolejności priorytetu:

1. **Override** — jawna lista `--override "ADR-050,ADR-007"` (z
   `workflow_dispatch`) wybiera dokładnie te identyfikatory i ignoruje
   rotację.
2. **Piny** — `--pin ADR-050` (powtarzalne) jest zawsze uwzględniany,
   dopóki nie zostanie raz potwierdzony (`--confirm ADR-050` zapisuje to
   w kursorze).
3. **Rotacja** — pozostałe miejsca wypełniane są kandydatami najdawniej
   próbkowanymi, na podstawie utrwalonego kursora, dzięki czemu pokrycie
   rotuje zamiast wciąż sprawdzać te same łatwe decyzje.

Kursor to mały plik JSON, który utrwala wywołujący między uruchomieniami
(zacommitowany z powrotem albo cache'owany przez workflow). Ten skrypt
tylko go odczytuje i zapisuje; nie decyduje, jak CI go utrwala.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from decision_provenance import human_weight_reason, is_adr

DEFAULT_ADR_DIR = Path("docs/adr")
DEFAULT_CURSOR = Path(".rederivation-cursor.json")
DEFAULT_OUT = Path(".claude-output/rederivation-sample.json")
DEFAULT_COUNT = 3
# Na razie brak przypiętych decyzji. Przypnij tu identyfikator, gdy
# decyzja ważona przez człowieka jest na tyle istotna, że każda rotacja
# musi ją ponownie wyprowadzić, dopóki nie zostanie raz potwierdzona.
DEFAULT_PINS: tuple[str, ...] = ()

_ADR_HEADING = "# "


@dataclass(frozen=True)
class Candidate:
    id: str
    title: str
    reference: str  # ścieżka ADR + powód ważenia przez człowieka


@dataclass
class Cursor:
    run: int
    sampled: dict[str, int]  # id kandydata -> numer run, w którym był ostatnio próbkowany
    confirmed: list[str]  # piny, które zostały choć raz potwierdzone

    @classmethod
    def load(cls, *, path: Path) -> Cursor:
        if not path.exists():
            return cls(run=0, sampled={}, confirmed=[])
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            run=int(data.get("run", 0)),
            sampled={str(k): int(v) for k, v in data.get("sampled", {}).items()},
            confirmed=list(data.get("confirmed", [])),
        )

    def dump(self, *, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"run": self.run, "sampled": self.sampled, "confirmed": self.confirmed},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


def _adr_title(*, text: str) -> str:
    for line in text.splitlines():
        if line.startswith(_ADR_HEADING):
            return line[len(_ADR_HEADING) :].strip()
    return ""


def adr_candidates(*, adr_dir: Path) -> list[Candidate]:
    candidates: list[Candidate] = []
    for path in sorted(adr_dir.glob("*.md")):
        if not is_adr(path=path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        reason = human_weight_reason(text=text)
        if reason is None:
            continue
        candidates.append(
            Candidate(
                id=f"ADR-{path.stem.split('-')[0]}",
                title=_adr_title(text=text),
                reference=f"{path} ({reason})",
            )
        )
    return candidates


def select(
    *,
    candidates: list[Candidate],
    cursor: Cursor,
    count: int,
    pins: list[str],
    override: list[str],
) -> list[Candidate]:
    by_id = {c.id: c for c in candidates}

    if override:
        missing = [cid for cid in override if cid not in by_id]
        for cid in missing:
            print(
                f"::warning::override id {cid} is not a known candidate",
                file=sys.stderr,
            )
        return [by_id[cid] for cid in override if cid in by_id]

    selected: list[Candidate] = []
    for cid in pins:
        if cid in by_id and cid not in cursor.confirmed and cid not in {c.id for c in selected}:
            selected.append(by_id[cid])

    remaining = [c for c in candidates if c.id not in {s.id for s in selected}]
    # Najpierw najdawniej próbkowane (nigdy-niepróbkowane == run 0); przy remisie
    # rozstrzyga stabilnie identyfikator.
    remaining.sort(key=lambda c: (cursor.sampled.get(c.id, 0), c.id))
    for candidate in remaining:
        if len(selected) >= count:
            break
        selected.append(candidate)
    return selected


def main(*, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adr-dir", type=Path, default=DEFAULT_ADR_DIR)
    parser.add_argument("--cursor", type=Path, default=DEFAULT_CURSOR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--pin", action="append", default=None)
    parser.add_argument("--override", default="")
    parser.add_argument("--confirm", action="append", default=None)
    args = parser.parse_args(argv)

    cursor = Cursor.load(path=args.cursor)

    confirms = args.confirm or []
    for cid in confirms:
        if cid not in cursor.confirmed:
            cursor.confirmed.append(cid)

    pins = args.pin if args.pin is not None else list(DEFAULT_PINS)
    override = [item.strip() for item in args.override.split(",") if item.strip()]
    candidates = adr_candidates(adr_dir=args.adr_dir)

    selected = select(
        candidates=candidates,
        cursor=cursor,
        count=args.count,
        pins=pins,
        override=override,
    )

    cursor.run += 1
    for candidate in selected:
        cursor.sampled[candidate.id] = cursor.run
    cursor.dump(path=args.cursor)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"run": cursor.run, "decisions": [asdict(c) for c in selected]},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Selected {len(selected)} decision(s) for re-derivation run "
        f"{cursor.run}: {', '.join(c.id for c in selected) or '(none)'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
