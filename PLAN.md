# `agent-raw-rag-mcp` 詳細技術規劃與介面規格（Implementation Plan）

本文件為專案的 **Single Source of Truth (SSOT)**，定義 `agent-raw-rag-mcp` 的全域架構可追溯性、核心資料結構、五大 CLI 解析器規格、儲存與檢索演算法，以及 MCP 工具介面。

---

## 0. 全域追溯性與生命週期矩陣（Global Traceability & Lifecycle Matrix）

專案內所有技術報告（`reports/`）與執行子計畫（`plans/`）嚴格依據其工程推進生命週期，歸納於以下三大狀態，並統一與本 Master `PLAN.md` 雙向鏈結：

### 狀態 1：調研與決策參考（State 1: Pure Reference / Architectural Decision - No Plan Triggered）
> **定義**：產生了深度技術報告、架構對比或理論探索，但經決策評估後判定「維持現狀」、「無需變更核心代碼」或「純屬文獻/決策背景參考」，因此**不啟動任何 `plans/`**。
> **目的**：避免報告成為無人過問的孤島，明確記錄「知其不為」的架構決策原因。

| 報告 ID 與檔案 | 核心主題與決策結論 | 決策依據 / 狀態說明 |
| :--- | :--- | :--- |
| [`reports/002-comparison-agent-raw-rag-vs-agy-memory-engine.md`](./reports/002-comparison-agent-raw-rag-vs-agy-memory-engine.md) | 對比 Raw-RAG 旁路架構與 Google AGY 內嵌記憶引擎 | **維持現狀**：確認 Raw-RAG 旁路 Append-Only 模式與跨五大 CLI Agent 共享具備獨特優勢，不啟動修改計畫。 |
| [`reports/003-llm-abstraction-and-karpathy-wiki-in-organizations.md`](./reports/003-llm-abstraction-and-karpathy-wiki-in-organizations.md) | 探討 LLM 抽象層與 Andrej Karpathy 個人 Wiki 模式在組織工程中的落地 | **組織/理論參考**：提供長程組織知識庫演進思考，無立即代碼重構需求，不啟動修改計畫。 |

---

### 狀態 2：推進中子計畫（State 2: Active / In-Progress Sub-plans - Linked & Incomplete）
> **定義**：已有前導分析報告（或由重大架構議題觸發），已撰寫對應子計畫 `plans/<3-digit-id>-<slug>.md` 且內部包含指向報告的鏈結，但目前**尚未完工驗證（進行中、部分完成或受阻）**。
> **目的**：在 SSOT 即時揭露專案活動脈搏、當前 Phase、阻斷點（Blockers）與預計驗證標準。

*（目前無進行中子計畫，所有已立項之計畫皆已全數完工驗證）*

---

### 狀態 3：已結案閉環子計畫（State 3: Completed & Verified Sub-plans - Linked & Closed-Loop）
> **定義**：依據報告制定計畫（或計畫交付後產出實測驗證報告），子計畫與報告間具備雙向鏈結，且**所有 Phase 全數實作完畢、單元/整合測試全綠、實測數據落盤**。
> **目的**：提供完整的變更可追溯性（Traceability），可直接查驗 Git Commit、實測報告與全綠測試套件。

