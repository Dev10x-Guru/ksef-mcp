# Review Checks — Cross-Cutting Concerns

Universal review checks regardless of domain-specific agent. For
workflow rules, see `review-guidelines.md`.

## Enforcement Levels

- **CRITICAL/WARNING** or **REQUIRED**: Enforced by CI/merge protection or code review; blocks merge
- **INFO** or **RECOMMENDED**: Advisory only; does not block merge
- Do not flag as REQUIRED unless enforcement mechanism exists (CI/merge protection/code review gate)

## False Positive Prevention Gate

Before posting **any** inline comment:

0. **Diff scope** — is this file changed in the current PR diff?
   Run `gh pr diff --name-only` to confirm. If not in diff, mark
   pre-existing and skip.
1. Does this violate a documented CLAUDE.md rule? (No rule = preference)
2. Does this contradict an established codebase pattern? (5+ uses)
3. Does referenced documentation file exist? (Verify with Read tool)
4. Quality improvement or just preference?

**If any answer fails, DO NOT post.**

## Code Verification Protocol

1. Read actual code file — never rely on diff snippets alone
2. Verify exact line numbers and values
3. Check for fixes in later commits
4. Quote exact code when making claims

## Known False-Positive Traps

Before raising any of these, **verify actual code**:

1. **Formatting already correct**: read current code, not diff context
2. **Code that no longer exists**: verify line exists after force-push
3. **Return type already present**: read the actual signature
4. **YAGNI**: accept author's "defer" judgment
5. **Intentional behavior removal**: when PR title/ticket states
   removal is intentional, external bots flagging it as a bug are
   false positives
6. **Re-raise after side-effect**: `except E: side_effect(); raise`
   is NOT swallowing — caller still receives the error
7. **Shell script style**: different scripts use different shells
   (bash, sh) — check the shebang before flagging syntax
8. **`/pull/new/` commit links**: pre-creation artifacts generated before the
   PR number is assigned — flag as RECOMMENDED to update to
   `/pull/<number>/commits/<sha>`, not REQUIRED.
9. **Ticket-ID when self-motivated** — if PR body contains
   `Fixes: none — self-motivated`, do not flag missing ticket ID in
   PR title or commit messages. No issue exists to reference.
10. **Branch name convention** — flag branch name violations
    INFORMATIONAL in Round 1 only. Do not re-raise in subsequent rounds
    as the branch name is immutable while the PR is open.
11. **RECOMMENDED items after author acknowledgment** — once an author
    has acknowledged a RECOMMENDED suggestion but not acted on it, do
    not re-raise it in later rounds. Only REQUIRED issues block merge.
12. **PR body header position** — when a PR body contains Markdown headers
    (e.g., `## Summary`, `## Details`), verify the JTBD Job Story appears
    BEFORE all headers. A header before the JTBD breaks release notes parsing.
13. **JTBD voice violations** — when a PR body, commit message, or issue
    title contains a Job Story using first-person ("I want to") or a
    faceless actor ("the user wants to"), flag as REQUIRED. Third-person
    domain-actor voice with a concrete role and beneficiary is required.
    See `references/git-jtbd.md` § Voice Requirement and § Choosing the Actor.

## Architecture Checklist

Structural checks for new or substantially modified files. These
catch violations that surface-level bug hunting misses.

1. **Layering** — new MCP tools/handlers in `src/ksef_mcp/` MUST
   delegate KSeF API calls and business logic to a dedicated client/
   service module, not inline the HTTP call inside the tool handler.
2. **Function size** — functions/methods exceeding 50 lines likely
   violate SRP. Flag as WARNING with extraction suggestion.
3. **DTO usage** — inline dicts with 4+ keys crossing module
   boundaries should be typed (pydantic models or dataclasses). Flag as INFO.
4. **Input validation** — manual parsing of MCP tool arguments without
   schema/pydantic validation is a WARNING. Validate early.
5. **God function detection** — a single function performing
   validation + business logic + I/O + response formatting
   is a structural violation regardless of line count.

**When to apply:** On every PR that adds or significantly modifies
MCP tool/server code. Skip for docs-only, config-only, or test-only PRs.

## Parameter Change Analysis

When parameters are added/removed/made optional:
- Grep **all** call sites (not just the diff)
- Check if optional serves backward compatibility
- When suggesting required: list all call sites to confirm

## Dead Code Detection

For new classes/functions/constants in the PR:
- Grep for imports and references outside the definition file
- If no references found, flag as potential dead code
- Exclude test classes, abstract base classes, `__init__` exports

## Naming Convention Verification

Before suggesting naming changes:
1. Search codebase patterns first (5+ files = established convention)
2. Only suggest genuinely unclear/misleading names
3. Do NOT flag: established patterns, version suffixes, domain prefixes

## Documentation File & Command Verification

When docs reference CLI commands, files, or directories (e.g., install
instructions, code examples):
- **Commands**: Verify they appear in `CLAUDE.md` Development section or
  are known `uv`/CLI built-ins
- **Files and directories**: Use Glob to verify they exist in the current
  commit (e.g., `src/ksef_mcp/server.py`)
- **Planned features**: If documenting future features not yet implemented,
  clearly mark as `[PLANNED]` or `[NOT YET IMPLEMENTED]`
- Unverified references in user-facing docs are WARNING severity

## Shell Anti-Patterns

- **Silent error swallowing**: `|| true` on setup steps and `2>/dev/null`
  on commands whose output drives branching hide failures; replace with
  a fallback action or remove the redirect.
- **Implicit defaults driving branching** (e.g., `jq '.field // ""'`)
  should validate explicitly instead: `[[ -z "$VAR" ]] && { error; exit 1; }`.

## Python Linting Checks

- **f-strings without expressions**: `f"static string"` with no `{}`
  placeholders is ruff F541. Flag and suggest removing the `f` prefix.

## KSeF-Specific Concerns

These are domain-critical for this project — treat violations as
REQUIRED/CRITICAL, not style preferences:

1. **Never call production KSeF from tests or examples** — any test,
   script, or doc snippet that could reach the production KSeF
   environment (`KSEF_ENV=prod` or equivalent) instead of the test/demo
   environment is CRITICAL. Verify the environment is pinned to test/demo
   by default.
2. **Credentials are secrets** — `KSEF_TOKEN`, `KSEF_NIP`, `KSEF_ENV`
   (and any other KSeF auth material) must never be hardcoded, logged,
   committed in fixtures, or printed in error messages. Flag any
   f-string or log call that interpolates these values directly.
3. **Never log or persist invoice XML** — invoice payloads (FA(2)/FA(3)
   XML) contain taxpayer PII and business data. Flag any code path that
   writes raw invoice XML to logs, test fixtures committed to the repo,
   CI artifacts, or debug output. Redact or use synthetic fixtures instead.
4. **Network-touching tests need a `ksef_live` marker** — any test that
   makes a real network call to KSeF (even the test environment) must be
   marked (e.g., `@pytest.mark.ksef_live`) and must be excluded from the
   default `pytest` run (deselected via `pyproject.toml` addopts or CI
   config). A network test running unmarked in the default suite is a
   WARNING; one that could hit production is CRITICAL.
