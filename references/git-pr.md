# Pull Request & Branch Grooming Guidelines

Standards for pull requests and branch grooming in this repository.

## Branch Grooming

Restructure commit history to create atomic, well-organized commits.

### When Grooming is Acceptable

- ✅ Before creating a PR
- ✅ While PR is in **draft** status
- ✅ After CI feedback (before human review starts)
- ✅ When CI blocks merge due to fixup commits

### When Grooming is Discouraged

- ❌ After a human reviewer has started reviewing

*Why?* Rewriting history after human review started creates noise and
confusion. Reviewers lose context, and GitHub shows "force-pushed"
which hides the diff of what changed.

### Strategies

**A. Fixup + Autosquash** (small targeted fixes):
```bash
git commit --fixup=<target-sha>
git rebase -i --autosquash $(git merge-base main HEAD)
```

**B. Soft Reset** (major reorganization):
```bash
git reset --soft $(git merge-base main HEAD)
git reset HEAD  # unstage
git add -p      # selectively stage
git commit -m "First logical change"
# repeat for each logical unit
```

**C. Interactive Rebase** (reorder/edit/split):
```bash
git rebase -i $(git merge-base main HEAD)
```

### Safety

- Create backup before complex rewrites: `git branch backup-before-rewrite`
- Use `--force-with-lease` not `--force` when pushing rewrites
- Coordinate with teammates before force-pushing shared branches

## Pull Request Guidelines

### Before Creating PR

1. Ensure all commits are atomic and well-organized
2. Squash any fixup commits
3. Run quality checks locally (`uv run pytest`, ruff, mypy if configured)
4. Verify branch is up to date with `main`
5. Rebase to linearize history — no merge commits before opening

### PR Title

Use the main commit's title line (gitmoji + issue ref + description).

**Important**: The gitmoji appears in the **commit message**, not in the
GitHub PR title field. GitHub's UI shows these separately — your main
commit's gitmoji will automatically appear in release notes and git log
regardless of how the PR title field is filled. The critical requirement
is that your commit contains the gitmoji; GitHub renders it in the PR UI.

### PR Body

The body should be **compact** to avoid cluttering Slack previews.

**Required elements** (in this order):
1. A JTBD Job Story as the **first paragraph** (1-3 lines, see `git-jtbd.md`)
2. `Fixes:` link — must be the **absolute last line** of the body:
   - `Fixes: https://github.com/Dev10x-Guru/ksef-mcp/issues/NUMBER`
     (for issue-tracked work)
   - `Fixes: none — self-motivated refactor` (for internal improvements,
     features, or experiments without a tracking issue)
   - Do NOT manually add `---`, blank lines, or separators after `Fixes:`

**Optional elements** (keep brief):
- Compact commit list with links (one line per commit)
- Critical context that reviewers need immediately

**Do not include in body**:
- Detailed summaries (put in first comment)
- Implementation checklists (put in first comment)
- Known limitations or TODOs (put in first comment)

### Examples

**VOICE REQUIREMENT**: Third-person domain-actor voice is mandatory — name a
concrete actor and beneficiary (`**the integrator wants to** … **so the
accountant can** …`). Never use first-person ("I want to") or a faceless "the
user wants to". See `references/git-jtbd.md` § Voice Requirement and
§ Choosing the Actor.

**WRONG** — Header before JTBD (breaks release notes parsing):
```markdown
## Summary

**When** reconciling invoices, **the accountant wants to** see live KSeF
status, **so the accountant can** catch rejections early.

[Details...]

Fixes: ...
```

**CORRECT** — JTBD as absolute first element:
```markdown
**When** reconciling invoices, **the accountant wants to** see live KSeF
status, **so the accountant can** catch rejections early.

[Details or commit list — optional...]

Fixes: ...
```

### Proper Format

```markdown
**When** an MCP client needs to check invoice status without a browser,
**the integrator wants to** query KSeF through a tool call, **so the
accountant can** confirm submission without leaving their chat client.

[`b3a015a`](REPO_URL/commit/HASH) ✨ 42 Enable invoice status lookup
[`fec4999`](REPO_URL/commit/HASH) 📝 42 Document the new tool

Fixes: https://github.com/Dev10x-Guru/ksef-mcp/issues/42
```

*Why?* The JTBD Job Story must be the first paragraph because the
release notes process parses PR descriptions by position.

*Bootstrapping exception:* A PR that introduces a new PR body
requirement may not follow that requirement itself — the rule
wasn't enforced when the PR was submitted.

### If Review Issues Are Found

If Claude finds code or metadata issues during review, your PR will be
automatically converted to **draft** status. This prevents the merge button
from becoming available while issues remain unfixed.

After fixing all flagged issues, click **"Ready for review"** on the
PR page to re-trigger the review workflows and allow merge once checks pass.

### PR First Comment (Summary + Checklist)

Detailed context for reviewers without bloating the Slack preview.

```markdown
### Summary

- Added the get_invoice_status MCP tool
- Wired it to the KSeF test-environment client
```

### PR Checklist

- [ ] Self-reviewed the diff
- [ ] Updated documentation if needed
- [ ] No fixup commits remaining

## Handling Review Feedback

1. Create fixup commits for each review comment
2. Reference the comment in fixup commit body
3. Reply to comment with commit SHA
4. Before final push, squash all fixups:
   ```bash
   git rebase -i --autosquash $(git merge-base main HEAD)
   git push --force-with-lease
   ```

For commit format and branch naming, see `git-commits.md`.
