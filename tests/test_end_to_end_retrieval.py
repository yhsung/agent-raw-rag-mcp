import pytest
import numpy as np
from pathlib import Path
from datetime import datetime

from agent_raw_rag.config import AppConfig, RetrievalConfig, StorageConfig
from agent_raw_rag.engine.chunker import IncrementalChunker
from agent_raw_rag.engine.store import SQLiteStore
from agent_raw_rag.engine.mlx_retriever import MLXRetriever
from agent_raw_rag.models import NormalizedTurn, SessionMetadata


def test_two_stage_retrieval_and_chronological_reorder(tmp_path: Path):
    db_file = tmp_path / "test_retrieval.db"
    store = SQLiteStore(StorageConfig(sqlite_path=str(db_file)))
    chunker = IncrementalChunker()
    retriever = MLXRetriever(RetrievalConfig(), store, chunker)

    # Ingest two sessions with temporal separation
    meta1 = SessionMetadata(
        session_id="session_old",
        agent="claude",
        project_path="/proj/auth",
        git_branch="main",
        created_at=datetime(2026, 10, 20, 10, 0, 0),
    )
    turns1 = [
        NormalizedTurn(
            agent="claude",
            session_id="session_old",
            turn_index=0,
            role="user",
            content="We decided to use JWT with HS256 for token authentication.",
            timestamp=datetime(2026, 10, 20, 10, 0, 1),
        ),
        NormalizedTurn(
            agent="claude",
            session_id="session_old",
            turn_index=1,
            role="assistant",
            content="Configured JWT HS256 in auth.py with 15-minute expiration.",
            timestamp=datetime(2026, 10, 20, 10, 0, 5),
        ),
    ]

    chunks1, tail1, ctx1, units1, fc1 = chunker.add_incremental_turns(meta1, turns1)
    # Generate embeddings for chunks
    embeddings1 = retriever.embed_texts([c.content for c in tail1])
    for c, emb in zip(tail1, embeddings1):
        c.embedding = emb
    store.save_session_chunks(meta1, ctx1, units1, chunks1, first_changed=fc1)

    meta2 = SessionMetadata(
        session_id="session_new",
        agent="codex",
        project_path="/proj/auth",
        git_branch="main",
        created_at=datetime(2026, 10, 25, 14, 0, 0),
    )
    turns2 = [
        NormalizedTurn(
            agent="codex",
            session_id="session_new",
            turn_index=0,
            role="user",
            content="We should migrate JWT algorithm from HS256 to RS256 for asymmetric verification.",
            timestamp=datetime(2026, 10, 25, 14, 0, 1),
        ),
        NormalizedTurn(
            agent="codex",
            session_id="session_new",
            turn_index=1,
            role="assistant",
            content="Migrated auth.py to RS256 with public key validation.",
            timestamp=datetime(2026, 10, 25, 14, 0, 10),
        ),
    ]

    chunks2, tail2, ctx2, units2, fc2 = chunker.add_incremental_turns(meta2, turns2)
    embeddings2 = retriever.embed_texts([c.content for c in tail2])
    for c, emb in zip(tail2, embeddings2):
        c.embedding = emb
    store.save_session_chunks(meta2, ctx2, units2, chunks2, first_changed=fc2)

    # Search for JWT authentication migration
    results = retriever.search(
        query="What JWT algorithm are we currently using?",
        project_path="/proj/auth",
        top_k=5,
    )

    assert len(results) >= 2
    # Chronological reorder check: session_old (Oct 20) must appear before session_new (Oct 25)
    dates = [r.start_date for r in results if r.start_date]
    if len(dates) >= 2:
        assert dates[0] <= dates[1]

    # Check dynamic turn expansion
    first_result = results[0]
    assert "Conversation ID:" in first_result.expanded_turn_content
    assert "User:" in first_result.expanded_turn_content
    assert "Assistant:" in first_result.expanded_turn_content
