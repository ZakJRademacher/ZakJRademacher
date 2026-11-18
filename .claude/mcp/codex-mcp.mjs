#!/usr/bin/env node
// Minimal MCP stdio server that lets Claude Code use OpenAI's Codex CLI as a sub-agent.
// Newer Codex releases dropped the built-in `codex mcp-server`, so this wraps
// `codex exec` / `codex exec review` instead. No dependencies.
//
// Tools:
//   codex_task    - delegate a task to Codex: build from a plan (mode "write") or
//                   research / critique without touching files (mode "read-only").
//                   Returns a session_id so follow-ups continue the same Codex thread.
//   codex_review  - have Codex review uncommitted changes, a branch diff, or a commit.

import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";

const TIMEOUT_MS = Number(process.env.CODEX_MCP_TIMEOUT_MS || 30 * 60 * 1000);
const DEFAULT_MODEL = process.env.CODEX_MCP_MODEL || undefined;

const TOOLS = [
  {
    name: "codex_task",
    description:
      "Delegate a task to OpenAI Codex as a sub-agent. mode \"write\" (default) lets Codex edit files " +
      "and run commands inside the working directory (workspace-write sandbox), e.g. to implement a plan. " +
      "mode \"read-only\" is for research, design critique, or sanity checks without changes. " +
      "Codex does not see this conversation: the prompt must be a complete, self-contained brief. " +
      "Returns Codex's final report, a session_id for follow-ups, and the files changed. Slow (minutes).",
    inputSchema: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "Self-contained brief: goal, plan, files, constraints, acceptance criteria, what to report back." },
        mode: { type: "string", enum: ["write", "read-only"], description: "write (default): may edit files. read-only: may only read." },
        session_id: { type: "string", description: "Continue an earlier Codex session (from a previous result) instead of starting fresh." },
        cwd: { type: "string", description: "Directory Codex works in (e.g. a git worktree). Defaults to the project root." },
        model: { type: "string", description: "Optional Codex model override." },
      },
      required: ["prompt"],
    },
  },
  {
    name: "codex_review",
    description:
      "Have OpenAI Codex code-review changes in the repository and report bugs and risks. " +
      "Choose one target: uncommitted changes (default), a base branch to diff against, or a commit SHA.",
    inputSchema: {
      type: "object",
      properties: {
        base: { type: "string", description: "Review the current branch's diff against this base branch." },
        commit: { type: "string", description: "Review the changes introduced by this commit SHA." },
        instructions: { type: "string", description: "Optional extra focus, e.g. what the change is meant to do." },
        cwd: { type: "string", description: "Repository directory. Defaults to the project root." },
        model: { type: "string", description: "Optional Codex model override." },
      },
    },
  },
];

function findSessionId(jsonl) {
  for (const line of jsonl.split("\n")) {
    try {
      const ev = JSON.parse(line);
      const id = ev.thread_id || ev.session_id || ev.conversation_id || ev.msg?.session_id;
      if (id) return id;
    } catch {}
  }
  return null;
}

