# 架构与设计取舍

## 1. 目标和约束

这个 Harness 围绕一个直接的反馈循环设计：模型选择动作，本地环境执行动作，执行结果回传模型，模型再依据观察调整下一步。项目只使用基础模型客户端，不使用 Agent Framework，也不把文件或命令执行托管给 API 服务端。这样能够直接观察 messages、tool calls 和环境结果如何流转。

设计优先考虑协议正确性、运行可靠性和模块边界。环境失败需要能够回传给模型，每一层也应当可以独立测试。

## 2. 主数据流

```text
Desktop GUI / CLI arguments
    ↓
SessionRuntime / runtime.py
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
           ├─ run_command → Workspace cwd + subprocess
           └─ optional delegate_task → bounded pool
                    └─ temporary Child: fresh model/context + read-only tools
           ↓
       ToolResult (stdout/stderr/exit code/diff/error)
           ↓
       complete interaction block → next model request
```

`runtime.py` 是 CLI composition root，`session_runtime.py` 是持久桌面会话的 composition root。Config 的 Key 只传入 `DeepSeekChatClient`；
AgentRunner、ContextManager、Workspace、Registry 和 Tools 都拿不到凭据。

## 3. 模块职责

### Config 与 CLI

`config.py` 负责读取和校验模型、Workspace 与 Harness 设置。dataclass 将 `api_key` 设为 `repr=False`；凭据只会从 Config 传入 `DeepSeekChatClient`。`cli.py` 处理参数和退出码，`runtime.py` 负责构造各层。这个分工防止凭据和环境操作渗入 Agent 状态。

### DeepSeekChatClient

`llm.py` 输入 messages 与 tool definitions，输出统一的 `AssistantResponse`。非流式和流式入口共享同一结果结构；流式入口另外发出 `reasoning_delta` 与 `content_delta`，并聚合可能跨多个 chunk 的 tool call id、函数名和 JSON arguments。它不执行工具，
也不决定循环。Thinking 模式通过 `extra_body={"thinking":{"type":"enabled"}}` 打开，
`reasoning_effort` 可为 low/high/max。

DeepSeek 官方协议要求：携带 tools 的 thinking 多轮中，assistant 的 `reasoning_content` 必须在
后续请求完整回传，否则 API 会返回 400。因此客户端另外保存可重放的 `provider_message`，
其中保留 `content`、`reasoning_content` 与 `tool_calls`。CLI 不输出 reasoning；桌面端可以在本地折叠卡片中查看，持久化时也不会与 API 凭据混合。

暂时性连接错误、timeout、429 和 5xx 最多有限重试并指数退避；认证、权限、请求格式等明确
4xx 快速失败。异常中的 Key 会被替换为 `[REDACTED]`。

### Tool 与 ToolRegistry

每个 Tool 只有名称、面向模型的描述、有限 JSON Schema 和 Python handler。Registry 是模型
声明与本地函数之间唯一桥梁：注册、生成 Chat Completions tool definitions、解析 JSON、检查
必需字段/类型/enum/多余参数、捕获 handler 异常并返回统一 `ToolResult`。

没有实现完整 JSON Schema draft。核心工具只需要 object、string、integer、boolean、array、
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

普通和 mixed tool call 顺序执行，因为文件操作有副作用；并行会让结果依赖调度顺序。唯一例外是整批 call 都属于 `delegate_task`：Runner 先按 provider 顺序展示全部 call，再交给最多 4 worker 的有界线程池，最后仍按原 call 顺序和 call id 写回全部 Tool Results。畸形调用、路径失败、命令非零和 timeout 都是 Agent 可观察的反馈，不直接结束整个进程。连续工具错误和最大步数提供有限的循环保护。Agent 不靠 planner/reviewer 状态机，模型在同一反馈循环中自行调整。

### Optional Sub-Agent delegation

GUI 的每个 Session 保存独立 `subagents_enabled` metadata，旧 Session 默认 false。Toggle 在 Worker 运行期间禁用，因此本轮使用启动时从 Session 加载的能力快照。关闭时 system prompt 不描述 delegation，Registry 也没有 `delegate_task`；开启时两者同时出现。CLI 默认不暴露该能力。

