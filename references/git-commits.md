# Git Commit & Branch Guidelines

Standards for commits and branches in this repository.

## Branch Targeting Policy

- **All PRs**: Always target `main` — this project has no `develop`
  branch; `main` is the single trunk.
- **CLI rule**: Always pass `--base main` to `gh pr create`.

*Why?* All work targets `main` so quality gates (CI, code review) run
against the same branch that ships.

## Branch Naming Convention

Format: `username/ISSUE-NUMBER/short-description`
Worktree: `username/ISSUE-NUMBER/worktree-name/short-description`

Examples:
- `janusz/42/add-invoice-status-tool`
- `maria/57/fix-token-refresh`
- `janusz/63/ksef-mcp-3/port-github-actions-ci` (worktree)

## Commit Message Format

### Structure

```
<gitmoji> <ISSUE-NUMBER> <short description>

<problem explanation - what was wrong and why it needed fixing>

Solution:
- <change 1>
- <change 2>

Fixes: <ISSUE-NUMBER>
```

### Title Writing Principle

Focus on what the change **enables**, not what it changes in code.
See `git-jtbd.md` for comprehensive Job Story format and examples.

**User-facing features** (required):
- Bad: `Add get_invoice_status MCP tool` (implementation)
- Good: `Enable checking invoice status from an MCP client` (outcome)

**Meta-work** (docs, tooling — preferred but not required):
- Acceptable: `Add missing ruff workflow`
- Also good: `Prevent lint regressions in CI`

### Rules

1. **Title line**: Max 72 characters (gitmoji + space + issue ref + space + description)
2. **Body lines**: Max 72 characters each
3. **Gitmoji**: Use emoji character, not `:code:` format
4. **No co-authoring**: Never add "Co-Authored-By: Claude" footer

### Gitmoji Reference

| Emoji | Code | Use for |
|-------|------|---------|
| ✅ | `:white_check_mark:` | Adding/fixing tests |
| 🐛 | `:bug:` | Bug fixes |
| ♻️ | `:recycle:` | Refactoring |
| ✨ | `:sparkles:` | New features |
| 📝 | `:memo:` | Documentation |
| 🔒 | `:lock:` | Security fixes |
| ⚡ | `:zap:` | Performance |
| 🔧 | `:wrench:` | Configuration |
| 🔖 | `:bookmark:` | Version bumps |
| 🩹 | `:adhesive_bandage:` | Simple/minor fixes |
| 🔥 | `:fire:` | Removing code/files |

### Example Commit

```
🐛 42 Fix token refresh race under concurrent MCP calls

get_access_token() could issue two refresh requests when two tool
calls raced past the expiry check, invalidating the first token.

Solution:
- Guard refresh with an asyncio lock
- Add regression test for concurrent refresh

Fixes: 42
```

## Atomic Commits

Each commit should represent **one logical change**:

- ✅ One feature, one commit
- ✅ One bug fix, one commit
- ✅ One refactoring, one commit
- ❌ Multiple unrelated changes in one commit
- ❌ Half-finished work in a commit

### Commit Ordering

When a feature touches multiple layers, commit in dependency order:

1. Utilities/helpers (no dependencies)
2. Configuration and infrastructure
3. Core implementation
4. Documentation and rules
5. Tests

For PR and branch grooming guidelines, see `git-pr.md`.
