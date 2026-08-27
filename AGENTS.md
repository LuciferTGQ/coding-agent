# Contributor guide

NJU Coding Agent uses a small, explicit harness. Keep the orchestration local and inspectable;
do not replace the core loop with an Agent framework or server-hosted file/command tools.

Architecture map:

```text
GUI → SessionRuntime → SessionStore
             ↓
CLI/config → runtime → AgentRunner ↔ ContextManager → DeepSeekChatClient
                                      ↓
                              ToolRegistry → Workspace/tools → ToolResult
```

Maintain these invariants:

- Credentials only cross from `Config` into the model client. Never place them in prompts,
  tool results, logs, fixtures, or subprocess environments.
- Every agent-visible file path and command cwd goes through `Workspace`.
- An assistant tool-call message and all matching tool results form one indivisible context block;
  multi-turn pruning should remove complete old user turns first.
- Preserve DeepSeek `reasoning_content` when replaying provider messages. The CLI excludes it;
  the local GUI may show it only in an explicitly labeled, collapsed reasoning card.
- Keep the complete GUI transcript separate from the budgeted model context. Session JSON lives
  under `~/.nju-coding-agent`, never in the repository or selected workspace.
- Keep model and tool work off the Qt main thread. Cancellation is cooperative at safe agent/tool
  boundaries and must not create an assistant tool call without all matching tool results.
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
python -m coding_agent.gui
```

Live API checks must remain opt-in through `RUN_LIVE_TESTS=1`.
