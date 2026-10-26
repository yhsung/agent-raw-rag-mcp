"""Command-line interface (raw-rag-ingest) for incremental ingestion and status checks.

Supports two-stage pipeline:
1. Pure CPU log harvesting and incremental tail chunking across multiple agents.
2. Cross-agent unified MLX batch embedding with single forward pass.
3. Coupled atomic SQLite WAL transaction.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import AppConfig, load_config
from .engine.chunker import IncrementalChunker
from .engine.mlx_retriever import MLXRetriever
from .engine.store import SQLiteStore
from .models import (
    IngestionCursor,
    IngestionStats,
    PendingSessionUpdate,
    SessionMetadata,
)
from .parsers.antigravity_parser import AntigravityParser
from .parsers.claude_parser import ClaudeParser
from .parsers.codex_parser import CodexParser
from .parsers.omp_parser import OmpParser
from .parsers.opencode_parser import OpenCodeParser
from .parsers.sanitizer import DeterministicSanitizer

logger = logging.getLogger(__name__)


class IngestionEngine:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()
        self.sanitizer = DeterministicSanitizer(self.config.sanitizer)
        self.chunker = IncrementalChunker(self.config.chunking)
        self.store = SQLiteStore(self.config.storage)
        self.retriever = MLXRetriever(self.config.retrieval, self.store, self.chunker)

        self.claude_parser = ClaudeParser(self.sanitizer)
        self.codex_parser = CodexParser(self.sanitizer)
        self.opencode_parser = OpenCodeParser(self.sanitizer)
        self.omp_parser = OmpParser(self.sanitizer)
        self.antigravity_parser = AntigravityParser(self.sanitizer)

    def _prepare_update_for_session(
        self,
        meta: SessionMetadata,
        new_turns: list,
        src_key: str,
        agent: str,
        new_offset: int,
        new_timestamp: int,
    ) -> PendingSessionUpdate:
        state = self.store.get_session_state(meta.session_id)
        if state is None:
            ctx, chunks, units = None, None, None
        else:
            ctx, chunks, units = state

        all_chunks, tail_chunks, upd_ctx, upd_units, fc = (
            self.chunker.add_incremental_turns(
                meta=meta,
                new_turns=new_turns,
                existing_context=ctx,
                existing_chunks=chunks,
                existing_units=units,
            )
        )

        return PendingSessionUpdate(
            meta=meta,
            src_key=src_key,
            agent=agent,
            new_offset=new_offset,
            new_timestamp=new_timestamp,
            updated_context=upd_ctx,
            updated_units=upd_units,
            all_chunks=all_chunks,
            tail_chunks=tail_chunks,
            first_changed_idx=fc,
        )

    def _execute_batch_embedding_and_commit(
        self, updates: List[PendingSessionUpdate], max_batch_size: int = 64
    ) -> int:
        """Batch embed all tail chunks across sessions in single or sliced forward passes,

        then commit changes atomically.
        """
        if not updates:
            return 0

        flat_tail_chunks = [c for u in updates for c in u.tail_chunks]
        if flat_tail_chunks:
            total_chunks = len(flat_tail_chunks)
            total_batches = (total_chunks + max_batch_size - 1) // max_batch_size
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Processing {len(updates)} sessions with {total_chunks} tail chunks ({total_batches} batches)...",
                flush=True,
            )
            all_texts = [c.content for c in flat_tail_chunks]
            for batch_num, i in enumerate(range(0, len(all_texts), max_batch_size), start=1):
                batch_texts = all_texts[i : i + max_batch_size]
                batch_chunks = flat_tail_chunks[i : i + max_batch_size]
                t_b0 = time.perf_counter()
                embeddings = self.retriever.embed_texts(batch_texts)
                for c, emb in zip(batch_chunks, embeddings):
                    c.embedding = emb
                elapsed_b = (time.perf_counter() - t_b0) * 1000.0
                print(
                    f"  -> Embedded batch {batch_num}/{total_batches} ({len(batch_texts)} chunks, {elapsed_b:.0f}ms)",
                    flush=True,
                )

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Committing to database...", flush=True)
        self.store.commit_batch_updates(updates)
        return len(flat_tail_chunks)

    def collect_claude(self) -> Tuple[List[PendingSessionUpdate], IngestionStats]:
        stats = IngestionStats(agent="claude")
        updates: List[PendingSessionUpdate] = []
        if not self.config.sources.claude_code.enabled:
            return updates, stats

        root_dir = Path(self.config.sources.claude_code.path).expanduser().resolve()
        if not root_dir.is_dir():
            return updates, stats

        for jsonl_path in root_dir.glob("*/*.jsonl"):
            stats.sessions_scanned += 1
            src_key = f"claude:{jsonl_path}"
            cursor = self.store.get_cursor(src_key)
            start_offset = cursor.byte_offset if cursor else 0

            try:
                turns, new_offset, meta = self.claude_parser.parse_file_incremental(
                    jsonl_path, start_offset=start_offset
                )
            except Exception as e:
                logger.warning("Error parsing Claude log %s: %s", jsonl_path, e)
                continue

            if not turns or new_offset <= start_offset:
                continue

            upd = self._prepare_update_for_session(
                meta=meta,
                new_turns=turns,
                src_key=src_key,
                agent="claude",
                new_offset=new_offset,
                new_timestamp=0,
            )
            updates.append(upd)
            stats.new_sessions += 1 if start_offset == 0 else 0
            stats.new_turns += len(turns)

        return updates, stats

    def collect_codex(self) -> Tuple[List[PendingSessionUpdate], IngestionStats]:
        stats = IngestionStats(agent="codex")
        updates: List[PendingSessionUpdate] = []
        if not self.config.sources.codex.enabled:
            return updates, stats

        root_dir = Path(self.config.sources.codex.path).expanduser().resolve()
        if not root_dir.is_dir():
            return updates, stats

        for jsonl_path in root_dir.glob("**/rollout-*.jsonl"):
            stats.sessions_scanned += 1
            src_key = f"codex:{jsonl_path}"
            cursor = self.store.get_cursor(src_key)
            start_offset = cursor.byte_offset if cursor else 0

            try:
                turns, new_offset, meta = self.codex_parser.parse_file_incremental(
                    jsonl_path, start_offset=start_offset
                )
            except Exception as e:
                logger.warning("Error parsing Codex log %s: %s", jsonl_path, e)
                continue

            if not turns or new_offset <= start_offset:
                continue

            upd = self._prepare_update_for_session(
                meta=meta,
                new_turns=turns,
                src_key=src_key,
                agent="codex",
                new_offset=new_offset,
                new_timestamp=0,
            )
            updates.append(upd)
            stats.new_sessions += 1 if start_offset == 0 else 0
            stats.new_turns += len(turns)

        return updates, stats

    def collect_opencode(self) -> Tuple[List[PendingSessionUpdate], IngestionStats]:
        stats = IngestionStats(agent="opencode")
        updates: List[PendingSessionUpdate] = []
        if not self.config.sources.opencode.enabled:
            return updates, stats

        db_path = Path(self.config.sources.opencode.path).expanduser().resolve()
        if not db_path.is_file():
            return updates, stats

        src_key = f"opencode:{db_path}"
        cursor = self.store.get_cursor(src_key)
        last_time_updated = cursor.last_timestamp if cursor else 0

        try:
            turns, max_updated, metas = self.opencode_parser.parse_db_incremental(
                db_path, last_time_updated=last_time_updated
            )
        except Exception as e:
            logger.warning("Error parsing OpenCode db %s: %s", db_path, e)
            return updates, stats

        if not turns:
            return updates, stats

        meta_by_id = {m.session_id: m for m in metas}
        turns_by_sid: Dict[str, list] = {}
        for t in turns:
            turns_by_sid.setdefault(t.session_id, []).append(t)

        for sid, s_turns in turns_by_sid.items():
            meta = meta_by_id.get(
                sid,
                SessionMetadata(session_id=sid, agent="opencode", project_path=""),
            )
            upd = self._prepare_update_for_session(
                meta=meta,
                new_turns=s_turns,
                src_key=src_key,
                agent="opencode",
                new_offset=0,
                new_timestamp=max_updated,
            )
            updates.append(upd)
            stats.new_turns += len(s_turns)
            stats.new_sessions += 1

        return updates, stats

    def collect_omp(self) -> Tuple[List[PendingSessionUpdate], IngestionStats]:
        stats = IngestionStats(agent="omp")
        updates: List[PendingSessionUpdate] = []
        if not self.config.sources.oh_my_pi.enabled:
            return updates, stats

        root_dir = Path(self.config.sources.oh_my_pi.path).expanduser().resolve()
        if not root_dir.is_dir():
            return updates, stats

        for jsonl_path in root_dir.glob("**/*.jsonl"):
            if ".lock" in jsonl_path.name:
                continue
            stats.sessions_scanned += 1
            src_key = f"omp:{jsonl_path}"
            cursor = self.store.get_cursor(src_key)
            start_offset = cursor.byte_offset if cursor else 0

            try:
                turns, new_offset, meta = self.omp_parser.parse_file_incremental(
                    jsonl_path, start_offset=start_offset
                )
            except Exception as e:
                logger.warning("Error parsing OMP log %s: %s", jsonl_path, e)
                continue

            if not turns or new_offset <= start_offset:
                continue

            upd = self._prepare_update_for_session(
                meta=meta,
                new_turns=turns,
                src_key=src_key,
                agent="omp",
                new_offset=new_offset,
                new_timestamp=0,
            )
            updates.append(upd)
            stats.new_sessions += 1 if start_offset == 0 else 0
            stats.new_turns += len(turns)

        return updates, stats

    def collect_antigravity(self) -> Tuple[List[PendingSessionUpdate], IngestionStats]:
        stats = IngestionStats(agent="antigravity")
        updates: List[PendingSessionUpdate] = []
        if not self.config.sources.antigravity.enabled:
            return updates, stats

        root_dir = Path(self.config.sources.antigravity.path).expanduser().resolve()
        if not root_dir.is_dir():
            return updates, stats

        # Traverse brain directories for transcript.jsonl
        for transcript_path in root_dir.glob("*/.system_generated/logs/transcript.jsonl"):
            stats.sessions_scanned += 1
            src_key = f"antigravity:{transcript_path}"
            cursor = self.store.get_cursor(src_key)
            start_offset = cursor.byte_offset if cursor else 0

            try:
                turns, new_offset, meta = self.antigravity_parser.parse_file_incremental(
                    transcript_path, start_offset=start_offset
                )
            except Exception as e:
                logger.warning("Error parsing Antigravity transcript %s: %s", transcript_path, e)
                continue

            if not turns or new_offset <= start_offset:
                continue

            upd = self._prepare_update_for_session(
                meta=meta,
                new_turns=turns,
                src_key=src_key,
                agent="antigravity",
                new_offset=new_offset,
                new_timestamp=0,
            )
            updates.append(upd)
            stats.new_sessions += 1 if start_offset == 0 else 0
            stats.new_turns += len(turns)

        return updates, stats

    def ingest_claude(self) -> IngestionStats:
        t0 = time.perf_counter()
        updates, stats = self.collect_claude()
        stats.recomputed_chunks = self._execute_batch_embedding_and_commit(updates)
        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def ingest_codex(self) -> IngestionStats:
        t0 = time.perf_counter()
        updates, stats = self.collect_codex()
        stats.recomputed_chunks = self._execute_batch_embedding_and_commit(updates)
        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def ingest_opencode(self) -> IngestionStats:
        t0 = time.perf_counter()
        updates, stats = self.collect_opencode()
        stats.recomputed_chunks = self._execute_batch_embedding_and_commit(updates)
        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def ingest_omp(self) -> IngestionStats:
        t0 = time.perf_counter()
        updates, stats = self.collect_omp()
        stats.recomputed_chunks = self._execute_batch_embedding_and_commit(updates)
        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def ingest_antigravity(self) -> IngestionStats:
        t0 = time.perf_counter()
        updates, stats = self.collect_antigravity()
        stats.recomputed_chunks = self._execute_batch_embedding_and_commit(updates)
        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def ingest_all(self, target_agent: Optional[str] = None) -> List[IngestionStats]:
        t0 = time.perf_counter()
        target = (target_agent or "").lower()

        all_updates: List[PendingSessionUpdate] = []
        stats_list: List[IngestionStats] = []

        collectors = [
            ("claude", self.collect_claude),
            ("codex", self.collect_codex),
            ("opencode", self.collect_opencode),
            ("omp", self.collect_omp),
            ("antigravity", self.collect_antigravity),
        ]

        for agent_name, collector_fn in collectors:
            if not target or target == agent_name:
                ag_updates, ag_stats = collector_fn()
                all_updates.extend(ag_updates)
                stats_list.append(ag_stats)

        # Cross-Agent unified batch embedding and coupled atomic commit
        recomputed = self._execute_batch_embedding_and_commit(all_updates)

        total_elapsed = (time.perf_counter() - t0) * 1000.0
        # Distribute timing proportionally
        for st in stats_list:
            st.recomputed_chunks = sum(len(u.tail_chunks) for u in all_updates if u.agent == st.agent)
            st.duration_ms = total_elapsed / max(len(stats_list), 1)

        return stats_list


def format_datetime_display(dt_str: Optional[str]) -> str:
    """Format ISO or UTC datetime string into human readable local time 'YYYY-MM-DD HH:MM:SS'."""
    if not dt_str:
        return "-"
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        s = dt_str.replace("T", " ")
        if "." in s:
            s = s.split(".")[0]
        if "+" in s:
            s = s.split("+")[0]
        return s[:19]


def format_project_context(path: Optional[str], max_width: int = 31) -> str:
    """Format project path concisely for terminal tabular presentation."""
    if not path:
        return "-"
    clean = (
        path.replace("\\n", "")
        .replace("\\r", "")
        .replace("\\t", "")
        .replace("\n", "")
        .replace("\r", "")
        .replace("\t", "")
        .replace('"', "")
        .replace("'", "")
        .strip()
    )
    if not clean:
        return "-"
    for prefix in ["/Volumes/Samsung970EVOPlus/", "/Users/yhsung/", str(Path.home()) + "/"]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    if len(clean) > max_width:
        return "..." + clean[-(max_width - 3):]
    return clean


def render_table(
    headers: List[str],
    rows: List[List[str]],
    aligns: Optional[List[str]] = None,
    min_widths: Optional[List[int]] = None,
) -> str:
    """Render ASCII table with box-drawing characters."""
    num_cols = len(headers)
    aligns = aligns or ["left"] * num_cols
    min_widths = min_widths or [0] * num_cols
    widths = [max(len(str(h)), min_w) for h, min_w in zip(headers, min_widths)]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    top_border = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    header_sep = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
    bottom_border = "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"

    lines = [top_border]
    # Header
    h_cells = []
    for h, w, a in zip(headers, widths, aligns):
        if a == "right":
            h_cells.append(f" {str(h):>{w}} ")
        else:
            h_cells.append(f" {str(h):<{w}} ")
    lines.append("│" + "│".join(h_cells) + "│")
    lines.append(header_sep)

    # Data rows
    for row in rows:
        r_cells = []
        for cell, w, a in zip(row, widths, aligns):
            if a == "right":
                r_cells.append(f" {str(cell):>{w}} ")
            else:
                r_cells.append(f" {str(cell):<{w}} ")
        lines.append("│" + "│".join(r_cells) + "│")
    lines.append(bottom_border)
    return "\n".join(lines)


def render_status_text(
    stats: Dict[str, Any],
    target_agent: Optional[str] = None,
    agent_cursors: Optional[List[Dict[str, Any]]] = None,
    engine_status: Optional[Dict[str, Any]] = None,
    title: str = "=== agent-raw-rag-mcp Storage Status ===",
) -> str:
    """Render human-readable multi-agent status text with ASCII table."""
    total_sessions = f"{stats.get('total_sessions', 0):,}"
    total_chunks = f"{stats.get('total_chunks', 0):,}"
    storage_path = stats.get("storage_path", "")

    output_lines = [
        title,
        f"Database: {storage_path}",
    ]

    if engine_status:
        device = engine_status.get("device", "Unknown")
        mode = engine_status.get("mode", "UNKNOWN")
        emb = engine_status.get("embedding", {})
        rer = engine_status.get("reranker", {})
        dyn = rer.get("dynamic_top_k", {})

        output_lines.append(f"Inference Engine: {device} [{mode}]")
        output_lines.append(
            f"  • Embedding: {emb.get('model', 'N/A')} ({emb.get('dimension', 2560)}d) [{emb.get('status', 'N/A')}]"
        )
        dyn_str = f"Enabled > {dyn.get('score_threshold', 0.5)}" if dyn.get("enabled") else "Disabled"
        output_lines.append(
            f"  • Reranker:  {rer.get('model', 'N/A')} (Dynamic Top-K: {dyn_str}) [{rer.get('status', 'N/A')}]"
        )
        if engine_status.get("is_fallback"):
            output_lines.append("  ⚠️ [Notice] Running on fallback scoring (token overlap/hash); download MLX models to activate full semantic precision.")

    output_lines.extend([
        f"Total Sessions: {total_sessions} | Total Chunks: {total_chunks} | Status: ACTIVE",
        "",
        "Agent Ingestion Cursors & Dialogue Freshness:",
    ])

    agents_data = stats.get("agents", {})

    def dialogue_time_sort_key(item: Tuple[str, Dict[str, Any]]) -> Tuple[int, float, int]:
        ag_name, data = item
        dt_str = data.get("latest_dialogue_time")
        chunks = data.get("chunks", 0)
        if not dt_str:
            return (0, 0.0, chunks)
        try:
            dt = datetime.fromisoformat(dt_str)
            return (1, dt.timestamp(), chunks)
        except Exception:
            return (1, 0.0, chunks)

    sorted_agents = sorted(agents_data.items(), key=dialogue_time_sort_key, reverse=True)

    # Filter by target_agent if requested
    if target_agent:
        display_agents = [(k, v) for k, v in sorted_agents if k == target_agent]
    else:
        display_agents = sorted_agents

    headers = [
        "Agent",
        "Sessions",
        "Chunks",
        "Latest Dialogue",
        "Last Ingested At",
        "Latest Active Context",
    ]
    aligns = ["left", "right", "right", "left", "left", "left"]
    min_widths = [11, 9, 12, 19, 19, 29]

    rows = []
    for ag_name, ag_info in display_agents:
        rows.append(
            [
                ag_name,
                f"{ag_info.get('sessions', 0):,}",
                f"{ag_info.get('chunks', 0):,}",
                format_datetime_display(ag_info.get("latest_dialogue_time")),
                format_datetime_display(ag_info.get("last_ingested_at")),
                format_project_context(ag_info.get("latest_project_path")),
            ]
        )

    if rows:
        output_lines.append(render_table(headers, rows, aligns, min_widths))
    else:
        output_lines.append("No recorded agents found in database.")

    # Detailed cursors for single agent
    if target_agent and agent_cursors is not None:
        output_lines.append("")
        output_lines.append(
            f"Detailed Ingestion Cursors for Agent '{target_agent}' ({len(agent_cursors)} sources):"
        )
        if not agent_cursors:
            output_lines.append("No active ingestion cursors recorded.")
        else:
            c_headers = ["Source Key / File", "Offset / Pos", "Last Ingested At"]
            c_aligns = ["left", "right", "left"]
            c_min_widths = [45, 12, 19]
            c_rows = []
            for c in agent_cursors:
                s_key = c.get("source_key", "")
                offset_str = f"{c.get('byte_offset', 0):,} B"
                c_rows.append(
                    [
                        s_key,
                        offset_str,
                        format_datetime_display(c.get("updated_at")),
                    ]
                )
            output_lines.append(render_table(c_headers, c_rows, c_aligns, c_min_widths))

    return "\n".join(output_lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="agent-raw-rag-mcp incremental ingestion CLI"
    )
    parser.add_argument(
        "--agent",
        choices=["claude", "codex", "opencode", "omp", "antigravity"],
        help="Specify single agent to ingest or inspect",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to custom YAML config file",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show database storage statistics and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output status as structured JSON",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously in background with periodic polling",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Polling interval in seconds for --watch (default: 15s)",
    )

    args = parser.parse_args()
    config = load_config(args.config)
    engine = IngestionEngine(config)

    if args.status:
        stats = engine.store.get_stats()
        eng_status = engine.retriever.engine_status
        agent_cursors = None
        if args.agent:
            agent_cursors = engine.store.get_agent_cursors(args.agent)

        if args.json:
            json_obj: Dict[str, Any] = {
                "storage_path": stats["storage_path"],
                "total_sessions": stats["total_sessions"],
                "total_chunks": stats["total_chunks"],
                "overall_last_updated": stats["overall_last_updated"],
                "engine": eng_status,
                "agents": stats.get("agents", {}),
            }
            if args.agent and agent_cursors is not None:
                json_obj["cursors"] = agent_cursors
            print(json.dumps(json_obj, indent=2, ensure_ascii=False))
            sys.exit(0)
        else:
            print(
                render_status_text(
                    stats,
                    target_agent=args.agent,
                    agent_cursors=agent_cursors,
                    engine_status=eng_status,
                )
            )
            sys.exit(0)

    try:
        while True:
            results = engine.ingest_all(target_agent=args.agent)
            total_new_turns = sum(r.new_turns for r in results)
            total_sessions = sum(r.new_sessions for r in results)
            total_ms = sum(r.duration_ms for r in results)
            if total_new_turns > 0 or not args.watch:
                print(
                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ingested {total_new_turns} new turns across {total_sessions} sessions ({total_ms:.1f}ms)"
                )

            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Daemon stopped gracefully.")


if __name__ == "__main__":
    main()