| 計畫 ID 與檔案 | 關聯/依據報告 | 結案驗證報告 / 實測產物 | 交付內容與核心 Commit | 驗證測試狀態 |
| :--- | :--- | :--- | :--- | :--- |
| **核心基底五大階段**<br>([`PLAN.md`](./PLAN.md) 本身) | [`reports/001-raw-rag-reproduction-report.md`](./reports/001-raw-rag-reproduction-report.md) | [`reports/001-raw-rag-reproduction-report.md`](./reports/001-raw-rag-reproduction-report.md) | 完成 Phase 1~5 確定性降噪、四合一解析、尾部切塊、MLX 兩階段檢索與 MCP Server。 | `pytest tests/` 21 項測試通過 |
| [`plans/001-ingestion-concurrency-failsafe-plan.md`](./plans/001-ingestion-concurrency-failsafe-plan.md) | [`reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md`](./reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md) | [`reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md`](./reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md) | 實作多 Agent 聚合批次推論（`max_batch_size=64`）、Antigravity Parser、交易原子性與壞檔隔離。 | 整合入全域迴歸測試 |
| [`plans/002-status-cursor-observability-plan.md`](./plans/002-status-cursor-observability-plan.md) | [`reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md`](./reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md) | [`reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md`](./reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md) | 實作 `raw-rag-ingest --status` 多 Agent 游標新鮮度、即時 Lag 與專案目錄脈絡輸出。 | `tests/test_status_cli.py` 通過 |
| [`plans/003-review-reconciliation-and-optimization-plan.md`](./plans/003-review-reconciliation-and-optimization-plan.md) | 獨立審查 7 大缺口評估 | [`reports/005-raw-rag-truth-alignment-and-reranker-4b-evaluation-report.md`](./reports/005-raw-rag-truth-alignment-and-reranker-4b-evaluation-report.md) | 1. 修正 Schema 文件（3 表）與 `dynamic_top_k: true`<br>2. 解耦 Reranker 支援 4B/0.6B<br>3. 實作兩階段延遲 SQL 載入與單次批次展開（根除暴力載入與 N+1）<br>4. 曝光 `engine_status` 與新增真實權重測試<br>5. 實作 LaunchAgent 服務管理 CLI<br>(Commits: `d5e0ea3`, `02656a6`, `2d87631`, `8b37a22`) | `tests/test_retrieval_real_weights.py`<br>`tests/test_daemon_cli.py`<br>全套 31 項測試全數通過 |
| [`plans/004-uvx-execution-and-pypi-distribution-plan.md`](./plans/004-uvx-execution-and-pypi-distribution-plan.md) | [`reports/006-uvx-execution-gap-analysis.md`](./reports/006-uvx-execution-gap-analysis.md) | [`docs/PUBLISHING.md`](./docs/PUBLISHING.md)<br>測試套件 `tests/test_package_build.py` | 1. Entrypoint 命名對齊 (`agent-raw-rag-mcp`)<br>2. 內嵌設定檔資源化 (`src/agent_raw_rag/resources/`) 與全域 DB fallback<br>3. 各 Agent 範本更新為 `uvx` 與 LaunchAgent 暫存快取防護<br>4. Wheel/Sdist 打包安全自動化稽核（100% 排除 `data/` 與敏感資訊）<br>5. GitHub Actions `publish.yml` 與 PyPI 發布手冊 `docs/PUBLISHING.md` | `tests/test_uvx_integration.py`<br>`tests/test_package_build.py`<br>全套 36 項測試全數通過 |

---

## 1. 目錄結構規劃

