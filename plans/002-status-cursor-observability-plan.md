# Raw-RAG CLI `--status` 多 Agent 游標進度與時序可觀測性規劃

> **專案**：`agent-raw-rag-mcp`  
> **關聯架構報告**：[`reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md`](../reports/004-raw-rag-ingestion-concurrency-and-failsafe-architecture.md)  
> **規劃目標**：升級 `--status` 輸出，提供每個 Agent 攝取游標（Ingestion Cursor）的即時進度、對話時序新鮮度（Latest Dialogue Time）與最新活躍工作區上下文。  
> **日期**：2026-10-26  
> **環境**：Apple Silicon, macOS, SQLite WAL, Python 3.12  

---

## 1. 背景與問題定義（Motivation & Problem Statement）

在多 CLI Coding Agent 並行工作的場景中（同時使用 Antigravity、Claude Code、Codex CLI、OpenCode、Oh My Pi），開發者透過常駐守護進程（Daemon）進行日誌游標拉取（Pull-based Log Ingestion）。

目前執行 `raw-rag-ingest --status` 時，系統僅輸出全域匯總與各 Agent 的切片數量字典：
```text
=== agent-raw-rag-mcp Storage Status ===
Database: /Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/data/raw_rag.db
Total Sessions: 1075
Total Chunks:   96899
Chunks by Agent: {'antigravity': 1166, 'claude': 4627, 'codex': 6747, 'omp': 73242, 'opencode': 11117}
Last Updated:   2026-10-26T13:30:06.089563
```

### 現有輸出的痛點
1. **無法得知對話新鮮度**：無法直觀看出各 Agent「最新處理到何時的對話」（例如 Antigravity 是今天下午的對話，還是停留在昨天）。
2. **無法辨別同步即時性（Lag / Staleness）**：無法看出某個 Agent 是「已追平目前最新對話」，還是守護進程尚未掃描到。
3. **缺乏工作區上下文**：不知道各 Agent 目前最新處理的是哪一個專案目錄或對話會話。

---

## 2. 核心指標與時間維度定義（Data Dimensions）

每個 Agent 應提供以下三個關鍵維度：

| 欄位名稱 | 意義說明 | 資料來源 |
| :--- | :--- | :--- |
| **`Sessions`** | 該 Agent 於資料庫中已建立的會話總數 | `sessions` 表 `COUNT(session_id)` |
| **`Chunks`** | 該 Agent 已切塊並完成向量索引的總切片數 | `chunks` 表 `COUNT(chunk_id)` |
| **`Latest Dialogue`** | 該 Agent **歷史紀錄中最後一則訊息的真實時間戳記** | `chunks.session_timestamp` / `chunks.end_date` 之 `MAX` |
| **`Last Ingested At`** | 守護進程**最後一次為該 Agent 執行掃描與落盤的時間** | `ingestion_cursors.updated_at` 之 `MAX` |
| **`Latest Active Context`** | 該 Agent 最新落盤對話所在的專案路徑或工作區特徵 | 依 `session_timestamp DESC` 排序取第一筆的 `project_path` |

---

## 3. 資料庫查詢層設計（Zero-Schema-Change Query Architecture）

現有 SQLite WAL 資料庫架構已具備完整資料，**無須改動任何 Schema**，僅需在 `SQLiteStore.get_stats()` 擴充高效聚合查詢：

### 3.1 各 Agent 聚合資訊查詢
```sql
SELECT 
    c.agent,
    COUNT(DISTINCT c.session_id) AS session_count,
    COUNT(c.chunk_id) AS chunk_count,
    MAX(c.session_timestamp) AS latest_dialogue_time,
    MAX(cur.updated_at) AS last_sync_time
FROM chunks c
LEFT JOIN ingestion_cursors cur ON cur.agent = c.agent
GROUP BY c.agent;
```

### 3.2 各 Agent 最新活躍專案路徑提取
針對各個 Agent，以時序降冪取得最新一筆切片之 `project_path`：
```sql
SELECT project_path
FROM chunks
WHERE agent = :agent AND project_path != ''
ORDER BY session_timestamp DESC, chunk_index DESC
LIMIT 1;
```

---

## 4. 終端機呈現規範（Terminal UI & Output Format）

