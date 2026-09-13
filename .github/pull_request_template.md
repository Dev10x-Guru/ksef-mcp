<!--
  Title convention: <gitmoji> <outcome>
  e.g. ✨ Enable invoice lookup through the KSeF test environment
-->

## Job Story

<!-- Third person, with a concrete role — accountant, integrator,
     taxpayer. Never "I want to", never "the user".
     When <situation>, <actor> wants to <motivation>, so <beneficiary>
     can <expected outcome>. -->

## Summary

<!-- What changed and why. Link the issue(s) this closes. -->

Fixes:

## KSeF safety gate

Sending an invoice to production KSeF creates a document with tax
consequences and **cannot be undone**. Anything that can reach the KSeF
API has to justify itself here.

- [ ] This PR does **not** add or change code that calls the KSeF API,
      **or** every new call is confined to the test environment.
- [ ] No test added here reaches a live KSeF endpoint by default —
      network-touching tests carry the `ksef_live` marker and stay
      deselected in the default suite.
- [ ] No credential, token or NIP is committed, logged, or written into
      a CI artifact; no invoice XML is uploaded as a build artifact.

> Justification (required if any box is unchecked):

## Testing

- [ ] Tests pass (`uv run pytest`) with coverage at 100%
- [ ] `bin/` tooling tests pass, if touched
      (`uv run --no-project --with pytest pytest bin/`)
- [ ] Manual verification, if user-facing

## Rollback

<!-- How to revert if this breaks. "Revert the PR" is a valid answer only
     if the change is self-contained. A released version cannot be
     unpublished from PyPI — say how to yank and re-release instead. -->
