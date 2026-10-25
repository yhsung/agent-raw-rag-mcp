"""SQLite WAL persistence and vector storage engine for agent-raw-rag-mcp.

Provides cursor tracking, incremental chunk saving, turn context serialization,
and candidate matrix retrieval for two-stage MLX ranking.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from ..config import StorageConfig
from ..models import ChunkRecord, IngestionCursor, NormalizedTurn, SessionMetadata, TurnContext


class SQLiteStore:
    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self.db_path = Path(self.config.sqlite_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for high concurrency
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_cursors (
                    source_key TEXT PRIMARY KEY,
                    agent TEXT NOT NULL,
                    byte_offset INTEGER DEFAULT 0,
                    last_timestamp INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    agent TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    git_branch TEXT DEFAULT '',
                    header TEXT NOT NULL,
                    turn_context_json TEXT NOT NULL,
                    units_blob BLOB,
                    total_tokens INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    project_path TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    content TEXT NOT NULL,
                    start_token INTEGER NOT NULL,
                    end_token INTEGER NOT NULL,
                    start_turn INTEGER DEFAULT 0,
                    end_turn INTEGER DEFAULT 0,
                    start_date TEXT,
                    end_date TEXT,
                    session_timestamp TEXT,
                    embedding BLOB,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_path);
                CREATE INDEX IF NOT EXISTS idx_chunks_agent ON chunks(agent);
                CREATE INDEX IF NOT EXISTS idx_chunks_session ON chunks(session_id);
                """
            )

    def get_cursor(self, source_key: str) -> Optional[IngestionCursor]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT source_key, agent, byte_offset, last_timestamp, updated_at FROM ingestion_cursors WHERE source_key = ?",
                (source_key,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return IngestionCursor(
                source_key=row["source_key"],
                agent=row["agent"],
                byte_offset=row["byte_offset"],
                last_timestamp=row["last_timestamp"],
                updated_at=row["updated_at"],
            )

    def save_cursor(self, cursor: IngestionCursor) -> None:
        now_str = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_cursors (source_key, agent, byte_offset, last_timestamp, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    byte_offset = excluded.byte_offset,
                    last_timestamp = excluded.last_timestamp,
                    updated_at = excluded.updated_at
                """,
                (
                    cursor.source_key,
                    cursor.agent,
                    cursor.byte_offset,
                    cursor.last_timestamp,
                    now_str,
                ),
            )

    def get_session_state(
        self, session_id: str
    ) -> Optional[Tuple[TurnContext, List[ChunkRecord], List[int]]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT header, turn_context_json, units_blob FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            s_row = cur.fetchone()
            if not s_row:
                return None

            turn_context = TurnContext.from_dict(
                json.loads(s_row["turn_context_json"])
            )
            units: List[int] = []
            if s_row["units_blob"]:
                units = np.frombuffer(s_row["units_blob"], dtype=np.uint32).tolist()

            cur.execute(
                """
                SELECT chunk_id, session_id, chunk_index, project_path, agent, content,
                       start_token, end_token, start_turn, end_turn, start_date, end_date,
                       session_timestamp, embedding
                FROM chunks
                WHERE session_id = ?
                ORDER BY chunk_index ASC
                """,
                (session_id,),
            )
            chunk_rows = cur.fetchall()

            chunks: List[ChunkRecord] = []
            for r in chunk_rows:
                emb = None
                if r["embedding"]:
                    emb = np.frombuffer(r["embedding"], dtype=np.float32)
                chunks.append(
                    ChunkRecord(
                        chunk_id=r["chunk_id"],
                        session_id=r["session_id"],
                        chunk_index=r["chunk_index"],
                        project_path=r["project_path"],
                        agent=r["agent"],
                        content=r["content"],
                        start_token=r["start_token"],
                        end_token=r["end_token"],
                        start_turn=r["start_turn"],
                        end_turn=r["end_turn"],
                        start_date=r["start_date"],
                        end_date=r["end_date"],
                        session_timestamp=r["session_timestamp"],
                        embedding=emb,
                    )
                )

            return turn_context, chunks, units

    def save_session_chunks(
        self,
        meta: SessionMetadata,
        context: TurnContext,
        units: List[int],
        all_chunks: List[ChunkRecord],
        first_changed: int,
    ) -> None:
        now_str = datetime.now().isoformat()
        units_arr = np.array(units, dtype=np.uint32)
        units_blob = units_arr.tobytes()

        with self._get_connection() as conn:
            # 1. Upsert session row
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, agent, project_path, git_branch, header,
                    turn_context_json, units_blob, total_tokens, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    project_path = excluded.project_path,
                    git_branch = excluded.git_branch,
                    header = excluded.header,
                    turn_context_json = excluded.turn_context_json,
                    units_blob = excluded.units_blob,
                    total_tokens = excluded.total_tokens,
                    updated_at = excluded.updated_at
                """,
                (
                    meta.session_id,
                    meta.agent,
                    meta.project_path,
                    meta.git_branch,
                    context.header,
                    json.dumps(context.to_dict()),
                    units_blob,
                    len(units),
                    now_str,
                ),
            )

            # 2. Prune old mutable chunks >= first_changed
            conn.execute(
                "DELETE FROM chunks WHERE session_id = ? AND chunk_index >= ?",
                (meta.session_id, first_changed),
            )

            # 3. Insert newly computed tail chunks
            tail_chunks = all_chunks[first_changed:]
            chunk_params = []
            for c in tail_chunks:
                emb_bytes = None
                if c.embedding is not None:
                    emb_bytes = np.asarray(c.embedding, dtype=np.float32).tobytes()

                chunk_params.append(
                    (
                        c.chunk_id,
                        c.session_id,
                        c.chunk_index,
                        c.project_path,
                        c.agent,
                        c.content,
                        c.start_token,
                        c.end_token,
                        c.start_turn,
                        c.end_turn,
                        c.start_date,
                        c.end_date,
                        c.session_timestamp,
                        emb_bytes,
                    )
                )

            if chunk_params:
                conn.executemany(
                    """
                    INSERT INTO chunks (
                        chunk_id, session_id, chunk_index, project_path, agent,
                        content, start_token, end_token, start_turn, end_turn,
                        start_date, end_date, session_timestamp, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    chunk_params,
                )

    def load_candidates_with_embeddings(
        self,
        project_path: Optional[str] = None,
        agent: Optional[str] = None,
        after_date: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> List[ChunkRecord]:
        """Fetch matching chunks with valid embeddings for first-stage dense retrieval."""
        clauses = ["embedding IS NOT NULL"]
        params: List[Any] = []

        if project_path and project_path != "*":
            # Match exact project path or subdirectory
            clauses.append("(project_path = ? OR project_path LIKE ?)")
            params.append(project_path)
            params.append(f"{project_path.rstrip('/')}/%")

        if agent:
            clauses.append("agent = ?")
            params.append(agent)

        if after_date:
            clauses.append("(end_date >= ? OR start_date >= ?)")
            params.append(after_date)
            params.append(after_date)

        if before_date:
            clauses.append("(start_date <= ? OR end_date <= ?)")
            params.append(before_date)
            params.append(before_date)

        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT chunk_id, session_id, chunk_index, project_path, agent, content,
                   start_token, end_token, start_turn, end_turn, start_date, end_date,
                   session_timestamp, embedding
            FROM chunks
            WHERE {where_sql}
            ORDER BY session_timestamp DESC, chunk_index DESC
        """

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

            candidates: List[ChunkRecord] = []
            for r in rows:
                emb = np.frombuffer(r["embedding"], dtype=np.float32)
                candidates.append(
                    ChunkRecord(
                        chunk_id=r["chunk_id"],
                        session_id=r["session_id"],
                        chunk_index=r["chunk_index"],
                        project_path=r["project_path"],
                        agent=r["agent"],
                        content=r["content"],
                        start_token=r["start_token"],
                        end_token=r["end_token"],
                        start_turn=r["start_turn"],
                        end_turn=r["end_turn"],
                        start_date=r["start_date"],
                        end_date=r["end_date"],
                        session_timestamp=r["session_timestamp"],
                        embedding=emb,
                    )
                )
            return candidates

    def get_turn_context(self, session_id: str) -> Optional[TurnContext]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT turn_context_json FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return TurnContext.from_dict(json.loads(row["turn_context_json"]))

    def get_recent_turns(
        self, project_path: Optional[str] = None, limit_turns: int = 30
    ) -> List[Dict[str, Any]]:
        """Retrieve recent dialogue lines across sessions for context bootstrap or after /compact."""
        params: List[Any] = []
        where_sql = ""
        if project_path and project_path != "*":
            where_sql = "WHERE project_path = ? OR project_path LIKE ?"
            params.extend([project_path, f"{project_path.rstrip('/')}/%"])

        sql = f"""
            SELECT session_id, agent, project_path, header, turn_context_json, updated_at
            FROM sessions
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT 10
        """

        collected: List[Dict[str, Any]] = []
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            sessions = cur.fetchall()

            for s in sessions:
                ctx = TurnContext.from_dict(json.loads(s["turn_context_json"]))
                lines = ctx.turn_lines
                times = ctx.turn_timestamps
                for idx in reversed(range(len(lines))):
                    collected.append(
                        {
                            "session_id": s["session_id"],
                            "agent": s["agent"],
                            "project_path": s["project_path"],
                            "line": lines[idx],
                            "timestamp": times[idx] if idx < len(times) else None,
                        }
                    )
                    if len(collected) >= limit_turns:
                        break
                if len(collected) >= limit_turns:
                    break

        collected.reverse()
        return collected

    def get_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) as cnt FROM sessions")
            total_sessions = cur.fetchone()["cnt"]

            cur.execute("SELECT count(*) as cnt FROM chunks")
            total_chunks = cur.fetchone()["cnt"]

            cur.execute("SELECT agent, count(*) as cnt FROM chunks GROUP BY agent")
            agent_counts = {r["agent"]: r["cnt"] for r in cur.fetchall()}

            cur.execute("SELECT max(updated_at) as latest FROM sessions")
            row = cur.fetchone()
            latest_time = row["latest"] if row else None

            return {
                "total_sessions": total_sessions,
                "total_chunks": total_chunks,
                "chunks_by_agent": agent_counts,
                "last_updated": latest_time,
                "storage_path": str(self.db_path),
            }
