"""Incremental parser for OpenCode SQLite database (~/.local/share/opencode/opencode.db).

Operates via read-only SQLite cursor streaming to prevent memory ballooning
on multi-gigabyte databases (Architectural Invariant #2).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..models import NormalizedTurn, SessionMetadata
from .sanitizer import DeterministicSanitizer


class OpenCodeParser:
    def __init__(self, sanitizer: Optional[DeterministicSanitizer] = None):
        self.sanitizer = sanitizer or DeterministicSanitizer()

    def parse_db_incremental(
        self,
        db_path: Path | str,
        last_time_updated: int = 0,
        batch_limit: int = 100,
    ) -> Tuple[List[NormalizedTurn], int, List[SessionMetadata]]:
        """Incrementally stream newly updated sessions from OpenCode SQLite DB.

        Returns (all_new_turns, max_time_updated, session_metas).
        """
        path = Path(db_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"OpenCode database not found: {path}")

        # Connect with read-only URI
        conn_uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(conn_uri, uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row

        new_turns: List[NormalizedTurn] = []
        session_metas: List[SessionMetadata] = []
        max_seen_updated = last_time_updated

        try:
            cur = conn.cursor()
            # Select sessions updated since last_time_updated
            cur.execute(
                """
                SELECT id, directory, title, time_created, time_updated
                FROM session
                WHERE time_updated > ?
                ORDER BY time_updated ASC
                LIMIT ?
                """,
                (last_time_updated, batch_limit),
            )
            sessions = cur.fetchall()

            for s_row in sessions:
                sid = s_row["id"]
                directory = s_row["directory"] or ""
                s_updated = int(s_row["time_updated"])
                if s_updated > max_seen_updated:
                    max_seen_updated = s_updated

                created_dt = (
                    datetime.fromtimestamp(s_row["time_created"] / 1000.0)
                    if s_row["time_created"]
                    else datetime.now()
                )

                meta = SessionMetadata(
                    session_id=sid,
                    agent="opencode",
                    project_path=directory,
                    created_at=created_dt,
                    title=s_row["title"] or "",
                )
                session_metas.append(meta)

                # Fetch messages for this session
                msg_cur = conn.cursor()
                msg_cur.execute(
                    """
                    SELECT id, time_created, data
                    FROM message
                    WHERE session_id = ?
                    ORDER BY time_created ASC
                    """,
                    (sid,),
                )
                messages = msg_cur.fetchall()

                for m_row in messages:
                    mid = m_row["id"]
                    m_created = int(m_row["time_created"])
                    m_dt = datetime.fromtimestamp(m_created / 1000.0) if m_created else created_dt

                    try:
                        m_data = json.loads(m_row["data"])
                    except Exception:
                        m_data = {}

                    role = m_data.get("role", "user")

                    # Query parts for this message
                    part_cur = conn.cursor()
                    part_cur.execute(
                        """
                        SELECT id, time_created, data
                        FROM part
                        WHERE message_id = ?
                        ORDER BY time_created ASC
                        """,
                        (mid,),
                    )
                    parts = part_cur.fetchall()

                    user_texts: List[str] = []
                    asst_texts: List[str] = []
                    thinking_texts: List[str] = []
                    actions: List[str] = []

                    for p_row in parts:
                        try:
                            p_data = json.loads(p_row["data"])
                        except Exception:
                            continue

                        p_type = p_data.get("type")
                        if p_type == "text":
                            t_val = p_data.get("text", "")
                            if role == "user":
                                user_texts.append(t_val)
                            else:
                                asst_texts.append(t_val)
                        elif p_type == "reasoning":
                            t_val = p_data.get("text", "")
                            thinking_texts.append(
                                self.sanitizer.sanitize_thinking(t_val)
                            )
                        elif p_type in ("tool", "tool-call", "tool_use"):
                            t_name = p_data.get("tool") or p_data.get("name", "Tool")
                            t_input = p_data.get("input") or p_data.get("args", {})
                            # Skip giant tool outputs, record compact action
                            act = self.sanitizer.sanitize_tool_action(t_name, t_input)
                            actions.append(act)

                    if role == "user":
                        combined_user = "\n".join(user_texts).strip()
                        sanitized = self.sanitizer.sanitize_user_message(combined_user)
                        if sanitized:
                            new_turns.append(
                                NormalizedTurn(
                                    agent="opencode",
                                    session_id=sid,
                                    turn_index=len(new_turns),
                                    role="user",
                                    content=sanitized,
                                    timestamp=m_dt,
                                    project_path=directory,
                                )
                            )
                    else:
                        if actions:
                            merged_acts = self.sanitizer.merge_actions(actions)
                            new_turns.append(
                                NormalizedTurn(
                                    agent="opencode",
                                    session_id=sid,
                                    turn_index=len(new_turns),
                                    role="action",
                                    content=merged_acts,
                                    timestamp=m_dt,
                                    project_path=directory,
                                )
                            )

                        combined_asst = "\n".join(asst_texts).strip()
                        if thinking_texts:
                            think_str = "\n".join(thinking_texts).strip()
                            combined_asst = f"<thinking>\n{think_str}\n</thinking>\n\n{combined_asst}".strip()

                        if combined_asst:
                            new_turns.append(
                                NormalizedTurn(
                                    agent="opencode",
                                    session_id=sid,
                                    turn_index=len(new_turns),
                                    role="assistant",
                                    content=combined_asst,
                                    timestamp=m_dt,
                                    project_path=directory,
                                )
                            )
        finally:
            conn.close()

        return new_turns, max_seen_updated, session_metas
