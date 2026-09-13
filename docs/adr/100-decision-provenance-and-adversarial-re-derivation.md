# ADR-100: Decision Provenance and Adversarial Re-Derivation

- **Date:** 2026-09-13
- **Status:** Proposed
- **Deciders:** Janusz Skonieczny
- **Authored-by:** agent (claude-opus-5)
- **Reviewed-by:** —
- **Sources:** prior practice on AI-authored codebases; the procedure is
  implemented here by `bin/check-adr-numbers.py`,
  `bin/check-adr-drift.py`, `bin/decision_provenance.py` and
  `bin/rederive-sample.py`

## Context

This codebase is written almost entirely by AI agents committing under a
human operator's git identity. Two consequences follow, both of which
have been observed in practice on codebases of this shape:

1. **Authorship is structurally unanswerable after the fact.** The
   `Deciders:` field is written by whoever authors the file, so it cannot
   distinguish "a human decided this" from "an agent filled in the
   human's name". Provenance recorded at decision time is cheap;
   reconstructing it later is impossible.
2. **Review runs one way only.** An AI decision gets a human reviewer. A
   human decision gets nothing — even when the ground shifts under it.
   Nothing re-examines whether the implementation still honours it, in
   either direction.

Because nearly every commit here is agent-authored, marking AI authorship
is near-universal noise. The load-bearing goal is not authorship
bookkeeping but **protecting the subset of human-weighted decisions from
silent AI reversal**.

## Decision

Adopt a decision-provenance procedure with five parts. Authorship is
recorded at decision time, and *both* directions of decision-making are
subject to review.

1. **Provenance header fields** (see [TEMPLATE.md](TEMPLATE.md)):
   - `Authored-by:` — `human` or `agent (<model id>)`. Unmarked means
     AI-made, which is the default here.
   - `Reviewed-by:` — named human(s) who read the full text before
     acceptance. Empty means **unreviewed**; an unreviewed ADR may not
     claim `Accepted`.
   - `Sources:` — inspiration and pattern origins. Opaque references are
     allowed, so that imported assumptions are visible rather than
     reading as first-principles derivation.
   - `Deciders:` — one canonical string per person.

   `Authored-by: human` or a non-empty `Reviewed-by:` is what marks a
   decision as human-weighted, and therefore high-attention.

2. **Drift warning on human-weighted ADRs.** A PR that modifies an ADR
   carrying either marker raises a CI warning
   (`adr-provenance-drift.yml`). It is advisory, not blocking — the point
   is visibility, not a gate.

3. **Acceptance gate (Proposed → Accepted).** A human flips the status.
   The PR that does so must pass:
   - **(a) Number-collision scan** — no duplicate `docs/adr/NNN-*`
     number in the merged tree. Enforced by `adr-numbering.yml`.
   - **(b) Realization check** — every runtime-behaviour claim in the
     Decision section is verified against code, or explicitly marked
     "not yet wired, tracked in GH-XXXX".
   - **(c) Supersession backlinks** — older ADRs whose decisions this one
     changes get a pointer back.

4. **Periodic adversarial re-derivation.** Quarterly (or on dispatch), a
   capable model re-derives a sample of human-weighted decisions from
   current circumstances and files confirm / amend / supersede / drift
   recommendations, citing `path:line` evidence. Wired by
   `adr-rederivation.yml`; the result is a review bundle in a GitHub
   issue, never an automatic change.

5. **Status hygiene.** Use `Superseded` for fully-replaced decisions with
   a machine-checkable `Superseded-by:` pointer; use the dated-amendment
   style (a `> **Amendment (YYYY-MM-DD):**` block at the top) for partial
   supersession.

## Rationale

Making `Reviewed-by:` a precondition for `Accepted` turns "was merged"
back into "is current, vetted guidance" — the property an ADR corpus
loses once every entry drifts to a bare `Accepted`.

Adversarial re-derivation closes the asymmetry in part 2 of the Context.
It treats "a decision that changed under new circumstances" as something
to re-decide consciously, rather than by default of whoever last touched
the code.

## Consequences

**Positive:**
- Human-vs-AI authorship is answerable at decision time, not by archaeology.
- Unreviewed ADRs cannot masquerade as accepted guidance.
- Human decisions gain a re-examination path symmetric to AI review.

**Negative:**
- Adds header fields and CI checks to every ADR-touching PR.
- Adversarial re-derivation needs a periodic owner and an API budget;
  unscheduled, it lapses.
- `Reviewed-by:` gating `Accepted` slows solo-authored ADRs until a
  second human reads them.

## Related

- [TEMPLATE.md](TEMPLATE.md) — carries the provenance header fields
- `.github/workflows/adr-numbering.yml` — gate 3a
- `.github/workflows/adr-provenance-drift.yml` — part 2
- `.github/workflows/adr-rederivation.yml` — part 4
