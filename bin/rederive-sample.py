#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Select a sample of human-weighted decisions for adversarial re-derivation.

The ADR-100 re-derivation half re-examines *human* decisions under
current circumstances. This sampler builds the candidate set, picks a small
rotating sample, and emits it as JSON for the re-derivation agent to consume.

One candidate source today: the **ADR corpus** (`docs/adr/*.md`) — ADRs the
human-weighted predicate (from `decision_provenance`, the shared library)
flags (`Authored-by: human` or a non-empty `Reviewed-by:`). Empty today;
grows as provenance markers accumulate.

Selection, in priority order:

1. **Override** — an explicit `--override "ADR-050,ADR-007"` list (from
   `workflow_dispatch`) selects exactly those IDs and ignores rotation.
2. **Pins** — `--pin ADR-050` (repeatable) is always included until it has
   been confirmed once (`--confirm ADR-050` records that in the cursor).
3. **Rotation** — remaining slots are filled from the least-recently-sampled
   candidates via a persisted cursor, so coverage rotates instead of
   re-checking the same easy decisions.

The cursor is a small JSON file the caller persists between runs (committed
back or cached by the workflow). This script only reads and writes
it; it does not decide how CI persists it.
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
# No pinned decisions yet. Pin an id here once a human-weighted decision
# is important enough that every rotation must re-derive it until it has
# been confirmed once.
DEFAULT_PINS: tuple[str, ...] = ()

_ADR_HEADING = "# "


@dataclass(frozen=True)
class Candidate:
    id: str
    title: str
    reference: str  # ADR path + human-weight reason


@dataclass
class Cursor:
    run: int
    sampled: dict[str, int]  # candidate id -> run number it was last sampled
    confirmed: list[str]  # pins that have been confirmed at least once

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
    # Least-recently-sampled first (never-sampled == run 0); stable id tiebreak.
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
