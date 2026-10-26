"""Incremental parser for Oh My Pi (omp/pi) session logs (~/.omp/agent/sessions/<slug>/*.jsonl)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import logging

from ..models import NormalizedTurn, SessionMetadata
from .sanitizer import DeterministicSanitizer

logger = logging.getLogger(__name__)


def _parse_iso_time(ts_val: Any) -> Optional[datetime]:
    if not ts_val:
        return None
    if isinstance(ts_val, (int, float)):
        if ts_val > 1e11:
            return datetime.fromtimestamp(ts_val / 1000.0)
        return datetime.fromtimestamp(ts_val)
    if isinstance(ts_val, str):
        try:
            return datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        except Exception:
            pass
    return None


class OmpParser:
    def __init__(self, sanitizer: Optional[DeterministicSanitizer] = None):
        self.sanitizer = sanitizer or DeterministicSanitizer()

    def parse_file_incremental(
        self,
        file_path: Path | str,
        start_offset: int = 0,
    ) -> Tuple[List[NormalizedTurn], int, SessionMetadata]:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"OMP session file not found: {path}")

        file_size = path.stat().st_size
        if start_offset >= file_size:
            meta = SessionMetadata(session_id=path.stem, agent="omp", project_path="")
            return [], start_offset, meta

        session_id = path.stem
        project_path = ""
        git_branch = ""
        first_timestamp: Optional[datetime] = None

        new_turns: List[NormalizedTurn] = []
        current_offset = start_offset
        pending_actions: List[str] = []

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if start_offset > 0:
                f.seek(start_offset)

            while True:
                line_start_pos = f.tell()
                line = f.readline()
                if not line:
                    break

                # If incomplete trailing line at EOF, roll back offset for next retry
                if not line.endswith("\n"):
                    current_size = path.stat().st_size
                    if f.tell() >= current_size:
                        f.seek(line_start_pos)
                        current_offset = line_start_pos
                        break

                stripped = line.strip()
                if not stripped:
                    current_offset = f.tell()
                    continue

                try:
                    event = json.loads(stripped)
                    current_offset = f.tell()
                except json.JSONDecodeError as jde:
                    current_size = path.stat().st_size
                    if f.tell() >= current_size:
                        f.seek(line_start_pos)
                        current_offset = line_start_pos
                        break
                    logger.warning(
                        "Malformed JSON in %s at %d: %s. Skipping line.",
                        path,
                        line_start_pos,
                        jde,
                    )
                    current_offset = f.tell()
                    continue

                ts = _parse_iso_time(event.get("timestamp"))
                if ts and first_timestamp is None:
                    first_timestamp = ts

                ev_type = event.get("type")

                if ev_type == "session":
                    sid = event.get("id")
                    if sid:
                        session_id = sid
                    cwd = event.get("cwd")
                    if cwd and not project_path:
                        project_path = cwd
                    branch = event.get("branch")
                    if branch:
                        git_branch = branch

                elif ev_type == "message":
                    msg_obj = event.get("message", {})
                    role = msg_obj.get("role", "user")
                    content_list = msg_obj.get("content", [])
                    if isinstance(content_list, str):
                        content_list = [{"type": "text", "text": content_list}]

                    user_texts: List[str] = []
                    asst_texts: List[str] = []
                    thinking_texts: List[str] = []

                    for block in content_list:
                        if not isinstance(block, dict):
                            continue
                        b_type = block.get("type")
                        if b_type == "text":
                            t_val = block.get("text", "")
                            if role == "user":
                                user_texts.append(t_val)
                            else:
                                asst_texts.append(t_val)
                        elif b_type in ("thinking", "thought"):
                            t_val = block.get("thinking") or block.get("text", "")
                            thinking_texts.append(
                                self.sanitizer.sanitize_thinking(t_val)
                            )
                        elif b_type in ("tool_use", "toolUse", "tool_call"):
                            t_name = block.get("name") or block.get("tool", "Tool")
                            t_input = block.get("input") or block.get("args", {})
                            act = self.sanitizer.sanitize_tool_action(t_name, t_input)
                            pending_actions.append(act)

                    if role == "user":
                        combined_user = "\n".join(user_texts).strip()
                        sanitized = self.sanitizer.sanitize_user_message(combined_user)
                        if sanitized:
                            new_turns.append(
                                NormalizedTurn(
                                    agent="omp",
                                    session_id=session_id,
                                    turn_index=len(new_turns),
                                    role="user",
                                    content=sanitized,
                                    timestamp=ts or datetime.now(),
                                    project_path=project_path,
                                    git_branch=git_branch,
                                )
                            )
                    else:
                        if pending_actions:
                            merged = self.sanitizer.merge_actions(pending_actions)
                            new_turns.append(
                                NormalizedTurn(
                                    agent="omp",
                                    session_id=session_id,
                                    turn_index=len(new_turns),
                                    role="action",
                                    content=merged,
                                    timestamp=ts or datetime.now(),
                                    project_path=project_path,
                                    git_branch=git_branch,
                                )
                            )
                            pending_actions = []

                        combined_asst = "\n".join(asst_texts).strip()
                        if thinking_texts:
                            think_str = "\n".join(thinking_texts).strip()
                            combined_asst = f"<thinking>\n{think_str}\n</thinking>\n\n{combined_asst}".strip()

                        if combined_asst:
                            new_turns.append(
                                NormalizedTurn(
                                    agent="omp",
                                    session_id=session_id,
                                    turn_index=len(new_turns),
                                    role="assistant",
                                    content=combined_asst,
                                    timestamp=ts or datetime.now(),
                                    project_path=project_path,
                                    git_branch=git_branch,
                                )
                            )

                elif ev_type in ("toolResult", "tool_result"):
                    t_name = event.get("toolName") or event.get("name", "Tool")
                    out = event.get("output") or event.get("result", "")
                    code = event.get("exitCode", 0)
                    if pending_actions:
                        last_act = pending_actions.pop()
                        if "[Bash]" in last_act and code != 0:
                            last_act += f" (exit {code})"
                            lines = str(out).strip().splitlines()
                            if lines:
                                err_tail = "\n  | ".join(lines[-15:])
                                last_act += f"\n  | Error Tail:\n  | {err_tail}"
                        pending_actions.append(last_act)

        if pending_actions:
            merged = self.sanitizer.merge_actions(pending_actions)
            new_turns.append(
                NormalizedTurn(
                    agent="omp",
                    session_id=session_id,
                    turn_index=len(new_turns),
                    role="action",
                    content=merged,
                    timestamp=ts or datetime.now(),
                    project_path=project_path,
                    git_branch=git_branch,
                )
            )

        meta = SessionMetadata(
            session_id=session_id,
            agent="omp",
            project_path=project_path,
            git_branch=git_branch,
            created_at=first_timestamp,
        )
        return new_turns, current_offset, meta
