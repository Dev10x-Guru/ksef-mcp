"""Tests for bin/check-adr-drift.py — the human-weighted ADR guard.

Exercised as a black box (subprocess) so the tests pin the real CLI contract
the CI workflow depends on: exit 0 always (advisory), a `::warning file=…::`
annotation for each human-weighted ADR, and silence for AI-made ones. The
script has empty dependencies, so plain `sys.executable` runs it.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-adr-drift.py"

HUMAN_AUTHORED = """# ADR-200: Example

- **Date:** 2026-07-11
- **Status:** Accepted
- **Authored-by:** human
- **Reviewed-by:** —

## Context
Body text.
"""

AGENT_REVIEWED = """# ADR-201: Example

- **Authored-by:** agent (claude-opus-4-8)
- **Reviewed-by:** Janusz Skonieczny

## Context
Body text.
"""

AGENT_UNREVIEWED = """# ADR-202: Example

- **Authored-by:** agent (claude-opus-4-8)
- **Reviewed-by:** —

## Context
An `Authored-by: human` reference in prose must not trigger the guard.
"""

TEMPLATE = """# ADR-NNN: [Title]

- **Authored-by:** human | agent (<model id>)
- **Reviewed-by:** [named human(s) who read the full text before acceptance]

## Context
"""


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(path) for path in paths]],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def adr_file(tmp_path: Path) -> Callable[..., Path]:
    def _make(*, filename: str, content: str) -> Path:
        path = tmp_path / filename
        path.write_text(content, encoding="utf-8")
        return path

    return _make


@pytest.mark.parametrize(
    "filename,content,should_warn",
    [
        ("200-human-authored.md", HUMAN_AUTHORED, True),
        ("201-agent-but-reviewed.md", AGENT_REVIEWED, True),
        ("202-agent-unreviewed.md", AGENT_UNREVIEWED, False),
        ("TEMPLATE.md", TEMPLATE, False),
        ("notes.md", HUMAN_AUTHORED, False),
    ],
)
def test_flags_only_human_weighted_adrs(
    adr_file: Callable[..., Path],
    filename: str,
    content: str,
    should_warn: bool,
) -> None:
    path = adr_file(filename=filename, content=content)
    result = _run(path)
    assert result.returncode == 0
    assert (f"::warning file={path}::" in result.stdout) is should_warn


def test_no_arguments_is_silent_and_passes() -> None:
    result = _run()
    assert result.returncode == 0
    assert "::warning" not in result.stdout


def test_reason_names_the_triggering_marker(adr_file: Callable[..., Path]) -> None:
    path = adr_file(filename="203-human-authored.md", content=HUMAN_AUTHORED)
    result = _run(path)
    assert "Authored-by: human" in result.stdout
