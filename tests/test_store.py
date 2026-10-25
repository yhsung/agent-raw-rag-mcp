import pytest
import numpy as np
from pathlib import Path
from datetime import datetime

from agent_raw_rag.config import StorageConfig
from agent_raw_rag.engine.store import SQLiteStore
from agent_raw_rag.models import ChunkRecord, IngestionCursor, SessionMetadata, TurnContext


def test_sqlite_store_crud(tmp_path: Path):
    db_file = tmp_path / "test_store.db"
    store = SQLiteStore(StorageConfig(sqlite_path=str(db_file)))

    # 1. Cursor
    c = IngestionCursor(source_key="file_a", agent="claude", byte_offset=1234, last_timestamp=5678)
    store.save_cursor(c)
    loaded_c = store.get_cursor("file_a")
    assert loaded_c is not None
    assert loaded_c.byte_offset == 1234

    # 2. Session and Chunks
    meta = SessionMetadata(
        session_id="sess_123",
        agent="claude",
        project_path="/proj/abc",
        git_branch="feat",
        created_at=datetime(2026, 10, 25, 10, 0, 0),
    )
    context = TurnContext(
        header="Header line",
        turn_lines=["Line 1", "Line 2"],
        turn_spans=[(0, 10), (10, 20)],
        turn_timestamps=["2026-10-25T10:00:00Z", "2026-10-25T10:00:05Z"],
        turn_session_dates=["2026-10-25", "2026-10-25"],
    )
    units = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    dummy_emb = np.ones(2560, dtype=np.float32)

    chunk1 = ChunkRecord(
        chunk_id="sess_123_chunk_0",
        session_id="sess_123",
        chunk_index=0,
        project_path="/proj/abc",
        agent="claude",
        content="Chunk 0 text",
        start_token=0,
        end_token=10,
        start_turn=0,
        end_turn=1,
        start_date="2026-10-25",
        end_date="2026-10-25",
        session_timestamp="2026-10-25T10:00:00Z",
        embedding=dummy_emb,
    )

    store.save_session_chunks(meta, context, units, [chunk1], first_changed=0)

    # 3. Read back session state
    state = store.get_session_state("sess_123")
    assert state is not None
    ctx, chunks, loaded_units = state
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "sess_123_chunk_0"
    assert chunks[0].embedding is not None
    assert np.allclose(chunks[0].embedding, dummy_emb)
    assert loaded_units == units

    # 4. Search Candidates
    candidates = store.load_candidates_with_embeddings(project_path="/proj/abc")
    assert len(candidates) == 1
    assert candidates[0].session_id == "sess_123"

    # 5. Stats and recent turns
    stats = store.get_stats()
    assert stats["total_sessions"] == 1
    assert stats["total_chunks"] == 1
    assert stats["chunks_by_agent"]["claude"] == 1

    recent = store.get_recent_turns(project_path="/proj/abc", limit_turns=10)
    assert len(recent) == 2
    assert recent[-1]["line"] == "Line 2"
