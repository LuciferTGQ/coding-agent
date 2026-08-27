# 架构与设计取舍

## 1. 目标和约束

本项目的核心目标不是复刻完整的 Claude Code 或 Codex，而是以较小代码量清楚展示一个
Coding Agent Harness 的必要闭环：模型选择动作，本地环境执行动作，结果回传模型，模型再
依据观察继续行动。考核禁止 Agent Framework、SDK 托管 Agent 逻辑、服务端代码执行和文件
工具，因此这里只使用普通模型厂商客户端，编排逻辑全部位于仓库内。

设计优先级为：可运行的反馈闭环、协议正确、边界集中、失败可恢复、易测试、易在面试中解释。

## 2. 主数据流

```text
CLI arguments
    ↓
Config ── API credential ──────────────┐
    ↓ workspace/runtime settings        │ (only credential edge)
Runtime composition                     ↓
    ├─ Workspace                  DeepSeekChatClient
    ├─ ToolRegistry                      ↑ ↓ Chat Completions
    ├─ ContextManager              normalized AssistantResponse
    └─ AgentRunner ── tool_calls ───────┘
           ↓
       ToolRegistry
           ├─ list/read/search/write/edit → Workspace
           └─ run_command → Workspace cwd + subprocess
           ↓
       ToolResult (stdout/stderr/exit code/diff/error)
           ↓
       complete interaction block → next model request
```

`runtime.py` 只是 composition root。Config 的 Key 只传入 `DeepSeekChatClient`；
AgentRunner、ContextManager、Workspace、Registry 和 Tools 都拿不到凭据。

## 3. 模块职责

### Config 与 CLI

`config.py` 从环境变量读取模型和 Harness 设置，并仅在 Key 缺失时回退到启动目录下未入库的
`api.txt`。dataclass 将 `api_key` 设为 `repr=False`。`cli.py` 只负责参数和退出码；
`runtime.py` 构造各层。这样配置、行为和环境操作没有混在一起。

### DeepSeekChatClient

`llm.py` 输入 messages 与 tool definitions，输出统一的 `AssistantResponse`。它不执行工具，
也不决定循环。Thinking 模式通过 `extra_body={"thinking":{"type":"enabled"}}` 打开，
`reasoning_effort` 可为 low/high/max。

DeepSeek 官方协议要求：携带 tools 的 thinking 多轮中，assistant 的 `reasoning_content` 必须在
后续请求完整回传，否则 API 会返回 400。因此客户端另外保存可重放的 `provider_message`，
其中保留 `content`、`reasoning_content` 与 `tool_calls`；私有推理不进入 CLI 输出。

暂时性连接错误、timeout、429 和 5xx 最多有限重试并指数退避；认证、权限、请求格式等明确
4xx 快速失败。异常中的 Key 会被替换为 `[REDACTED]`。

### Tool 与 ToolRegistry

每个 Tool 只有名称、面向模型的描述、有限 JSON Schema 和 Python handler。Registry 是模型
声明与本地函数之间唯一桥梁：注册、生成 Chat Completions tool definitions、解析 JSON、检查
必需字段/类型/enum/多余参数、捕获 handler 异常并返回统一 `ToolResult`。

没有实现完整 JSON Schema draft。六个工具只需要 object、string、integer、boolean、array、
enum 和简单范围；实现 `$ref`、`oneOf` 等只会增加与 Agent 核心无关的复杂度。

### Workspace 与 Tools

`Workspace.resolve_path()` 是 list、read、search、write、edit 和 command cwd 的共同边界。
路径必须相对 Workspace；解析后再次检查祖先，可拒绝 `..`、绝对路径和可解析的 symlink
escape。Secret 名称在这里统一判断，避免六个 handler 各写一套规则。

文件 ACI 采用 just-in-time context：

1. `list_files` 给有限结构视图；
2. `search_text` 只返回 `path:line:text`；
3. `read_file` 默认最多 200 行并带行号；
4. `edit_file` 要求 old text 恰好出现一次，返回 bounded unified diff；
5. `write_file` 默认不覆盖；
6. `run_command` 回传 exit code、stdout、stderr、duration、timeout 和 truncation。

命令使用 argv 与 `shell=False`，避免额外 shell 解析和注入面；限制 shell wrapper、明显危险
系统命令、Git 强制推送/清理/硬重置等。子进程环境过滤常见凭据名。文件写入有大小限制，
工具结果和 CLI 展示各自有预算。

### AgentRunner

核心循环刻意保持线性：

```text
for step in max_steps:
    messages = context.messages()
    response = model.complete(messages, tools)
    preserve response.provider_message
    if response has tool calls:
        execute each call sequentially
        append every matching role=tool result
        store one complete interaction block
        continue
    if verification guard allows finish:
        return final answer
max_steps → controlled stop
```

多个 tool call 顺序执行，因为文件操作有副作用；并行会让结果依赖调度顺序。畸形调用、路径
失败、命令非零和 timeout 都是 Agent 可观察的反馈，不直接结束整个进程。连续工具错误和最大
步数提供有限的循环保护。Agent 不靠 planner/reviewer 状态机，模型在同一反馈循环中自行调整。

