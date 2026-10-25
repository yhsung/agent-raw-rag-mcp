import pytest
from datetime import datetime
from agent_raw_rag.engine.chunker import IncrementalChunker
from agent_raw_rag.models import NormalizedTurn, SessionMetadata, ChunkRecord
from agent_raw_rag.config import ChunkingConfig


def test_first_and_incremental_chunking():
    chunker = IncrementalChunker(ChunkingConfig(chunk_size=50, chunk_overlap=10))
    meta = SessionMetadata(
        session_id="test-sess-1",
        agent="claude",
        project_path="/proj/foo",
        git_branch="main",
        created_at=datetime(2026, 10, 25, 12, 0, 0),
    )

    turns_batch1 = [
        NormalizedTurn(
            agent="claude",
            session_id=meta.session_id,
            turn_index=0,
            role="user",
            content="Can you explain how Raw-RAG handles incremental chunking?",
            timestamp=datetime(2026, 10, 25, 12, 0, 1),
        ),
        NormalizedTurn(
            agent="claude",
            session_id=meta.session_id,
            turn_index=1,
            role="assistant",
            content="Raw-RAG uses an append-only token stream with bounded tail updates.",
            timestamp=datetime(2026, 10, 25, 12, 0, 5),
        ),
    ]

    all_chunks1, tail1, ctx1, units1, fc1 = chunker.add_incremental_turns(
        meta, turns_batch1
    )
    assert fc1 == 0
    assert len(all_chunks1) >= 1
    assert len(tail1) == len(all_chunks1)
    assert all_chunks1[0].start_turn == 0

    # Test incremental append
    turns_batch2 = [
        NormalizedTurn(
            agent="claude",
            session_id=meta.session_id,
            turn_index=2,
            role="user",
            content="Tell me more about the O(Delta L) complexity and locks on stable chunks.",
            timestamp=datetime(2026, 10, 25, 12, 1, 0),
        )
    ]

    all_chunks2, tail2, ctx2, units2, fc2 = chunker.add_incremental_turns(
        meta,
        turns_batch2,
        existing_context=ctx1,
        existing_chunks=all_chunks1,
        existing_units=units1,
    )

    # Tail should only contain new or modified chunks
    assert len(units2) > len(units1)
    assert len(ctx2.turn_lines) == 3


def test_expand_chunk_to_turns_and_dedup():
    chunker = IncrementalChunker(ChunkingConfig(chunk_size=256, chunk_overlap=32))
    meta = SessionMetadata(session_id="s1", agent="codex", project_path="/test")
    turns = [
        NormalizedTurn(
            agent="codex",
            session_id="s1",
            turn_index=0,
            role="user",
            content="First question",
            timestamp=datetime(2026, 10, 25, 10, 0, 0),
        ),
        NormalizedTurn(
            agent="codex",
            session_id="s1",
            turn_index=1,
            role="assistant",
            content="First answer with detailed explanation",
            timestamp=datetime(2026, 10, 25, 10, 0, 1),
        ),
    ]

    chunks, _, ctx, _, _ = chunker.add_incremental_turns(meta, turns)
    assert len(chunks) >= 1

    seen = set()
    expanded1 = chunker.expand_chunk_to_turns(chunks[0], ctx, seen_turns=seen)
    assert "First question" in expanded1
    assert "First answer" in expanded1

    # Second call with same seen set should deduplicate turns
    expanded2 = chunker.expand_chunk_to_turns(chunks[0], ctx, seen_turns=seen)
    assert "First question" not in expanded2
