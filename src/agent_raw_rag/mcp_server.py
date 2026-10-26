"""MCP Server for Raw-RAG incremental memory retrieval across Claude Code, Codex, OpenCode, and Oh My Pi."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from mcp.server.mcpserver import MCPServer

from .cli import IngestionEngine, render_status_text
from .config import AppConfig, load_config
from .engine.chunker import IncrementalChunker
from .engine.mlx_retriever import MLXRetriever
from .engine.store import SQLiteStore
from .models import SearchResult

# Initialize server and state
mcp = MCPServer(name="raw-rag-mcp")

_CONFIG: Optional[AppConfig] = None
_STORE: Optional[SQLiteStore] = None
_CHUNKER: Optional[IncrementalChunker] = None
_RETRIEVER: Optional[MLXRetriever] = None
_INGESTION_ENGINE: Optional[IngestionEngine] = None


def get_components() -> Tuple[AppConfig, SQLiteStore, IncrementalChunker, MLXRetriever, IngestionEngine]:
    global _CONFIG, _STORE, _CHUNKER, _RETRIEVER, _INGESTION_ENGINE
    if _CONFIG is None:
        _CONFIG = load_config()
        _STORE = SQLiteStore(_CONFIG.storage)
        _CHUNKER = IncrementalChunker(_CONFIG.chunking)
        _RETRIEVER = MLXRetriever(_CONFIG.retrieval, _STORE, _CHUNKER)
        _INGESTION_ENGINE = IngestionEngine(_CONFIG)
    return _CONFIG, _STORE, _CHUNKER, _RETRIEVER, _INGESTION_ENGINE


@mcp.tool()
def raw_rag_search(
    query: str,
    project_path: Optional[str] = None,
    agent: Optional[str] = None,
    after_date: Optional[str] = None,
    before_date: Optional[str] = None,
    top_k: int = 15,
) -> str:
    """Retrieve historical conversational memory across Claude Code, Codex, OpenCode, and Oh My Pi.

    Executes Raw-RAG two-stage bounded retrieval (Dense Top-128 -> Cross-Encoder Top-15),
    chronological reordering, and display-time turn expansion with deduplication.

    Args:
        query: Natural language question or search terms.
        project_path: Restrict search to project directory (or '*' for all projects).
        agent: Filter by agent ('claude', 'codex', 'opencode', 'omp').
        after_date: Filter turns after date (YYYY-MM-DD).
        before_date: Filter turns before date (YYYY-MM-DD).
        top_k: Maximum number of expanded memory passages to return (default 15).
    """
    _, _, _, retriever, _ = get_components()

    # Default to current directory if not wildcard and not specified
    target_project = project_path
    if not target_project:
        target_project = os.getcwd()

    results: List[SearchResult] = retriever.search(
        query=query,
        project_path=target_project,
        agent=agent,
        after_date=after_date,
        before_date=before_date,
        top_k=top_k,
    )

    if not results:
        return f"No memories found matching query '{query}' for project: {target_project or 'all'}."

    blocks: List[str] = [
        f"Found {len(results)} relevant memory passages (Chronologically Ordered & Turn-Expanded):\n"
    ]

    for idx, r in enumerate(results, start=1):
        score_info = f"Rerank Score: {r.rerank_score:.3f} | Cosine: {r.similarity_score:.3f}"
        header = f"### Memory {idx} | Agent: {r.agent} | Date: {r.start_date or 'N/A'} | {score_info}"
        proj_line = f"**Project**: `{r.project_path}`"
        turn_range = f"*(Turns {r.start_turn}..{r.end_turn})*"

        content = r.expanded_turn_content if r.expanded_turn_content.strip() else r.raw_content
        blocks.append(f"{header}\n{proj_line} {turn_range}\n\n{content}\n")

    return "\n---\n\n".join(blocks)


@mcp.tool()
def raw_rag_recent(
    project_path: Optional[str] = None,
    limit_turns: int = 30,
) -> str:
    """Retrieve recent dialogue turns and tool action summaries for a project.

    Used when resuming work in a new session or recovering loss of context after /compact.

    Args:
        project_path: Target project directory (defaults to current working directory).
        limit_turns: Number of recent turns to retrieve (default 30).
    """
    _, store, _, _, _ = get_components()
    target_project = project_path or os.getcwd()

    recent_turns = store.get_recent_turns(
        project_path=target_project, limit_turns=limit_turns
    )

    if not recent_turns:
        return f"No recent turns found for project: {target_project}."

    lines = [f"=== Recent {len(recent_turns)} Turns for Project: {target_project} ===\n"]
    for t in recent_turns:
        ts = t["timestamp"] or "unknown-time"
        lines.append(f"[{t['agent']}] {t['line']}")

    return "\n".join(lines)


@mcp.tool()
def raw_rag_ingest(
    agent: Optional[str] = None,
) -> str:
    """Incrementally scan and index new dialogue events from local CLI Agent logs.

    Zero-LLM writes: executes deterministic sanitizer, 256-token tail chunking,
    and MLX embedding computation only on new tail chunks.

    Args:
        agent: Optional agent filter ('claude', 'codex', 'opencode', 'omp'). If omitted, synchronizes all.
    """
    _, _, _, _, engine = get_components()
    stats_list = engine.ingest_all(target_agent=agent)

    summary_lines = ["=== Raw-RAG Incremental Ingestion Report ==="]
    total_turns = 0
    total_sessions = 0
    total_duration = 0.0

    for s in stats_list:
        summary_lines.append(
            f"- Agent {s.agent:8s}: {s.sessions_scanned} scanned, {s.new_sessions} new sessions, {s.new_turns} new turns ({s.duration_ms:.1f}ms)"
        )
        total_turns += s.new_turns
        total_sessions += s.new_sessions
        total_duration += s.duration_ms

    summary_lines.append(
        f"Total: {total_turns} new turns across {total_sessions} sessions in {total_duration:.1f}ms."
    )
    return "\n".join(summary_lines)


@mcp.tool()
def raw_rag_status(agent: Optional[str] = None) -> str:
    """Retrieve status, statistics, and multi-agent cursor freshness of the local Raw-RAG persistent memory index."""
    _, store, _, _, _ = get_components()
    stats = store.get_stats()
    agent_cursors = None
    if agent:
        agent_cursors = store.get_agent_cursors(agent)
    return render_status_text(
        stats,
        target_agent=agent,
        agent_cursors=agent_cursors,
        title="=== agent-raw-rag-mcp Status ===",
    )


def main() -> None:
    """Entry point for stdio MCP server."""
    # Run the standard stdio server
    mcp.run()


if __name__ == "__main__":
    main()
