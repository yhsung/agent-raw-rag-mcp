import json
import sqlite3
import pytest
from pathlib import Path

from agent_raw_rag.parsers.claude_parser import ClaudeParser
from agent_raw_rag.parsers.codex_parser import CodexParser
from agent_raw_rag.parsers.opencode_parser import OpenCodeParser
from agent_raw_rag.parsers.omp_parser import OmpParser


def test_claude_parser_incremental(tmp_path: Path):
    parser = ClaudeParser()
    jsonl_file = tmp_path / "claude_test.jsonl"

    events = [
        {"type": "user", "sessionId": "sess-claude-1", "cwd": "/project/foo", "gitBranch": "main", "timestamp": "2026-10-25T10:00:00Z", "message": {"content": "Can you fix the bug?"}},
        {"type": "assistant", "sessionId": "sess-claude-1", "cwd": "/project/foo", "timestamp": "2026-10-25T10:00:05Z", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "bug.py"}}, {"type": "text", "text": "I found the bug."}]}},
    ]
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    turns, offset1, meta = parser.parse_file_incremental(jsonl_file, start_offset=0)
    assert len(turns) == 3  # user, action, assistant
    assert turns[0].role == "user"
    assert turns[0].content == "Can you fix the bug?"
    assert turns[1].role == "action"
    assert "[Read] bug.py" in turns[1].content
    assert turns[2].role == "assistant"
    assert turns[2].content == "I found the bug."
    assert meta.session_id == "sess-claude-1"

    # Append new event
    append_ev = {"type": "user", "sessionId": "sess-claude-1", "timestamp": "2026-10-25T10:01:00Z", "message": {"content": "Great, thanks!"}}
    with open(jsonl_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(append_ev) + "\n")

    new_turns, offset2, _ = parser.parse_file_incremental(jsonl_file, start_offset=offset1)
    assert len(new_turns) == 1
    assert new_turns[0].content == "Great, thanks!"
    assert offset2 > offset1


def test_codex_parser_incremental(tmp_path: Path):
    parser = CodexParser()
    jsonl_file = tmp_path / "codex_test.jsonl"

    events = [
        {"type": "session_meta", "timestamp": "2026-10-25T10:00:00Z", "payload": {"session_id": "codex-1", "cwd": "/project/codex"}},
        {"type": "response_item", "timestamp": "2026-10-25T10:00:02Z", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run tests"}]}},
        {"type": "response_item", "timestamp": "2026-10-25T10:00:03Z", "payload": {"type": "function_call", "name": "bash", "arguments": {"command": "pytest"}}},
        {"type": "response_item", "timestamp": "2026-10-25T10:00:05Z", "payload": {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "Tests running"}]}},
    ]
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    turns, offset1, meta = parser.parse_file_incremental(jsonl_file, start_offset=0)
    assert len(turns) == 3
    assert turns[0].role == "user"
    assert turns[1].role == "action"
    assert turns[2].role == "assistant"
    assert meta.session_id == "codex-1"


def test_opencode_parser_incremental(tmp_path: Path):
    parser = OpenCodeParser()
    db_file = tmp_path / "opencode_test.db"

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("CREATE TABLE session (id text PRIMARY KEY, directory text, title text, time_created integer, time_updated integer);")
    cur.execute("CREATE TABLE message (id text PRIMARY KEY, session_id text, time_created integer, time_updated integer, data text);")
    cur.execute("CREATE TABLE part (id text PRIMARY KEY, message_id text, session_id text, time_created integer, time_updated integer, data text);")

    cur.execute("INSERT INTO session VALUES ('s1', '/path/opencode', 'Title 1', 1000, 2000);")
    cur.execute("INSERT INTO message VALUES ('m1', 's1', 1000, 1000, '{\"role\": \"user\"}');")
    cur.execute("INSERT INTO part VALUES ('p1', 'm1', 's1', 1000, 1000, '{\"type\": \"text\", \"text\": \"Analyze this code\"}');")
    cur.execute("INSERT INTO message VALUES ('m2', 's1', 1500, 1500, '{\"role\": \"assistant\"}');")
    cur.execute("INSERT INTO part VALUES ('p2', 'm2', 's1', 1500, 1500, '{\"type\": \"text\", \"text\": \"Here is analysis\"}');")
    conn.commit()
    conn.close()

    turns, max_updated, metas = parser.parse_db_incremental(db_file, last_time_updated=0)
    assert len(metas) == 1
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].content == "Analyze this code"
    assert turns[1].role == "assistant"
    assert max_updated == 2000


def test_omp_parser_incremental(tmp_path: Path):
    parser = OmpParser()
    jsonl_file = tmp_path / "omp_test.jsonl"

    events = [
        {"type": "session", "id": "omp-1", "cwd": "/project/omp", "timestamp": "2026-10-25T10:00:00Z"},
        {"type": "message", "timestamp": "2026-10-25T10:00:01Z", "message": {"role": "user", "content": [{"type": "text", "text": "Check git status"}]}},
        {"type": "message", "timestamp": "2026-10-25T10:00:02Z", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "bash", "input": {"command": "git status"}}, {"type": "text", "text": "Clean working tree"}]}},
    ]
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    turns, offset1, meta = parser.parse_file_incremental(jsonl_file, start_offset=0)
    assert len(turns) == 3
    assert turns[0].role == "user"
    assert turns[1].role == "action"
    assert turns[2].role == "assistant"
    assert meta.session_id == "omp-1"
