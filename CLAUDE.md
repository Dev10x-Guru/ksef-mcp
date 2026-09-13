# ksef-mcp

A Model Context Protocol (MCP) server for the Polish KSeF (Krajowy
System e-Faktur, the national e-invoicing system). Public repo,
licensed AGPL-3.0.

## Directory Layout

| Directory        | Purpose                                    |
|------------------|--------------------------------------------|
| `src/ksef_mcp/`  | Python package: MCP server, KSeF client, tools |
| `tests/`         | Test suite (mirrors `src/ksef_mcp/`)       |
| `bin/`           | Helper/CI scripts                          |
| `docs/`          | Project documentation, ADRs                |
| `references/`    | Shared docs (git, review, JTBD guides)      |
| `.claude/rules/` | Path-aware rule routing (`INDEX.md`)       |
| `.claude/agents/`| Domain-specific reviewer agents            |

## Stack

- Python, `requires-python >= 3.12`; CI matrix runs 3.12 and 3.13.
- Dependency management via `uv`; dev dependencies are a PEP 735
  `[dependency-groups] dev` group, not an extra.
- src-layout package: `src/ksef_mcp/`.
- Packaged to PyPI as `ksef-mcp`, console script
  `ksef-mcp = "ksef_mcp.server:main"` — so `uvx ksef-mcp` works.
- No Django, no GraphQL, no Celery, no frontend framework, no
  database/migrations, no monorepo `apps/` layout. Keep it that way;
  flag any PR that introduces one of these without discussion.

## MCP Server Notes

- `mcp` >= 2.2.0 **removed `FastMCP`**. Use
  `from mcp.server import MCPServer` — do not add or copy `FastMCP`
  examples into code, docs, or rules files.

## Development

```bash
uv sync --group dev       # install dependencies incl. dev tools
uv run pytest             # run the test suite with coverage
uvx ksef-mcp              # run the packaged server via uvx
```

Coverage gate: `pyproject.toml` sets `fail_under = 100`. New code must
not lower it; legacy code touched must leave coverage no worse.

## KSeF Safety Rules (non-negotiable)

- **Never call production KSeF** from tests, examples, or default
  configuration. `KSEF_ENV` must default to the test/demo environment.
- **Credentials are secrets.** `KSEF_TOKEN`, `KSEF_NIP`, and `KSEF_ENV`
  must never be hardcoded, logged, or written to any file committed to
  the repo or produced as a CI artifact.
- **Never log or persist invoice XML.** Invoice payloads (FA(2)/FA(3))
  contain taxpayer PII. Use synthetic fixtures in tests; redact invoice
  content from logs and error messages.
- **Network-touching tests carry `@pytest.mark.ksef_live`** and must be
  deselected from the default `uv run pytest` invocation (see the
  marker config in `pyproject.toml` / CI). A test that reaches a real
  KSeF endpoint without this marker is a bug, not a convenience.

## Code Style

- Type annotations on every definition; multi-line the signature when
  it has 3+ parameters.
- Named/keyword arguments in calls, not positional, once a call has
  more than a couple of arguments.
- Comments are a last resort — rename or restructure until the code
  explains itself; document *why*, never *what*.
- Raise custom exceptions close to the source with descriptive
  messages; validate input early and fail loud with context.

## Git & PR Conventions

- **Base branch**: `main` — there is no `develop` branch in this repo.
- **Branch naming**: `username/ISSUE-NUMBER/short-description`
  (worktree: `username/ISSUE-NUMBER/worktree-name/short-description`).
- **Commit format**: `<gitmoji> <ISSUE-NUMBER> <JTBD outcome>` —
  outcome-focused ("Enable X"), not implementation-focused ("Add X").
- **Job Story voice** (REQUIRED): third-person domain actor —
  "**[actor] wants to** ... **so [beneficiary] can** ..." with a
  concrete role (accountant, integrator, taxpayer) — never
  first-person ("I want to") or a faceless "the user wants to". See
  `references/git-jtbd.md`.
- Every PR must link a GitHub issue at
  `https://github.com/Dev10x-Guru/ksef-mcp/issues`. If none exists,
  use `Fixes: none — self-motivated refactor`.
- See `references/git-commits.md`, `references/git-pr.md`,
  `references/git-jtbd.md` for full detail.

## Code Review

Domain-routed reviewer agents live in `.claude/agents/`. See
`.claude/rules/INDEX.md` for the file-pattern routing table and
`references/review-guidelines.md` / `references/review-checks-common.md`
for workflow and cross-cutting checks.
