# NJU Coding Agent

NJU Coding Agent 是一个轻量、自实现 Harness 的本地 Coding Agent。它提供持久化桌面界面和 CLI，使用 DeepSeek V4 的原生 Tool Calling，在选定项目中浏览代码、修改文件并运行测试。

项目的 Harness 从基础模型 API 搭建：对话历史、工具注册、参数校验、本地执行、上下文裁剪、错误反馈和循环终止都在仓库内显式实现。没有引入 Agent 框架，也不依赖服务端文件工具或远程代码执行。

## 核心能力

- 在单 Agent 循环中完成“观察、行动、获取反馈、继续调整”的多步编程任务。
- 以多轮会话持续处理同一 Workspace，关闭应用后可恢复完整对话和模型上下文。
- 桌面端可让多个独立 Session 同时运行；事件、Stop、上下文和状态按 Session 隔离。
- 每个 GUI Session 可选择开启只读 Sub-Agent；Main 可并行委派最多 4 个独立调查并只接收 condensed findings。
- 流式展示回答、可折叠 reasoning 和带状态的工具卡片；模型与工具运行在后台线程。
- 通过六个受限工具浏览、搜索、读取、写入、精确编辑文件并执行命令。
- 将 stdout、stderr、退出码和超时信息回传给模型，让失败成为下一步决策的输入。
- 长会话使用旧只读 Tool Result 压缩、Rolling Structured Summary 和 Recent Raw Context，同时保持 Tool Calling 协议完整。
- Verification Guard 在文件变更后要求一次有意义的执行验证，减少“修改完就结束”的情况。
- Workspace 边界、凭据隔离、命令超时和输出限额为本地执行提供基础防护。

## 数据流

```text
Desktop GUI ── AgentTaskManager ── session A/B/... workers
                              ↓
CLI ───────────────────→ SessionRuntime ── SessionStore (~/.nju-coding-agent)
                              ↓                 ↑ full transcript + model context
                         AgentRunner ←→ ContextManager
       ↓                ↑
DeepSeek Chat Client (streaming or non-streaming)
       ↓ tool_calls     ↑ assistant + tool results
ToolRegistry → Workspace / local subprocess
       └ optional delegate_task → bounded pool → isolated read-only Child Agent(s)
```

`AgentRunner` 仍只负责编排。桌面层通过 `SessionRuntime` 复用同一 Harness，并将完整 UI transcript 与有预算的 provider context 分开持久化。模型适配、上下文、工具解析和环境操作分属不同模块，因此可以用 FakeModel 和临时 Workspace 分别测试。更完整的数据流和取舍见 [架构文档](docs/architecture.md)。

## Windows 打包版

