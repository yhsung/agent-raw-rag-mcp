# `agent-raw-rag-mcp` 本機實戰安裝與配置範例（Example Guide）

本文件記錄在 **Apple Silicon 64GB Mac** 主機上，將 `agent-raw-rag-mcp` 整合至 **Claude Code**、**Codex CLI**、**OpenCode**、**Oh My Pi (`omp`)** 與 **Antigravity** 的完整設定範例。

---

## 1. 環境與模型配置（專屬外接碟範例）

### 1.1 硬體與環境變數
- **主機配置**：Apple Silicon（64GB Unified Memory）
- **專案路徑**：`/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp`
- **模型儲存目錄**：`/Volumes/Samsung980/hf-home/`（避免模型權重佔用系統空間或暫存目錄）

### 1.2 MLX 量化模型選用
本服務在檢索時使用 Apple Silicon 原生 MLX 4-bit 量化模型（推論速度快、記憶體佔用極低）：
- **Embedding**：`mlx-community/Qwen3-Embedding-4B-4bit-DWQ`
- **Reranker**：`mlx-community/Qwen3-Reranker-0.6B-4bit`

---

## 2. 安裝與虛擬環境建立

```bash
cd /Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp

# 使用 uv 建立 Python 3.12 虛擬環境並安裝依賴
uv venv
source .venv/bin/activate
uv pip install -e . pytest
```

---

## 3. 各 CLI Agent 的設定檔範例

### 3.1 Claude Code
在 `~/.claude/.mcp.json`（或 `~/.claude/settings.json`）中加入：

```json
{
  "mcpServers": {
    "raw-rag": {
      "command": "uvx",
      "args": [
        "--from",
        "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp",
        "agent-raw-rag-mcp"
      ],
      "env": {
        "HF_HOME": "/Volumes/Samsung980/hf-home/"
      }
    }
  }
}
```

> **提示**：若透過 PyPI 發行版本安裝，`args` 僅需填入 `["agent-raw-rag-mcp"]` 即可。

---

### 3.2 Codex CLI
在 `~/.codex/config.toml` 末尾加入：

```toml
[mcp_servers.raw_rag]
command = "uvx"
args = ["--from", "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp", "agent-raw-rag-mcp"]

[mcp_servers.raw_rag.env]
HF_HOME = "/Volumes/Samsung980/hf-home/"
```

---

### 3.3 OpenCode
在 `~/.config/opencode/opencode.jsonc`（或 `opencode.json`）的 `"mcp"` 區塊中加入：

```json
{
  "mcp": {
    "raw-rag": {
      "type": "local",
      "command": [
        "uvx",
        "--from",
        "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp",
        "agent-raw-rag-mcp"
      ],
      "enabled": true,
      "environment": {
        "HF_HOME": "/Volumes/Samsung980/hf-home/"
      }
    }
  }
}
```

---

### 3.4 Oh My Pi (`omp` / `pi`)

#### A. 全域 MCP 設定
`omp` 原生讀取的標準全域 MCP 設定檔為 **`~/.omp/agent/mcp.json`**（或專案根目錄的 `mcp.json`），內容格式如下：

```json
{
  "mcpServers": {
    "raw-rag": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp",
        "agent-raw-rag-mcp"
      ],
      "env": {
        "HF_HOME": "/Volumes/Samsung980/hf-home/"
      }
    }
  }
}
```

#### B. 跨工具工具註冊 Extension
將 [`configs/omp_extension_raw_rag.ts`](./configs/omp_extension_raw_rag.ts) 複製或軟連結至擴充目錄：
```bash
ln -sf /Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/configs/omp_extension_raw_rag.ts ~/.omp/agent/extensions/raw-rag.ts
```
> 此腳本為 OMP 註冊 `raw_rag_search`、`raw_rag_recent` 與 `raw_rag_status` 工具。增量索引已改由常駐守護進程（`raw-rag-ingest --watch` / LaunchAgent）以 Pull-based 方式自動掃描落盤，移除原本的 `turn:complete` hook 以免造成多 Agent 併發推論衝突與 GPU 搶佔。

---

### 3.5 Antigravity / Gemini CLI
在 `~/.gemini/config/mcp_config.json` 與 `~/.gemini/settings.json` 加入：

```json
{
  "mcpServers": {
    "raw-rag": {
      "command": "uvx",
      "args": [
        "--from",
        "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp",
        "agent-raw-rag-mcp"
      ],
      "env": {
        "HF_HOME": "/Volumes/Samsung980/hf-home/"
      }
    }
  }
}
```

---

## 4. 索引維護與狀態檢查

### 4.1 手動增量同步
```bash
/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/.venv/bin/raw-rag-ingest
```

### 4.2 查看資料庫狀態
```bash
/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/.venv/bin/raw-rag-ingest --status
```
*預期輸出範例*：
```text
=== agent-raw-rag-mcp Storage Status ===
Database: /Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/data/raw_rag.db
Total Sessions:   404
Total 256t Chunks: 86624
Chunks by Agent:  {'claude': 4627, 'codex': 6512, 'omp': 73127, 'opencode': 2358}
Last Updated:   2026-10-26T07:30:02.317858
```

### 4.3 背景守護程序（推薦）
若希望持續自動同步，可啟動背景輪詢模式（每 30 秒自動掃描四大工具的新對話）：
```bash
nohup /Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/.venv/bin/raw-rag-ingest --watch > /dev/null 2>&1 &
```

---

## 5. 日常對話使用範例（Prompting）

在 Claude Code、Codex、OpenCode、OMP 或 Antigravity 中，直接在對話中呼叫：

### 範例 1：跨工具檢索歷史決策
> 「請使用 `raw_rag_search` 查詢我們先前在其他工具討論過的 JWT 認證遷移方案。」

### 範例 2：`/compact` 後無損恢復脈絡
> 「剛才執行了 `/compact`，請調用 `raw_rag_recent` 幫我還原最近 30 輪的討論與修改重點。」

### 範例 3：全域跨專案搜尋技術結論
> 「請調用 `raw_rag_search(query='MLX benchmark', project_path='*')`，幫我找跨專案所有關於 MLX 效能評測的結論。」