`delegate_task(task)` 进入 `DelegateTaskService`。Service 属于一个 parent user turn，使用锁内计数限制本轮最多 8 次委派；每次调用都新建 `Workspace`、只含 list/read/search 的 `ToolRegistry`、`ModelClient`、`ContextManager` 和 `AgentRunner`，Child max steps 为 8。Child 没有 write/edit/run/delegate，无法递归委派，也不创建 `Session`、Sidebar item 或长期记忆。

Child 的 delegated task 是唯一 user message，Main 完整历史不会复制进去。Child 内部的 list/read/search trajectory 只存在于临时 Context；Main 最终只收到一个 bounded `ToolResult`，内容为 Child final findings 与 status/steps。这样大量局部探索不会污染 Parent model context。Main 仍负责决策、文件修改、Verification Guard 和用户回答。

纯 delegation batch 使用 `ThreadPoolExecutor(max_workers=min(4, calls))` 真正并行；5 个以上 call 会在池内排队而不会创建更多线程。future 全部提交后按原 call 列表读取结果，因此 completion timing 不改变 provider 顺序。Registry 把单个 Child 异常转成该 call 的失败结果，sibling 仍可完成。Parent Stop 的同一个 cooperative flag 传入所有 Child；网络调用无法强制中断，但返回后 Child 会在模型/工具安全边界停止。这里没有 thread terminate。

### ContextManager

system prompt 是 stable context。每个用户请求开启一个 turn；期间每个 assistant tool-call message 加其全部 tool results 组成一个 interaction block，最终 assistant message 关闭该 turn。Context 使用序列化 JSON 的字符长度作为近似 soft budget，而不是 token 数；字段名明确标记为 `*_chars`，不引入 tokenizer 依赖。

超过预算时分两级处理。第一层只处理已变老 completed turns 中超过统一阈值的 `list_files`、`read_file`、`search_text` 结果，把正文换成包含 tool name、call id、原始字符数和状态的短占位；最近 completed turns 和 active turn 不处理。第二层把仍然过大的旧 completed turns 交给 `ModelContextSummarizer`。它复用 Main 本轮已创建的同一个 `ModelClient`，发送独立 system/user messages 且 `tools=[]`，只生成 Goal、Constraints、Decisions、Completed Work、Files、Verification、Findings 和 Remaining Work 等结构化 working memory。

摘要提交采用 copy-then-commit：模型返回非空且未超过上限后，才更新 summary 并整体移除输入 turns；provider error、timeout、rate limit 或异常输出只增加失败统计，不改变原 turns，也不终止 Main。相同候选集失败后不会在每个 Agent step 重复请求；有新的 turn 变老或 Session 重启后可以重试。下一次压缩仅输入 previous summary 和之后新近变老的 turns，因此是 rolling 更新，不会重新发送第一轮以来的完整 transcript。

实际 messages 由 stable system prompt（附带 working memory）、最近两个完整 completed turns 和 current active turn 组成。极端情况下如果没有更多安全候选，Context 可以暂时超过 soft budget，但不会拆 active turn 或协议块。这条不变量同时保证：

- 不留下孤立 `role=tool`；
- 不留下缺少执行结果的 assistant tool call；
- 保留下来的 DeepSeek provider fields 结构完整。

Summary 与 Context compaction 统计进入 `model_context` version 2；`from_dict()` 仍接受 version 1 的 `soft_budget` 和 turns。Summary 是有损 working memory，system policy 明确要求以当前 Workspace 和最新 Tool Result 为事实依据，存在不确定性时重新观察。

### SessionStore 与桌面层

`SessionStore` 在 `~/.nju-coding-agent/sessions/` 中以单会话 JSON 文件保存 id、标题、Workspace、模型、reasoning effort、UTC 时间、置顶/未读/Sub-Agent metadata、完整 UI transcript 和序列化的模型上下文。新增 metadata 仍使用 schema v1 的可选默认字段，因此旧 Session 无需迁移即可加载。置顶会话和普通会话分别按 `updated_at` 倒序排列。写入使用进程内 per-session lock，为每次保存创建唯一临时文件，再用原子 replace 提交；Windows 短暂拒绝 replace 时进行有限退避重试，并在失败后清理临时文件。API Key 不属于 Session 数据结构。

