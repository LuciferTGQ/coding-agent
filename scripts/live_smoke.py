"""Explicit, low-cost DeepSeek thinking + tool-calling protocol smoke test."""

from __future__ import annotations

import json
import os
from pathlib import Path

from coding_agent.config import Config
from coding_agent.llm import DeepSeekChatClient


TOOL = {
    "type": "function",
    "function": {
        "name": "get_protocol_marker",
        "description": "Return a fixed marker used only to verify local tool-result round trips.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def main() -> int:
    if os.environ.get("RUN_LIVE_TESTS") != "1":
        print("Skipped: set RUN_LIVE_TESTS=1 to make a real DeepSeek request.")
        return 2

    config = Config.from_sources(workspace=Path.cwd())
    model = DeepSeekChatClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        reasoning_effort=config.reasoning_effort,
        thinking_enabled=True,
    )
    messages = [
        {
            "role": "system",
            "content": "Call get_protocol_marker once, then report the returned marker.",
        },
        {"role": "user", "content": "Verify the local tool-calling round trip."},
    ]

    saw_tool_call = False
    saw_stream_delta = False
    for _ in range(4):
        events = []
        response = model.complete_stream(
            messages=messages,
            tools=[TOOL],
            on_event=events.append,
        )
        saw_stream_delta = saw_stream_delta or bool(events)
        messages.append(response.provider_message)
        if not response.tool_calls:
            if not saw_tool_call:
                raise RuntimeError("Protocol smoke failed: the model did not call the tool")
            if "PROTOCOL_OK" not in response.content:
                raise RuntimeError("Protocol smoke failed: final answer omitted the tool result")
            if not saw_stream_delta:
                raise RuntimeError("Protocol smoke failed: no streaming delta was observed")
            print(
                "PASS: streaming thinking + tool call + tool result + next request + final response"
            )
            return 0
        for call in response.tool_calls:
            if call.name != "get_protocol_marker" or json.loads(call.arguments) != {}:
                tool_result = "Tool error: unexpected smoke-test call"
            else:
                saw_tool_call = True
                tool_result = "PROTOCOL_OK"
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": tool_result}
            )

    raise RuntimeError("Protocol smoke failed: model did not finish within four turns")


if __name__ == "__main__":
    raise SystemExit(main())
