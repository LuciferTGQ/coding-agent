# NJU Coding Agent

NJU Coding Agent 是一个轻量、自实现 Harness 的本地 Coding Agent。它提供持久化桌面界面和 CLI，使用 DeepSeek V4 的原生 Tool Calling，在选定项目中浏览代码、修改文件并运行测试。

项目的 Harness 从基础模型 API 搭建：对话历史、工具注册、参数校验、本地执行、上下文裁剪、错误反馈和循环终止都在仓库内显式实现。没有引入 Agent 框架，也不依赖服务端文件工具或远程代码执行。

## 核心能力

- 在单 Agent 循环中完成“观察、行动、获取反馈、继续调整”的多步编程任务。
- 以多轮会话持续处理同一 Workspace，关闭应用后可恢复完整对话和模型上下文。
- 流式展示回答、可折叠 reasoning 和带状态的工具卡片；模型与工具运行在后台线程。
- 通过六个受限工具浏览、搜索、读取、写入、精确编辑文件并执行命令。
- 将 stdout、stderr、退出码和超时信息回传给模型，让失败成为下一步决策的输入。
- 按完整的 assistant-tool 交互块管理上下文，避免裁剪后出现残缺的 Tool Calling 协议。
- Verification Guard 在文件变更后要求一次有意义的执行验证，减少“修改完就结束”的情况。
- Workspace 边界、凭据隔离、命令超时和输出限额为本地执行提供基础防护。

## 数据流

```text
Desktop GUI / CLI
       ↓
SessionRuntime ── SessionStore (~/.nju-coding-agent)
       ↓                 ↑ full transcript + model context
AgentRunner ←→ ContextManager
       ↓                ↑
DeepSeek Chat Client (streaming or non-streaming)
       ↓ tool_calls     ↑ assistant + tool results
ToolRegistry → Workspace / local subprocess
```

`AgentRunner` 仍只负责编排。桌面层通过 `SessionRuntime` 复用同一 Harness，并将完整 UI transcript 与有预算的 provider context 分开持久化。模型适配、上下文、工具解析和环境操作分属不同模块，因此可以用 FakeModel 和临时 Workspace 分别测试。更完整的数据流和取舍见 [架构文档](docs/architecture.md)。

## 安装

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

macOS/Linux 上使用 `source .venv/bin/activate` 激活虚拟环境。

## API 配置

设置 DeepSeek API Key：

```powershell
$env:DEEPSEEK_API_KEY = "your_api_key_here"
```

可选设置：

```powershell
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "deepseek-v4-flash"
$env:DEEPSEEK_REASONING_EFFORT = "high"
```

Harness 还支持以下运行参数：

- `CODING_AGENT_MAX_STEPS`：最大模型轮次，默认 24。
- `CODING_AGENT_COMMAND_TIMEOUT`：单次命令超时，默认 60 秒。
- `CODING_AGENT_TOOL_OUTPUT_LIMIT`：单次工具结果字符限额，默认 12000。
- `CODING_AGENT_CONTEXT_BUDGET`：上下文近似软预算，默认 120000 字符。

## 桌面端使用

启动持久化桌面应用：

```powershell
python -m coding_agent.gui
# 或
coding-agent-gui
```

也可以直接运行仓库根目录的 `start_gui.py` 启动桌面界面，例如 `python start_gui.py`。

点击 **New conversation** 选择项目目录，然后输入任务。左侧会话列表可恢复历史；每个会话固定绑定一个 Workspace。已有对话切换 Workspace 时，应用会提示并创建新会话，防止不同项目的上下文混合。

模型输出会逐步显示。Reasoning 默认折叠，read/search/edit/run 等调用显示为工具卡片，参数、diff 和命令输出可按需展开。点击 **Stop** 后，Agent 会在下一步骤边界或完整 tool-call 组执行完毕后停止，不会强制终止正在写文件的 handler。

附件仅支持 UTF-8 文本。Workspace 内文件只把相对路径加入任务，不把全文直接塞进提示；Workspace 外文件必须经用户确认后复制到 `.agent-attachments/`，仍由原有路径边界和工具读取规则管理。

桌面会话以 JSON 保存在用户目录 `~/.nju-coding-agent/sessions/`，不写入仓库或目标 Workspace。存储内容包括会话设置、完整 transcript 和恢复所需的 provider context，不包括 API Key。

## CLI 使用

```powershell
coding-agent --workspace .\path\to\project "Fix the failing tests"
```

也可以通过 Python 模块入口运行：

```powershell
python -m coding_agent --workspace .\path\to\project --max-steps 20 `
  "Inspect the project, fix the bug without weakening tests, and verify it."
