# AGENTS.md — `agent-raw-rag-mcp`

> 本檔案為 AI Coding Agents（Antigravity、Oh My Pi `omp`/`pi`、Claude Code、Codex、OpenCode）進入本專案時的核心作業規範與架構上下文。
> 與使用者溝通一律使用**正體中文（Traditional Chinese）**。

---

## 1. 專案目標（Project Goal）

將論文 **`Raw-RAG`**（*Is Complex Agentic Memory Always Needed? Naive RAG Is Stronger Than We Thought*, arXiv:2610.30755）經實證最有效的記憶機制，封裝為跨五大 CLI Coding Agent（**Antigravity**、**Claude Code**、**Codex CLI**、**OpenCode**、**Oh My Pi**）共用的本機 **MCP 記憶檢索與增量索引服務（`agent-raw-rag-mcp`）**。

### 核心解決痛點
1. **消除 `/compact` 有損壓縮失憶**：CLI Agent 內建的 `/compact`（滾動摘要）實證會造成 **21.9% 永久表徵損失（Representation Loss）**。本服務在旁路以 Append-Only 保存 100% 原始對白切片，壓縮後可隨時按需無損救援。
2. **跨工具記憶互通**：同一專案在 Antigravity、Claude Code、Codex、OpenCode、Oh My Pi 之間的討論、架構決策與除錯脈絡寫入同一索引，切換工具不斷線。
3. **寫入端零 LLM 呼叫、讀取端免跑本機 Reader LLM**：
   - 寫入端僅做確定性降噪（Deterministic Sanitizer）與尾部增量切塊 $O(\Delta L)$，**0 次 LLM 呼叫**。
   - 讀取端僅使用本機 Apple Silicon MLX 執行 `Qwen3-Embedding-4B`（粗排 Top-128）+ `Qwen3-Reranker-4B`（精排 Top-15），直接將展開後的完整對話回合回傳給當前 CLI Agent（由 Claude / GPT / Gemini 自身擔任 Reader LLM），省去本機大型 Reader LLM 的 KV Cache 與統一記憶體開銷。
4. **Pull-based 背景守護與跨 Agent 聚合批次推論**：
   - 由背景常駐 Daemon（`raw-rag-ingest --watch` / LaunchAgent）以日誌游標非阻塞拉取，**移除 Agent 端 turn-based 阻斷式 Hook / Extension**。
   - 跨多 Agent 採集未滿尾部區塊，單次 MLX Forward Pass 批次推論（`max_batch_size=64`），並透過單一 SQLite WAL 交易耦合落盤（Chunks + Cursors），杜絕 GPU 搶佔與表徵丟失。

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

## 3. 硬體、模型與資料安全約束（Machine, Model & Data Rules）

1. **Hugging Face 權重路徑**：
   - 一律設定 `HF_HOME=/Volumes/Samsung980/hf-home/`。
   - **嚴禁**將模型下載至 `~/.cache` 或 `/tmp`，且不要另外設定 `HF_HUB_CACHE`。
2. **Apple Silicon 推論後端**：
   - 這是一台 **64GB Apple Silicon Mac**。
   - Embedding（`Qwen/Qwen3-Embedding-4B` 或 MLX 4-bit 量化版）與 Reranker（`Qwen/Qwen3-Reranker-4B` 或 MLX 4-bit 量化版）**優先使用 MLX**，禁止預設使用會造成 Metal graph cache / swap 暴增的 PyTorch MPS。
   - 本專案**不需要啟動本機 LLM 生成伺服器**（Reader 由宿主 CLI Agent 擔任）。
3. **本機五大 CLI 工具之真實資料來源路徑**：
   - **Antigravity (`agy`)**：`~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`
   - **Claude Code (`claude`)**：`~/.claude/projects/<project-slug>/*.jsonl`
   - **Codex CLI (`codex`)**：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
   - **OpenCode (`opencode`)**：`~/.local/share/opencode/opencode.db`（SQLite 資料庫，注意目前已達 **3.19 GB**，讀取時務必使用 SQLite 游標與欄位過濾，不可全表載入記憶體）
   - **Oh My Pi (`omp` / `pi`)**：`~/.omp/agent/sessions/<project-slug>/*.jsonl`（以及 `~/.omp/agent/agent.db`）
