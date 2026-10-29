from steward.sources.chat_messages import parse_chatml_tool_markup, parse_openai_messages


def test_openai_messages() -> None:
    steps = parse_openai_messages(
        [
            {"role": "system", "content": "You are an agent"},
            {"role": "user", "content": "fix it"},
            {
                "role": "assistant",
                "content": "<think>plan</think>On it",
                "tool_calls": [
                    {
                        "function": {"name": "execute_bash", "arguments": '{"command": "pytest"}'},
                        "id": "1",
                        "type": "function",
                    }
                ],
            },
            {"role": "tool", "content": "1 passed", "tool_call_id": "1"},
        ]
    )
    assert [s.kind for s in steps] == ["system", "user", "agent_text", "action", "result"]
    assert steps[2].content == "On it"
    assert steps[3].content == "pytest"


def test_chatml_markup() -> None:
    chatml = (
        "<|im_start|>user\nDo the task<|im_end|>"
        "<|im_start|>assistant\n<think>hmm</think>Running"
        '<tool_use id="a">{"name": "Bash", "arguments": {"command": "cargo test"}}</tool_use><|im_end|>'
        '<|im_start|>user\n<tool_result id="a">ok</tool_result><|im_end|>'
    )
    steps = parse_chatml_tool_markup(chatml)
    assert [s.kind for s in steps] == ["user", "agent_text", "action", "result"]
    assert steps[2].tool == "Bash" and steps[2].content == "cargo test"
    assert "hmm" not in steps[1].content
