"""Incremental parser for Claude Code session logs (~/.claude/projects/<slug>/*.jsonl)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..models import NormalizedTurn, SessionMetadata
from .sanitizer import DeterministicSanitizer


def _parse_iso_time(ts_val: Any) -> Optional[datetime]:
    if not ts_val:
        return None
    if isinstance(ts_val, (int, float)):
        # May be seconds or milliseconds
        if ts_val > 1e11:
            return datetime.fromtimestamp(ts_val / 1000.0)
        return datetime.fromtimestamp(ts_val)
    if isinstance(ts_val, str):
        try:
            return datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        except Exception:
            pass
    return None


class ClaudeParser:
    def __init__(self, sanitizer: Optional[DeterministicSanitizer] = None):
        self.sanitizer = sanitizer or DeterministicSanitizer()

    def parse_file_incremental(
        self,
        file_path: Path | str,
        start_offset: int = 0,
    ) -> Tuple[List[NormalizedTurn], int, SessionMetadata]:
        """Read newly appended JSONL events starting from start_offset.

        Returns (turns, new_byte_offset, metadata).
        """
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Claude session file not found: {path}")

        file_size = path.stat().st_size
        if start_offset >= file_size:
            meta = SessionMetadata(
                session_id=path.stem,
                agent="claude",
                project_path="",
            )
            return [], start_offset, meta

        session_id = path.stem
        project_path = ""
        git_branch = ""
        first_timestamp: Optional[datetime] = None

        new_turns: List[NormalizedTurn] = []
        current_offset = start_offset

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if start_offset > 0:
                f.seek(start_offset)

            while True:
                line = f.readline()
                if not line:
                    break
                current_offset = f.tell()
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except Exception:
                    continue

                # Extract environment context if available
                sid = event.get("sessionId")
                if sid:
                    session_id = sid
                cwd = event.get("cwd")
                if cwd and not project_path:
                    project_path = cwd
                branch = event.get("gitBranch")
                if branch and not git_branch:
                    git_branch = branch

                ts = _parse_iso_time(event.get("timestamp"))
                if ts and first_timestamp is None:
                    first_timestamp = ts

                ev_type = event.get("type")
                if ev_type == "user":
                    msg_obj = event.get("message", {})
                    content_raw = msg_obj.get("content", "")
                    user_text = ""
                    if isinstance(content_raw, str):
                        user_text = content_raw
                    elif isinstance(content_raw, list):
                        parts = []
                        for block in content_raw:
                            if isinstance(block, dict) and block.get("type") == "text":
                                parts.append(block.get("text", ""))
                            elif isinstance(block, str):
                                parts.append(block)
                        user_text = "\n".join(parts)

                    sanitized_user = self.sanitizer.sanitize_user_message(user_text)
                    if sanitized_user:
                        new_turns.append(
                            NormalizedTurn(
                                agent="claude",
                                session_id=session_id,
                                turn_index=len(new_turns),
                                role="user",
                                content=sanitized_user,
                                timestamp=ts or datetime.now(),
                                project_path=project_path,
                                git_branch=git_branch,
                            )
                        )

                elif ev_type == "assistant":
                    msg_obj = event.get("message", {})
                    content_raw = msg_obj.get("content", "")

                    assistant_texts: List[str] = []
                    actions: List[str] = []
                    thinking_parts: List[str] = []

                    if isinstance(content_raw, str):
                        assistant_texts.append(content_raw)
                    elif isinstance(content_raw, list):
                        for block in content_raw:
                            if not isinstance(block, dict):
                                continue
                            b_type = block.get("type")
                            if b_type == "text":
                                assistant_texts.append(block.get("text", ""))
                            elif b_type == "thinking":
                                thinking_parts.append(
                                    self.sanitizer.sanitize_thinking(
                                        block.get("thinking", "")
                                    )
                                )
                            elif b_type == "tool_use":
                                t_name = block.get("name", "Tool")
                                t_inp = block.get("input", {})
                                actions.append(
                                    self.sanitizer.sanitize_tool_action(
                                        t_name, t_inp
                                    )
                                )

                    # Check for tool results in the event or attachments
                    if "attachment" in event:
                        att = event["attachment"]
                        if isinstance(att, dict) and att.get("type") == "tool_result":
                            actions.append(
                                self.sanitizer.sanitize_tool_action(
                                    att.get("toolName", "Tool"),
                                    att.get("input", {}),
                                    tool_output=att.get("output"),
                                    exit_code=att.get("exitCode", 0),
                                )
                            )

                    # Insert action summary if any tool was called
                    if actions:
                        merged_actions = self.sanitizer.merge_actions(actions)
                        new_turns.append(
                            NormalizedTurn(
                                agent="claude",
                                session_id=session_id,
                                turn_index=len(new_turns),
                                role="action",
                                content=merged_actions,
                                timestamp=ts or datetime.now(),
                                project_path=project_path,
                                git_branch=git_branch,
                            )
                        )

                    # Insert assistant response text
                    combined_text = "\n".join(assistant_texts).strip()
                    if thinking_parts:
                        think_combined = "\n".join(thinking_parts).strip()
                        combined_text = f"<thinking>\n{think_combined}\n</thinking>\n\n{combined_text}".strip()

                    if combined_text:
                        new_turns.append(
                            NormalizedTurn(
                                agent="claude",
                                session_id=session_id,
                                turn_index=len(new_turns),
                                role="assistant",
                                content=combined_text,
                                timestamp=ts or datetime.now(),
                                project_path=project_path,
                                git_branch=git_branch,
                            )
                        )

        meta = SessionMetadata(
            session_id=session_id,
            agent="claude",
            project_path=project_path,
            git_branch=git_branch,
            created_at=first_timestamp,
        )
        return new_turns, current_offset, meta