不需要配置 Python 的 Windows 用户可以从 [GitHub Releases](https://github.com/LuciferTGQ/coding-agent/releases/latest) 下载 `CodingAgent-windows-x64.zip`：

1. 完整解压 ZIP；
2. 运行解压目录中的 `CodingAgent.exe`；
3. 在首次调用模型前按下文配置 `DEEPSEEK_API_KEY`。

打包版与 `python start_gui.py` 是同一份 Coding Agent 实现的两种分发形式，共用 Agent Loop、Tools、Context、Session 和 Sub-Agent 代码。默认配置下，同一个 Windows 用户的两种启动方式都会读取 `~/.nju-coding-agent`，因此可以在源码版创建 Session，关闭后用 EXE 继续，也可以反向切换。不要同时用多个进程修改同一个 Session；Session Store 不提供跨进程事务一致性。

`CodingAgent.exe` 所在目录不是 Agent Workspace。应用可以放在任意普通目录，实际项目目录在 GUI 新建对话时单独选择和授权；Release 不包含示例 Workspace、用户 Session 或本地凭据。

## 源码安装

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

macOS/Linux 上使用 `source .venv/bin/activate` 激活虚拟环境。

## API 配置

推荐通过环境变量设置 DeepSeek API Key。以下命令中的值都是占位符。

### PowerShell

仅对当前 PowerShell 窗口生效：

```powershell
$env:DEEPSEEK_API_KEY="your_api_key_here"
```

写入当前 Windows 用户环境变量：

```powershell
[Environment]::SetEnvironmentVariable(
    "DEEPSEEK_API_KEY",
    "your_api_key_here",
    "User"
)
```

持久设置后请重新启动应用或终端。

### CMD

仅对当前 CMD 窗口生效：

```cmd
set DEEPSEEK_API_KEY=your_api_key_here
```

写入当前 Windows 用户环境变量：

```cmd
setx DEEPSEEK_API_KEY "your_api_key_here"
```

`setx` 不会更新已经打开的进程；请重新启动应用或终端。

### Bash / Git Bash

```bash
export DEEPSEEK_API_KEY="your_api_key_here"
```

需要长期使用时，可将同一 `export` 命令加入对应 shell profile。

### 本地 fallback

如果环境变量未设置，Config 仍会尝试读取**当前工作目录**下未跟踪的 `api.txt`。这是源码开发 fallback：环境变量优先，`api.txt` 已由 `.gitignore` 保护且不应提交或放入 Release。直接双击打包版时工作目录可能随启动方式变化，因此 EXE 用户应使用环境变量。

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

应用默认使用中文。点击 **新建对话** 选择项目目录，确认文件与命令执行授权后输入任务。左侧可按标题或 Workspace 搜索历史会话；每条会话的 `...` 菜单和右键菜单提供重命名、置顶、未读标记、打开工作区和删除操作。每个会话固定绑定一个 Workspace；已有对话切换 Workspace 时，应用会提示并创建新会话，防止不同项目的上下文混合。

模型输出会逐步显示。思考过程默认折叠，read/search/edit/run 等调用显示为工具卡片，参数、diff 和命令输出可按需展开。点击 **停止** 后，Agent 会在下一步骤边界或完整 tool-call 组执行完毕后停止，不会强制终止正在写文件的 handler。

不同 Session 可以独立并行运行。切换会话不会把后台事件写入当前对话；后台完成显示未读圆点，后台失败显示 `!`，菜单可单独停止后台任务。同一 Workspace 的多个 Session 也可并发，但启动第二个任务前会明确警告：当前版本不提供修改隔离、文件锁、自动合并或一致性保证。

输入区提供每个 Session 独立保存的 **子代理** Toggle，默认关闭。开启后 Main 可以按需使用 `delegate_task`，并在一次纯 delegation batch 中并行启动最多 4 个临时只读 Child；简单任务不会被强制委派。Child 只获得 `list_files`、`read_file`、`search_text`，使用独立 Context 和 ModelClient，其内部探索不会进入 Main Context，只有最终 findings 作为原 call id 的 Tool Result 返回。修改 Workspace 和最终验证仍由 Main 完成。运行中的 Turn 使用启动时的开关快照。

轻量设置弹窗提供界面语言、默认模型、默认思考强度和默认最大步骤。语言可选择中文或 English；保存语言变更后可以稍后应用，也可以安全启动新进程并立即重启。中文界面会要求模型除代码、命令、路径和必要技术标识外，默认使用中文交流。Tool 名称、JSON Schema 和 DeepSeek 协议字段不会被翻译。

快捷键：`Ctrl+N` 新建对话，`Ctrl+,` 打开设置，`Ctrl+Return` 发送消息。

附件仅支持 UTF-8 文本。Workspace 内文件只把相对路径加入任务，不把全文直接塞进提示；Workspace 外文件必须经用户确认后复制到 `.agent-attachments/`，仍由原有路径边界和工具读取规则管理。

桌面会话以 JSON 保存在用户目录 `~/.nju-coding-agent/sessions/`，应用默认设置保存在同级 `settings.json`；它们都不写入仓库或目标 Workspace，也不包含 API Key。

Session 写入使用进程内 per-session lock、唯一临时文件和有限的 Windows `PermissionError` 重试。短暂持久化失败会作为独立状态显示，Agent 会继续运行并在后续事件中重试；它不会把已经执行成功的 Workspace 操作误报为 Agent 核心失败。多个 GUI 进程同时写同一 Session 仍不提供跨进程一致性保证。

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
| `delegate_task` | GUI 按 Session 可选；委派临时只读调查，纯 delegation batch 最多 4 个并行 Child |

`ToolRegistry` 统一处理未知工具、无效 JSON、缺少参数、类型错误、枚举错误和多余参数。文件不存在、编辑歧义、命令非零退出和超时都会作为 Tool Result 回传模型，而不是直接终止 Agent。

## Context 与 Verification Guard

`ContextManager` 始终保留 system prompt。每个用户请求、期间的 assistant/tool 交互及最终回答形成一轮；assistant tool call 与对应的全部 tool results 是不可拆分的 interaction block。近似字符大小超过软预算后，系统先把较早且较大的 `list_files`、`read_file`、`search_text` 结果替换为带 tool name、call id、状态和原始长度的短占位，但保留 assistant call、全部 tool result 和 DeepSeek provider fields。

如果免费压缩后仍超出预算，Main 使用当前 Session 的同一个模型发起一次不带 Tools 的结构化摘要请求，将已有 working memory 与新近变老的完整 turns 滚动合并。摘要成功后才原子移除这些 turns；失败时保留原 Context 并继续正常任务。最近两个 completed turns 与 current active turn 始终保留原文，极端情况下宁可暂时超过 soft budget，也不拆坏协议块。Summary 随 `model_context` 保存和恢复，不影响完整 UI transcript。

Working memory 是有损历史，不是代码事实源。模型必须以当前 Workspace 和新 Tool Result 为准；实现细节可能变化时应重新 read、search 或 verify。

桌面 transcript 不受模型上下文预算影响，因此 UI 可以保留完整历史，而传给模型的上下文仍保持有界。DeepSeek 的 streaming chunks 会规范化为 `reasoning_delta` 与 `content_delta`，再聚合成与非流式路径相同的 `AssistantResponse`；thinking + tools 所需的 `reasoning_content` 会在后续请求中原样重放。

成功写入或编辑后，Harness 会记录一个未验证的 Workspace revision。如果模型未执行合适的 test、build、lint 或程序就直接结束，Verification Guard 会提醒它继续验证。同一 revision 只提醒一次；确实没有可用的自动验证时，模型可以说明原因后结束。

## 安全边界

- 文件路径和命令 cwd 统一经过 `Workspace.resolve_path()`，拒绝绝对路径、`..` 越界和可解析的 symlink escape。
- 文件浏览、读取和搜索会隐藏或拒绝已配置的凭据文件。
- 子进程环境过滤 `*_API_KEY`、`*_TOKEN`、`*_SECRET` 和 `*_PASSWORD` 等变量。
- Harness 生成的 System Prompt 不包含本机 Workspace 绝对路径；Tool 路径保持为 Workspace 相对路径。
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

## 已知限制

- 目前只实现 DeepSeek Chat Completions provider，桌面模型列表只提供已按该协议验证的 `deepseek-v4-flash`。
- Context soft budget 是序列化消息的近似字符预算，并非精确 token 计数；Rolling Summary 仍可能遗漏次要历史细节。
- 当前没有 Repo Map、RAG、LSP 或长期协作式 Agent 拓扑；Sub-Agent 是无持久历史的临时只读调查对象。
- Stop 是协作式边界停止，不会中断正在进行的网络请求或单个工具 handler。
- 每个 Parent turn 最多委派 8 次、同时最多运行 4 个 Child；已进入模型网络调用的 Child 只能在调用返回后的安全边界响应 Stop。
- 同一 Workspace 并发不保证修改隔离和一致性。Git Worktree 可作为未来的每任务独立副本方案，但当前版本不会自动创建。
- Exact replacement 适合小而精确的变更，不适合大规模结构化重构。
- 本地 command filtering 不能替代容器、VM 或其他 OS 级隔离。
