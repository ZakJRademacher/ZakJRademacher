
## Working with Codex

A user-level `codex` MCP server (`~/.claude/mcp/codex-mcp.mjs`) wraps the OpenAI Codex CLI:
- `codex_review`: Codex reviews uncommitted changes (default), a diff against `base`, or a `commit`.
- `codex_task`: send Codex a brief. `mode: "read-only"` is for critique and research; `mode: "write"`
  lets it edit files and run commands. The result includes `session_id`; pass it back to continue
  the same Codex thread.

Codex's main value is an independent second opinion from a different model. Use it for review
and critique by default; delegate builds only when asked or when it clearly pays off.

### Default: second opinions (do these without being asked)

1. **Review before committing non-trivial changes.** That means new logic, data handling,
   anything touching totals, reconciliation or validation, and anything going public. Run
   `codex_review` with `instructions` stating what the change is meant to do. Fix real issues it finds.
   Skip it for docs, copy, formatting and small fixes.
2. **Critique hard-to-reverse decisions.** Before committing to a data model, architecture,
   library/tooling choice or public interface, send `codex_task` (`read-only`) a short brief:
   goal, constraints, options, your recommendation. Ask for a critique and anything you missed.
3. **Research where being wrong is costly.** Have Codex answer independently *before* you
   share your own conclusion, then compare the two.

Skip all of these when the user wants a quick answer.

### Opt-in: Claude plans, Codex builds, Claude reviews

Use only when the user asks for it, or suggest it (don't just do it) for large, clearly specified
work whose acceptance criteria are testable. Do small/medium changes, exploratory work and
debugging directly.

1. **Plan.** Turn the user's input into a concrete plan: goal, files, interfaces, edge cases,
   tests, acceptance criteria. Confirm it with the user.
2. **Build.** Send it to `codex_task` (`write`) as a self-contained brief; Codex doesn't see this
   conversation. Ask it to run the tests. For risky work, use a separate git worktree
   (`git worktree add ...`, pass that path as `cwd`).
3. **Review.** Check the diff and tests yourself rather than trusting Codex's summary. Send
   specific fixes back with the same `session_id`, or make small fixes directly.
4. **Report.** Tell the user what Codex built, what changed in review, and what's still open.
   Commit only after review passes. Don't also run `codex_review` on code Codex wrote.

### Keep context and tokens lean

Only Codex's final report (capped at ~6k chars) and a changed-file list come back; its
transcript stays out of this context. Keep it that way:

- **Briefs:** reference files by path rather than pasting code. End every brief with:
  *"Report in ≤200 words: findings or changes, test results, open questions. No code in the report."*
- **One task, one Codex session.** Reuse `session_id` only for follow-ups on the same task.
- **Review cheaply first.** Start with `git diff --stat` and the test run, then read diffs file by
  file (`git diff -- <path>`) where the logic matters. Skip generated, lockfile and formatting changes.
- **Fixes:** short, specific lists (file, problem, expected behaviour), not rewritten plans.
- **Long sessions:** once work is committed, summarise it in a line or two and move on.
  Suggest `/clear` or a new session between unrelated features.

### Rules

- Codex is a reviewer, not an authority. Verify its claims before acting on them.
- When you and Codex disagree on something material, tell the user both positions and your recommendation.
- Briefly mention when Codex was consulted and what changed because of it.
- Never put secrets in a brief.
