# JTBD Job Story Guidelines

Rules for writing Job Stories used in PR titles, PR descriptions,
commit messages, and issue tickets.

> **Scope**: This format governs Job Stories in commits and PR descriptions.
> It also applies to issue titles and tickets.
> **Critical dependency**: Release notes parsing requires the precise JTBD
> structured format — `**When** … **[actor] wants to** … **so [beneficiary]
> can** …`. Dropping the `**[actor] wants to**` / `**so [beneficiary] can**`
> markers breaks automated release notes collection.

## Format

```
**When** [situation], **[actor] wants to** [motivation], **so [beneficiary] can** [expected outcome].
```

One sentence. No bullet points. No implementation details.

Name a concrete domain **actor** (who has the need) and a concrete
**beneficiary** (who gains from the outcome). They are often the same
role — then name that role in both slots ("**the accountant wants to** …
**so the accountant can** …"). When they differ, name both explicitly.
See § Choosing the Actor for how to pick the role.

## Voice Requirement

Job Stories must use **third-person, domain-actor voice**: name the
actor and beneficiary as concrete roles. First-person ("I want to") and
faceless ("the user wants to") phrasing are both wrong.

| Form | Example | Status |
|------|---------|--------|
| ✅ Third-person actor | **the integrator wants to** query invoice status | REQUIRED |
| ✅ Explicit beneficiary | **so the accountant can** reconcile submissions | REQUIRED |
| ❌ First-person | **I want to** query invoice status | WRONG (legacy) |
| ❌ Faceless actor | **the user wants to** query invoice status | WRONG (no role) |

The difference: name the role ("the integrator wants to"), never
"I" and never a generic "user"/"customer". When the outcome benefits a
different role, say so explicitly: `**so [role/system] can** ...`.

## Choosing the Actor

- **Actors want outcomes, not work.** Nobody wants to *work* — people
  want **outcomes**. The actor rarely wants to *do* anything; in the
  ideal case they want the outcome to happen with zero effort on their
  part. If the actor would be happiest doing nothing, name the outcome
  they want to *happen* — then check whether the true beneficiary is a
  *different* role than the one performing the action. When it is, the
  performer is a **mechanism**, and the beneficiary owns the job.
- Name a concrete domain role, never a faceless "user" or "customer".
  In ksef-mcp the common actors are **accountant**, **integrator**
  (the developer/system wiring an MCP client to KSeF), and
  **taxpayer/business owner**.
- This set is open — discover new actors as the domain grows.
- Internal maintainers are rarely the actor. When one genuinely is, make
  the benefit explicit (reduces cost, increases reliability). Developer
  tooling is the honest exception — a Job Story whose actor is a
  maintainer is legitimate when the change is tooling (CI, packaging,
  release process).

## Key Principles

### 1. No Personas — Focus on Situation

Job stories replace "As a [persona]..." with the **situation** — the
context that creates the need.

### 2. Situation Over Implementation

The "When" clause describes the real-world context, not UI interactions.

- Good: "When an invoice fails KSeF validation"
- Bad:  "When calling the validate_invoice tool"

### 3. Motivation Reveals Anxiety

The "[actor] wants to" clause captures what the actor is trying to
accomplish.

- Good: "the accountant wants to see the rejection reason immediately"
- Bad:  "the accountant wants a new MCP tool"

### 4. Expected Outcome Shows Value

The "so [beneficiary] can" clause describes the measurable benefit or
the problem that goes away. It should contrast with the current broken
state.

- Good: "so the taxpayer can correct and resubmit before the deadline"
- Bad:  "so the system has validation"

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|---|---|---|
| Technical language | Not understandable by stakeholders | Use business/domain language |
| Solution-focused "When" | Prescribes implementation | Describe the real-world trigger |
| CLI/command-invocation "When" | "When running `uvx ksef-mcp`" prescribes the tool | Describe the real-world trigger: "When an invoice submission times out" |
| Vague outcome | Not testable | Be specific about what improves |
| No contrast with current state | Unclear why it matters | Show what's wrong today |
| Faceless actor ("the user wants to") | No concrete role — untraceable to the domain | Name the role: "the accountant wants to" (see § Choosing the Actor) |
| Solution-focused "wants to" | "the integrator wants to call a new endpoint" names the implementation, not the need | Describe the motivation: "the integrator wants to detect KSeF outages without polling manually" |
| UI-verb motivation ("wants to see/view/manage X") | Describes operating the feature, not the outcome | Name the end state: "wants to be told when a submission fails", not "wants to view the status page" |

## Title Writing Principle

Shift the perspective from what changed in the code to what it
enables for the actor. The "so [beneficiary] can" clause captures the
outcome.

### Common patterns

| Change type | Bad (implementation) | Good (outcome) |
|---|---|---|
| New MCP tool | `Add get_invoice_status tool` | `Enable checking invoice status from an MCP client` |
| Bug fix | `Fix token refresh race` | `Prevent duplicate token refresh under concurrent calls` |
| CI | `Add ruff workflow` | `Catch lint errors before merge` |
| Refactor | `Extract KSeF client from server.py` | `Enable reusing the KSeF client across tools` |
| Docs | `Add review guidelines rule file` | `Standardize code review workflow` |
| Release | `Bump version to 0.2.0` | `Release invoice-status and token-refresh fixes` |

### The "rename test"

If your title reads like a git diff summary, rewrite it. Ask:
*"What can the actor do now that they couldn't before?"* — that
answer is your title.

## Examples

### New MCP Tool
**When** an MCP client needs invoice status without opening the KSeF
web portal, **the integrator wants to** expose a `get_invoice_status`
tool, **so the accountant can** confirm submission outcome inside their
chat client.

### Bug Fix
**When** two tool calls race past the token-expiry check, **the
maintainer wants to** serialize token refresh with a lock, **so the
integrator can** rely on the client without intermittent 401s.

### Documentation
**When** onboarding a new contributor, **the maintainer wants to** have
clear commit and PR conventions, **so contributors can** follow them
without reading every prior PR.

### Release
**When** a batch of fixes is ready, **the maintainer wants to** publish
a semver release to PyPI, **so integrators can** pin `ksef-mcp` to a
stable version via `uvx`.

## See Also

`.claude/rules/INDEX.md` documents where these references are loaded from.
If a skill's own reference doc diverges from this format, this file is
authoritative for PR/commit JTBD text.
