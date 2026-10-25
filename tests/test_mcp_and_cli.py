import pytest
from pathlib import Path
from agent_raw_rag.config import AppConfig, StorageConfig
from agent_raw_rag.cli import IngestionEngine
from agent_raw_rag.mcp_server import mcp, raw_rag_status, raw_rag_recent, raw_rag_ingest


def test_mcp_tools_and_engine(tmp_path: Path):
    db_file = tmp_path / "mcp_test.db"
    cfg = AppConfig()
    cfg.storage = StorageConfig(sqlite_path=str(db_file))
    cfg.resolve_paths()

    engine = IngestionEngine(cfg)
    status_dict = engine.store.get_stats()
    assert status_dict["total_sessions"] == 0
    assert status_dict["total_chunks"] == 0

    # Test raw_rag_status tool output
    res = raw_rag_status()
    assert "agent-raw-rag-mcp Status" in res
