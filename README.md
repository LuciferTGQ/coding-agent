# NJU Coding Agent

这是一个为南京大学软件工程专业推免考核实现的 CLI 编程智能体。项目不依赖
LangChain、OpenAI Agents SDK 等 Agent 框架，也不使用服务端代码执行或文件工具；
对话历史、上下文裁剪、工具声明与校验、本地执行、循环终止、错误反馈和验证门禁均在仓库中自行实现。

默认模型为 `deepseek-v4-flash`，通过 DeepSeek 官方 OpenAI-compatible Chat
Completions 接口使用原生 tool calling。

## 核心数据流

```text
CLI / Config
    ↓
AgentRunner ←→ ContextManager
    ↓                ↑
DeepSeek Chat Client
    ↓ tool_calls     ↑ assistant + tool results
ToolRegistry
    ↓
Workspace / local subprocess
```

Agent 会自主执行“浏览 → 搜索/读取 → 运行 → 根据失败定位 → 精确修改 → 再次运行 →
总结”的多步循环，而不是按固定脚本处理 Demo。

## 安装

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

在 macOS/Linux 上将激活命令换为 `source .venv/bin/activate`。

## 配置 DeepSeek

推荐通过环境变量提供凭据：

```powershell
$env:DEEPSEEK_API_KEY = "your_api_key_here"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "deepseek-v4-flash"
$env:DEEPSEEK_REASONING_EFFORT = "high"
```

本地开发时，Config 也会回退读取启动目录下未入库的 `api.txt`。该文件已被
`.gitignore` 排除；其他使用者无需创建它。配置优先级为环境变量高于本地文件。

可选设置：

- `CODING_AGENT_MAX_STEPS`：最大模型轮次，默认 24；
- `CODING_AGENT_COMMAND_TIMEOUT`：单次命令最大秒数，默认 60；
- `CODING_AGENT_TOOL_OUTPUT_LIMIT`：单次工具结果字符预算，默认 12000；
- `CODING_AGENT_CONTEXT_BUDGET`：上下文近似软预算，默认 120000 字符。

## 运行

```powershell
coding-agent --workspace .\path\to\project "Fix the failing tests"
```

也可以使用模块入口：

```powershell
python -m coding_agent --workspace .\path\to\project --max-steps 20 `
  "Inspect the project, fix the bug without weakening tests, and verify it."
```

常用参数包括 `--model`、`--reasoning-effort {low,high,max}`、`--no-thinking` 和
`--verbose`。CLI 会展示模型公开消息、工具名、参数摘要、受限工具结果及最终回答；
不会展示 `reasoning_content` 或 API Key。

## 六个本地工具

| 工具 | 作用与主要约束 |
| --- | --- |
| `list_files` | 有限深度和数量地浏览，隐藏噪声目录与 Secret |
| `read_file` | 按行读取 UTF-8 文本，默认最多 200 行并显示行号 |
| `search_text` | 文字面量搜索，返回精简的 `path:line:text` |
| `write_file` | 创建文本；覆盖已有文件必须显式 `overwrite=true` |
| `edit_file` | `old_text` 必须恰好出现一次，成功返回 unified diff |
| `run_command` | argv 列表、`shell=False`、Workspace cwd、超时和输出截断 |

ToolRegistry 统一完成未知工具、无效 JSON、缺参、类型、枚举和多余参数检查。
文件不存在、编辑歧义、命令非零退出、超时等错误会作为 Tool Result 回传模型，
使 Agent 可以根据真实环境反馈继续调整。

## Context 与 Verification Guard

ContextManager 始终保留 system prompt 和原始任务，并把 assistant tool call 与其全部
tool results 视为一个不可拆分的 interaction block。超过软预算时只删除最旧完整块，
避免孤立 tool message；DeepSeek 的 `content`、`reasoning_content` 和 `tool_calls`
会作为完整 provider message 保留。

成功写入或编辑后，Harness 会记录新的未验证修订。如果模型未执行合适的 test、build、
lint 或程序命令就直接结束，Verification Guard 会提醒一次继续验证；若确实没有可行的
自动验证，模型说明原因后可以结束，因而不会死锁。

## 安全边界

- 所有文件路径和命令 cwd 统一通过 `Workspace.resolve_path()`，拒绝绝对路径、`..`
  越界和可解析的 symlink 越界；
- 文件浏览、读取和搜索隐藏或拒绝 `api.txt`、`.env`、`.env.*` 等本地凭据；
- 子进程环境过滤 `*_API_KEY`、`*_TOKEN`、`*_SECRET`、`*_PASSWORD` 等变量；
- 命令不用 shell，并拦截 shell wrapper、关机/磁盘命令及破坏 Git 历史的常见命令；
- API 调用对连接失败、超时、429 和 5xx 做有限指数退避，不可恢复 4xx 快速失败。

这些措施是基础防护，不是真正的安全 Sandbox。任意本地程序仍继承当前操作系统用户的
文件和网络权限；处理不可信仓库时应使用容器/VM、独立用户、网络隔离和资源配额。

## 测试

普通测试完全离线，不会消耗 API：

```powershell
python -m pytest
python -m compileall -q src scripts
```

真实协议 smoke 必须显式开启：

```powershell
$env:RUN_LIVE_TESTS = "1"
python scripts/live_smoke.py
```

它只验证 thinking + tool call + 本地固定结果 + 下一轮 final，不打印推理内容或凭据。

## 可重复 Demo

```powershell
python scripts/prepare_demo.py
python -m coding_agent --workspace .demo-workspace --max-steps 20 `
  "Inspect this project, identify why its tests fail, fix the bug without weakening the tests, and verify the result."
```

每次运行 `prepare_demo.py` 都会从 `examples/buggy_project` 重建被忽略的
`.demo-workspace`，所以 Demo 可以重复录制。完整流程与两分钟视频建议见
[docs/demo.md](docs/demo.md)。

## 文档与设计依据

- [架构与设计取舍](docs/architecture.md)
- [Demo 与视频流程](docs/demo.md)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)
- [SWE-agent ACI paper](https://arxiv.org/abs/2405.15793)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [OpenAI: Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
- [Aider repository map](https://aider.chat/docs/repomap.html)

## 已知限制

- 目前只实现 DeepSeek Chat Completions provider；
- 字符数只是 token 预算的近似，裁剪不做模型摘要；
- 没有 Repo Map、RAG、长期会话、LSP、多 Agent 或权限确认 UI；
- 精确替换不适合大规模结构化重构；
- 本地 command filtering 无法替代操作系统级隔离。
