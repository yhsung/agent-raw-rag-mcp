"""Deterministic Sanitizer for Coding Agent Logs.

Adheres strictly to Architectural Invariant #2:
- 100% preservation of User Prompts and Assistant responses.
- Thinking block compression (<= 512 tokens full, else prefix 128 + suffix 256 tokens).
- Compact single-line action summary for tool calls ([ToolName] target (exit_code)).
- Complete stripping of large file reads and multi-hundred line git diffs.
- Retention of the last 15 lines of stderr/stdout for failed commands (exit_code != 0).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
import tiktoken

from ..config import SanitizerConfig

_ENCODING = None


def get_tokenizer() -> tiktoken.Encoding:
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


class DeterministicSanitizer:
    def __init__(self, config: Optional[SanitizerConfig] = None):
        self.config = config or SanitizerConfig()
        self.enc = get_tokenizer()

    def sanitize_user_message(self, text: str) -> str:
        """100% preservation of user input. Strip purely internal UI wrapper tags if present."""
        if not text:
            return ""
        # Remove common CLI wrapper caveats that provide no information
        text = re.sub(
            r"<local-command-caveat>.*?</local-command-caveat>",
            "",
            text,
            flags=re.DOTALL,
        )
        return text.strip()

    def sanitize_thinking(self, thinking_text: str) -> str:
        """Compress thinking blocks if they exceed max_thinking_tokens."""
        if not thinking_text:
            return ""
        tokens = self.enc.encode(thinking_text, disallowed_special=())
        max_tok = self.config.max_thinking_tokens
        if len(tokens) <= max_tok:
            return thinking_text

        # Keep first 128 tokens and last 256 tokens
        prefix_tokens = tokens[:128]
        suffix_tokens = tokens[-256:]
        prefix_str = self.enc.decode(prefix_tokens)
        suffix_str = self.enc.decode(suffix_tokens)
        omitted = len(tokens) - (len(prefix_tokens) + len(suffix_tokens))
        return f"{prefix_str}\n... [omitted {omitted} thinking tokens] ...\n{suffix_str}"

    def sanitize_tool_action(
        self,
        tool_name: str,
        tool_input: Dict[str, Any] | str,
        tool_output: Optional[str] = None,
        exit_code: Optional[int] = 0,
    ) -> str:
        """Format a single-line action summary for tool invocation."""
        name_lower = tool_name.lower()
        inp_dict = tool_input if isinstance(tool_input, dict) else {}

        # 1. Read / View / File exploration
        if any(k in name_lower for k in ["read", "view", "cat", "open", "glob", "grep", "search"]):
            target = (
                inp_dict.get("path")
                or inp_dict.get("file_path")
                or inp_dict.get("filePath")
                or inp_dict.get("pattern")
                or inp_dict.get("query")
                or str(tool_input)
            )
            # Truncate target representation if excessively long
            target_str = str(target).strip().splitlines()[0] if str(target).strip() else "target"
            return f"[{tool_name}] {target_str[:120]}"

        # 2. Edit / Write / Patch
        if any(k in name_lower for k in ["edit", "write", "patch", "create", "modify", "update"]):
            target = (
                inp_dict.get("path")
                or inp_dict.get("file_path")
                or inp_dict.get("filePath")
                or "file"
            )
            target_str = str(target).strip().splitlines()[0] if str(target).strip() else "file"
            # Count diff stats if diff content exists in input or output
            added_lines = 0
            removed_lines = 0
            content = inp_dict.get("content") or inp_dict.get("replacement") or tool_output or ""
            if isinstance(content, str):
                for line in content.splitlines():
                    if line.startswith("+") and not line.startswith("+++"):
                        added_lines += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        removed_lines += 1
            diff_summary = f"(+{added_lines}/-{removed_lines})" if (added_lines or removed_lines) else ""
            return f"[{tool_name}] {target_str[:120]} {diff_summary}".strip()

        # 3. Bash / Terminal / Run Command
        if any(k in name_lower for k in ["bash", "terminal", "command", "exec", "run"]):
            cmd = (
                inp_dict.get("command")
                or inp_dict.get("cmd")
                or inp_dict.get("CommandLine")
                or str(tool_input)
            )
            cmd_line = str(cmd).strip().splitlines()[0] if str(cmd).strip() else "cmd"
            if len(cmd_line) > 100:
                cmd_line = cmd_line[:97] + "..."

            code = 0 if exit_code is None else exit_code
            summary = f"[{tool_name}] {cmd_line} (exit {code})"
            if code != 0 and tool_output:
                # Capture up to failed_bash_tail_lines from stderr / output
                lines = tool_output.strip().splitlines()
                tail_lines = lines[-self.config.failed_bash_tail_lines:]
                tail_err = "\n  | ".join(tail_lines)
                summary += f"\n  | Error Tail:\n  | {tail_err}"
            return summary

        # 4. Default / MCP tools / Other tools
        arg_summary = ""
        if inp_dict:
            first_val = next(iter(inp_dict.values()), "")
            arg_summary = f" {str(first_val)[:80]}"
        return f"[{tool_name}]{arg_summary}".strip()

    def merge_actions(self, actions: List[str]) -> str:
        """Merge multiple action summaries into a single line or compact block."""
        if not actions:
            return ""
        # Separate inline summaries and multiline error tails
        single_lines = []
        multilines = []
        for act in actions:
            if "\n" in act:
                multilines.append(act)
            else:
                single_lines.append(act)

        res = []
        if single_lines:
            res.append(" | ".join(single_lines))
        if multilines:
            res.extend(multilines)
        return "\n".join(res)
