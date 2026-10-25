/**
 * Oh My Pi (omp / pi) Raw-RAG Memory Extension
 * Place or symlink to: ~/.omp/agent/extensions/raw-rag.ts
 */

import { spawn } from "node:child_process";
import type { ExtensionContext } from "@oh-my-pi/agent";

const INGEST_BIN = "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/.venv/bin/raw-rag-ingest";
const MCP_BIN = "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/.venv/bin/raw-rag-mcp";

export default function activate(ctx: ExtensionContext) {
  // 1. Register stdio MCP Server for Pi / OMP
  ctx.registerMcpServer("raw-rag", {
    command: MCP_BIN,
    args: [],
    env: {
      HF_HOME: "/Volumes/Samsung980/hf-home/",
    },
  });

  // 2. Trigger non-blocking incremental sync after session turns
  ctx.on("turn:complete", async () => {
    try {
      const child = spawn(INGEST_BIN, ["--agent", "omp"], {
        detached: true,
        stdio: "ignore",
        env: {
          ...process.env,
          HF_HOME: "/Volumes/Samsung980/hf-home/",
        },
      });
      child.unref();
    } catch {
      // Non-blocking, ignore errors
    }
  });
}
