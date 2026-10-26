/**
 * Oh My Pi (omp / pi) Raw-RAG Memory Extension
 * Multi-Agent incremental memory retrieval and display-time turn expansion.
 *
 * NOTE: Background incremental ingestion is handled automatically by the pull-based
 * daemon (raw-rag-ingest --watch / LaunchAgent). Per Architectural Invariants,
 * turn-based blocking/spawn hooks have been removed to eliminate process contention.
 */

import { spawn } from "node:child_process";

const MCP_COMMAND = "uvx";
const MCP_ARGS = [
  "--from",
  "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp",
  "agent-raw-rag-mcp",
];
const HF_HOME = "/Volumes/Samsung980/hf-home/";

function runPyScript(code: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "uvx",
      ["--from", "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp", "python3", "-c", code],
      {
        env: {
          ...process.env,
          HF_HOME,
        },
      }
    );

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (d) => {
      stdout += d.toString();
    });
    child.stderr.on("data", (d) => {
      stderr += d.toString();
    });

    child.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        reject(new Error(stderr || stdout || `Exited with code ${code}`));
      }
    });
  });
}

export default function activate(pi: any) {
  // 1. Try registering stdio MCP server if supported by host runtime
  if (typeof pi.registerMcpServer === "function") {
    try {
      pi.registerMcpServer("raw-rag", {
        command: MCP_COMMAND,
        args: MCP_ARGS,
        env: { HF_HOME },
      });
    } catch {
      // Fallback to registered tools
    }
  }

  // 2. Register native tools if registerTool is available
  if (typeof pi.registerTool === "function") {
    pi.registerTool({
      name: "raw_rag_search",
      description: "Search historical memory across Claude Code, Codex, OpenCode, and OMP (Raw-RAG MLX 2-stage retrieval).",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Search query" },
          project_path: { type: "string", description: "Optional project path filter, '*' for global" },
          top_k: { type: "number", description: "Number of expanded memory turns (default 15)" },
        },
        required: ["query"],
      },
      handler: async (args: { query: string; project_path?: string; top_k?: number }) => {
        const queryEscaped = JSON.stringify(args.query);
        const projEscaped = JSON.stringify(args.project_path || "*");
        const topK = args.top_k || 15;
        const code = `
from agent_raw_rag.mcp_server import raw_rag_search
print(raw_rag_search(query=${queryEscaped}, project_path=${projEscaped}, top_k=${topK}))
`;
        return await runPyScript(code);
      },
    });

    pi.registerTool({
      name: "raw_rag_recent",
      description: "Get recent conversational turns for project after /compact or at session start.",
      parameters: {
        type: "object",
        properties: {
          project_path: { type: "string", description: "Optional project path filter" },
          limit_turns: { type: "number", description: "Number of turns (default 30)" },
        },
      },
      handler: async (args: { project_path?: string; limit_turns?: number }) => {
        const projEscaped = JSON.stringify(args.project_path || "*");
        const limit = args.limit_turns || 30;
        const code = `
from agent_raw_rag.mcp_server import raw_rag_recent
print(raw_rag_recent(project_path=${projEscaped}, limit_turns=${limit}))
`;
        return await runPyScript(code);
      },
    });

    pi.registerTool({
      name: "raw_rag_status",
      description: "Check status of the shared local Raw-RAG memory index.",
      parameters: { type: "object", properties: {} },
      handler: async () => {
        const code = `
from agent_raw_rag.mcp_server import raw_rag_status
print(raw_rag_status())
`;
        return await runPyScript(code);
      },
    });
  }
}
