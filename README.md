# `agent-raw-rag-mcp`

將論文 **`Raw-RAG`**（arXiv:2610.30755）的核心設計（**寫入端零 LLM 呼叫、256-token 增量尾部切塊、兩階段檢索 Top-128 $\to$ Top-15、時序升冪重排與呈現期動態回合展開**）整合至四大 CLI Coding Agent（**Claude Code**、**Codex CLI**、**OpenCode**、**Oh My Pi**）的本機 MCP 服務。

## 快速導覽
- **AI Agent 作業規範**：[`AGENTS.md`](./AGENTS.md)
- **詳細技術規劃與介面規格**：[`PLAN.md`](./PLAN.md)
- **視覺化架構藍圖（HTML）**：
  - [四大 CLI Agent 整合架構藍圖 (`raw-rag-cli-agents-blueprint.html`)](./reports/raw-rag-cli-agents-blueprint.html)
  - [Raw-RAG 資料結構與檢索負載圖解 (`raw-rag-data-payload-explained.html`)](./reports/raw-rag-data-payload-explained.html)
  - [Raw-RAG 膨脹管理與架構圖解 (`raw-rag-scaling-architecture.html`)](./reports/raw-rag-scaling-architecture.html)
- **論文復現與消融實驗報告（Markdown）**：[`reports/raw-rag-reproduction-report.md`](./reports/raw-rag-reproduction-report.md)
