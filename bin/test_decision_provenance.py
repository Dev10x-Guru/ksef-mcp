"""Unit tests for the shared human-weighted predicate.

Imported directly (not via subprocess) — this is the pure-function library
that both the drift guard and the re-derivation sampler depend on, so the
contract is the Python API, not a CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decision_provenance import (
    human_weight_reason,
    is_adr,
    is_human_authored,
    is_reviewed,
)

HUMAN_AUTHORED = """# ADR-200: Example

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
An `Authored-by: human` reference in prose must not trigger the predicate.
"""

TEMPLATE = """# ADR-NNN: [Title]

- **Authored-by:** human | agent (<model id>)
- **Reviewed-by:** [named human(s) who read the full text before acceptance]

## Context
"""


@pytest.mark.parametrize(
    "text,expected",
    [
        (HUMAN_AUTHORED, "Authored-by: human"),
        (AGENT_REVIEWED, "Reviewed-by: Janusz Skonieczny"),
        (AGENT_UNREVIEWED, None),
    ],
)
def test_human_weight_reason(text: str, expected: str | None) -> None:
    assert human_weight_reason(text=text) == expected


def test_template_header_is_flagged_so_is_adr_must_gate_it() -> None:
    # The template's `Authored-by: human | agent` header starts with "human"
    # and so satisfies the predicate; TEMPLATE.md is kept out of the drift
    # guard and the sampler by is_adr (the NNN-*.md filename filter), not by
    # this predicate. Documented so the filename gate is not "optimized away".
    assert human_weight_reason(text=TEMPLATE) is not None
    assert is_adr(path=Path("docs/adr/TEMPLATE.md")) is False


def test_human_weight_reason_reports_both_markers() -> None:
    text = "- **Authored-by:** human\n- **Reviewed-by:** Ada\n\n## Context\n"
    assert human_weight_reason(text=text) == "Authored-by: human / Reviewed-by: Ada"


@pytest.mark.parametrize(
    "reviewed_by,expected",
    [
        (None, False),
        # Every _EMPTY_MARKERS member, so an accidental future removal
        # from the constant is caught by the test.
        ("", False),
        ("—", False),
        ("-", False),
        ("–", False),  # noqa: RUF001 — en dash is a deliberate marker variant
        ("none", False),
        ("n/a", False),
        ("tbd", False),
        ("todo", False),
        ("brak", False),
        ("nikt", False),
        ("do uzupełnienia", False),
        ("BRAK", False),
        ("[named human(s) who read ...]", False),
        ("[nazwani człowiek/ludzie, którzy przeczytali cały tekst]", False),
        ("Janusz Skonieczny", True),
    ],
)
def test_is_reviewed(reviewed_by: str | None, expected: bool) -> None:
    assert is_reviewed(reviewed_by=reviewed_by) is expected


@pytest.mark.parametrize(
    "authored_by,expected",
    [
        (None, False),
        ("agent (claude-opus-4-8)", False),
        ("human", True),
        ("Human (workshop)", True),
    ],
)
def test_is_human_authored(authored_by: str | None, expected: bool) -> None:
    assert is_human_authored(authored_by=authored_by) is expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("100-decision-provenance.md", True),
        ("TEMPLATE.md", False),
        ("notes.md", False),
    ],
)
def test_is_adr(name: str, expected: bool) -> None:
    assert is_adr(path=Path("docs/adr") / name) is expected
