"""Shared "human-weighted decision" predicate for ADR-100 provenance tooling.

Two symmetric halves of the ADR-100 provenance procedure consume this module
and must never disagree on what counts as *human-weighted*:

- the **drift** guard (`check-adr-drift.py`) — warns when a PR modifies
  a human-weighted ADR;
- the **re-derivation** sampler (`rederive-sample.py`) — draws
  a sample of human-weighted decisions to re-derive under current circumstances.

The ADR predicate (`human_weight_reason`) is header-only by design: a decision
is human-weighted when its ADR header carries `Authored-by: human` **or** a
non-empty `Reviewed-by:`. It never inspects git trailers (`Co-Authored-By:
Claude` is forbidden by CLAUDE.md and would be noise).
"""

from __future__ import annotations

import re
from pathlib import Path

ADR_FILENAME = re.compile(r"^\d{3}-.+\.md$")

# The header is the preamble before the first `## ` section. Restricting the
# field scan to it avoids matching the same field names quoted in the body
# (e.g. ADR-100 explains `Authored-by:` in prose).
_HEADER_END = re.compile(r"^## ", re.MULTILINE)
_AUTHORED_BY = re.compile(r"^-\s*\*\*Authored-by:\*\*\s*(?P<value>.*?)\s*$", re.MULTILINE)
_REVIEWED_BY = re.compile(r"^-\s*\*\*Reviewed-by:\*\*\s*(?P<value>.*?)\s*$", re.MULTILINE)

# Values that mean "no reviewer" — an empty `Reviewed-by:` field.
# The three dash variants are deliberate, not a typo: a human filling in
# `Reviewed-by:` may type a hyphen, an en dash or an em dash, and all three
# mean "nobody reviewed this". RUF001 flags exactly the ambiguity we need.
_EMPTY_MARKERS = {"", "—", "-", "–", "none", "n/a", "tbd", "todo"}  # noqa: RUF001


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
    # Unfilled template placeholder, e.g. "[named human(s) who read ...]".
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
