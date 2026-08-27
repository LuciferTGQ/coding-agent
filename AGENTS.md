# Project map

This repository is an NJU recommendation-assessment coding agent. Its agent harness must
remain self-implemented: do not add Agent frameworks/SDKs or server-hosted file/command tools.

Core flow: `cli/config → runtime → AgentRunner ↔ ContextManager → DeepSeekChatClient →
ToolRegistry → Workspace/file tools/run_command → ToolResult`.

Design invariants:

- `Config` is the only `api.txt` reader; the API key only crosses into the model client.
- Every agent-visible path and command cwd goes through `Workspace`.
- Preserve assistant tool calls and all matching tool results as one context block.
- Preserve DeepSeek `reasoning_content` for provider replay, never print it.
- Execute multiple tool calls sequentially and return tool failures to the model.
- A successful write/edit requires later verification or one explicit unavailable explanation.
- `shell=False`, bounded output/time, and secret filtering are safeguards, not a real sandbox.

Common checks: `python -m pytest`, `python -m compileall -q src scripts`, `python -m coding_agent
--help`. Live API checks must require `RUN_LIVE_TESTS=1`. Before every commit, confirm `api.txt`
is ignored and untracked. Do not rewrite pushed history, force-push, or push after the assessment
deadline (2026-09-02 24:00 Beijing time).