### 4.1 預設 ASCII 表格格式（人類可讀易掃讀）
執行 `raw-rag-ingest --status` 時呈現：

```text
=== agent-raw-rag-mcp Storage Status ===
Database: /Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/data/raw_rag.db
Total Sessions: 1,075 | Total Chunks: 96,899 | Status: ACTIVE

Agent Ingestion Cursors & Dialogue Freshness:
┌─────────────┬───────────┬──────────────┬─────────────────────┬─────────────────────┬───────────────────────────────┐
│ Agent       │ Sessions  │ Chunks       │ Latest Dialogue     │ Last Ingested At    │ Latest Active Context         │
├─────────────┼───────────┼──────────────┼─────────────────────┼─────────────────────┼───────────────────────────────┤
│ antigravity │ 59        │ 1,166        │ 2026-10-26 13:34:10 │ 2026-10-26 13:34:12 │ dev-projects/agent-raw-rag    │
│ omp         │ 382       │ 73,242       │ 2026-10-26 12:26:01 │ 2026-10-26 13:30:06 │ dev-projects/omp-function-gem │
│ opencode    │ 512       │ 11,117       │ 2026-10-26 11:45:33 │ 2026-10-26 13:30:06 │ dev-projects/bois-opencode    │
│ codex       │ 82        │ 6,747        │ 2026-10-25 23:12:00 │ 2026-10-26 13:30:06 │ dev-projects/agent-raw-rag    │
│ claude      │ 40        │ 4,627        │ 2026-10-25 18:05:44 │ 2026-10-26 13:30:06 │ dev-projects/agent-raw-rag    │
└─────────────┴───────────┴──────────────┴─────────────────────┴─────────────────────┴───────────────────────────────┘
```

### 4.2 程式化 JSON 格式支援（`--json`）
執行 `raw-rag-ingest --status --json` 時輸出標準 JSON，方便 MCP Client、Webview、指令碼或擴充套件進行自動化探測：

```json
{
  "storage_path": "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/data/raw_rag.db",
  "total_sessions": 1075,
  "total_chunks": 96899,
  "overall_last_updated": "2026-10-26T13:30:06.089563",
  "agents": {
    "antigravity": {
      "sessions": 59,
      "chunks": 1166,
      "latest_dialogue_time": "2026-10-26T13:34:10Z",
      "last_ingested_at": "2026-10-26T13:34:12.124500",
      "latest_project_path": "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp"
    },
    "omp": {
      "sessions": 382,
      "chunks": 73242,
      "latest_dialogue_time": "2026-10-26T12:26:01Z",
      "last_ingested_at": "2026-10-26T13:30:06.089563",
      "latest_project_path": "/Volumes/Samsung970EVOPlus/dev-projects/omp-function-gemma"
    }
  }
}
```

### 4.3 單一 Agent 細節檢視模式（可選支援 `--agent <name>`）
當指定 `raw-rag-ingest --status --agent antigravity` 時，額外展開顯示該 Agent 下各個 Session 檔案的游標明細（檔案路徑、byte_offset、更新時間）。

---

## 5. 模組變更清單與實作步驟

```
agent-raw-rag-mcp/
├── src/agent_raw_rag/
│   ├── engine/
│   │   └── store.py       # [MOD] 擴充 get_stats() 回傳各 Agent 之游標時序與工作區資料
│   └── cli.py             # [MOD] 實作 ASCII 表格排版渲染與 --json 參數
├── tests/
│   └── test_status_cli.py # [NEW] 驗證各 Agent 時序提取正確性與表格輸出
└── plans/
    └── 002-status-cursor-observability-plan.md # [NEW] 本規劃文件
```

### 實作步驟
- [x] **步驟 1**：在 `store.py` 的 `SQLiteStore.get_stats()` 實作聚合查詢邏輯，計算每個 Agent 的 `latest_dialogue_time`、`last_ingested_at` 與 `latest_project`。
- [x] **步驟 2**：在 `cli.py` 擴充 `main()`，支援 `--json` 旗標，並編寫輕量無額外依賴的 ASCII 表格排版輔助函式。
- [x] **步驟 3**：撰寫單元測試 `tests/test_status_cli.py`，驗證時間排序與統計資料正確性。