```

常用选项有 `--model`、`--reasoning-effort {low,high,max}`、`--no-thinking` 和 `--verbose`。CLI 显示模型的公开消息、工具调用摘要、受限的执行结果和最终回答；模型的私有推理内容和 API 凭据不会输出。

## 工具

| 工具 | 作用与约束 |
| --- | --- |
| `list_files` | 按受限深度和数量列出 Workspace 内文件，跳过噪声目录和凭据文件 |
| `read_file` | 按行读取 UTF-8 文本，默认最多 200 行并显示行号 |
| `search_text` | 文字面量搜索，返回精简的 `path:line:text` |
| `write_file` | 创建文本；覆盖已有文件必须显式设置 `overwrite=true` |
| `edit_file` | `old_text` 必须恰好出现一次，成功后返回 unified diff |
| `run_command` | 使用 argv 列表和 `shell=False` 运行测试、构建、检查或程序 |

`ToolRegistry` 统一处理未知工具、无效 JSON、缺少参数、类型错误、枚举错误和多余参数。文件不存在、编辑歧义、命令非零退出和超时都会作为 Tool Result 回传模型，而不是直接终止 Agent。

## Context 与 Verification Guard

`ContextManager` 始终保留 system prompt。每个用户请求、期间的 assistant/tool 交互及最终回答形成一轮；assistant tool call 与对应的全部 tool results 仍是不可拆分的 interaction block。超过软预算时，系统优先删除最旧的完整用户轮次；单轮过长时才删除其中最旧的完整工具块。这样不会留下孤立的 tool message，DeepSeek thinking 模式所需的 provider fields 也会随块完整保留。

桌面 transcript 不受模型上下文预算影响，因此 UI 可以保留完整历史，而传给模型的上下文仍保持有界。DeepSeek 的 streaming chunks 会规范化为 `reasoning_delta` 与 `content_delta`，再聚合成与非流式路径相同的 `AssistantResponse`；thinking + tools 所需的 `reasoning_content` 会在后续请求中原样重放。

成功写入或编辑后，Harness 会记录一个未验证的 Workspace revision。如果模型未执行合适的 test、build、lint 或程序就直接结束，Verification Guard 会提醒它继续验证。同一 revision 只提醒一次；确实没有可用的自动验证时，模型可以说明原因后结束。

## 安全边界

- 文件路径和命令 cwd 统一经过 `Workspace.resolve_path()`，拒绝绝对路径、`..` 越界和可解析的 symlink escape。
- 文件浏览、读取和搜索会隐藏或拒绝已配置的凭据文件。
- 子进程环境过滤 `*_API_KEY`、`*_TOKEN`、`*_SECRET` 和 `*_PASSWORD` 等变量。
- 命令不经过 shell，并拦截 shell wrapper、危险系统命令和常见的破坏性 Git 操作。
- API 调用对连接失败、超时、429 和 5xx 进行有限重试，明确的不可恢复 4xx 会快速失败。

这些措施只是基础执行边界，不是操作系统级 Sandbox。目标项目的程序仍拥有当前用户的文件和网络权限。处理不可信代码时，应在容器或 VM 中运行，并配置独立用户、网络隔离和资源配额。

## 测试

普通测试完全离线，不会发起 API 请求：

```powershell
python -m pytest
python -m compileall -q src scripts
```

真实 streaming 协议 smoke 需要显式开启：

```powershell
$env:RUN_LIVE_TESTS = "1"
python scripts/live_smoke.py
```

该脚本验证 streaming thinking、tool call、本地 tool result 与下一轮 final response，不输出 reasoning 原文或 API 凭据。

## 可重复 Demo

```powershell
python scripts/prepare_demo.py
python -m coding_agent --workspace .demo-workspace --max-steps 20 `
  "Inspect this project, identify why its tests fail, fix the bug without weakening the tests, and verify the result."
```

`prepare_demo.py` 会从 `examples/buggy_project` 重建一份独立的 `.demo-workspace`。演示项目初始有 3 个失败测试；Agent 需要自行浏览源码、复现失败、定位缺陷、编辑并重新运行测试。再次执行准备脚本即可恢复初始状态。详细步骤见 [Demo 文档](docs/demo.md)。

## 设计参考

- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)
- [SWE-agent: Agent-Computer Interfaces](https://arxiv.org/abs/2405.15793)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [OpenAI: Unrolling the agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
- [Aider repository map](https://aider.chat/docs/repomap.html)

## 已知限制

- 目前只实现 DeepSeek Chat Completions provider，桌面模型列表只提供已按该协议验证的 `deepseek-v4-flash`。
- Context soft budget 使用字符数近似 token 数，不做模型摘要。
- 当前没有 Repo Map、RAG、LSP 或多 Agent 编排。
- Stop 是协作式边界停止，不会中断正在进行的网络请求或单个工具 handler。
- Exact replacement 适合小而精确的变更，不适合大规模结构化重构。
- 本地 command filtering 不能替代容器、VM 或其他 OS 级隔离。
