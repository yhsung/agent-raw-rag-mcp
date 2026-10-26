"""Unit tests for status cursor observability and multi-agent table formatting."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_raw_rag.cli import (
    format_datetime_display,
    format_project_context,
    main,
    render_status_text,
    render_table,
)
from agent_raw_rag.config import AppConfig, StorageConfig
from agent_raw_rag.engine.store import SQLiteStore
from agent_raw_rag.models import (
    ChunkRecord,
    IngestionCursor,
    PendingSessionUpdate,
    SessionMetadata,
    TurnContext,
)


@pytest.fixture
def temp_store(tmp_path: Path) -> SQLiteStore:
    db_file = tmp_path / "test_status.db"
    cfg = StorageConfig(sqlite_path=str(db_file))
    return SQLiteStore(cfg)


def test_store_get_stats_multi_agent(temp_store: SQLiteStore) -> None:
    # 1. Setup multi-agent session updates
    # Agent 1: antigravity (2 sessions, 3 chunks)
    meta_ag1 = SessionMetadata(
        session_id="ag_sess_1",
        agent="antigravity",
        project_path="/Volumes/Samsung970EVOPlus/dev-projects/demo\n\"",
    )
    c1 = ChunkRecord(
        chunk_id="chk_ag_1",
        session_id="ag_sess_1",
        chunk_index=0,
        project_path="/Volumes/Samsung970EVOPlus/dev-projects/demo\n\"",
        agent="antigravity",
        content="Antigravity prompt 1",
        start_token=0,
        end_token=10,
        session_timestamp="2026-10-26T05:44:31+00:00",
    )
    c2 = ChunkRecord(
        chunk_id="chk_ag_2",
        session_id="ag_sess_1",
        chunk_index=1,
        project_path="/Volumes/Samsung970EVOPlus/dev-projects/demo\n\"",
        agent="antigravity",
        content="Antigravity answer 1",
        start_token=10,
        end_token=20,
        session_timestamp="2026-10-26T05:45:00+00:00",
    )
    upd_ag1 = PendingSessionUpdate(
        meta=meta_ag1,
        src_key="antigravity:sess_1.jsonl",
        agent="antigravity",
        new_offset=1234,
        new_timestamp=1000,
        updated_context=TurnContext(
            header="H",
            turn_lines=["T1"],
            turn_spans=[(0, 10)],
            turn_timestamps=["2026-10-26T05:45:00Z"],
            turn_session_dates=["2026-10-26"],
        ),
        updated_units=[],
        all_chunks=[c1, c2],
        tail_chunks=[c1, c2],
        first_changed_idx=0,
    )

    # Agent 2: codex (1 session, 1 chunk)
    meta_codex = SessionMetadata(
        session_id="codex_sess_1",
        agent="codex",
        project_path="/Users/yhsung/workspace/app",
    )
    c3 = ChunkRecord(
        chunk_id="chk_codex_1",
        session_id="codex_sess_1",
        chunk_index=0,
        project_path="/Users/yhsung/workspace/app",
        agent="codex",
        content="Codex prompt 1",
        start_token=0,
        end_token=15,
        session_timestamp="2026-10-25T10:00:00+00:00",
    )
    upd_codex = PendingSessionUpdate(
        meta=meta_codex,
        src_key="codex:rollout-1.jsonl",
        agent="codex",
        new_offset=5678,
        new_timestamp=2000,
        updated_context=TurnContext(
            header="H",
            turn_lines=["C1"],
            turn_spans=[(0, 15)],
            turn_timestamps=["2026-10-25T10:00:00Z"],
            turn_session_dates=["2026-10-25"],
        ),
        updated_units=[],
        all_chunks=[c3],
        tail_chunks=[c3],
        first_changed_idx=0,
    )

    # Commit updates atomically
    temp_store.commit_batch_updates([upd_ag1, upd_codex])

    # Also add a second cursor for antigravity to test prevention of Cartesian product
    temp_store.save_cursor(
        IngestionCursor(
            source_key="antigravity:sess_2.jsonl",
            agent="antigravity",
            byte_offset=999,
            last_timestamp=0,
        )
    )

    stats = temp_store.get_stats()

    assert stats["total_sessions"] == 2
    assert stats["total_chunks"] == 3
    assert stats["chunks_by_agent"]["antigravity"] == 2
    assert stats["chunks_by_agent"]["codex"] == 1

    agents = stats["agents"]
    assert "antigravity" in agents
    assert "codex" in agents

    ag_stat = agents["antigravity"]
    assert ag_stat["sessions"] == 1
    assert ag_stat["chunks"] == 2
    assert ag_stat["latest_dialogue_time"] == "2026-10-26T05:45:00+00:00"
    assert ag_stat["last_ingested_at"] is not None
    assert ag_stat["latest_project_path"] == "/Volumes/Samsung970EVOPlus/dev-projects/demo"

    codex_stat = agents["codex"]
    assert codex_stat["sessions"] == 1
    assert codex_stat["chunks"] == 1
    assert codex_stat["latest_dialogue_time"] == "2026-10-25T10:00:00+00:00"
    assert codex_stat["latest_project_path"] == "/Users/yhsung/workspace/app"


def test_store_get_agent_cursors(temp_store: SQLiteStore) -> None:
    temp_store.save_cursor(
        IngestionCursor(
            source_key="ag:file1.jsonl",
            agent="antigravity",
            byte_offset=100,
            last_timestamp=1,
        )
    )
    temp_store.save_cursor(
        IngestionCursor(
            source_key="ag:file2.jsonl",
            agent="antigravity",
            byte_offset=200,
            last_timestamp=2,
        )
    )
    temp_store.save_cursor(
        IngestionCursor(
            source_key="other:file.jsonl",
            agent="codex",
            byte_offset=50,
            last_timestamp=0,
        )
    )

    cursors = temp_store.get_agent_cursors("antigravity")
    assert len(cursors) == 2
    keys = {c["source_key"] for c in cursors}
    assert keys == {"ag:file1.jsonl", "ag:file2.jsonl"}

    codex_cursors = temp_store.get_agent_cursors("codex")
    assert len(codex_cursors) == 1
    assert codex_cursors[0]["byte_offset"] == 50


def test_formatting_helpers() -> None:
    # 1. Datetime formatting
    assert format_datetime_display(None) == "-"
    assert format_datetime_display("") == "-"
    # Naive ISO
    formatted_naive = format_datetime_display("2026-10-26T13:30:00")
    assert formatted_naive == "2026-10-26 13:30:00"
    # UTC ISO conversion
    formatted_utc = format_datetime_display("2026-10-26T05:00:00+00:00")
    assert "2026-10-26" in formatted_utc

    # 2. Project context trimming
    assert format_project_context(None) == "-"
    assert format_project_context("") == "-"
    assert (
        format_project_context("/Volumes/Samsung970EVOPlus/dev-projects/demo\n\"")
        == "dev-projects/demo"
    )
    assert format_project_context("/Users/yhsung/projects/agent") == "projects/agent"
    long_path = "a" * 50
    trimmed = format_project_context(long_path, max_width=20)
    assert len(trimmed) == 20
    assert trimmed.startswith("...")

    # 3. Table rendering
    headers = ["ColA", "ColB"]
    rows = [["Val1", "123"], ["LongVal2", "4567"]]
    tbl = render_table(headers, rows, aligns=["left", "right"])
    assert "┌" in tbl and "┐" in tbl and "└" in tbl and "┘" in tbl
    assert "ColA" in tbl and "ColB" in tbl
    assert "Val1" in tbl and "123" in tbl


def test_cli_status_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    import yaml

    db_file = tmp_path / "cli_test.db"
    cfg_file = tmp_path / "config.yaml"
    with open(cfg_file, "w", encoding="utf-8") as f:
        yaml.safe_dump({"storage": {"sqlite_path": str(db_file)}}, f)

    store = SQLiteStore(StorageConfig(sqlite_path=str(db_file)))
    store.save_cursor(
        IngestionCursor(
            source_key="antigravity:transcript.jsonl",
            agent="antigravity",
            byte_offset=12345,
            last_timestamp=0,
        )
    )

    # Test 1: standard status output
    monkeypatch.setattr(
        "sys.argv",
        ["raw-rag-ingest", "--config", str(cfg_file), "--status"],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr().out
    assert "=== agent-raw-rag-mcp Storage Status ===" in captured
    assert "antigravity" in captured
    assert "┌" in captured

    # Test 2: JSON output
    monkeypatch.setattr(
        "sys.argv",
        ["raw-rag-ingest", "--config", str(cfg_file), "--status", "--json"],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured_json = capsys.readouterr().out
    data = json.loads(captured_json)
    assert data["storage_path"] == str(db_file)
    assert "agents" in data
    assert "antigravity" in data["agents"]

    # Test 3: Single agent with cursors
    monkeypatch.setattr(
        "sys.argv",
        [
            "raw-rag-ingest",
            "--config",
            str(cfg_file),
            "--status",
            "--agent",
            "antigravity",
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured_agent_json = capsys.readouterr().out
    agent_data = json.loads(captured_agent_json)
    assert "cursors" in agent_data
    assert len(agent_data["cursors"]) == 1
    assert agent_data["cursors"][0]["source_key"] == "antigravity:transcript.jsonl"
