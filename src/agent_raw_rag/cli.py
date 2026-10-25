"""Command-line interface (raw-rag-ingest) for incremental ingestion and status checks.

Can be called manually, via cron/systemd timer, or hooked into agent events.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .config import AppConfig, load_config
from .engine.chunker import IncrementalChunker
from .engine.mlx_retriever import MLXRetriever
from .engine.store import SQLiteStore
from .models import IngestionCursor, IngestionStats, SessionMetadata
from .parsers.claude_parser import ClaudeParser
from .parsers.codex_parser import CodexParser
from .parsers.omp_parser import OmpParser
from .parsers.opencode_parser import OpenCodeParser
from .parsers.sanitizer import DeterministicSanitizer


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

    def ingest_claude(self) -> IngestionStats:
        t0 = time.perf_counter()
        stats = IngestionStats(agent="claude")
        if not self.config.sources.claude_code.enabled:
            return stats

        root_dir = Path(self.config.sources.claude_code.path).expanduser().resolve()
        if not root_dir.is_dir():
            return stats

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
                continue

            if not turns or new_offset <= start_offset:
                continue

            self._process_turns_for_session(meta, turns)
            self.store.save_cursor(
                IngestionCursor(
                    source_key=src_key,
                    agent="claude",
                    byte_offset=new_offset,
                )
            )
            stats.new_sessions += 1 if start_offset == 0 else 0
            stats.new_turns += len(turns)

        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def ingest_codex(self) -> IngestionStats:
        t0 = time.perf_counter()
        stats = IngestionStats(agent="codex")
        if not self.config.sources.codex.enabled:
            return stats

        root_dir = Path(self.config.sources.codex.path).expanduser().resolve()
        if not root_dir.is_dir():
            return stats

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
                continue

            if not turns or new_offset <= start_offset:
                continue

            self._process_turns_for_session(meta, turns)
            self.store.save_cursor(
                IngestionCursor(
                    source_key=src_key,
                    agent="codex",
                    byte_offset=new_offset,
                )
            )
            stats.new_sessions += 1 if start_offset == 0 else 0
            stats.new_turns += len(turns)

        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def ingest_opencode(self) -> IngestionStats:
        t0 = time.perf_counter()
        stats = IngestionStats(agent="opencode")
        if not self.config.sources.opencode.enabled:
            return stats

        db_path = Path(self.config.sources.opencode.path).expanduser().resolve()
        if not db_path.is_file():
            return stats

        src_key = f"opencode:{db_path}"
        cursor = self.store.get_cursor(src_key)
        last_time_updated = cursor.last_timestamp if cursor else 0

        try:
            turns, max_updated, metas = self.opencode_parser.parse_db_incremental(
                db_path, last_time_updated=last_time_updated
            )
        except Exception:
            return stats

        if not turns:
            return stats

        # Group turns by session_id
        meta_by_id = {m.session_id: m for m in metas}
        turns_by_sid: Dict[str, list] = {}
        for t in turns:
            turns_by_sid.setdefault(t.session_id, []).append(t)

        for sid, s_turns in turns_by_sid.items():
            meta = meta_by_id.get(
                sid,
                SessionMetadata(session_id=sid, agent="opencode", project_path=""),
            )
            self._process_turns_for_session(meta, s_turns)
            stats.new_turns += len(s_turns)
            stats.new_sessions += 1

        if max_updated > last_time_updated:
            self.store.save_cursor(
                IngestionCursor(
                    source_key=src_key,
                    agent="opencode",
                    last_timestamp=max_updated,
                )
            )

        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def ingest_omp(self) -> IngestionStats:
        t0 = time.perf_counter()
        stats = IngestionStats(agent="omp")
        if not self.config.sources.oh_my_pi.enabled:
            return stats

        root_dir = Path(self.config.sources.oh_my_pi.path).expanduser().resolve()
        if not root_dir.is_dir():
            return stats

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
            except Exception:
                continue

            if not turns or new_offset <= start_offset:
                continue

            self._process_turns_for_session(meta, turns)
            self.store.save_cursor(
                IngestionCursor(
                    source_key=src_key,
                    agent="omp",
                    byte_offset=new_offset,
                )
            )
            stats.new_sessions += 1 if start_offset == 0 else 0
            stats.new_turns += len(turns)

        stats.duration_ms = (time.perf_counter() - t0) * 1000.0
        return stats

    def _process_turns_for_session(
        self, meta: SessionMetadata, new_turns: list
    ) -> None:
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

        if tail_chunks:
            # Embed only the mutable tail chunks
            embeddings = self.retriever.embed_texts([c.content for c in tail_chunks])
            for c, emb in zip(tail_chunks, embeddings):
                c.embedding = emb

        self.store.save_session_chunks(
            meta=meta,
            context=upd_ctx,
            units=upd_units,
            all_chunks=all_chunks,
            first_changed=fc,
        )

    def ingest_all(self, target_agent: Optional[str] = None) -> List[IngestionStats]:
        results: List[IngestionStats] = []
        target = (target_agent or "").lower()

        if not target or target == "claude":
            results.append(self.ingest_claude())
        if not target or target == "codex":
            results.append(self.ingest_codex())
        if not target or target == "opencode":
            results.append(self.ingest_opencode())
        if not target or target == "omp":
            results.append(self.ingest_omp())

        return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="agent-raw-rag-mcp incremental ingestion CLI"
    )
    parser.add_argument(
        "--agent",
        choices=["claude", "codex", "opencode", "omp"],
        help="Specify single agent to ingest",
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
        "--watch",
        action="store_true",
        help="Run continuously in background with 30s polling",
    )

    args = parser.parse_args()
    config = load_config(args.config)
    engine = IngestionEngine(config)

    if args.status:
        stats = engine.store.get_stats()
        print("=== agent-raw-rag-mcp Storage Status ===")
        print(f"Database: {stats['storage_path']}")
        print(f"Total Sessions: {stats['total_sessions']}")
        print(f"Total Chunks:   {stats['total_chunks']}")
        print(f"Chunks by Agent: {stats['chunks_by_agent']}")
        print(f"Last Updated:   {stats['last_updated']}")
        sys.exit(0)

    while True:
        results = engine.ingest_all(target_agent=args.agent)
        total_new_turns = sum(r.new_turns for r in results)
        total_sessions = sum(r.new_sessions for r in results)
        total_ms = sum(r.duration_ms for r in results)
        print(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ingested {total_new_turns} new turns across {total_sessions} sessions ({total_ms:.1f}ms)"
        )

        if not args.watch:
            break
        time.sleep(30)


if __name__ == "__main__":
    main()