4. **本機資料目錄保護（Local Data & Git Tracking Protection）**：
   - `data/` 目錄專門存放本機擷取之原始對白切片、SQLite 資料庫（如 `raw_rag.db`）、向量索引與快取，屬於本機環境與敏感對話資料，專案已於 `.gitignore` 設定忽略。
   - **嚴禁將 `data/` 或其內部任何檔案納入 git tracking**（禁止 `git add data/`、`git add -f data/` 或從 `.gitignore` 移除）。
   - **操作守門員（Flag & Warn）**：任何 Agent 執行任何指令或 git 操作時，若偵測到可能直接或間接將 `data/` 或其檔案加入版控（包含 `git add .`、`git commit` 涉及 `data/` 等），**必須立即主動攔截（flag）並提出明確警告**，阻止該操作，保護本機私有資料與避免大檔污染倉庫。

---

## 4. 不可違反的五個架構不變量（Architectural Invariants）

實作與維護任何模組時，必須嚴格遵守貫穿「寫入 $\to$ 儲存 $\to$ 檢索展開」生命週期的五項架構不變量：

> 💡 **端到端流程圖（Architectural Diagram）**：詳見 [`README.md`](./README.md#2-五大架構不變量architectural-invariants) 之端到端 Mermaid 架構流程圖。

1. **寫入端零 LLM 呼叫（Zero-LLM Ingestion）**：
   - 寫入端管線保證純確定性演算法，嚴禁在寫入管線呼叫 LLM 進行摘要、原子事實萃取或三元組抽取，不消耗任何本機或雲端 LLM 額度與時間。
2. **確定性降噪器（Deterministic Sanitizer for Coding Logs）**：
   - Coding Agent 原始紀錄有 90% 是檔案閱讀與指令輸出。
   - **100% 保留原文**：User Prompt 與 Assistant 回答／推理正文。
   - **壓縮為單行動作摘要**：Tool 呼叫僅保留 `[ToolName] target_file_or_cmd (exit_code)`；命令失敗（`exit_code != 0`）時最多保留最後 15 行 stderr。
   - **直接剝除**：`Read` / `View` 輸出的程式碼全文與巨型 git diff 全文（代碼由檔案系統或 `codebase-memory-mcp` 負責，不塞入對話記憶）。
3. **增量尾部切塊（Incremental Tail-Only Chunking，$O(\Delta L)$）**：
   - 使用 `tiktoken` (`cl100k_base`)，`chunk_size = 256` tokens，`chunk_overlap = 32` tokens（步長 224 tokens）。
   - 每個 Session 維護增量游標（JSONL `byte_offset` 或 SQLite `last_message_id` / `time_created`）。
   - 已滿 256 tokens 的 `stable_chunks` 與向量鎖定不重算；每次僅重切與重算最後一個未滿區塊與新增的 `tail_chunks`，並同步建立 `turn_spans` 高精度回合映射。
4. **時序座標與兩階段漏斗（Two-Stage Bounded Retrieval + Temporal Reordering）**：
   - 檢索流程固定為：`專案/時間範圍預過濾` $\to$ `Dense 粗排 Top-128`（`Qwen3-Embedding-4B`） $\to$ `Cross-Encoder 精排 Top-15`（`Qwen3-Reranker-4B`，支援 `dynamic_top_k` 分數門檻 `> 0.5`，保底 `fallback_top_k = 3`） $\to$ `依原始對話時序 (session_timestamp, start_turn, start_token) 升冪重排`。
5. **呈現期動態展開完整回合（Display-Time Turn Expansion）**：
   - 向量與 Reranker 計算針對 256-token 緊湊切片；但回傳給 MCP Client（CLI Agent）前，必須透過 `turn_spans` 將命中的切片**動態展開為完整的 User/Assistant 對話回合**並自動去除跨區塊重複回合（`dedup_turns_on_display`），0 本機 Reader LLM，直接回傳給宿主 CLI Agent 擔任 Reader（消除 `/compact` 造成的 21.9% 表徵損失）。

---

## 5. 架構指引與模組職責（Architectural Guidance & Implemented Modules）

本專案的核心里程碑已全數完成實作與自動化驗證（詳見各單元測試與 [`PLAN.md`](./PLAN.md)）。以下為各核心模組的架構職責指引與維護規範，後續擴充或調整時須嚴格遵循其責任劃分：

- [x] **Phase 1：確定性降噪器與四合一增量解析器（`src/agent_raw_rag/parsers/`）**
  - `sanitizer.py`：純文字降噪，嚴格執行零 LLM 規則。負責截斷工具呼叫輸出、剝除原始碼全文及 giant diff，僅保留失敗 stderr（最多 15 行）與 100% 原始對白本文。
  - `claude_parser.py`、`codex_parser.py`、`opencode_parser.py`、`omp_parser.py`：針對四大 CLI 工具各自的儲存結構（JSONL 與 SQLite），維護增量讀取游標（`byte_offset`、`last_message_id`、`time_created`），確保重複呼叫不產生重複轉錄。
- [x] **Phase 2：增量尾部切塊與儲存引擎（`src/agent_raw_rag/engine/chunker.py` & `store.py`）**
  - `chunker.py`：以 `tiktoken` (`cl100k_base`) 實作 256-token 窗口（步長 224 tokens，重疊 32 tokens）的 $O(\Delta L)$ 尾部增量切塊，並同時產生高精度的 `turn_spans` 回合映射表。
  - `store.py`：以 SQLite 為核心儲存（`sessions`、`chunks`、`ingestion_cursors`），以二進位快取管理 Dense 向量索引，支援事務一致性與增量快照。會話回合脈絡直接以 `turn_context_json` 緊湊序列化儲存於 `sessions` 表中，避免獨立 `turns` 表在十萬級對白下引發數百萬行索引膨脹與 WAL 鎖競爭，維持極致低儲存開銷。
- [x] **Phase 3：Apple Silicon MLX 檢索與動態展開引擎（`src/agent_raw_rag/engine/mlx_retriever.py`）**
  - `mlx_retriever.py`：整合本機 MLX 4-bit / 8-bit `Qwen3-Embedding-4B` 與 `Qwen3-Reranker-4B`，遵循兩階段漏斗（Dense 粗排 Top-128 $\to$ Cross-Encoder 精排 Top-15 $\to$ 動態分數門檻過濾）。
  - **時序升冪重排與動態展開**：檢索命中切片後，依原始對話時序（`session_timestamp, start_turn, start_token`）重排，並透過 `_expand_chunk_to_turns` 動態還原為完整 User/Assistant 對話回合，去除跨區塊重複回合。
- [x] **Phase 4：MCP Server 與 CLI 增量寫入命令（`src/agent_raw_rag/mcp_server.py` & `cli.py`）**
  - `mcp_server.py`：向宿主 CLI Agents 暴露標準 MCP 工具集（`raw_rag_search`、`raw_rag_recent`、`raw_rag_ingest`、`raw_rag_status`），提供零延遲跨工具記憶存取。
  - `cli.py`：提供獨立命令列工具（`agent-raw-rag`），支援單次/批次增量匯入、手動檢索驗證與系統狀態巡檢。
- [x] **Phase 5：四大 CLI Agent 接線腳本與端到端驗證（`configs/` & `tests/`）**
  - `configs/`：維護 Claude Code（Hook）、Codex CLI（TOML）、OpenCode（JSON）、Oh My Pi（TypeScript Extension）之即插即用接入配置。
  - `tests/`：涵蓋降噪器、四大解析器、尾部切塊、SQLite 儲存、MLX 兩階段檢索與 MCP 協定的端到端自動化測試套件。

---

## 6. 文件同步與計畫回寫規範（Documentation Synchronization & Plan Upstream Rule）

為杜絕架構文件、實作計畫與真實程式碼產生落差，所有參與本專案的 AI Coding Agents 必須嚴格遵守以下文件同步守則：

1. **修改完成即時回寫 `PLAN.md`（Single Source of Planning Truth）**：
   - 任何 Agent 在完成程式碼重構、Bug 修復、資料表結構（Schema）異動、配置參數預設值微調或模型版本升級後，**必須立即將正確、最新且經測試驗證的資訊同步回寫至 [`PLAN.md`](./PLAN.md)**。
   - 嚴禁讓 `PLAN.md` 停留在過時草案或與原始碼不符的狀態。
2. **三位一體一致性（Code, Docs & Plans Parity）**：
   - 每次提交或修改核心功能時，必須同步檢核：
     - **真實程式碼（Code）**：例如 `store.py`、`config.py`、`default_config.yaml`。
     - **架構規範與指引（Docs）**：`AGENTS.md`、`README.md`。
     - **執行計畫與介面規格（Plans）**：`PLAN.md` 及 `plans/` 資料夾下的專案藍圖。
   - 三者記載之資料表名稱（如 `ingestion_cursors`）、模型識別碼（如 `mlx-community/Qwen3-Reranker-4B-4bit`）及開關預設狀態（如 `dynamic_top_k.enabled: true`）必須保持 100% 事實一致。

