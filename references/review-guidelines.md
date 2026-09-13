# Claude Code Review Guidelines

Review **workflow** rules — how to conduct reviews, manage threads,
write summaries, and interact with authors. For **what to check**
in code, see the domain-specific agent specs in `.claude/agents/`.

## Approval State Guard

Before requesting review (or re-review) on a PR, check the PR's
current review state to avoid pinging reviewers on already-approved
PRs.

**Decision rule:**

1. Fetch state via `gh pr view N --json reviewDecision,reviews,headRefOid`.
2. If `reviewDecision == "APPROVED"` AND the latest review's
   `commit.oid` matches `headRefOid` → PR is approved on the
   current HEAD. **Short-circuit** the request and suggest merging
   instead.
3. If `reviewDecision == "APPROVED"` but newer commits have
   invalidated the approval (review SHA != HEAD SHA) →
   proceed with re-request, but **filter out** any reviewer whose
   most recent review on the current HEAD is already `APPROVED`.
4. Otherwise (`CHANGES_REQUESTED`, `REVIEW_REQUIRED`, or `null`) →
   proceed normally.

**Why?** Re-pings on approved PRs create reviewer fatigue and
churn — the next action is merge, not another review cycle.

## Review Workflow

1. Check existing review comments to avoid duplicating feedback
2. Check for previous summary comments (`gh pr view {PR_NUMBER} --json comments`)
   to identify obsolete summaries
3. Analyze current diff (`gh pr diff`)
4. For each previous Claude Code Review thread:
   - Fixed/removed → reply "Addressed" (do NOT resolve — leave for human)
   - Persists in unchanged code → reply "Still applies"; do NOT duplicate
   - Changed but issue remains → reply with update
5. Use inline comment tools ONLY for NEW issues
6. Hide obsolete review summaries before posting the new one:
   a. Query review threads via GitHub's review-thread API (never
      hand-rolled GraphQL string-splicing) — for each thread, check
      `isResolved` and group by `pullRequestReview.databaseId`
   b. For each previous Claude review with a non-empty body:
      - ALL threads `isResolved: true` → minimize as OUTDATED
      - ANY thread unresolved → leave visible
      - Review has NO inline threads (summary-only) → minimize
   c. Minimize: `minimizeComment(input: {subjectId: "<node_id>",
      classifier: OUTDATED})`
   d. Skip the current review cycle's own review
   e. Thread resolution must come from the human supervisor —
      the reviewer MUST NOT resolve threads to trigger this gate
7. Create ONE summary review comment (`gh pr review --comment`) with:
   - High-level observations and quality assessment
   - Cross-cutting concerns not tied to specific lines
   - Acknowledgment of addressed issues
   - DO NOT repeat inline comment content
8. Use COMMENT status (not REQUEST_CHANGES or APPROVE)
9. If inline comments were posted, convert PR to draft:
   `gh pr ready --undo $PR_NUMBER`
   - Prevents noisy re-reviews on fixup commits
   - Author marks "Ready for review" when fixes are complete
   - Do NOT convert if the review was clean (no inline comments)

## Scope & Noise

- Focus on lines changed in the PR diff
- Issues in **unchanged** code → "pre-existing, out of scope"
  (informational, not blocking)
- NEVER repeat feedback from previous review cycles
- Group related issues by topic; keep summary brief
- Each inline comment references file path and line number
- **Bootstrapping exception** — when a PR introduces a new rule,
  the PR itself may violate it because the rule wasn't enforced
  when the PR was submitted. Flag as informational, not blocking.

## Context-Aware Review Depth

| Context        | Focus                                    | Avoid                                |
|----------------|------------------------------------------|--------------------------------------|
| Production     | All standards                            | Bikeshedding                         |
| POC/Test       | Does it work? Security. Broken logic.    | YAGNI, error handling, edge cases    |
| Refactor       | Behavior preservation                    | New features, scope creep            |
| Infrastructure | Behavioral changes, help text accuracy   | Questioning stated design intent     |

### POC Detection

Check PR title (🧪, "POC", "test", "demo"), file paths (`test`,
`poc`), and description ("temporary", "exploratory"). If POC:
- Start summary with "Reviewing as POC/test code with relaxed standards"
- Review only: bugs, security, integration issues

### Author Design Clarifications

When an author explains flagged behavior is intentional:
1. Verify PR title/body supports the claim
2. Acknowledge and close the thread
3. Never re-raise in subsequent cycles
4. Never require re-explanation after force-pushes

## Summary Comment Strategy

- ONE summary per review cycle (not per commit)
- **Only post if** there are new issues or material changes to review
- **If there are no new issues, post NO review at all.** An empty-body
  COMMENTED review adds noise without value.
- After fixes: post ONE brief acknowledgment, not per-file comments
- After 3+ summaries saying "looks good": do NOT post another

### Re-Review Summary Structure

```markdown
## Review Summary (Round N)

### Addressed since last review
- [list items that were fixed]

### Remaining issues
- [only genuinely new or previously unfixed items]
```

Round numbering tracks review invocations, not author fix commits.

## Context Rot Mitigation

### Before Each Re-Review

1. Re-read PR description; don't rely on memory
2. Diff against previous review state; focus on what changed
3. Verify resolved threads are actually fixed
4. Read ALL author replies; build a "rejected suggestions" list

### During Re-Review

5. No zombie comments — don't re-raise intentionally rejected issues
6. Batch related feedback into ONE comment with all locations
7. Acknowledge progress explicitly
8. After 3 rounds: focus on correctness only (bugs, security)
9. Read actual file at HEAD, not diff context (force-pushes shift lines)

## Multi-Commit Review Awareness

1. First review: flag issues in current code
2. After new commits: check if previous issues are now fixed
3. Acknowledge fixes; only flag new or persistent issues
4. Check existing threads before creating new ones

## Code Suggestions Format

Use GitHub suggestion syntax for committable fixes:

```suggestion
fixed code here
```

- Single-line: comment on line N
- Multi-line: set start_line=N, line=M
- Add explanation before the suggestion block
- Use for straightforward fixes; describe approach for complex changes
- Do NOT use suggestion blocks for non-code changes (permissions,
  file renames, `git mv`). Use plain text instructions instead.

## Review Comment Format & JTBD Variants

Structure findings as **[REQUIRED/RECOMMENDED]** — [title], explanation,
rule reference, fix. For JTBD grammar: name a concrete beneficiary in the
outcome clause — "**so the integrator can** reconcile invoice status" (per
`git-jtbd.md`). When the actor and beneficiary are the same role, repeating
it is fine; a differing beneficiary must be named explicitly. Faceless
"**so the user can**" or first-person "**so I can**" phrasing is a
RECOMMENDED fix toward a concrete role.

## Positive Validation

When a PR demonstrates excellent practices:
- Call out strengths with file:line references
- Not every PR needs change requests
- One positive summary per resolved cycle

## Avoid Valueless Suggestions

- NEVER suggest code identical to the original
- NEVER suggest formatting changes (ruff/linters handle this)
- VERIFY character-by-character before suggesting
- Only suggest: bugs, security, architecture, performance, logic,
  naming
- When in doubt: skip it
