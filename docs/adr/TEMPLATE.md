# ADR-NNN: [Title]

- **Date:** YYYY-MM-DD
- **Status:** Proposed | Accepted | Deprecated | Superseded
- **Deciders:** [names]
- **Authored-by:** human | agent (<model id>)
- **Reviewed-by:** [named human(s) who read the full text before acceptance; empty = unreviewed]
- **Sources:** [inspiration / pattern origins; opaque references allowed]
- **Supersedes:** [reference to prior ADR this one fully replaces, if applicable]
- **Superseded-by:** [reference to the ADR that replaces this one, if applicable]
- **Refines:** [reference to an ADR this one elaborates without replacing, if applicable]
- **Depends-on:** [reference to an ADR whose mechanism this one builds on, if applicable]

> **Relationship & lifecycle fields.** Set `Superseded-by:` together with
> `Status: Superseded` (or `Deprecated`) when a later ADR fully replaces
> this decision. When only *part* of a decision changes, keep
> `Status: Accepted`, record the superseding ADR in `Superseded-by:` with
> an explicit scope note, and add a dated amendment blockquote directly
> under this header block, e.g. `> **Amendment (YYYY-MM-DD):** [what
> changed and why].`
> `Refines:` and `Depends-on:` capture non-superseding relationships so a
> graph builder sees every edge, not only `Supersedes:` ones. Drop any
> relationship field that does not apply.

> **Provenance fields.** `Authored-by:` distinguishes human from agent
> decisions at decision time; `Reviewed-by:` names the human(s) who read
> the full text before acceptance — an empty value means unreviewed, and
> an unreviewed ADR may not claim `Accepted`. `Sources:` records
> inspiration/pattern origins (opaque references allowed). See
> [ADR-100](100-decision-provenance-and-adversarial-re-derivation.md).

## Context

[What problem or need motivated this decision? What constraints
exist? Reference prior ADRs that provide context.]

## Decision

[What was decided? Include code examples or configuration snippets
where they clarify the choice.]

### Why [chosen option] over alternatives?

| Tool / Approach | Pros | Cons |
|-----------------|------|------|
| **Chosen** | ... | ... |
| Alternative A | ... | ... |
| Alternative B | ... | ... |

[Delete the comparison table if no alternatives were considered.]

## Rationale

[Why this approach? What trade-offs were accepted?]

## Consequences

**Positive:**
- [benefit 1]
- [benefit 2]

**Negative:**
- [trade-off 1]
- [trade-off 2]

## Related

- [ADR-NNN](NNN-slug.md) — [relationship description]
- [PR or issue link] — [context]