```text
/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/
├── AGENTS.md                        # AI Agent 核心規範與架構不變量
├── CLAUDE.md                        # Symlink -> AGENTS.md (相容 Claude Code)
├── PLAN.md                          # 本技術規劃書
├── pyproject.toml                   # uv 專案與 CLI entrypoints 設定
├── configs/
│   ├── default_config.yaml          # 切塊 (256/32)、Top-k (128/15)、Dynamic Top-K (預設開啟)、MLX 模型與路徑設定
│   ├── com.user.agent-raw-rag.plist.template # macOS LaunchAgent 常駐背景守護進程服務樣板
│   ├── claude_hooks_snippet.json    # Claude Code ~/.claude/settings.json 接線設定
│   ├── codex_config_snippet.toml    # Codex ~/.codex/config.toml 接線設定
│   ├── opencode_snippet.json        # OpenCode ~/.config/opencode/opencode.json 接線設定
│   └── omp_extension_raw_rag.ts     # Oh My Pi ~/.omp/agent/extensions/raw-rag.ts 擴充腳本
├── docs/
│   └── PUBLISHING.md                # PyPI 打包稽核、TestPyPI 演練與發布 SOP
├── plans/                           # 嚴格遵循 <3 digit id>-<slug>.md 時序遞增命名
│   ├── 001-ingestion-concurrency-failsafe-plan.md
│   ├── 002-status-cursor-observability-plan.md
│   ├── 003-review-reconciliation-and-optimization-plan.md
│   └── 004-uvx-execution-and-pypi-distribution-plan.md
├── reports/                         # 嚴格遵循 <3 digit id>-<slug>.md 時序遞增命名
│   ├── 001-raw-rag-reproduction-report.md     # 論文完整復現與反向消融報告 (Markdown)
│   ├── 002-comparison-agent-raw-rag-vs-agy-memory-engine.md
│   ├── 003-llm-abstraction-and-karpathy-wiki-in-organizations.md
│   ├── 004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md
│   ├── 005-raw-rag-truth-alignment-and-reranker-4b-evaluation-report.md
│   ├── 006-uvx-execution-gap-analysis.md             # uvx 隨選執行之 Gap 評估與實作路線報告
│   ├── raw-rag-cli-agents-blueprint.html      # 五大 CLI Agent 整合架構藍圖 (HTML)
│   ├── raw-rag-data-payload-explained.html    # Raw-RAG 資料結構與檢索負載圖解 (HTML)
│   └── raw-rag-scaling-architecture.html      # Raw-RAG 膨脹管理與架構圖解 (HTML)
├── src/
│   └── agent_raw_rag/
│       ├── __init__.py
│       ├── config.py                # 讀取設定與環境變數 (強制 HF_HOME=/Volumes/Samsung980/hf-home/，支援 4B 與 0.6B)
│       ├── models.py                # NormalizedTurn, SessionMetadata, ChunkRecord, TurnContext 資料模型
│       ├── resources/               # 打包內嵌資源 (Wheel 隨套件發布)
│       │   ├── __init__.py
│       │   ├── default_config.yaml
│       │   └── com.user.agent-raw-rag.plist.template
│       ├── parsers/
│       │   ├── __init__.py
│       │   ├── sanitizer.py         # Deterministic Sanitizer (濾除巨型 Tool stdout，保留對白與動作摘要)
│       │   ├── claude_parser.py     # 解析 ~/.claude/projects/<slug>/*.jsonl (增量 byte_offset)
│       │   ├── codex_parser.py      # 解析 ~/.codex/sessions/**/rollout-*.jsonl (增量 byte_offset)
│       │   ├── opencode_parser.py   # 解析 ~/.local/share/opencode/opencode.db (SQLite time_created 游標)
│       │   ├── omp_parser.py        # 解析 ~/.omp/agent/sessions/<slug>/*.jsonl (增量 byte_offset)
│       │   └── antigravity_parser.py # 解析 ~/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl
│       ├── engine/
│       │   ├── __init__.py
│       │   ├── chunker.py           # 移植 RAGAdapter.add_incremental_message + _build_turn_context
│       │   ├── store.py             # SQLite WAL 三表架構 (sessions, chunks, ingestion_cursors)
│       │   └── mlx_retriever.py     # MLX Qwen3-Embedding-4B + Qwen3-Reranker-4B/0.6B + Dynamic Top-K + Turn 展開
│       ├── cli.py                   # `raw-rag-ingest` CLI (支援 --status 觀測、--watch 輪詢與 LaunchAgent 服務管理)
│       └── mcp_server.py            # `raw-rag-mcp` 標準 stdio MCP Server
└── tests/
    ├── test_sanitizer.py
    ├── test_incremental_chunker.py
    ├── test_parsers.py
    ├── test_status_cli.py
    ├── test_two_stage_deferred_retrieval.py
    ├── test_retrieval_real_weights.py
    ├── test_daemon_cli.py
    └── test_end_to_end_retrieval.py
```

