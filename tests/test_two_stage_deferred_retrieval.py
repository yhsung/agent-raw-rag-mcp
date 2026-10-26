import pytest
import sqlite3
import numpy as np
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

from agent_raw_rag.config import RetrievalConfig, StorageConfig
from agent_raw_rag.engine.chunker import IncrementalChunker
from agent_raw_rag.engine.store import SQLiteStore
from agent_raw_rag.engine.mlx_retriever import MLXRetriever
from agent_raw_rag.models import ChunkRecord, NormalizedTurn, SessionMetadata, TurnContext


def test_deferred_loading_and_batch_turn_context(tmp_path: Path):
    db_file = tmp_path / "test_deferred.db"
    store = SQLiteStore(StorageConfig(sqlite_path=str(db_file)))
    chunker = IncrementalChunker()
    retriever = MLXRetriever(RetrievalConfig(candidate_top_k=5, rerank_top_k=3), store, chunker)

    # 1. Create multiple sessions with multiple chunks
    for s_idx in range(4):
        sid = f"session_{s_idx}"
        meta = SessionMetadata(
            session_id=sid,
            agent="claude",
            project_path="/proj/app",
            git_branch="main",
            created_at=datetime(2026, 10, 26, 10, s_idx, 0),
        )
        turns = [
            NormalizedTurn(
                agent="claude",
                session_id=sid,
                turn_index=0,
                role="user",
                content=f"Query prompt about feature {s_idx} and optimization",
                timestamp=datetime(2026, 10, 26, 10, s_idx, 1),
            ),
            NormalizedTurn(
                agent="claude",
                session_id=sid,
                turn_index=1,
                role="assistant",
                content=f"Detailed response explaining implementation for feature {s_idx}",
                timestamp=datetime(2026, 10, 26, 10, s_idx, 5),
            ),
        ]
        chunks, tail, ctx, units, fc = chunker.add_incremental_turns(meta, turns)
        embs = retriever.embed_texts([c.content for c in tail])
        for c, emb in zip(tail, embs):
            c.embedding = emb
        store.save_session_chunks(meta, ctx, units, chunks, first_changed=fc)

    # 2. Test load_candidate_vector_index projection
    chunk_ids, matrix = store.load_candidate_vector_index(project_path="/proj/app")
    assert len(chunk_ids) == 4
    assert matrix.shape == (4, 2560)

    # 3. Test get_chunks_by_ids (Deferred Hydration)
    sample_ids = chunk_ids[:2]
    chunks_map = store.get_chunks_by_ids(sample_ids)
    assert len(chunks_map) == 2
    for cid in sample_ids:
        assert cid in chunks_map
        rec = chunks_map[cid]
        assert rec.content != ""
        assert rec.embedding is None  # Deferred chunks omit raw embedding to save memory

    # 4. Test get_turn_contexts_batch
    target_sids = {"session_0", "session_1", "session_2"}
    ctx_map = store.get_turn_contexts_batch(target_sids)
    assert len(ctx_map) == 3
    for sid in target_sids:
        assert sid in ctx_map
        assert isinstance(ctx_map[sid], TurnContext)

    # 5. Verify Zero N+1 Queries during search()
    # Spy on SQLite connection executes to verify sessions table is queried at most once during expansion
    orig_batch_method = store.get_turn_contexts_batch
    batch_call_count = 0

    def spy_get_turn_contexts_batch(session_ids):
        nonlocal batch_call_count
        batch_call_count += 1
        return orig_batch_method(session_ids)

    with patch.object(store, "get_turn_contexts_batch", side_effect=spy_get_turn_contexts_batch):
        results = retriever.search("feature optimization", project_path="/proj/app")
        assert len(results) > 0
        # In step 5, get_turn_contexts_batch MUST be called exactly ONCE for all selected chunks!
        assert batch_call_count == 1
