# Contributor guide

NJU Coding Agent uses a small, explicit harness. Keep the orchestration local and inspectable;
do not replace the core loop with an Agent framework or server-hosted file/command tools.

Architecture map:

```text
cli/config → runtime → AgentRunner ↔ ContextManager → DeepSeekChatClient
                                      ↓
                              ToolRegistry → Workspace/tools → ToolResult
```

Maintain these invariants:

- Credentials only cross from `Config` into the model client. Never place them in prompts,
  tool results, logs, fixtures, or subprocess environments.
- Every agent-visible file path and command cwd goes through `Workspace`.
- An assistant tool-call message and all matching tool results form one context block.
- Preserve DeepSeek `reasoning_content` when replaying provider messages, but never display it.
- Execute multiple tool calls sequentially and return malformed calls or tool failures to the model.
- A successful write or edit requires execution-based verification, or one explicit explanation that
  no meaningful automated verification is available.
- `shell=False`, time/output bounds, command filtering, and secret filtering are safeguards, not a
  substitute for an OS-level sandbox.

Common checks:

```powershell
python -m pytest
python -m compileall -q src scripts
python -m coding_agent --help
```

Live API checks must remain opt-in through `RUN_LIVE_TESTS=1`.
