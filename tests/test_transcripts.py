from steward.sources.transcripts import (
    detect_format,
    parse_claude_code,
    parse_codex,
    parse_copilot,
    parse_pi,
)

CLAUDE = [
    {
        "type": "user",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": "user", "content": "fix the bug"},
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-6",
            "content": [
                {"type": "thinking", "thinking": "secret"},
                {"type": "text", "text": "Looking"},
                {"type": "tool_use", "id": "1", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "1", "content": "a.py"}],
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "[Request interrupted by user]"}],
        },
    },
    {
        "type": "user",
        "isSidechain": True,
        "message": {"role": "user", "content": "subagent prompt"},
    },
    {
        "type": "user",
        "message": {"role": "user", "content": "<system-reminder>x</system-reminder>"},
    },
    {"type": "progress", "data": {}},
]


def test_claude_code() -> None:
    parsed = parse_claude_code(CLAUDE)
    kinds = [s.kind for s in parsed.steps]
    assert kinds == ["user", "agent_text", "action", "result", "user", "system"]
    assert parsed.steps[2].tool == "Bash" and parsed.steps[2].content == "ls"
    assert parsed.steps[4].is_interruption
    assert all("secret" not in s.content for s in parsed.steps)
    assert parsed.models["claude-opus-4-6"] == 1
    assert detect_format(CLAUDE) == "claude_code"


CODEX = [
    {
        "type": "session_meta",
        "payload": {
            "id": "s",
            "git": {"commit_hash": "c" * 40, "repository_url": "https://github.com/o/r.git"},
        },
    },
    {"type": "turn_context", "payload": {"model": "gpt-5.4"}},
    {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "<environment_context>cwd</environment_context>"}
            ],
        },
    },
    {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "add a flag"}],
        },
    },
    {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
    {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"cmd": "git status"}',
            "call_id": "1",
        },
    },
    {
        "type": "response_item",
        "payload": {"type": "function_call_output", "call_id": "1", "output": "clean"},
    },
    {"type": "event_msg", "payload": {"type": "turn_aborted", "reason": "interrupted"}},
    {"type": "event_msg", "payload": {"type": "user_message", "message": "duplicate"}},
]


def test_codex() -> None:
    parsed = parse_codex(CODEX)
    assert [s.kind for s in parsed.steps] == ["system", "user", "action", "result", "system"]
    assert parsed.steps[2].content == "git status"
    assert parsed.steps[4].is_interruption
    assert parsed.base_commit == "c" * 40
    assert parsed.repository_url == "https://github.com/o/r.git"
    assert detect_format(CODEX) == "codex"


PI = [
    {"type": "session", "id": "s"},
    {
        "type": "message",
        "id": "1",
        "parentId": None,
        "message": {"role": "user", "content": [{"type": "text", "text": "go"}]},
    },
    {
        "type": "message",
        "id": "2",
        "parentId": "1",
        "message": {
            "role": "assistant",
            "model": "glm-5",
            "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "toolCall", "id": "t", "name": "bash", "arguments": {"command": "make"}},
            ],
            "stopReason": "aborted",
        },
    },
    {
        "type": "message",
        "id": "3",
        "parentId": "2",
        "message": {
            "role": "toolResult",
            "toolCallId": "t",
            "content": [{"type": "text", "text": "built"}],
        },
    },
]


def test_pi() -> None:
    parsed = parse_pi(PI)
    assert [s.kind for s in parsed.steps] == ["user", "action", "system", "result"]
    assert parsed.steps[2].is_interruption
    assert parsed.models["glm-5"] == 1
    assert detect_format(PI) == "pi"


COPILOT = [
    {"type": "session.start", "data": {"sessionId": "s"}},
    {"type": "user.message", "data": {"content": "hi"}},
    {
        "type": "assistant.message",
        "data": {
            "content": "ok",
            "toolRequests": [{"toolCallId": "1", "name": "view", "arguments": '{"path": "a"}'}],
        },
    },
    {
        "type": "tool.execution_complete",
        "data": {"toolCallId": "1", "success": True, "result": {"content": "text"}},
    },
]


def test_copilot() -> None:
    parsed = parse_copilot(COPILOT)
    assert [s.kind for s in parsed.steps] == ["user", "agent_text", "action", "result"]
    assert parsed.steps[2].tool_input == {"path": "a"}
    assert detect_format(COPILOT) == "copilot"