---

## 2. 標準化資料模型與確定性降噪規格（`models.py` & `sanitizer.py`）

### 2.1 統一對話文件格式（相容 `RAGAdapter._format_conversation`）
每個 CLI Session 經解析後轉換為標準化 Header 與 Turn Lines：

```text
Conversation ID: {agent}_{session_id}
Agent: {agent}
Project: {project_path}
Branch: {git_branch}

[2026-10-25 21:10:05] User: {100% 原始使用者輸入}
[2026-10-25 21:10:18] Assistant (Actions): [Read] src/rag_adapter.py | [Edit] src/chunker.py | [Bash] pytest -q (exit 0)
[2026-10-25 21:10:25] Assistant: {100% 原始 Assistant 回覆與分析推理內容}
```

### 2.2 確定性降噪規則（`sanitizer.py`）
1. **User / Assistant 正文**：100% 原文保留，不做任何截斷或改寫。
2. **Thinking / Reasoning 區塊**：
   - 若長度 $\le 512$ tokens 原文保留；若超過則保留前 128 tokens 與最後 256 tokens（因為結論通常在尾部）。
3. **Tool Use / Tool Result**：
   - `Read` / `View` / `Glob` / `Grep`：只記錄 `[ToolName] file_path_or_pattern`，**完全捨棄**回傳的檔案內容全文。
   - `Edit` / `Write` / `Patch`：只記錄 `[Edit] file_path (+added/-removed lines)`，不塞入數百行 diff 全文。
   - `Bash` / `RunCommand`：
     - 若 `exit_code == 0`：記錄 `[Bash] command_str (exit 0)`。
     - 若 `exit_code != 0`：記錄 `[Bash] command_str (exit {code})` 加上最後 15 行 `stderr`/`stdout`（保留關鍵錯誤堆疊供日後除錯檢索）。

---

## 3. 五大 CLI 工具解析器規格（`src/agent_raw_rag/parsers/`）

| 解析器 | 本機資料路徑 | 增量游標（Cursor Key） | 欄位提取重點 |
| :--- | :--- | :--- | :--- |
| **`claude_parser.py`** | `~/.claude/projects/<slug>/*.jsonl` | `(file_path, byte_offset)` | 讀取 `type in ("user", "assistant")`；從 `cwd` 提取 `project_path`，`gitBranch` 提取分支；合併連續 tool_use 為單行 Action 摘要。 |
| **`codex_parser.py`** | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `(file_path, byte_offset)` | 讀取 JSONL 事件中的 `message`、`function_call`、`function_call_output` 與 `timestamp`。 |
| **`opencode_parser.py`** | `~/.local/share/opencode/opencode.db` | `(session_id, last_part_time_created)` | 以唯讀模式連線 `file:...?mode=ro`；查詢 `session`、`message`、`part` 表；僅提取文字與工具名稱摘要，**跳過 `part` 表中數 MB 的 raw tool output**。 |
| **`omp_parser.py`** | `~/.omp/agent/sessions/<slug>/*.jsonl` | `(file_path, byte_offset)` | 解析 Oh My Pi 的 session JSONL 事件（`user`、`assistant`、`toolResult`）與專案路徑。 |
| **`antigravity_parser.py`** | `~/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl` | `(file_path, byte_offset)` | 解析 Google Antigravity 的 JSONL 事件流（`USER_INPUT`、`PLANNER_RESPONSE`、工具摘要）與專案工作區路徑。 |

---

## 4. 增量尾部切塊與儲存規格（`chunker.py` & `store.py`）

直接移植 `/Volumes/Samsung970EVOPlus/dev-projects/raw-rag/repo/methods/EverCore/evaluation/src/adapters/rag_adapter.py` 的核心機制：

