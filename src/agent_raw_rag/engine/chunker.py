"""Incremental Tail-Only Chunking and Display-Time Turn Expansion Engine.

Faithfully implements the Raw-RAG (arXiv:2610.30755) incremental token-stream algorithm
derived from EverCore evaluation/src/adapters/rag_adapter.py:
- O(Delta L) complexity: only the mutable tail and newly opened chunks are recomputed.
- Stable 256-token chunks are strictly locked.
- Maintains exact token-to-turn coordinate mappings (turn_spans).
- Display-time dynamic expansion of chunks back to complete dialogue turns with deduplication.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import tiktoken

from ..config import ChunkingConfig
from ..models import ChunkRecord, NormalizedTurn, SessionMetadata, TurnContext
from ..parsers.sanitizer import get_tokenizer


class IncrementalChunker:
    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or ChunkingConfig()
        self.enc: tiktoken.Encoding = get_tokenizer()

    def format_header(self, meta: SessionMetadata) -> str:
        lines = [
            f"Conversation ID: {meta.agent}_{meta.session_id}",
            f"Agent: {meta.agent}",
            f"Project: {meta.project_path}",
        ]
        if meta.git_branch:
            lines.append(f"Branch: {meta.git_branch}")
        return "\n".join(lines)

    def annotate_token_chunks_with_turns(
        self,
        chunks: List[ChunkRecord],
        turn_spans: List[Tuple[int, int]],
        turn_session_dates: List[Optional[str]],
    ) -> None:
        """Tag each chunk with start_turn and end_turn via bisect search."""
        if not turn_spans:
            return
        turn_starts = [int(span[0]) for span in turn_spans]
        turn_ends = [int(span[1]) for span in turn_spans]

        for chunk in chunks:
            start = chunk.start_token
            end = chunk.end_token
            first_turn = bisect_right(turn_ends, start)
            end_turn = bisect_left(turn_starts, end)
            if first_turn < end_turn:
                chunk.start_turn = first_turn
                chunk.end_turn = end_turn
                if turn_session_dates:
                    valid_dates = [
                        turn_session_dates[i]
                        for i in range(first_turn, min(end_turn, len(turn_session_dates)))
                        if turn_session_dates[i]
                    ]
                    if valid_dates:
                        chunk.start_date = valid_dates[0]
                        chunk.end_date = valid_dates[-1]

    def add_incremental_turns(
        self,
        meta: SessionMetadata,
        new_turns: List[NormalizedTurn],
        existing_context: Optional[TurnContext] = None,
        existing_chunks: Optional[List[ChunkRecord]] = None,
        existing_units: Optional[List[int]] = None,
    ) -> Tuple[List[ChunkRecord], List[ChunkRecord], TurnContext, List[int], int]:
        """Incremental chunking routine.

        Returns:
            (all_chunks, tail_chunks_to_embed, updated_context, updated_units, first_changed)
        """
        if existing_context is None or existing_units is None:
            header = self.format_header(meta)
            units = self.enc.encode(header + "\n\n", disallowed_special=())
            turn_lines: List[str] = []
            turn_spans: List[Tuple[int, int]] = []
            turn_timestamps: List[Optional[str]] = []
            turn_session_dates: List[Optional[str]] = []
            old_chunks: List[ChunkRecord] = []
        else:
            header = existing_context.header
            units = list(existing_units)
            turn_lines = list(existing_context.turn_lines)
            turn_spans = list(existing_context.turn_spans)
            turn_timestamps = list(existing_context.turn_timestamps)
            turn_session_dates = list(existing_context.turn_session_dates)
            old_chunks = list(existing_chunks or [])

        # Append new turns into token stream
        for turn in new_turns:
            line = turn.format_line()
            separator = "" if not turn_lines else "\n"
            start_unit = len(units)
            encoded_line = self.enc.encode(separator + line, disallowed_special=())
            units.extend(encoded_line)
            end_unit = len(units)

            turn_lines.append(line)
            turn_spans.append((start_unit, end_unit))
            ts_str = turn.timestamp.isoformat() if turn.timestamp else None
            turn_timestamps.append(ts_str)
            date_str = turn.timestamp.strftime("%Y-%m-%d") if turn.timestamp else None
            turn_session_dates.append(date_str)

        # Architectural Invariant #3: Determine mutable tail threshold
        chunk_size = self.config.chunk_size
        step = self.config.step

        if not old_chunks:
            first_changed = 0
        else:
            last = old_chunks[-1]
            last_size = last.end_token - last.start_token
            if last_size >= chunk_size:
                first_changed = len(old_chunks)
            else:
                first_changed = len(old_chunks) - 1

        stable_chunks = old_chunks[:first_changed]

        # Generate chunk slices from first_changed onward
        starts: List[int] = []
        start = first_changed * step
        while start < len(units):
            starts.append(start)
            if start + chunk_size >= len(units):
                break
            start += step

        tail_chunks: List[ChunkRecord] = []
        for offset, c_start in enumerate(starts):
            c_end = min(c_start + chunk_size, len(units))
            chunk_slice = units[c_start:c_end]
            content = self.enc.decode(chunk_slice)
            c_idx = first_changed + offset

            record = ChunkRecord(
                chunk_id=f"{meta.agent}_{meta.session_id}_chunk_{c_idx}",
                session_id=meta.session_id,
                chunk_index=c_idx,
                project_path=meta.project_path,
                agent=meta.agent,
                content=content,
                start_token=c_start,
                end_token=c_end,
                session_timestamp=(
                    meta.created_at.isoformat() if meta.created_at else None
                ),
            )
            tail_chunks.append(record)

        self.annotate_token_chunks_with_turns(
            tail_chunks, turn_spans, turn_session_dates
        )

        all_chunks = stable_chunks + tail_chunks

        updated_context = TurnContext(
            header=header,
            turn_lines=turn_lines,
            turn_spans=turn_spans,
            turn_timestamps=turn_timestamps,
            turn_session_dates=turn_session_dates,
        )

        return all_chunks, tail_chunks, updated_context, units, first_changed

    def expand_chunk_to_turns(
        self,
        chunk: ChunkRecord,
        turn_context: TurnContext,
        seen_turns: Optional[Set[Tuple[str, int]]] = None,
    ) -> str:
        """Display-time dynamic expansion: expands compact chunk to full user/assistant turns."""
        turn_spans = turn_context.turn_spans
        if not turn_spans:
            return chunk.content

        turn_starts = [int(span[0]) for span in turn_spans]
        turn_ends = [int(span[1]) for span in turn_spans]

        first_turn = bisect_right(turn_ends, chunk.start_token)
        end_turn = bisect_left(turn_starts, chunk.end_token)

        if first_turn >= end_turn:
            return chunk.content

        lines_to_render: List[str] = []
        include_header = (chunk.chunk_index == 0 or first_turn == 0)
        if include_header and turn_context.header:
            lines_to_render.append(turn_context.header)
            lines_to_render.append("")

        for idx in range(first_turn, end_turn):
            if idx < len(turn_context.turn_lines):
                if seen_turns is not None:
                    turn_key = (chunk.session_id, idx)
                    if turn_key in seen_turns:
                        continue
                    seen_turns.add(turn_key)
                lines_to_render.append(turn_context.turn_lines[idx])

        return "\n".join(lines_to_render).strip()
