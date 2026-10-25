"""Standard data models for agent-raw-rag-mcp."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import numpy as np


@dataclass
class NormalizedTurn:
    """Represents a sanitized dialogue turn or action summary line."""
    agent: str  # "claude" | "codex" | "opencode" | "omp"
    session_id: str
    turn_index: int
    role: str  # "user" | "assistant" | "action"
    content: str
    timestamp: Optional[datetime] = None
    project_path: str = ""
    git_branch: str = ""
    action_summary: Optional[str] = None

    def format_line(self) -> str:
        """Format turn line according to Raw-RAG specification."""
        time_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else "unknown-time"
        if self.role == "action":
            return f"[{time_str}] Assistant (Actions): {self.content}"
        elif self.role == "user":
            return f"[{time_str}] User: {self.content}"
        else:
            return f"[{time_str}] Assistant: {self.content}"


@dataclass
class SessionMetadata:
    session_id: str
    agent: str
    project_path: str
    git_branch: str = ""
    created_at: Optional[datetime] = None
    title: str = ""


@dataclass
class ChunkRecord:
    """A 256-token chunk with full boundary mappings and temporal coordinate."""
    chunk_id: str
    session_id: str
    chunk_index: int
    project_path: str
    agent: str
    content: str
    start_token: int
    end_token: int
    start_turn: int = 0
    end_turn: int = 0
    start_date: Optional[str] = None  # "YYYY-MM-DD"
    end_date: Optional[str] = None
    session_timestamp: Optional[str] = None
    embedding: Optional[np.ndarray] = None


@dataclass
class TurnContext:
    """Session-level turn mapping structure for display-time dynamic turn expansion."""
    header: str
    turn_lines: List[str]
    turn_spans: List[Tuple[int, int]]  # [start_tok, end_tok] for each turn line
    turn_timestamps: List[Optional[str]]
    turn_session_dates: List[Optional[str]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "header": self.header,
            "turn_lines": self.turn_lines,
            "turn_spans": self.turn_spans,
            "turn_timestamps": self.turn_timestamps,
            "turn_session_dates": self.turn_session_dates,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TurnContext:
        return cls(
            header=data.get("header", ""),
            turn_lines=data.get("turn_lines", []),
            turn_spans=[(int(s[0]), int(s[1])) for s in data.get("turn_spans", [])],
            turn_timestamps=data.get("turn_timestamps", []),
            turn_session_dates=data.get("turn_session_dates", []),
        )


@dataclass
class IngestionCursor:
    source_key: str
    agent: str
    byte_offset: int = 0
    last_timestamp: int = 0
    updated_at: str = ""


@dataclass
class IngestionStats:
    agent: str
    sessions_scanned: int = 0
    new_sessions: int = 0
    new_turns: int = 0
    recomputed_chunks: int = 0
    duration_ms: float = 0.0


@dataclass
class SearchResult:
    chunk_id: str
    session_id: str
    chunk_index: int
    agent: str
    project_path: str
    similarity_score: float
    rerank_score: float
    start_date: Optional[str]
    raw_content: str
    expanded_turn_content: str
    start_turn: int
    end_turn: int
