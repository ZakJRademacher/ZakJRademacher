# CLAUDE.md

## Working with Codex (sub-agent)

This project has a `codex` MCP server (`.mcp.json` → `.claude/mcp/codex-mcp.mjs`) that wraps the
OpenAI Codex CLI so Claude can use Codex as a sub-agent:

- `codex_task`: delegate work. `mode: "write"` (default) lets Codex edit files and run commands in
  the working directory; `mode: "read-only"` is for research and critique. The result includes
  `session_id`; pass it back to continue the same Codex thread (e.g. to request fixes).
- `codex_review`: Codex reviews uncommitted changes (default), a diff against `base`, or a `commit`.

### Default workflow for building features: Claude plans, Codex builds, Claude reviews

1. **Plan (Claude).** Turn the user's input into a concrete plan: goal, files to touch, interfaces,
   edge cases, tests to add, and acceptance criteria. For non-trivial plans, confirm it with the
   user before delegating.
2. **Build (Codex).** Send the plan to `codex_task` in `write` mode as a self-contained brief
   (Codex doesn't see this conversation). Ask it to run the tests and report what it did, what
   it skipped, and anything it was unsure of. For larger or riskier work, run it in a separate
   git worktree (`git worktree add ...`, then pass that path as `cwd`) so the main checkout stays clean.
3. **Review (Claude).** Review the diff yourself (see "Keep context lean" below) and run the tests. Don't rely on Codex's summary.
   Check the work against the plan and acceptance criteria. Send concrete fixes back with the same
   `session_id`, or make small fixes directly. Repeat until it's right.
4. **Report.** Tell the user what Codex built, what you changed in review, and anything still open.
   Commit only after review passes.

Do small or fiddly changes directly; delegating them costs more than it saves.

### Keep context and tokens lean

Context rot and token use matter more than thoroughness theatre. Codex's transcript stays in Codex;
only its final report (capped at ~6k chars) and a changed-file list come back.

- **Briefs:** reference files by path rather than pasting code; state the plan once. End every
  brief with: *"Report in ≤200 words: what you changed, test results, open questions. No code in the report."*
- **One task, one Codex session.** Start fresh for each new task; reuse `session_id` only to send
  fixes on that same task. Codex re-reads the repo itself, so don't re-send context it already has.
- **Review cheaply, then deeply where it matters.** Start with `git diff --stat` and the test run.
  Then read diffs file by file (`git diff -- <path>`), focusing on logic, interfaces and tests.
  Skim or skip generated, lockfile and formatting-only changes. Never dump a huge diff all at once.
- **Fixes:** send short, specific fix lists (file, problem, expected behaviour), not rewritten plans.
- **Don't double up.** Skip `codex_review` on code Codex just wrote; reviewing it is Claude's job.
  Use `codex_review` on code Claude wrote.
- **Long sessions:** once a feature is reviewed and committed, summarise its state in a line or two
  and move on rather than carrying its details forward. Suggest `/clear` or a new session between unrelated features.

### Second opinions

- **Design decisions.** Before a non-trivial, hard-to-reverse choice, send Codex a brief
  (goal, constraints, options, your recommendation) via `codex_task` in `read-only` mode and ask for a critique.
- **Research.** Where being wrong is costly, have Codex answer independently *before* you share
  your own conclusion, then compare the two.
- **Sanity checks.** After non-trivial changes you wrote yourself, run `codex_review` with `instructions`
  describing the intent.

### Rules

- Codex is a collaborator, not an authority. Verify its claims and code before relying on them.
- When you and Codex disagree on something material, tell the user both positions and your recommendation.
- Never put secrets in a brief.
