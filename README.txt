项目名称：NJU Coding Agent
Git 仓库：https://github.com/LuciferTGQ/coding-agent.git

本项目是基于 DeepSeek Chat Completions 与原生 Tool Calling 自实现 Harness 的本地编程智能体，不依赖 Agent 框架或服务端文件、执行工具。支持持久化 PySide6 桌面端和 CLI。安装 Python 3.11+ 后执行 python -m pip install -e ".[dev]"，设置环境变量 DEEPSEEK_API_KEY。桌面端运行 python -m coding_agent.gui，也可直接运行 start_gui.py；CLI 运行 python -m coding_agent --workspace 项目目录 "Fix the failing tests and verify the result."

Agent 可自主浏览、搜索、分段读取、精确修改和运行测试，并把执行结果反馈给模型。ToolRegistry 统一声明、校验和执行六个本地工具；无效 JSON、路径越界、编辑歧义与命令失败都会成为可观察反馈。ContextManager 支持多轮对话，按完整用户轮次和 assistant-tool block 裁剪，并保留 thinking/tool calling 所需的 provider fields。

桌面端流式显示回答、可折叠 reasoning 和工具状态卡片。会话保存在 ~/.nju-coding-agent，不写入仓库或 Workspace；完整 transcript 与有预算的模型上下文分开保存，重启后可以继续追问。每个会话固定绑定一个 Workspace，外部附件须确认复制后才能读取。模型与工具运行在后台线程，Stop 在安全边界生效。

Verification Guard 在修改后要求执行验证。Workspace 限制文件路径和命令 cwd；子进程过滤常见密钥变量，命令采用 shell=False、超时和输出截断。这些是基础防护，不是真正 Sandbox。

离线测试运行 python -m pytest；真实流式协议验证需设置 RUN_LIVE_TESTS=1 后运行 python scripts/live_smoke.py。Demo 先运行 python scripts/prepare_demo.py，再让桌面端或 CLI 修复 .demo-workspace 中的失败测试。
