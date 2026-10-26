"""Unit tests for cross-agent unified batch inference and coupled atomic commit."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import numpy as np

from agent_raw_rag.cli import IngestionEngine
from agent_raw_rag.config import (
    AppConfig,
    SanitizerConfig,
    SourceItemConfig,
    SourcesConfig,
    StorageConfig,
)


def test_cross_agent_batching_single_forward_pass(tmp_path: Path):
    db_file = tmp_path / "raw_rag_batch_test.db"
    claude_dir = tmp_path / "claude_projects"
    antigravity_dir = tmp_path / "antigravity_brain"

    claude_dir.mkdir(parents=True)
    antigravity_dir.mkdir(parents=True)

    # 1. Create a Claude session file
    claude_session_dir = claude_dir / "my-project"
    claude_session_dir.mkdir()
    claude_file = claude_session_dir / "session-claude-1.jsonl"
    with open(claude_file, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "claude-session-1",
                    "cwd": "/Volumes/Samsung970EVOPlus/dev-projects/demo",
                    "message": {"content": "Claude prompt: Implement feature A"},
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "Claude answer: Feature A implemented"},
                }
            )
            + "\n"
        )

    # 2. Create an Antigravity session file
    ag_session_dir = antigravity_dir / "ag-uuid-2" / ".system_generated" / "logs"
    ag_session_dir.mkdir(parents=True)
    ag_file = ag_session_dir / "transcript.jsonl"
    with open(ag_file, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "step_index": 0,
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>Antigravity prompt: Implement feature B</USER_REQUEST>",
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "content": "Antigravity answer: Feature B implemented",
                }
            )
            + "\n"
        )

    # 3. Configure IngestionEngine with these temporary sources
    cfg = AppConfig(
        storage=StorageConfig(sqlite_path=str(db_file)),
        sources=SourcesConfig(
            claude_code=SourceItemConfig(enabled=True, path=str(claude_dir)),
            codex=SourceItemConfig(enabled=False, path=""),
            opencode=SourceItemConfig(enabled=False, path=""),
            oh_my_pi=SourceItemConfig(enabled=False, path=""),
            antigravity=SourceItemConfig(enabled=True, path=str(antigravity_dir)),
        ),
    )
    engine = IngestionEngine(cfg)

    # Mock retriever.embed_texts to track call count and batch size
    mock_embed = MagicMock()
    # Return dummy vectors of dimension 64 for each text
    def dummy_embed(texts):
        return [np.ones(64, dtype=np.float32) for _ in texts]

    mock_embed.side_effect = dummy_embed
    engine.retriever.embed_texts = mock_embed

    # 4. Run ingest_all
    stats_list = engine.ingest_all()

    # 5. Assertions
    # Both agents should have scanned and produced turns
    claude_stats = next(s for s in stats_list if s.agent == "claude")
    ag_stats = next(s for s in stats_list if s.agent == "antigravity")

    assert claude_stats.new_turns == 2
    assert ag_stats.new_turns == 2

    # Verification of Invariant: embed_texts MUST be called exactly ONCE with all tail chunks combined!
    assert mock_embed.call_count == 1
    call_args, _ = mock_embed.call_args
    batch_texts = call_args[0]
    # Tail chunks from both sessions are batched together
    assert len(batch_texts) >= 2
    assert any("Claude prompt" in t or "Feature A" in t for t in batch_texts)
    assert any("Antigravity prompt" in t or "Feature B" in t for t in batch_texts)

    # 6. Verify database state
    with engine.store._get_connection() as conn:
        cursor_rows = conn.execute("SELECT source_key, agent, byte_offset FROM ingestion_cursors").fetchall()
        assert len(cursor_rows) == 2

        chunk_rows = conn.execute("SELECT chunk_id, session_id, agent, embedding FROM chunks").fetchall()
        assert len(chunk_rows) >= 2
        # Verify embedding BLOB is populated
        for r in chunk_rows:
            assert r["embedding"] is not None
            arr = np.frombuffer(r["embedding"], dtype=np.float32)
            assert len(arr) == 64