function runCodex(args, { cwd, stdin }) {
  const dir = mkdtempSync(join(tmpdir(), "codex-mcp-"));
  const outFile = join(dir, "last.txt");
  const fullArgs = [...args, "--json", "--output-last-message", outFile];
  return new Promise((resolve) => {
    const child = spawn("codex", fullArgs, { cwd, env: process.env, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let head = ""; // first events, where the session id appears
    child.stdout.on("data", (d) => {
      if (head.length < 20_000) head += d;
      stdout += d;
      if (stdout.length > 200_000) stdout = stdout.slice(-100_000);
    });
    child.stderr.on("data", (d) => { stderr += d; if (stderr.length > 200_000) stderr = stderr.slice(-100_000); });
    const timer = setTimeout(() => child.kill("SIGTERM"), TIMEOUT_MS);
    child.on("error", (err) => {
      clearTimeout(timer);
      rmSync(dir, { recursive: true, force: true });
      resolve({ ok: false, text: `Failed to start codex: ${err.message}. Is the Codex CLI installed (npm i -g @openai/codex)?` });
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      let last = "";
      try { last = readFileSync(outFile, "utf8").trim(); } catch {}
      rmSync(dir, { recursive: true, force: true });
      const sessionId = findSessionId(head);
      if (code === 0 && last) return resolve({ ok: true, text: last, sessionId });
      const why = signal ? `killed by ${signal} (timeout ${TIMEOUT_MS} ms?)` : `exit code ${code}`;
      resolve({
        ok: false,
        sessionId,
        text: `Codex failed (${why}).\n${last ? `Last message:\n${last}\n` : ""}stderr (tail):\n${stderr.slice(-4000)}\nstdout (tail):\n${stdout.slice(-2000)}`,
      });
    });
    if (stdin) child.stdin.write(stdin);
    child.stdin.end();
  });
}

function changedFiles(cwd) {
  const r = spawnSync("git", ["status", "--short"], { cwd, encoding: "utf8" });
  return r.status === 0 ? r.stdout.trim() || "(no changes)" : "(not a git repository)";
}

async function callTool(name, a = {}) {
  const cwd = a.cwd || process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const common = ["--skip-git-repo-check"];
  const model = a.model || DEFAULT_MODEL;
  if (model) common.push("--model", model);

  if (name === "codex_task") {
    if (!a.prompt) return { ok: false, text: "Missing required argument: prompt" };
    const sandbox = a.mode === "read-only" ? "read-only" : "workspace-write";
    const cfg = ["-c", `sandbox_mode="${sandbox}"`, "-c", 'approval_policy="never"'];
    const args = a.session_id
      ? ["exec", "resume", ...cfg, ...common, a.session_id, "-"]
      : ["exec", ...cfg, ...common, "-"];
    const res = await runCodex(args, { cwd, stdin: a.prompt });
    const footer = [
      `\n---\nCodex session_id: ${res.sessionId || "(unknown)"}  (pass as session_id to follow up)`,
      sandbox === "workspace-write" ? `Files changed in ${cwd}:\n${changedFiles(cwd)}` : "",
    ].filter(Boolean).join("\n");
    return { ok: res.ok, text: res.text + footer };
  }

  if (name === "codex_review") {
    if (a.base && a.commit) return { ok: false, text: "Pass either base or commit, not both." };
    const target = a.base ? ["--base", a.base] : a.commit ? ["--commit", a.commit] : ["--uncommitted"];
    const args = ["exec", "review", "--ephemeral", ...common, ...target];
    if (a.instructions) args.push(a.instructions);
    return runCodex(args, { cwd });
  }

  return { ok: false, text: `Unknown tool: ${name}` };
}

// ---- JSON-RPC over newline-delimited stdio ----
const send = (msg) => process.stdout.write(JSON.stringify(msg) + "\n");
const reply = (id, result) => send({ jsonrpc: "2.0", id, result });
const fail = (id, code, message) => send({ jsonrpc: "2.0", id, error: { code, message } });

createInterface({ input: process.stdin }).on("line", async (line) => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return fail(null, -32700, "Parse error"); }
  const { id, method, params } = msg;
  if (id === undefined) return; // notification
  switch (method) {
    case "initialize":
      return reply(id, {
        protocolVersion: params?.protocolVersion || "2025-06-18",
        capabilities: { tools: {} },
        serverInfo: { name: "codex", version: "1.1.0" },
      });
    case "ping":
      return reply(id, {});
    case "tools/list":
      return reply(id, { tools: TOOLS });
    case "tools/call": {
      const res = await callTool(params?.name, params?.arguments);
      return reply(id, { content: [{ type: "text", text: res.text }], isError: !res.ok });
    }
    default:
      return fail(id, -32601, `Method not found: ${method}`);
  }
});
