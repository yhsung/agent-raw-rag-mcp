import os
import pytest
from pathlib import Path
from datetime import datetime

os.environ["HF_HOME"] = "/Volumes/Samsung980/hf-home/"

from agent_raw_rag.config import DynamicTopKConfig, RetrievalConfig, StorageConfig
from agent_raw_rag.engine.chunker import IncrementalChunker
from agent_raw_rag.engine.mlx_retriever import HAS_MLX, MLXRetriever
from agent_raw_rag.engine.store import SQLiteStore
from agent_raw_rag.models import NormalizedTurn, SessionMetadata

# Check if real weights exist on local machine
EMB_DIR = Path("/Volumes/Samsung980/hf-home/hub/models--mlx-community--Qwen3-Embedding-4B-4bit-DWQ")
RERANK_DIR = Path("/Volumes/Samsung980/hf-home/hub/models--mlx-community--Qwen3-Reranker-0.6B-4bit")
HAS_REAL_WEIGHTS = HAS_MLX and EMB_DIR.exists() and RERANK_DIR.exists()


@pytest.mark.skipif(not HAS_REAL_WEIGHTS, reason="Real MLX Qwen3 weights required in /Volumes/Samsung980/hf-home/")
def test_real_weights_end_to_end_semantic_ranking(tmp_path: Path):
    """End-to-end evaluation using real MLX Qwen3 weights on Apple Silicon.

    Validates that real embedding + reranker achieves true semantic discernment
    without falling back to token overlap or hash heuristics.
    """
    db_file = tmp_path / "test_real_weights.db"
    store = SQLiteStore(StorageConfig(sqlite_path=str(db_file)))
    chunker = IncrementalChunker()

    ret_cfg = RetrievalConfig(
        embedding_model="mlx-community/Qwen3-Embedding-4B-4bit-DWQ",
        reranker_model="mlx-community/Qwen3-Reranker-0.6B-4bit",
        candidate_top_k=10,
        rerank_top_k=5,
        dynamic_top_k=DynamicTopKConfig(enabled=True, score_threshold=0.5, fallback_top_k=2),
    )
    retriever = MLXRetriever(ret_cfg, store, chunker)

    # 1. Ingest Database WAL Session (Target Positive)
    meta_db = SessionMetadata(
        session_id="session_db",
        agent="claude",
        project_path="/proj/storage",
        git_branch="main",
        created_at=datetime(2026, 10, 26, 10, 0, 0),
    )
    turns_db = [
        NormalizedTurn(
            agent="claude",
            session_id="session_db",
            turn_index=0,
            role="user",
            content="How is SQLite WAL journal checkpoint executed in our store?",
            timestamp=datetime(2026, 10, 26, 10, 0, 1),
        ),
        NormalizedTurn(
            agent="claude",
            session_id="session_db",
            turn_index=1,
            role="assistant",
            content="When total_chunks_written exceeds 50, SQLiteStore invokes PRAGMA wal_checkpoint(PASSIVE) to truncate WAL files.",
            timestamp=datetime(2026, 10, 26, 10, 0, 5),
        ),
    ]
    chunks_db, tail_db, ctx_db, units_db, fc_db = chunker.add_incremental_turns(meta_db, turns_db)
    embs_db = retriever.embed_texts([c.content for c in tail_db])
    for c, emb in zip(tail_db, embs_db):
        c.embedding = emb
    store.save_session_chunks(meta_db, ctx_db, units_db, chunks_db, first_changed=fc_db)

    # 2. Ingest React Frontend Session (Hard Distractor)
    meta_ui = SessionMetadata(
        session_id="session_ui",
        agent="codex",
        project_path="/proj/frontend",
        git_branch="main",
        created_at=datetime(2026, 10, 26, 11, 0, 0),
    )
    turns_ui = [
        NormalizedTurn(
            agent="codex",
            session_id="session_ui",
            turn_index=0,
            role="user",
            content="How does useEffect handle dependencies and component cleanup in React?",
            timestamp=datetime(2026, 10, 26, 11, 0, 1),
        ),
        NormalizedTurn(
            agent="codex",
            session_id="session_ui",
            turn_index=1,
            role="assistant",
            content="The return callback in useEffect is invoked before unmounting or re-running the effect hook.",
            timestamp=datetime(2026, 10, 26, 11, 0, 5),
        ),
    ]
    chunks_ui, tail_ui, ctx_ui, units_ui, fc_ui = chunker.add_incremental_turns(meta_ui, turns_ui)
    embs_ui = retriever.embed_texts([c.content for c in tail_ui])
    for c, emb in zip(tail_ui, embs_ui):
        c.embedding = emb
    store.save_session_chunks(meta_ui, ctx_ui, units_ui, chunks_ui, first_changed=fc_ui)

    # 3. Query regarding WAL checkpointing
    query = "Where and when is PRAGMA wal_checkpoint triggered?"
    results = retriever.search(query, project_path="*")

    assert len(results) > 0
    top1 = results[0]

    # Verify top result is from session_db
    assert top1.session_id == "session_db"
    assert "PRAGMA wal_checkpoint" in top1.expanded_turn_content
    # Cross-encoder confidence should be high for this clear semantic match
    assert top1.rerank_score > 0.5

    # 4. Verify engine observability status reflects MLX native
    status = retriever.engine_status
    assert status["mode"] == "MLX_ACCELERATED"
    assert status["is_fallback"] is False
    assert status["embedding"]["status"] == "mlx_native"
    assert status["reranker"]["status"] == "mlx_native"
