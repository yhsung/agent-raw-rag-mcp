# AGENTS.md — `agent-raw-rag-mcp`

> 本檔案為 AI Coding Agents（Antigravity、Oh My Pi `omp`/`pi`、Claude Code、Codex、OpenCode）進入本專案時的核心作業規範與架構上下文。
> 與使用者溝通一律使用**正體中文（Traditional Chinese）**。

---

## 1. 專案目標（Project Goal）

將論文 **`Raw-RAG`**（*Is Complex Agentic Memory Always Needed? Naive RAG Is Stronger Than We Thought*, arXiv:2610.30755）經實證最有效的記憶機制，封裝為跨四大 CLI Coding Agent（**Claude Code**、**Codex CLI**、**OpenCode**、**Oh My Pi**）共用的本機 **MCP 記憶檢索與增量索引服務（`agent-raw-rag-mcp`）**。

### 核心解決痛點
1. **消除 `/compact` 有損壓縮失憶**：CLI Agent 內建的 `/compact`（滾動摘要）實證會造成 **21.9% 永久表徵損失（Representation Loss）**。本服務在旁路以 Append-Only 保存 100% 原始對白切片，壓縮後可隨時按需無損救援。
2. **跨工具記憶互通**：同一專案在 Claude Code、Codex、OpenCode、Oh My Pi 之間的討論、架構決策與除錯脈絡寫入同一索引，切換工具不斷線。
3. **寫入端零 LLM 呼叫、讀取端免跑本機 Reader LLM**：
   - 寫入端僅做確定性降噪（Deterministic Sanitizer）與尾部增量切塊 $O(\Delta L)$，**0 次 LLM 呼叫**。
   - 讀取端僅使用本機 Apple Silicon MLX 執行 `Qwen3-Embedding-4B`（粗排 Top-128）+ `Qwen3-Reranker-4B`（精排 Top-15），直接將展開後的完整對話回合回傳給當前 CLI Agent（由 Claude / GPT / Gemini 自身擔任 Reader LLM），省去本機大型 Reader LLM 的 KV Cache 與統一記憶體開銷。

---

## 2. 本機參考資產與藍圖位置（Primary References）

開發時請優先參考以下已完成驗證之本機原始碼與設計藍圖：

### 2.1 本專案內建架構藍圖與實測報告（`reports/`）
- **CLI Agents 整合架構藍圖（HTML）**：[`reports/raw-rag-cli-agents-blueprint.html`](./reports/raw-rag-cli-agents-blueprint.html)
- **Raw-RAG 資料結構與檢索負載圖解（HTML）**：[`reports/raw-rag-data-payload-explained.html`](./reports/raw-rag-data-payload-explained.html)
- **Raw-RAG 膨脹管理與架構圖解（HTML）**：[`reports/raw-rag-scaling-architecture.html`](./reports/raw-rag-scaling-architecture.html)
- **論文完整復現與反向消融技術報告（Markdown）**：[`reports/raw-rag-reproduction-report.md`](./reports/raw-rag-reproduction-report.md)
- **實作計畫與介面規格（Markdown）**：[`PLAN.md`](./PLAN.md)

### 2.2 官方 `Raw-RAG` 參考實作（位於 `/Volumes/Samsung970EVOPlus/dev-projects/raw-rag`）
- **核心演算法參考**：`/Volumes/Samsung970EVOPlus/dev-projects/raw-rag/repo/methods/EverCore/evaluation/src/adapters/rag_adapter.py`
  - 增量尾部切塊：`RAGAdapter.add_incremental_message`（Lines 335–500）
  - 時間排序鍵：`RAGAdapter._selected_chunk_time_sort_key`（Lines 750–766）
  - 暫態向量記憶體釋放：`RAGAdapter.release_conversation_search_cache`（Lines 915–926）
  - 檢索前時序範圍過濾：`RAGAdapter._filter_chunks_for_question_date`（Lines 1021–1065）
  - 動態分數門檻截斷：`RAGAdapter._select_reranked_candidates`（Lines 1330–1357）
  - 對話格式化與 256-token 切塊：`_format_conversation`、`_create_token_chunks`（Lines 1359–1520）
  - 回合映射表與呈現期動態展開：`_build_turn_context`、`_expand_chunk_to_turns`、`_format_context`（Lines 1542–1748）
- **PostgreSQL `pgvector` 後端參考**：`/Volumes/Samsung970EVOPlus/dev-projects/raw-rag/repo/methods/EverCore/evaluation/src/adapters/rag_pgvector_store.py`
- **Apple Silicon MLX Embedding & Reranker 推論邏輯參考**：`/Volumes/Samsung970EVOPlus/dev-projects/raw-rag/mlx_rag_server.py`

---

## 3. 硬體、模型與環境約束（Machine & Model Rules）

1. **Hugging Face 權重路徑**：
   - 一律設定 `HF_HOME=/Volumes/Samsung980/hf-home/`。
   - **嚴禁**將模型下載至 `~/.cache` 或 `/tmp`，且不要另外設定 `HF_HUB_CACHE`。
