# Rule Index & Agent Routing

Path-aware routing table for `.claude/rules/` and `.claude/agents/`
in this repository.

## Directory Contract

- This file is the single source of truth in `.claude/rules/`.
- Full rule content lives in `references/*.md`.
- Agent triggers and checklists live in `.claude/agents/*.md`.

## File Patterns -> Agents -> References

| File Pattern | Primary Agent | Required References |
|---|---|---|
| `src/ksef_mcp/**/*.py` | `reviewer-generic`, `reviewer-security` | `references/review-checks-common.md` |
| `tests/**/*.py` | `reviewer-test-patterns`, `reviewer-security` | `references/review-checks-common.md` |
| `.github/workflows/**`, `pyproject.toml`, `bin/**` | `reviewer-infra` | `references/review-checks-common.md` |
| `docs/**`, `.claude/**/*.md`, `README.md`, `CLAUDE.md` | `reviewer-docs` | `references/review-checks-common.md` |

## Loading Strategy

| Location | When loaded | Content |
|----------|------------|---------|
| `CLAUDE.md` | Every session | Project conventions and stack summary |
| `.claude/rules/INDEX.md` | Every session | This routing table |
| `references/*.md` | On-demand, matched by file pattern above | Detailed git, review, JTBD guides |

## Cross-Cutting Checks

Always apply `references/review-checks-common.md`, including its
KSeF-Specific Concerns section (production-call safety, credential
handling, invoice XML handling, `ksef_live` test isolation).

## Reference Documents (`references/`)

| File | Topic | Scope |
|------|-------|-------|
| `git-commits.md` | Commit format, gitmoji, atomic commits | Mandatory for all commits |
| `git-pr.md` | PR format, grooming, review feedback | Mandatory for all PRs |
| `git-jtbd.md` | Job Story format, principles, examples | Mandatory for JTBD decisions |
| `review-guidelines.md` | Review workflow, threads, summaries | Mandatory for PR reviews |
| `review-checks-common.md` | False positives, verification, KSeF-specific concerns | Mandatory for code review agents |

## Agent Specs (`.claude/agents/`)

| File | Trigger | References |
|------|---------|------------|
| `reviewer-generic.md` | `src/ksef_mcp/**/*.py` | `references/review-checks-common.md` |
| `reviewer-security.md` | `src/ksef_mcp/**/*.py`, `tests/**/*.py` | `references/review-checks-common.md` |
| `reviewer-test-patterns.md` | `tests/**/*.py` | `references/review-checks-common.md` |
| `reviewer-infra.md` | `.github/workflows/**`, `pyproject.toml`, `bin/**` | `references/review-checks-common.md` |
| `reviewer-docs.md` | `docs/**`, `.claude/**/*.md`, `README.md`, `CLAUDE.md` | `references/review-checks-common.md` |

## Size Budgets

| File type | Max lines |
|-----------|-----------|
| Agent specs | 200 |
| Reference docs | 300 |
| `CLAUDE.md` | 120 |

These are guidelines, not hard gates — split a file when it becomes
hard to navigate, not purely on line count.
