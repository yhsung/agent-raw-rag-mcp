"""Unit tests for Antigravity transcript parser."""

from __future__ import annotations

import json
from pathlib import Path
from agent_raw_rag.parsers.antigravity_parser import AntigravityParser
from agent_raw_rag.parsers.sanitizer import DeterministicSanitizer


def test_antigravity_parser_basic(tmp_path: Path):
    brain_dir = tmp_path / "brain" / "test-session-123" / ".system_generated" / "logs"
    brain_dir.mkdir(parents=True)
    transcript_file = brain_dir / "transcript.jsonl"

    events = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-26T04:30:00Z",
            "content": (
                "<USER_REQUEST>\n"
                "Please implement the new feature in auth.py\n"
                "</USER_REQUEST>\n"
                "<ADDITIONAL_METADATA>\n"
                "The current local time is: 2026-10-26T12:30:00+08:00.\n"
                "Command Working Directory: /Volumes/Samsung970EVOPlus/dev-projects/demo\n"
                "</ADDITIONAL_METADATA>\n"
                "<USER_SETTINGS_CHANGE>\n"
                "Setting changed\n"
                "</USER_SETTINGS_CHANGE>"
            ),
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-26T04:30:05Z",
            "thinking": "Thinking about the auth architecture...",
            "tool_calls": [
                {
                    "name": "read_file",
                    "args": {"Path": "/Volumes/Samsung970EVOPlus/dev-projects/demo/auth.py"},
                }
            ],
            "content": "",
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "GENERIC",
            "status": "DONE",
            "created_at": "2026-10-26T04:30:07Z",
            "content": "def authenticate():\n    pass\n",
        },
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-26T04:30:10Z",
            "thinking": "I have inspected auth.py, now reporting to user.",
            "tool_calls": [],
            "content": "I have checked auth.py and will implement JWT tokens.",
        },
    ]

    with open(transcript_file, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    parser = AntigravityParser(DeterministicSanitizer())
    turns, new_offset, meta = parser.parse_file_incremental(transcript_file, start_offset=0)

    assert meta.session_id == "test-session-123"
    assert meta.agent == "antigravity"
    assert meta.project_path == "/Volumes/Samsung970EVOPlus/dev-projects/demo"
    assert new_offset > 0

    assert len(turns) == 3
    # Turn 0: User input
    assert turns[0].role == "user"
    assert turns[0].content == "Please implement the new feature in auth.py"
    assert "USER_REQUEST" not in turns[0].content
    assert "ADDITIONAL_METADATA" not in turns[0].content

    # Turn 1: Action
    assert turns[1].role == "action"
    assert "[read_file]" in turns[1].content
    assert "auth.py" in turns[1].content

    # Turn 2: Assistant
    assert turns[2].role == "assistant"
    assert "JWT tokens" in turns[2].content
    assert "<thinking>" in turns[2].content


def test_antigravity_parser_error_command(tmp_path: Path):
    brain_dir = tmp_path / "brain" / "error-session-456" / ".system_generated" / "logs"
    brain_dir.mkdir(parents=True)
    transcript_file = brain_dir / "transcript.jsonl"

    events = [
        {
            "step_index": 0,
            "type": "USER_INPUT",
            "created_at": "2026-10-26T04:31:00Z",
            "content": "<USER_REQUEST>Run the tests</USER_REQUEST>",
        },
        {
            "step_index": 1,
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-26T04:31:02Z",
            "tool_calls": [
                {
                    "name": "run_command",
                    "args": {"CommandLine": "pytest tests/test_auth.py"},
                }
            ],
            "content": "",
        },
        {
            "step_index": 2,
            "type": "GENERIC",
            "created_at": "2026-10-26T04:31:05Z",
            "content": "Command exited with code 1.\n\nCommand output:\nFAIL: test_auth.py\nAssertionError: login failed",
        },
        {
            "step_index": 3,
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-26T04:31:08Z",
            "tool_calls": [],
            "content": "The test failed due to an AssertionError.",
        },
    ]

    with open(transcript_file, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    parser = AntigravityParser()
    turns, new_offset, meta = parser.parse_file_incremental(transcript_file, start_offset=0)

    assert meta.session_id == "error-session-456"
    assert len(turns) == 3

    action_turn = turns[1]
    assert action_turn.role == "action"
    assert "exit 1" in action_turn.content
    assert "AssertionError: login failed" in action_turn.content


def test_antigravity_parser_incremental(tmp_path: Path):
    brain_dir = tmp_path / "brain" / "inc-session-789" / ".system_generated" / "logs"
    brain_dir.mkdir(parents=True)
    transcript_file = brain_dir / "transcript.jsonl"

    ev1 = {
        "step_index": 0,
        "type": "USER_INPUT",
        "content": "<USER_REQUEST>First prompt</USER_REQUEST>",
    }
    with open(transcript_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(ev1) + "\n")

    parser = AntigravityParser()
    turns1, offset1, _ = parser.parse_file_incremental(transcript_file, start_offset=0)
    assert len(turns1) == 1
    assert turns1[0].content == "First prompt"
    assert offset1 > 0

    # Append second event
    ev2 = {
        "step_index": 1,
        "type": "PLANNER_RESPONSE",
        "content": "First response",
    }
    with open(transcript_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev2) + "\n")

    turns2, offset2, _ = parser.parse_file_incremental(transcript_file, start_offset=offset1)
    assert len(turns2) == 1
    assert turns2[0].content == "First response"
    assert offset2 > offset1