### ContextManager

system prompt 与原始 user task 是 stable context。每个 assistant tool-call message 加其全部
tool results 组成一个 interaction block，裁剪时整体删除或保留。这条不变量同时保证：

- 不留下孤立 `role=tool`；
- 不留下缺少执行结果的 assistant tool call；
- 保留下来的 DeepSeek provider fields 结构完整。

第一版使用字符数 soft budget，而非精确 tokenizer；超预算从最旧完整块开始删除，至少保留最近
块。没有调用模型生成摘要：小型仓库、1M context 模型和窄工具下，确定性 block pruning 更容易
测试和解释，也不会增加一次昂贵且可能失真的模型调用。

### Verification Guard

每次成功 write/edit 都增加 workspace revision，并使最新 revision 处于未验证状态。只有识别为
test/build/lint/program 的成功 `run_command` 才把它标记为已验证。若模型此时直接 final，Harness
保存该 assistant 回答并追加一次反馈，要求执行合适验证。相同 revision 只提醒一次；如果没有
合理自动验证，下一次 final 可说明原因后结束。

它防止“写完即自信成功”，但不是正确性的形式化证明：命令可能覆盖不全，测试也可能有缺陷；
失败验证后模型仍可如实报告未解决问题。

## 4. 关键方案对比

### 为什么不用 Agent Framework

题目明确禁止；更重要的是，框架会隐藏 messages、tool-call parsing、local execution 与 termination
这些正需展示的 Harness 逻辑。当前模块均可用 FakeModel 和临时 Workspace 单独测试。

### 为什么当前用 Chat Completions

`assistant.tool_calls → local tool → role=tool → next messages` 与考核数据流一一对应，便于观察
provider message、Tool Result 和上下文。DeepSeek Responses API 也是有效替代方案，未来若需要
reasoning/function item 或 Responses 专属能力，可在模型适配层实现；当前切换不会提升小型单 Agent
Harness 的核心能力，反而增加协议对象种类。

### 为什么只实现 DeepSeek

当前验收模型明确，先把一个 provider 的真实 thinking/tool 多轮协议做对，比建立未被实际验证的
provider factory 更可靠。`ModelClient` Protocol 和 normalized response 已给测试及未来 provider
留出边界。

### 为什么只有约六个工具

工具描述本身占 Context。list/search/read/write/edit/run 覆盖小型真实编程任务的观察、修改和验证，
且职责边界自然。增加几十个重叠工具会提高模型选错接口的概率。

### 为什么 exact replacement

它迫使模型先读文件并提供唯一上下文，0 次与多次匹配都有明确反馈，成功 diff 易审计。
替代方案包括完整文件覆盖、apply_patch 和 AST rewrite：前者误覆盖风险高，后两者更强但实现和协议
复杂度也更高。大型重构时应升级到 patch 或语言感知编辑。

### 为什么不做 Repo Map

Aider 的 Repo Map 用语法树和依赖图在大仓库中选择重要符号，很有价值。但本项目 Demo 小，
list/search/read 已足够；构建多语言 symbol graph 会分散 Agent Loop 与 ACI 的实现重点。未来面向
大仓库时可加入 token-bounded repo map。

## 5. 安全模型与诚实边界

直接文件工具不能访问 Workspace 外或配置的 Secret；命令 cwd 不能越界；Harness 的 API Key
不进入子进程环境。这里仍不是 Sandbox：目标项目程序在当前 OS 用户下运行，可以访问该用户有权
访问的其他文件与网络，轻量 denylist 也不可能覆盖所有等价命令。不要对不可信代码声称安全隔离。

生产化需要容器/namespace/VM、独立低权限用户、只挂载 Workspace、网络默认拒绝、CPU/内存/进程
配额、系统调用策略、可审计 permission policy，并把 Harness 凭据保存在隔离边界之外。

## 6. 测试策略

- Unit：Config、DeepSeek normalization/retry、Workspace、Registry、每个 Tool、Context；
- P0：`../`/absolute/symlink escape、Secret 拒绝、invalid JSON/参数、exact edit 三种结果、命令
  success/non-zero/timeout、完整块裁剪、Verification Guard、max steps；
- Integration：FakeModel + real AgentRunner + real Registry/Workspace 完成 read/edit/execute/final；
- Demo integration：复制真实多文件小项目，证明初始失败，再通过 Harness 修复并验证；
- Live protocol：显式脚本验证 thinking/tool/result/next request；
- Live E2E：自然语言任务驱动真实 DeepSeek 完成探索、失败、修改和再次测试。

普通 `pytest` 永不发起真实 API 请求。

## 7. 未来升级方向

- 容器或 VM Sandbox、网络隔离、资源 quota 与细粒度权限；
- token-aware context、model-assisted compaction、语义摘要和 retrieval cache；
- Aider 风格 Repo Map、LSP、AST/patch 编辑；
- session persistence、observability、trajectory replay 与系统化 evaluation suite；
- Responses API 和更多 provider adapter；
- 在不破坏单循环可读性的前提下增加用户审批与可恢复 checkpoint。