2. **Apple Silicon 推論後端**：
   - 這是一台 **64GB Apple Silicon Mac**。
   - Embedding（`Qwen/Qwen3-Embedding-4B` 或 MLX 4-bit 量化版）與 Reranker（`Qwen/Qwen3-Reranker-4B` 或 MLX 4-bit 量化版）**優先使用 MLX**，禁止預設使用會造成 Metal graph cache / swap 暴增的 PyTorch MPS。
   - 本專案**不需要啟動本機 LLM 生成伺服器**（Reader 由宿主 CLI Agent 擔任）。
3. **本機四大 CLI 工具之真實資料來源路徑**：
   - **Claude Code (`claude`)**：`~/.claude/projects/<project-slug>/*.jsonl`
   - **Codex CLI (`codex`)**：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
   - **OpenCode (`opencode`)**：`~/.local/share/opencode/opencode.db`（SQLite 資料庫，注意目前已達 **3.19 GB**，讀取時務必使用 SQLite 游標與欄位過濾，不可全表載入記憶體）
   - **Oh My Pi (`omp` / `pi`)**：`~/.omp/agent/sessions/<project-slug>/*.jsonl`（以及 `~/.omp/agent/agent.db`）

---

## 4. 不可違反的五個架構不變量（Architectural Invariants）

實作任何模組時，必須嚴格遵守以下五項設計原則：

1. **寫入端零 LLM 呼叫（Zero-LLM Ingestion）**：
   - 嚴禁在寫入管線呼叫 LLM 做摘要、原子事實萃取或三元組抽取。
2. **確定性降噪器（Deterministic Sanitizer for Coding Logs）**：
   - Coding Agent 原始紀錄有 90% 是 `Read` 檔案內容、`Grep` 輸出或長篇 `Bash` stdout。
   - 進入切塊器前，必須：
     - **100% 保留原文**：User Prompt 與 Assistant 回答／推理正文。
     - **壓縮為單行動作摘要**：Tool 呼叫僅保留 `[ToolName] target_file_or_cmd (exit_code)`；若命令失敗（`exit_code != 0`），最多保留最後 15 行錯誤輸出（stderr/tail）。
     - **直接剝除**：`Read` / `View` 輸出的原始碼全文、巨型 git diff 全文（程式碼由檔案系統或 `codebase-memory-mcp` 負責，不塞入對話記憶）。
3. **增量尾部切塊（Incremental Tail-Only Chunking，$O(\Delta L)$）**：
   - 使用 `tiktoken` (`cl100k_base`)，`chunk_size = 256` tokens，`chunk_overlap = 32` tokens（步長 224 tokens）。
   - 每個 Session 紀錄增量游標（JSONL `byte_offset` 或 SQLite `last_message_id` / `time_created`）。
   - 已填滿 256 tokens 的 `stable_chunks` 與其向量鎖定不重算；每次僅重切與重算最後一個未滿區塊與新增的 `tail_chunks`。
4. **時序座標與兩階段漏斗（Two-Stage Bounded Retrieval + Temporal Reordering）**：
   - 檢索流程固定為：`專案/時間範圍預過濾` $\to$ `Dense 粗排 Top-128` $\to$ `Cross-Encoder Rerank Top-15`（支援 `dynamic_top_k` 分數門檻 `> 0.5`，保底 `fallback_top_k = 3`）$\to$ `依原始對話時序 (session_timestamp, start_turn, start_token) 升冪重排`。
5. **呈現期動態展開完整回合（Display-Time Turn Expansion）**：
   - 向量與 Reranker 計算針對 256-token 緊湊切片；但回傳給 MCP Client（CLI Agent）前，必須透過 `turn_spans` 將命中的切片**動態展開為完整的 User/Assistant 對話回合**並自動去除重疊回合（`dedup_turns_on_display`）。

---

## 5. 開發里程碑（Implementation Milestones）

接手實作時，請依序推進以下 5 個階段（詳見 [`PLAN.md`](./PLAN.md)）：

- [ ] **Phase 1：確定性降噪器與四合一增量解析器（`src/agent_raw_rag/parsers/`）**
  - 實作 `sanitizer.py`、`claude_parser.py`、`codex_parser.py`、`opencode_parser.py`、`omp_parser.py` 與單元測試。
- [ ] **Phase 2：增量尾部切塊與儲存引擎（`src/agent_raw_rag/engine/chunker.py` & `store.py`）**
  - 移植 `add_incremental_message`、`turn_spans` 映射與 SQLite + Vector（含 `pgvector` 選配）持久化。
- [ ] **Phase 3：Apple Silicon MLX 檢索與動態展開引擎（`src/agent_raw_rag/engine/retriever.py`）**
  - 整合 `Qwen3-Embedding-4B` + `Qwen3-Reranker-4B`、時序預過濾、時序重排與 `_expand_chunk_to_turns`。
- [ ] **Phase 4：MCP Server 與 CLI 增量寫入命令（`src/agent_raw_rag/mcp_server.py` & `cli.py`）**
  - 實作 `raw-rag-mcp`（提供 `raw_rag_search`、`raw_rag_recent`、`raw_rag_ingest`、`raw_rag_status`）與 `raw-rag-ingest` CLI。
- [ ] **Phase 5：四大 CLI Agent 接線腳本與端到端驗證（`configs/` & `tests/`）**
  - 驗證從 `.claude`、`.codex`、`opencode.db`、`.omp` 實機增量匯入與跨工具檢索延遲／召回率。