Worker 使用 `save_runtime()` 写 transcript/model context，并合并磁盘上较新的 GUI metadata；重命名、置顶和未读则通过 `update_metadata()` 做锁内 patch，避免两个 stale Session 对象互相覆盖。非关键 event 的持久化失败不会中断 Tool 执行：GUI 显示独立 warning，后续 event 和 turn 结束时继续尝试完整保存。该机制提供同一进程的线程安全，不提供多个 GUI 进程同时写同一 Session 的跨进程事务保证。

GUI 文案由一个集中维护的 `zh/en` 映射提供，不使用额外 locale framework。`settings.json` 只保存语言、默认模型、默认 reasoning effort 和默认最大步骤；保存语言变更后可稍后生效，也可用 `QProcess.startDetached` 启动新 GUI 进程并正常关闭当前窗口。界面语言也会更新同一个 `ContextManager` 的 stable system prompt，只约束用户可见交流语言，不改变 Tool 名称、JSON Schema 或 provider message 协议。

侧边栏搜索仅过滤 Session 标题和 Workspace 路径，不读取 transcript。置顶、未读属于持久 Session metadata；执行中的点动画只属于当前窗口状态，不写入 transcript、模型上下文或 Tool 协议。重命名、打开目录和删除 Session 都不会修改 Workspace 文件。

新建 Conversation 或切换到新 Workspace 时，GUI 会展示完整路径并请求一次文件修改与本地命令授权。该提示描述的是应用能力边界，不把 Workspace 路径约束表述为 OS-level sandbox。

完整 transcript 和 provider context 是两份目的不同的数据：前者保留 reasoning、工具状态、diff、命令输出和最终回答，供 UI 恢复；后者由 `ContextManager` 保存 rolling summary 与协议完整的 recent raw context。销毁窗口并重新加载 Session 后会直接恢复已有 summary，不为同一批旧 turns 再次调用摘要模型；新用户消息继续追加到恢复的 Context。

每个 Session 固定绑定一个 Workspace。已有历史时切换目录会创建新 Session，从结构上避免项目 A 的工具结果进入项目 B 的模型上下文。附件不会绕过这条边界：内部文件只记录相对路径；外部文件必须经确认复制进 Workspace，随后仍通过 `Workspace.resolve_path()` 和文件工具读取。

Qt 主线程只处理窗口和控件。`AgentTaskManager` 维护 `workers[session_id]`；每个 `AgentWorker` 在自己的 `QThread` 中运行一套既有 `SessionRuntime → AgentRunner → ToolRegistry → Workspace`。Manager 只把 queued signals 包装为 `(session_id, event/result/error)`，不参与 prompt、reasoning、Tool Calling 或 Context 裁剪。前台 Session 实时渲染自己的 event，后台 event 只更新对应 Sidebar 状态；切回时从该 Session 的持久 transcript 恢复。Parallel GUI Sessions 是多个持久 Parent 各自运行；Parallel Sub-Agent 则是单个 Parent turn 内部的临时只读 children，两者生命周期与状态层级不同。

同一个 Session 同时最多有一个 Worker，不实现消息队列。不同 Session 可以并发，Stop 按 session_id 设置各自的协作式 cancellation flag；关闭应用时 `stop_all()` 请求所有 Worker 在安全边界停止，绝不调用 `QThread.terminate()`。多个运行任务存在时，语言设置可以保存，但立即重启被推迟。

不同 Session 的 Workspace 可以相同。应用会在第二个任务启动前警告文件覆盖、stale state、测试与命令互相干扰风险，但用户确认后仍允许继续；当前没有 Git Worktree、Workspace 文件锁或自动 merge。未来可用 Worktree 为每个任务建立独立副本，再增加 diff/review/merge 流程，本版本不实现。

### Verification Guard

每次成功 write/edit 都增加 workspace revision，并使最新 revision 处于未验证状态。只有识别为
test/build/lint/program 的成功 `run_command` 才把它标记为已验证。若模型此时直接 final，Harness
保存该 assistant 回答并追加一次反馈，要求执行合适验证。相同 revision 只提醒一次；如果没有
合理自动验证，下一次 final 可说明原因后结束。

轻量命令分类覆盖常见测试、构建与 lint，也识别明确的 `node --check`、基于 `vm.Script` 的 Node 内联语法检查，以及使用 `html.parser`/`ast.parse`/`compile` 的 Python 内联校验。它仍是启发式策略：成功的普通命令不会自动算作验证，识别出的验证也不等于形式化正确性证明。

