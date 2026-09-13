#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Warn when a PR modifies a *human-weighted* ADR (ADR-100 drift guard).

This repo is developed almost entirely by AI, so marking AI authorship is
near-universal noise. The load-bearing risk is the inverse: a decision a
human actually weighed in on being silently reversed or narrowed by an
agent and never revisited.

A decision is *human-weighted* when its ADR header carries either
`Authored-by: human` or a non-empty `Reviewed-by:`. When a PR modifies such
an ADR, this guard emits a GitHub Actions warning so a human reviews the
change rather than letting it slip through. It is **advisory** — it always
exits 0 and never blocks the merge.

The check is header-only by design: it never inspects git trailers
(`Co-Authored-By: Claude` is forbidden by CLAUDE.md and would be noise).

The "human-weighted" predicate itself lives in `decision_provenance` so the
re-derivation sampler shares one definition; this script is a thin caller
that maps flagged ADRs to GitHub Actions warnings.

Usage: pass the changed ADR paths as arguments. The CI workflow computes
the modified/renamed `docs/adr/*.md` set (`git diff --diff-filter=MR`) and
passes it here, so this script stays a pure function of its inputs and does
not touch git itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The shared predicate lives beside this script; `uv run --script` and a plain
# `python bin/check-adr-drift.py` both put bin/ on sys.path[0], but insert it
# explicitly so the import is robust regardless of the invocation's cwd.
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
