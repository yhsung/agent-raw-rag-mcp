"""Unit tests for Poison-Pill defense, incomplete line recovery, and atomic commit rollback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import sqlite3
import numpy as np

from agent_raw_rag.cli import IngestionEngine
from agent_raw_rag.config import (
    AppConfig,
    SourceItemConfig,
    SourcesConfig,
    StorageConfig,
)
from agent_raw_rag.parsers.antigravity_parser import AntigravityParser
from agent_raw_rag.parsers.claude_parser import ClaudeParser
from agent_raw_rag.parsers.omp_parser import OmpParser


def test_incomplete_json_line_at_eof_retried_next_poll(tmp_path: Path):
    """When an agent is actively writing a line without trailing newline,

    offset should not advance past the line start, allowing clean retry.
    """
    brain_dir = tmp_path / "brain" / "partial-session" / ".system_generated" / "logs"
    brain_dir.mkdir(parents=True)
    transcript_file = brain_dir / "transcript.jsonl"

    # 1. Complete line 1
    ev1 = {
        "step_index": 0,
        "type": "USER_INPUT",
        "content": "<USER_REQUEST>First prompt</USER_REQUEST>",
    }
    with open(transcript_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(ev1) + "\n")

    parser = AntigravityParser()
    turns1, offset1, _ = parser.parse_file_incremental(transcript_file, start_offset=0)
    assert len(turns1) == 1
    assert offset1 > 0

    # 2. Append an incomplete line at EOF (no newline, partial JSON)
    with open(transcript_file, "a", encoding="utf-8") as f:
        f.write('{"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Working on it...')

    # Incremental parse should see the partial line, but NOT advance offset past offset1
    turns2, offset2, _ = parser.parse_file_incremental(transcript_file, start_offset=offset1)
    assert len(turns2) == 0
    assert offset2 == offset1  # Offset stays pinned at line start!

    # 3. Agent finishes writing the line and appends newline
    with open(transcript_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(ev1) + "\n")
        f.write(
            json.dumps(
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "content": "Working on it... Finished now!",
                }
            )
            + "\n"
        )

    # Next poll cleanly reads the completed line without representation loss
    turns3, offset3, _ = parser.parse_file_incremental(transcript_file, start_offset=offset1)
    assert len(turns3) == 1
    assert "Finished now!" in turns3[0].content
    assert offset3 > offset1


def test_malformed_isolated_line_skipped_safely(tmp_path: Path):
    """An isolated corrupted JSON line between valid lines is safely skipped."""
    claude_dir = tmp_path / "claude_project"
    claude_dir.mkdir(parents=True)
    claude_file = claude_dir / "session-corrupt.jsonl"

    with open(claude_file, "w", encoding="utf-8") as f:
        # Valid line 1
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "Prompt 1"},
                }
            )
            + "\n"
        )
        # Poison pill malformed line
        f.write("{this is not valid json content!!!\n")
        # Valid line 2
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "Answer 2"},
                }
            )
            + "\n"
        )

    parser = ClaudeParser()
    turns, new_offset, _ = parser.parse_file_incremental(claude_file, start_offset=0)

    # Both valid lines should be preserved, malformed line safely skipped
    assert len(turns) == 2
    assert turns[0].content == "Prompt 1"
    assert turns[1].content == "Answer 2"
    assert new_offset == claude_file.stat().st_size


def test_atomic_transaction_rollback_preserves_cursors(tmp_path: Path):
    """If database write fails mid-batch, entire transaction rolls back,

    ensuring cursor never advances past uncommitted data (At-Least-Once guarantee).
    """
    db_file = tmp_path / "raw_rag_rollback.db"
    claude_dir = tmp_path / "claude_p"
    claude_proj = claude_dir / "my_project"
    claude_proj.mkdir(parents=True)
    claude_file = claude_proj / "session-1.jsonl"

    with open(claude_file, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "s-1",
                    "message": {"content": "Hello world"},
                }
            )
            + "\n"
        )

    cfg = AppConfig(
        storage=StorageConfig(sqlite_path=str(db_file)),
        sources=SourcesConfig(
            claude_code=SourceItemConfig(enabled=True, path=str(claude_dir)),
            codex=SourceItemConfig(enabled=False, path=""),
            opencode=SourceItemConfig(enabled=False, path=""),
            oh_my_pi=SourceItemConfig(enabled=False, path=""),
            antigravity=SourceItemConfig(enabled=False, path=""),
        ),
    )
    engine = IngestionEngine(cfg)

    # Mock embed_texts to return valid dummy vector
    engine.retriever.embed_texts = MagicMock(return_value=[np.zeros(64, dtype=np.float32)])

    # Inject an error into sqlite cursor insertion
    orig_commit = engine.store.commit_batch_updates

    def failing_commit(updates):
        # Trigger an exception intentionally inside the transaction
        with engine.store._get_connection() as conn:
            conn.execute("INSERT INTO nonexistent_table VALUES (1)")

    with patch.object(engine.store, "commit_batch_updates", side_effect=sqlite3.OperationalError("Table missing")):
        with pytest.raises(sqlite3.OperationalError):
            # When commit fails
            updates, _ = engine.collect_claude()
            engine._execute_batch_embedding_and_commit(updates)

    # Verify that cursor was NEVER saved in the database
    with engine.store._get_connection() as conn:
        cursor_row = conn.execute("SELECT * FROM ingestion_cursors").fetchone()
        assert cursor_row is None

        chunks_row = conn.execute("SELECT * FROM chunks").fetchone()
        assert chunks_row is None

    # Now run without failure — should succeed idempotently
    stats = engine.ingest_all()
    assert stats[0].new_turns == 1

    with engine.store._get_connection() as conn:
        cursor_row = conn.execute("SELECT * FROM ingestion_cursors").fetchone()
        assert cursor_row is not None
        assert cursor_row["byte_offset"] > 0


def test_real_sqlite_transaction_rollback(tmp_path: Path):
    """Verify actual SQLite transaction atomicity: failure on 2nd update rolls back 1st update."""
    db_file = tmp_path / "raw_rag_real_rollback.db"
    cfg = StorageConfig(sqlite_path=str(db_file))
    from agent_raw_rag.engine.store import SQLiteStore
    from agent_raw_rag.models import PendingSessionUpdate, SessionMetadata, TurnContext, ChunkRecord

    store = SQLiteStore(cfg)

    # Valid update 1
    meta1 = SessionMetadata(session_id="sess-1", agent="claude", project_path="/test")
    ctx1 = TurnContext(header="h1", turn_lines=["line1"], turn_spans=[(0, 10)], turn_timestamps=[], turn_session_dates=[])
    chunk1 = ChunkRecord(
        chunk_id="c1",
        session_id="sess-1",
        chunk_index=0,
        project_path="/test",
        agent="claude",
        content="hello",
        start_token=0,
        end_token=10,
        embedding=np.zeros(64, dtype=np.float32),
    )
    upd1 = PendingSessionUpdate(
        meta=meta1,
        src_key="claude:sess-1",
        agent="claude",
        new_offset=100,
        new_timestamp=0,
        updated_context=ctx1,
        updated_units=[10],
        all_chunks=[chunk1],
        tail_chunks=[chunk1],
        first_changed_idx=0,
    )

    # Malformed update 2 (invalid embedding dimension / type or trigger sqlite error)
    # We create an update where chunk violates not-null constraint by injecting invalid chunk
    meta2 = SessionMetadata(session_id="sess-2", agent="claude", project_path="/test")
    ctx2 = TurnContext(header="h2", turn_lines=["line2"], turn_spans=[(0, 10)], turn_timestamps=[], turn_session_dates=[])
    # chunk_id cannot be None in DB (PRIMARY KEY)
    chunk2_bad = ChunkRecord(
        chunk_id="c2_bad",
        session_id=None,  # Violates NOT NULL constraint: chunks.session_id
        chunk_index=0,
        project_path="/test",
        agent="claude",
        content="world",
        start_token=0,
        end_token=10,
    )
    upd2 = PendingSessionUpdate(
        meta=meta2,
        src_key="claude:sess-2",
        agent="claude",
        new_offset=200,
        new_timestamp=0,
        updated_context=ctx2,
        updated_units=[10],
        all_chunks=[chunk2_bad],
        tail_chunks=[chunk2_bad],
        first_changed_idx=0,
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.commit_batch_updates([upd1, upd2])

    # Assert upd1 was rolled back completely!
    with store._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) as c FROM ingestion_cursors").fetchone()["c"] == 0