它防止“写完即自信成功”，但不是正确性的形式化证明：命令可能覆盖不全，测试也可能有缺陷；
失败验证后模型仍可如实报告未解决问题。

## 4. 关键方案对比

### 为什么不用 Agent Framework

项目希望 messages、tool-call parsing、local execution 与 termination 都是显式的。引入框架会隐藏这些协议边界，也会让调试更依赖框架内部状态。当前每个模块都能使用 FakeModel 和临时 Workspace 独立测试。

### 为什么当前用 Chat Completions

Chat Completions 的路径是 `assistant.tool_calls → local tool → role=tool → next messages`。这种结构很直接，provider message、Tool Result 和上下文都能独立检查。DeepSeek Responses API 也是有效选项；当项目需要 reasoning/function item 或 Responses 专属能力时，可以在模型适配层增加实现。对当前的单 Agent 本地工具循环来说，切换 API 会增加协议对象，却不会改变核心编排。

### 为什么只实现 DeepSeek

目前只有 DeepSeek provider 会走真实协议测试。与其先建立一个缺少真实验证的 provider factory，项目选择先把 thinking 与 tool calling 的多轮回传做稳定。`ModelClient` Protocol 和 normalized response 已经隔离了 provider 边界，以后可以在不改动 AgentRunner 的情况下增加适配器。

### 为什么只有约六个工具

工具描述本身占 Context。list/search/read/write/edit/run 覆盖小型真实编程任务的观察、修改和验证，
且职责边界自然。增加几十个重叠工具会提高模型选错接口的概率。

### 为什么 exact replacement

它迫使模型先读文件并提供唯一上下文，0 次与多次匹配都有明确反馈，成功 diff 易审计。
替代方案包括完整文件覆盖、apply_patch 和 AST rewrite：前者误覆盖风险高，后两者更强但实现和协议
复杂度也更高。大型重构时应升级到 patch 或语言感知编辑。

### 为什么不做 Repo Map

Aider 的 Repo Map 用语法树和依赖图在大仓库中选择重要符号。当前范围主要是小型和中型仓库，受限的 list/search/read 已经提供足够的按需上下文。多语言 symbol graph 会引入解析器、排名算法和缓存一致性问题。如果后续面向大型仓库，token-bounded repo map 会是合理的扩展。

## 5. 安全模型与诚实边界

直接文件工具不能访问 Workspace 外或配置的 Secret；命令 cwd 不能越界；Harness 的 API Key
不进入子进程环境。这里仍不是 Sandbox：目标项目程序在当前 OS 用户下运行，可以访问该用户有权
访问的其他文件与网络，轻量 denylist 也不可能覆盖所有等价命令。不要对不可信代码声称安全隔离。

生产化需要容器/namespace/VM、独立低权限用户、只挂载 Workspace、网络默认拒绝、CPU/内存/进程
配额、系统调用策略、可审计 permission policy，并把 Harness 凭据保存在隔离边界之外。

## 6. 测试策略

- Unit：Config、DeepSeek normalization/retry、Workspace、Registry、每个 Tool、Context；
- Boundary and execution：`../`/absolute/symlink escape、Secret 拒绝、invalid JSON/参数、exact edit 三种结果、命令
  success/non-zero/timeout、Context compaction、Verification Guard、max steps、Sub-Agent limits/cancellation；
- Integration：FakeModel + real AgentRunner + real Registry/Workspace 完成 read/edit/execute/final；
- Live protocol：显式脚本验证 thinking/tool/result/next request；
- Live E2E：自然语言任务驱动真实 DeepSeek 完成探索、失败、修改和再次测试。

普通 `pytest` 永不发起真实 API 请求。

## 7. 未来升级方向

- 容器或 VM Sandbox、网络隔离、资源 quota 与细粒度权限；
- 可选的 token-aware budget 校准、摘要质量评估和 retrieval cache；
- Aider 风格 Repo Map、LSP、AST/patch 编辑；
- 更细粒度的 trajectory replay、会话导入导出与系统化 evaluation suite；
- Responses API 和更多 provider adapter；
- 在不破坏单循环可读性的前提下增加用户审批与可恢复 checkpoint。
