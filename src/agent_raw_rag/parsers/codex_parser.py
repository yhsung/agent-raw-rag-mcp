"""Incremental parser for Codex CLI session logs (~/.codex/sessions/**/rollout-*.jsonl)."""

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


class CodexParser:
    def __init__(self, sanitizer: Optional[DeterministicSanitizer] = None):
        self.sanitizer = sanitizer or DeterministicSanitizer()

    def parse_file_incremental(
        self,
        file_path: Path | str,
        start_offset: int = 0,
    ) -> Tuple[List[NormalizedTurn], int, SessionMetadata]:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Codex session file not found: {path}")

        file_size = path.stat().st_size
        if start_offset >= file_size:
            meta = SessionMetadata(session_id=path.stem, agent="codex", project_path="")
            return [], start_offset, meta

        session_id = path.stem
        project_path = ""
        git_branch = ""
        first_timestamp: Optional[datetime] = None

        new_turns: List[NormalizedTurn] = []
        current_offset = start_offset

        # Collect pending tool actions within a turn
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
                payload = event.get("payload", {})

                if ev_type == "session_meta":
                    if isinstance(payload, dict):
                        session_id = payload.get("session_id") or payload.get("id") or session_id
                        cwd = payload.get("cwd")
                        if cwd and not project_path:
                            project_path = cwd
                        git_info = payload.get("git")
                        if isinstance(git_info, dict) and git_info.get("branch"):
                            git_branch = git_info["branch"]

                elif ev_type == "turn_context":
                    if isinstance(payload, dict):
                        cwd = payload.get("cwd")
                        if cwd and not project_path:
                            project_path = cwd

                elif ev_type == "response_item":
                    if not isinstance(payload, dict):
                        continue
                    item_type = payload.get("type")
                    role = payload.get("role")

                    if item_type == "message":
                        content_list = payload.get("content", [])
                        if isinstance(content_list, str):
                            content_list = [{"type": "text", "text": content_list}]

                        user_texts: List[str] = []
                        assistant_texts: List[str] = []
                        thinking_texts: List[str] = []

                        for block in content_list:
                            if not isinstance(block, dict):
                                continue
                            b_type = block.get("type")
                            if b_type in ("input_text", "text"):
                                txt = block.get("text", "")
                                if role == "user":
                                    user_texts.append(txt)
                                else:
                                    assistant_texts.append(txt)
                            elif b_type in ("thought", "reasoning"):
                                thinking_texts.append(
                                    self.sanitizer.sanitize_thinking(
                                        block.get("text") or block.get("thought", "")
                                    )
                                )

                        if role == "user":
                            combined = "\n".join(user_texts).strip()
                            sanitized = self.sanitizer.sanitize_user_message(combined)
                            if sanitized:
                                new_turns.append(
                                    NormalizedTurn(
                                        agent="codex",
                                        session_id=session_id,
                                        turn_index=len(new_turns),
                                        role="user",
                                        content=sanitized,
                                        timestamp=ts or datetime.now(),
                                        project_path=project_path,
                                        git_branch=git_branch,
                                    )
                                )
                        elif role == "assistant":
                            if pending_actions:
                                merged = self.sanitizer.merge_actions(pending_actions)
                                new_turns.append(
                                    NormalizedTurn(
                                        agent="codex",
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

                            combined_asst = "\n".join(assistant_texts).strip()
                            if thinking_texts:
                                think_str = "\n".join(thinking_texts).strip()
                                combined_asst = f"<thinking>\n{think_str}\n</thinking>\n\n{combined_asst}".strip()

                            if combined_asst:
                                new_turns.append(
                                    NormalizedTurn(
                                        agent="codex",
                                        session_id=session_id,
                                        turn_index=len(new_turns),
                                        role="assistant",
                                        content=combined_asst,
                                        timestamp=ts or datetime.now(),
                                        project_path=project_path,
                                        git_branch=git_branch,
                                    )
                                )

                    elif item_type in ("function_call", "custom_tool_call"):
                        t_name = payload.get("name") or payload.get("tool_name", "Tool")
                        args = payload.get("arguments") or payload.get("call", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                pass
                        act = self.sanitizer.sanitize_tool_action(t_name, args)
                        pending_actions.append(act)

                    elif item_type in ("function_call_output", "custom_tool_call_output"):
                        out = payload.get("output") or payload.get("result", "")
                        exit_code = payload.get("exit_code", 0)
                        # If the last action was this function call, attach exit code and error tail
                        if pending_actions:
                            last_act = pending_actions.pop()
                            # Re-sanitize with exit code if bash
                            if "[Bash]" in last_act and exit_code != 0:
                                last_act += f" (exit {exit_code})"
                                lines = str(out).strip().splitlines()
                                if lines:
                                    err_tail = "\n  | ".join(lines[-15:])
                                    last_act += f"\n  | Error Tail:\n  | {err_tail}"
                            pending_actions.append(last_act)

        # Flush any trailing actions
        if pending_actions:
            merged = self.sanitizer.merge_actions(pending_actions)
            new_turns.append(
                NormalizedTurn(
                    agent="codex",
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
            agent="codex",
            project_path=project_path,
            git_branch=git_branch,
            created_at=first_timestamp,
        )
        return new_turns, current_offset, meta
