"""Incremental parser for Antigravity session logs (~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


class AntigravityParser:
    def __init__(self, sanitizer: Optional[DeterministicSanitizer] = None):
        self.sanitizer = sanitizer or DeterministicSanitizer()

    def parse_file_incremental(
        self,
        file_path: Path | str,
        start_offset: int = 0,
    ) -> Tuple[List[NormalizedTurn], int, SessionMetadata]:
        """Read newly appended JSONL events starting from start_offset.

        Returns (turns, new_byte_offset, metadata).
        Failsafe behavior:
        - If EOF is reached with an incomplete line (no trailing newline or partial JSON),
          offset rolls back to the start of that line for clean retry on next poll.
        - Corrupted lines in between are safely skipped without fatal exceptions.
        """
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Antigravity transcript file not found: {path}")

        file_size = path.stat().st_size
        if start_offset > file_size:
            # File was truncated or recreated
            start_offset = 0

        # Extract session_id from parent directory structure (.../brain/<uuid>/...)
        # e.g., /Users/.../brain/38dc6a13-fddb-47f6-9659-1dac56763336/.system_generated/logs/transcript.jsonl
        session_id = path.stem
        try:
            parts = path.parts
            if "brain" in parts:
                brain_idx = parts.index("brain")
                if brain_idx + 1 < len(parts):
                    session_id = parts[brain_idx + 1]
            elif len(path.parents) >= 3:
                session_id = path.parents[2].name
        except Exception:
            session_id = path.stem

        if start_offset >= file_size:
            meta = SessionMetadata(
                session_id=session_id,
                agent="antigravity",
                project_path="",
            )
            return [], start_offset, meta

        project_path = ""
        git_branch = ""
        first_timestamp: Optional[datetime] = None

        new_turns: List[NormalizedTurn] = []
        last_committed_offset = start_offset
        pending_actions: List[str] = []
        last_tool_call: Optional[Dict[str, Any]] = None

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if start_offset > 0:
                f.seek(start_offset)

            while True:
                line_start_pos = f.tell()
                line = f.readline()
                if not line:
                    break

                # If line is incomplete (no ending newline at EOF), roll back offset for next retry
                if not line.endswith("\n"):
                    current_size = path.stat().st_size
                    if f.tell() >= current_size:
                        # Reached partial trailing line
                        f.seek(line_start_pos)
                        last_committed_offset = line_start_pos
                        break

                stripped = line.strip()
                if not stripped:
                    last_committed_offset = f.tell()
                    continue

                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError as jde:
                    # Check if this happened at EOF
                    current_size = path.stat().st_size
                    if f.tell() >= current_size:
                        # Partial write in progress, retry next poll
                        f.seek(line_start_pos)
                        last_committed_offset = line_start_pos
                        break
                    # Otherwise, skip isolated malformed line safely
                    logger.warning(
                        "Malformed JSON in %s at %d: %s. Skipping line.",
                        path,
                        line_start_pos,
                        jde,
                    )
                    last_committed_offset = f.tell()
                    continue

                last_committed_offset = f.tell()

                ts = _parse_iso_time(event.get("created_at") or event.get("timestamp"))
                if ts and first_timestamp is None:
                    first_timestamp = ts

                ev_type = event.get("type")

                # Extract project path or workspace if present in content
                content_str = str(event.get("content") or "")
                if not project_path and content_str:
                    ws_match = re.search(r"Active Workspaces:\s*\n\s*-\s*([^\n\r]+)", content_str)
                    if ws_match:
                        project_path = ws_match.group(1).strip()
                    else:
                        cwd_match = re.search(r"Command Working Directory:\s*([^\n\r]+)", content_str)
                        if cwd_match:
                            project_path = cwd_match.group(1).strip()

                if ev_type == "USER_INPUT":
                    # Flush any pending actions before new user turn
                    if pending_actions:
                        new_turns.append(
                            NormalizedTurn(
                                agent="antigravity",
                                session_id=session_id,
                                turn_index=len(new_turns),
                                role="action",
                                content=self.sanitizer.merge_actions(pending_actions),
                                timestamp=ts or datetime.now(),
                                project_path=project_path,
                                git_branch=git_branch,
                            )
                        )
                        pending_actions = []
                        last_tool_call = None

                    user_text = ""
                    # Regex extract <USER_REQUEST> if wrapped
                    req_match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content_str, re.DOTALL)
                    if req_match:
                        user_text = req_match.group(1)
                    else:
                        # Strip metadata wrappers
                        cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", content_str, flags=re.DOTALL)
                        cleaned = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", cleaned, flags=re.DOTALL)
                        user_text = cleaned.strip()

                    sanitized_user = self.sanitizer.sanitize_user_message(user_text)
                    if sanitized_user:
                        new_turns.append(
                            NormalizedTurn(
                                agent="antigravity",
                                session_id=session_id,
                                turn_index=len(new_turns),
                                role="user",
                                content=sanitized_user,
                                timestamp=ts or datetime.now(),
                                project_path=project_path,
                                git_branch=git_branch,
                            )
                        )

                elif ev_type == "PLANNER_RESPONSE":
                    thinking_raw = event.get("thinking") or ""
                    tool_calls = event.get("tool_calls") or []
                    asst_content = event.get("content") or ""

                    if tool_calls and isinstance(tool_calls, list):
                        for tc in tool_calls:
                            if not isinstance(tc, dict):
                                continue
                            t_name = tc.get("name", "Tool")
                            t_args = tc.get("args", {})
                            last_tool_call = {"name": t_name, "args": t_args}
                            pending_actions.append(
                                self.sanitizer.sanitize_tool_action(t_name, t_args)
                            )

                    # Build assistant text
                    thinking_clean = self.sanitizer.sanitize_thinking(thinking_raw) if thinking_raw else ""
                    asst_clean = asst_content.strip()

                    # Only emit assistant turn if there is actual assistant text,
                    # or if there are no tool calls and thinking exists
                    if asst_clean or (not tool_calls and thinking_clean):
                        combined_text = asst_clean
                        if thinking_clean:
                            if combined_text:
                                combined_text = f"<thinking>\n{thinking_clean}\n</thinking>\n\n{combined_text}"
                            else:
                                combined_text = f"<thinking>\n{thinking_clean}\n</thinking>"

                        # Flush actions first if present
                        if pending_actions:
                            new_turns.append(
                                NormalizedTurn(
                                    agent="antigravity",
                                    session_id=session_id,
                                    turn_index=len(new_turns),
                                    role="action",
                                    content=self.sanitizer.merge_actions(pending_actions),
                                    timestamp=ts or datetime.now(),
                                    project_path=project_path,
                                    git_branch=git_branch,
                                )
                            )
                            pending_actions = []
                            last_tool_call = None

                        new_turns.append(
                            NormalizedTurn(
                                agent="antigravity",
                                session_id=session_id,
                                turn_index=len(new_turns),
                                role="assistant",
                                content=combined_text,
                                timestamp=ts or datetime.now(),
                                project_path=project_path,
                                git_branch=git_branch,
                            )
                        )

                elif ev_type == "GENERIC":
                    # Tool execution output
                    if last_tool_call and content_str:
                        # Check for non-zero exit code
                        code_match = re.search(r"Command exited with code\s+([0-9]+)", content_str)
                        if code_match:
                            exit_code = int(code_match.group(1))
                            if exit_code != 0:
                                # Replace last action with error tail
                                error_act = self.sanitizer.sanitize_tool_action(
                                    tool_name=last_tool_call["name"],
                                    tool_input=last_tool_call["args"],
                                    tool_output=content_str,
                                    exit_code=exit_code,
                                )
                                if pending_actions:
                                    pending_actions[-1] = error_act
                                else:
                                    pending_actions.append(error_act)
                        last_tool_call = None

        # Final flush for trailing actions
        if pending_actions:
            new_turns.append(
                NormalizedTurn(
                    agent="antigravity",
                    session_id=session_id,
                    turn_index=len(new_turns),
                    role="action",
                    content=self.sanitizer.merge_actions(pending_actions),
                    timestamp=datetime.now(),
                    project_path=project_path,
                    git_branch=git_branch,
                )
            )

        meta = SessionMetadata(
            session_id=session_id,
            agent="antigravity",
            project_path=project_path,
            git_branch=git_branch,
            created_at=first_timestamp,
        )
        return new_turns, last_committed_offset, meta
