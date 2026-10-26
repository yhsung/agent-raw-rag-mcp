"""Tests for uvx integration, snippet configs, package resources, and LaunchAgent cache warnings."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
import tomllib
import pytest

from agent_raw_rag.config import default_sqlite_path, load_config
from agent_raw_rag.cli import install_launch_agent


def test_snippet_configs_validity_and_uvx_entrypoint():
    """Verify that all Agent snippet configurations are valid and use uvx with agent-raw-rag-mcp."""
    configs_dir = Path(__file__).resolve().parent.parent / "configs"

    # 1. Claude snippet
    claude_path = configs_dir / "claude_hooks_snippet.json"
    assert claude_path.exists(), "claude_hooks_snippet.json must exist"
    with open(claude_path, "r", encoding="utf-8") as f:
        claude_data = json.load(f)
    claude_mcp = claude_data["mcpServers"]["raw-rag"]
    assert claude_mcp["command"] == "uvx"
    assert "agent-raw-rag-mcp" in claude_mcp["args"]

    # 2. Codex snippet
    codex_path = configs_dir / "codex_config_snippet.toml"
    assert codex_path.exists(), "codex_config_snippet.toml must exist"
    with open(codex_path, "rb") as f:
        codex_data = tomllib.load(f)
    codex_mcp = codex_data["mcp_servers"]["raw_rag"]
    assert codex_mcp["command"] == "uvx"
    assert "agent-raw-rag-mcp" in codex_mcp["args"]

    # 3. OpenCode snippet
    opencode_path = configs_dir / "opencode_snippet.json"
    assert opencode_path.exists(), "opencode_snippet.json must exist"
    with open(opencode_path, "r", encoding="utf-8") as f:
        opencode_data = json.load(f)
    opencode_mcp = opencode_data["mcp"]["raw-rag"]
    assert opencode_mcp["command"][0] == "uvx"
    assert "agent-raw-rag-mcp" in opencode_mcp["command"]


def test_default_sqlite_path_env_and_fallback(tmp_path):
    """Test default_sqlite_path resolution logic."""
    # 1. Custom env var takes precedence
    custom_db = str(tmp_path / "custom.db")
    with patch.dict(os.environ, {"RAW_RAG_DB_PATH": custom_db}):
        assert default_sqlite_path() == custom_db

    # 2. Without env var, if dev path doesn't exist, fallback to home directory
    with patch.dict(os.environ, {}, clear=True):
        with patch("pathlib.Path.exists", return_value=False):
            expected = str(Path.home() / ".agent-raw-rag" / "raw_rag.db")
            assert default_sqlite_path() == expected


def test_load_config_fallback_to_package_resource(tmp_path):
    """Verify load_config falls back to bundled resource when disk paths are not found."""
    # Point away from local project configs
    non_existent = str(tmp_path / "does_not_exist.yaml")
    with patch.dict(os.environ, {"RAW_RAG_CONFIG": non_existent}):
        with patch("pathlib.Path.is_file", return_value=False):
            # Patch is_file so that default_locations on disk return False,
            # but package resource remains readable
            cfg = load_config()
            assert cfg.chunking.chunk_size == 256
            assert cfg.chunking.chunk_overlap == 32
            assert cfg.retrieval.candidate_top_k == 128


def test_install_launch_agent_uvx_cache_warning(capsys):
    """Verify install_launch_agent prints warning when running inside a temporary .cache/uv environment."""
    fake_python = "/Users/test/.cache/uv/archive-abc123/bin/python"
    with patch.object(sys, "executable", fake_python):
        with patch("subprocess.run") as mock_subproc:
            mock_subproc.return_value.returncode = 0
            with patch("pathlib.Path.write_text"):
                res = install_launch_agent(interval=30)
                captured = capsys.readouterr()
                assert "⚠️ 警告：偵測到正在 uvx 暫存環境中執行" in captured.out
                assert res == 0
