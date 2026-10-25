import pytest
from agent_raw_rag.parsers.sanitizer import DeterministicSanitizer
from agent_raw_rag.config import SanitizerConfig


def test_sanitize_user_message():
    sanitizer = DeterministicSanitizer()
    text = "<local-command-caveat>internal stuff</local-command-caveat>Hello, what is the meaning of life?"
    cleaned = sanitizer.sanitize_user_message(text)
    assert cleaned == "Hello, what is the meaning of life?"


def test_sanitize_thinking():
    cfg = SanitizerConfig(max_thinking_tokens=100)
    sanitizer = DeterministicSanitizer(cfg)

    short_think = "I need to check the file existence."
    assert sanitizer.sanitize_thinking(short_think) == short_think

    long_think = "word " * 600
    compressed = sanitizer.sanitize_thinking(long_think)
    assert "... [omitted" in compressed
    assert "thinking tokens] ..." in compressed


def test_sanitize_tool_action_read():
    sanitizer = DeterministicSanitizer()
    act = sanitizer.sanitize_tool_action(
        "Read",
        {"file_path": "src/agent_raw_rag/parsers/sanitizer.py"},
        tool_output="huge code base with 1000 lines...",
    )
    assert act == "[Read] src/agent_raw_rag/parsers/sanitizer.py"
    assert "huge code" not in act


def test_sanitize_tool_action_bash_success():
    sanitizer = DeterministicSanitizer()
    act = sanitizer.sanitize_tool_action(
        "Bash",
        {"command": "pytest -q tests/test_sanitizer.py"},
        tool_output=".. 2 passed in 0.05s",
        exit_code=0,
    )
    assert act == "[Bash] pytest -q tests/test_sanitizer.py (exit 0)"
    assert "passed" not in act


def test_sanitize_tool_action_bash_failure():
    sanitizer = DeterministicSanitizer()
    long_err = "\n".join([f"line error {i}" for i in range(50)])
    act = sanitizer.sanitize_tool_action(
        "Bash",
        {"command": "cargo build"},
        tool_output=long_err,
        exit_code=1,
    )
    assert "[Bash] cargo build (exit 1)" in act
    assert "Error Tail:" in act
    assert "line error 49" in act
    assert "line error 0" not in act  # Only last 15 lines kept


def test_sanitize_tool_action_edit():
    sanitizer = DeterministicSanitizer()
    diff = "--- a.py\n+++ a.py\n@@ ... @@\n+def foo():\n+    return 1\n-def bar():\n"
    act = sanitizer.sanitize_tool_action(
        "Edit",
        {"path": "a.py", "content": diff},
    )
    assert act == "[Edit] a.py (+2/-1)"