1. **增量切塊（`add_incremental_turns`）**：
   - 每個 `session_id` 在資料庫保存：
     - `turn_lines: List[str]`
     - `turn_spans: List[Tuple[int, int]]`（每個 Turn 在 `cl100k_base` token 流中的 `[start_tok, end_tok]`）
     - `turn_timestamps: List[str]`
     - `turn_dates: List[str]`（`YYYY-MM-DD`）
   - 當新 Turn 追加時：
     - 鎖定 `old_chunks[:-1]`（所有已填滿 `chunk_size = 256` tokens 的歷史區塊為 `stable_chunks`，**不重新計算 Embedding**）。
     - 從最後一個未滿區塊的 `start_token` 開始，以 `chunk_size = 256`、`chunk_overlap = 32`（步長 224）切出新的 `tail_chunks`。
     - 呼叫 `_annotate_token_chunks_with_turns` 標記每個區塊的 `start_turn` 與 `end_turn`。
     - 僅對 `tail_chunks` 計算 Embedding 並寫入儲存層。

2. **儲存層架構與低膨脹設計（`store.py`）**：
   - 預設儲存路徑：`/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/data/raw_rag.db`（SQLite WAL 模式 + 浮點向量 Blob / NumPy memmap）。
   - **實體 Schema 為 3 張核心表**（不設獨立 `turns` 表）：
     - `ingestion_cursors(source_key TEXT PRIMARY KEY, agent TEXT, byte_offset INTEGER, last_timestamp INTEGER, updated_at TEXT)`：各 Agent 檔案增量位移與時間戳記。
     - `sessions(session_id TEXT PRIMARY KEY, agent TEXT, project_path TEXT, git_branch TEXT, header TEXT, turn_context_json TEXT, units_blob BLOB, total_tokens INTEGER, updated_at TEXT)`：會話階層中繼資料，對話脈絡直接以 `turn_context_json` 緊湊序列化儲存。**設計優勢**：若將數十萬對白拆為獨立 `turns` 表，將引發數百萬行小列索引膨脹、B-Tree 深度增加與頻繁 WAL 寫入競爭；以 JSON 序列化封裝可維持 $O(1)$ 單會話寫入與極低磁碟開銷。
     - `chunks(chunk_id TEXT PRIMARY KEY, session_id TEXT, chunk_index INTEGER, project_path TEXT, agent TEXT, content TEXT, start_token INTEGER, end_token INTEGER, start_turn INTEGER, end_turn INTEGER, start_date TEXT, end_date TEXT, session_timestamp TEXT, embedding BLOB)`：256-token 緊湊切片與 Dense 向量。

---

## 5. 本機 MLX 兩階段檢索與呈現期展開（`mlx_retriever.py`）

1. **模型配置（`HF_HOME=/Volumes/Samsung980/hf-home/`）**：
   - Embedding：`mlx-community/Qwen3-Embedding-4B-4bit-DWQ`（對應 `Qwen/Qwen3-Embedding-4B`，2560 維，支援指令前綴 `Instruct: Given a search query, retrieve relevant conversation passages that answer the query\nQuery: `）。
   - Reranker：預設採用 `mlx-community/Qwen3-Reranker-4B-4bit`（或 `mxfp8`），並顯式支援低延遲模式 `mlx-community/Qwen3-Reranker-0.6B-4bit`。以 2-column Logit Projection 最佳化計算 `yes` / `no` 分數。
