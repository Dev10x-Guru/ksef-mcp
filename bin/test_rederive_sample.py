"""Unit tests for bin/rederive-sample.py.

The script filename is hyphenated (not importable as a dotted name), so it is
loaded via importlib to exercise the pure selection/cursor logic directly
rather than only as a black box.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "rederive_sample", Path(__file__).resolve().parent / "rederive-sample.py"
)
sampler = importlib.util.module_from_spec(_SPEC)
# Register before exec so the module's @dataclass fields can resolve their own
# module namespace (dataclasses looks the module up in sys.modules).
sys.modules[_SPEC.name] = sampler
_SPEC.loader.exec_module(sampler)

Candidate = sampler.Candidate
Cursor = sampler.Cursor

HUMAN_ADR = """# ADR-050: Example human decision

- **Authored-by:** human
- **Reviewed-by:** —

## Context
Body.
"""

AGENT_ADR = """# ADR-051: Agent decision

- **Authored-by:** agent (claude-opus-4-8)
- **Reviewed-by:** —

## Context
Body.
"""


def _candidates(ids: list[str]) -> list[Candidate]:
    return [Candidate(id=i, title=i, reference="") for i in ids]


def test_adr_candidates_flags_only_human_weighted(tmp_path: Path) -> None:
    (tmp_path / "050-human.md").write_text(HUMAN_ADR, encoding="utf-8")
    (tmp_path / "051-agent.md").write_text(AGENT_ADR, encoding="utf-8")
    (tmp_path / "TEMPLATE.md").write_text(HUMAN_ADR, encoding="utf-8")
    result = sampler.adr_candidates(adr_dir=tmp_path)
    assert [c.id for c in result] == ["ADR-050"]
    assert result[0].title == "ADR-050: Example human decision"


def test_adr_candidates_skips_unreadable(tmp_path: Path) -> None:
    (tmp_path / "050-human.md").write_text(HUMAN_ADR, encoding="utf-8")
    # A directory whose name matches the ADR pattern: read_text raises
    # IsADirectoryError (an OSError), which the guard must skip.
    (tmp_path / "052-dir.md").mkdir()
    assert [c.id for c in sampler.adr_candidates(adr_dir=tmp_path)] == ["ADR-050"]


def test_override_selects_exact_ids_and_ignores_rotation() -> None:
    candidates = _candidates(["ADR-001", "ADR-007", "ADR-033"])
    cursor = Cursor(run=5, sampled={"ADR-007": 5}, confirmed=["ADR-033"])
    selected = sampler.select(
        candidates=candidates,
        cursor=cursor,
        count=1,
        pins=["ADR-033"],
        override=["ADR-007", "ADR-001"],
    )
    assert [c.id for c in selected] == ["ADR-007", "ADR-001"]


def test_override_warns_on_unknown_id(capsys: pytest.CaptureFixture[str]) -> None:
    selected = sampler.select(
        candidates=_candidates(["ADR-001"]),
        cursor=Cursor(run=0, sampled={}, confirmed=[]),
        count=3,
        pins=[],
        override=["ADR-099"],
    )
    assert selected == []
    assert "ADR-099" in capsys.readouterr().err


def test_unconfirmed_pin_is_forced_in() -> None:
    candidates = _candidates(["ADR-001", "ADR-007", "ADR-033"])
    cursor = Cursor(run=9, sampled={"ADR-033": 9}, confirmed=[])
    selected = sampler.select(
        candidates=candidates, cursor=cursor, count=1, pins=["ADR-033"], override=[]
    )
    assert "ADR-033" in {c.id for c in selected}


def test_confirmed_pin_is_not_forced() -> None:
    candidates = _candidates(["ADR-001", "ADR-007", "ADR-033"])
    cursor = Cursor(
        run=9,
        sampled={"ADR-001": 1, "ADR-007": 2, "ADR-033": 3},
        confirmed=["ADR-033"],
    )
    selected = sampler.select(
        candidates=candidates, cursor=cursor, count=1, pins=["ADR-033"], override=[]
    )
    # ADR-033 confirmed → not forced; least-recently-sampled (ADR-001) wins.
    assert [c.id for c in selected] == ["ADR-001"]


def test_rotation_prefers_least_recently_sampled() -> None:
    candidates = _candidates(["ADR-001", "ADR-007", "ADR-033"])
    cursor = Cursor(run=3, sampled={"ADR-001": 3, "ADR-007": 1}, confirmed=[])
    selected = sampler.select(candidates=candidates, cursor=cursor, count=2, pins=[], override=[])
    # ADR-033 never sampled (0) first, then ADR-007 (1); ADR-001 (3) excluded.
    assert [c.id for c in selected] == ["ADR-033", "ADR-007"]


def test_cursor_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    Cursor(run=2, sampled={"ADR-033": 2}, confirmed=["ADR-001"]).dump(path=path)
    loaded = Cursor.load(path=path)
    assert loaded.run == 2
    assert loaded.sampled == {"ADR-033": 2}
    assert loaded.confirmed == ["ADR-001"]


def test_cursor_load_missing_defaults(tmp_path: Path) -> None:
    loaded = Cursor.load(path=tmp_path / "absent.json")
    assert (loaded.run, loaded.sampled, loaded.confirmed) == (0, {}, [])


def test_main_writes_sample_and_advances_cursor(tmp_path: Path) -> None:
    adr_dir = tmp_path / "adr"
    adr_dir.mkdir()
    (adr_dir / "050-human.md").write_text(HUMAN_ADR, encoding="utf-8")
    cursor_path = tmp_path / "cursor.json"
    out_path = tmp_path / "out" / "sample.json"
    code = sampler.main(
        argv=[
            "--adr-dir",
            str(adr_dir),
            "--cursor",
            str(cursor_path),
            "--out",
            str(out_path),
            "--count",
            "2",
            "--pin",
            "ADR-050",
        ]
    )
    assert code == 0
    sample = json.loads(out_path.read_text(encoding="utf-8"))
    ids = [d["id"] for d in sample["decisions"]]
    assert "ADR-050" in ids  # explicit pin, unconfirmed
    assert sample["run"] == 1
    cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert cursor["run"] == 1
    assert all(cursor["sampled"][i] == 1 for i in ids)


def test_main_confirm_flag_records_confirmation(tmp_path: Path) -> None:
    adr_dir = tmp_path / "adr"
    adr_dir.mkdir()
    (adr_dir / "050-human.md").write_text(HUMAN_ADR, encoding="utf-8")
    cursor_path = tmp_path / "cursor.json"
    code = sampler.main(
        argv=[
            "--adr-dir",
            str(adr_dir),
            "--cursor",
            str(cursor_path),
            "--out",
            str(tmp_path / "s.json"),
            "--confirm",
            "ADR-050",
        ]
    )
    assert code == 0
    assert "ADR-050" in json.loads(cursor_path.read_text(encoding="utf-8"))["confirmed"]
