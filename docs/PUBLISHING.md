# `agent-raw-rag-mcp` 發布指南（PyPI Distribution Guide）

本文件詳細記載 `agent-raw-rag-mcp` 的建置稽核、TestPyPI 演練與正式 PyPI 發布標準作業程序（SOP）。

---

## 1. 架構原則與安全不變量

1. **先本機位置（Local `--from`），最後 PyPI**：
   在發布至 PyPI 前，必須確認本機執行已 100% 綠燈：
   ```bash
   uvx --from . agent-raw-rag-mcp --help
   uvx --from . raw-rag-ingest --status
   ```
2. **私有資料 100% 排除（Zero Data Leakage Invariant）**：
   打包出的 Wheel 與 Sdist **嚴禁**包含任何 `data/`、`*.db`、`*.wal`、`*.shm` 或 `transcript.jsonl` 等機敏對白與向量快取檔案。
   此規則已由 `tests/test_package_build.py` 進行常態性自動化稽核。

---

## 2. 本機建置與稽核（Pre-flight Verification）

在發布前，請務必先於本機執行全套打包安全測試：

```bash
# 1. 執行打包安全性測試
uv run pytest tests/test_package_build.py tests/test_uvx_integration.py -v

# 2. 產出發行檔案
uv build

# 3. 檢視 dist/ 目錄
ls -lh dist/
```

預期產出：
- `dist/agent_raw_rag_mcp-<version>-py3-none-any.whl`
- `dist/agent_raw_rag_mcp-<version>.tar.gz`

---

## 3. TestPyPI 驗證演練（TestPyPI Staging）

建議在正式發布至生產 PyPI 前，先上架至 TestPyPI 進行驗證：

### 3.1 手動上傳至 TestPyPI
使用 `uv publish` 上傳：
```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token <TEST_PYPI_TOKEN>
```

### 3.2 驗證 TestPyPI 套件拉取與執行
在另一個乾淨目錄（如 `/tmp`）驗證拉取執行：
```bash
# 驗證 MCP Server 進入點
uvx --index https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ agent-raw-rag-mcp --help

# 驗證 CLI Ingestion 進入點
uvx --from agent-raw-rag-mcp --index https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ raw-rag-ingest --help
```

---

## 4. 正式生產發布（Production Release to PyPI）

### 方法 A：GitHub Actions 自動化發布（推薦）
本專案已配置 Trusted Publisher 工作流（`.github/workflows/publish.yml`）：
1. 確保 `pyproject.toml` 中的 `version` 已更新（如 `0.1.0`）。
2. 建立並推送 Git Tag：
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
3. GitHub Actions 會自動執行安全稽核、打包並發布至 PyPI。

### 方法 B：本機命令列發布
若具備 PyPI API Token，亦可直接使用 `uv publish`：
```bash
uv publish --token <PYPI_API_TOKEN>
```

---

## 5. 發布後各 CLI Agent 接線（開箱即用模式）

發布至 PyPI 後，使用者無需再指定本機路徑 `--from`，直接透過 `uvx` 隨選運行：

### Claude Code (`~/.claude/.mcp.json`)
```json
{
  "mcpServers": {
    "raw-rag": {
      "command": "uvx",
      "args": ["agent-raw-rag-mcp"],
      "env": {
        "HF_HOME": "/Volumes/Samsung980/hf-home/"
      }
    }
  }
}
```

### Codex CLI (`~/.codex/config.toml`)
```toml
[mcp_servers.raw_rag]
command = "uvx"
args = ["agent-raw-rag-mcp"]

[mcp_servers.raw_rag.env]
HF_HOME = "/Volumes/Samsung980/hf-home/"
```

### OpenCode (`~/.config/opencode/opencode.jsonc`)
```json
{
  "mcp": {
    "raw-rag": {
      "type": "local",
      "command": ["uvx", "agent-raw-rag-mcp"],
      "environment": {
        "HF_HOME": "/Volumes/Samsung980/hf-home/"
      }
    }
  }
}
```

### 背景守護進程（LaunchAgent / CLI）
```bash
# 隨選查詢狀態
uvx --from agent-raw-rag-mcp raw-rag-ingest --status

# 隨選單次增量掃描
uvx --from agent-raw-rag-mcp raw-rag-ingest

# 安裝常駐服務（建議先透過 uv tool install 安裝持久 binary）
uv tool install agent-raw-rag-mcp
raw-rag-ingest --install-daemon
```