2. **兩階段延遲檢索與批次展開五步驟（消除全表暴力載入與 N+1 查詢）**：
   - **Step 1（範圍過濾與輕量向量投影載入）**：透過 `store.load_candidate_vector_index` 僅自 `chunks` 表撈取 `chunk_id` 與 `embedding` BLOB，不載入 `content` 與中繼欄位，將十萬切片記憶體峰值壓制在 100 MB 內（降低 85% 記憶體開銷）。
   - **Step 2（Dense 粗排 Top-128）**：以矩陣內積（Cosine Similarity）快速計算分數，選出前 `candidate_top_k = 128` 之 `chunk_id` 集合。
   - **Step 2.5（精準延遲水合 Deferred Hydration）**：透過 `store.get_chunks_by_ids` 僅針對 Top-128 切片批次自 SQLite 載入完整 `content` 與回合座標，省略向量 BLOB，節省後續記憶體。
   - **Step 3（Cross-Encoder 精排 Top-15 & Dynamic Top-K）**：以 `Qwen3-Reranker` 對 128 個候選評分，取前 `rerank_top_k = 15`。**預設啟用 `dynamic_top_k: true`**：保留 `rerank_score >= 0.5` 之區塊，不足時保底取前 3 個高分切片，嚴格過濾無關雜訊。
   - **Step 4（時序升冪重排）**：依 `(session_start_timestamp, start_turn, start_token, chunk_index)` **由舊到新排序**，讓 CLI Agent 直接看清狀態與決策的演進順序。
   - **Step 5（單次批次動態展開，`get_turn_contexts_batch`）**：在動態展開前，以 Set 聚合命中切片的 `session_ids`，單次批次查詢 `sessions` 表並透過記憶體 LRU 快取解析 `TurnContext`（**將資料庫查詢次數自 $N$ 次降為嚴格 1 次，徹底根除 N+1 查詢**），隨後呼叫 `_expand_chunk_to_turns` 展開完整對話回合並自動去除重疊回合。
3. **推論引擎可觀測性與真實權重驗證**：
   - `MLXRetriever.engine_status` 提供執行期硬體加速狀態（`Apple Silicon Metal GPU [MLX_ACCELERATED]` vs `CPU [FALLBACK]`）、Embedding 維度（2560d）、模型載入狀態及 Dynamic Top-K 門檻設定。
   - `raw-rag-ingest --status` 與 MCP 工具 `raw_rag_status` 整合顯示引擎狀態，徹底消除無模型時靜默降級造成的品質誤判。
   - 新增 `tests/test_retrieval_real_weights.py` 端到端真實權重評估套件，使用本機真實模型驗證跨領域對話檢索之語意召回率。

---

## 6. MCP 工具規格（`mcp_server.py`）

提供以下 4 個 MCP Tools 給 Claude Code、Codex、OpenCode、Oh My Pi 呼叫：

1. **`raw_rag_search`**
   - **參數**：
     - `query: str`（必填，自然語言問題或關鍵字）
     - `project_path: Optional[str] = None`（預設優先搜尋當前專案，設為 `"*"` 則跨所有專案搜尋）
     - `agent: Optional[str] = None`（可限定 `"claude" | "codex" | "opencode" | "omp"`）
     - `after_date: Optional[str] = None`（`YYYY-MM-DD`）
     - `before_date: Optional[str] = None`（`YYYY-MM-DD`）
     - `top_k: int = 15`
   - **回傳**：格式化之 `Memory 1 ... Memory k`（含來源 Agent、日期、專案、相似度與精排分數，以及展開後的完整對話回合）。
2. **`raw_rag_recent`**
   - **參數**：`project_path: Optional[str] = None`, `limit_turns: int = 30`
   - **用途**：剛開啟新 Session 或剛執行完 `/compact` 時，快速取回該專案跨工具最近 `N` 輪完整對話與動作摘要。
3. **`raw_rag_ingest`**
   - **參數**：`agent: Optional[str] = None`（預設同步全部四套工具之增量更新）
   - **回傳**：本次新增的 Session 數、新增 Turn 數、尾部重算之 Chunk 數與耗時（毫秒）。
4. **`raw_rag_status`**
   - **回傳**：目前索引中的總 Session 數、總 Chunk 數、各 Agent（Claude / Codex / OpenCode / OMP）佔比與最後同步時間。
