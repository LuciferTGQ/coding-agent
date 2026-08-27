项目名称：NJU Coding Agent
Git 仓库：https://github.com/LuciferTGQ/coding-agent.git

本项目是从基础模型 API 实现 Harness 的命令行编程智能体，使用 DeepSeek V4 Flash Chat Completions 与原生 Tool Calling，不依赖 Agent 框架或服务端文件/执行工具。安装 Python 3.11+ 后执行 python -m pip install -e ".[dev]"，并设置环境变量 DEEPSEEK_API_KEY。运行：python -m coding_agent --workspace 项目目录 "Fix the failing tests and verify the result."

Agent 可自主浏览、搜索、分段读取、精确修改、运行测试，并把 stdout、stderr、退出码和超时结果反馈给模型继续决策。六个工具由 ToolRegistry 统一声明、校验和执行；无效 JSON、路径越界、编辑歧义、命令失败等会成为模型可观察的错误反馈。ContextManager 保留原任务，并按完整 assistant-tool 交互块裁剪历史，同时正确回传 DeepSeek thinking 所需的 reasoning_content。

Verification Guard 会在文件修改后检查是否执行有效验证；若模型过早结束，Harness 会提醒其运行测试、构建或程序。Workspace 统一限制文件路径和命令 cwd，隐藏已配置的凭据文件；子进程环境过滤常见密钥变量，命令采用 shell=False、超时和输出截断。这些是基础防护，不是真正 Sandbox。

离线测试执行 python -m pytest；真实协议验证需设置 RUN_LIVE_TESTS=1 后运行 python scripts/live_smoke.py。可重复演示先运行 python scripts/prepare_demo.py，再让 Agent 修复 .demo-workspace 中的失败测试。项目已用真实 DeepSeek 完成“探索—失败—定位—修改—验证—总结”完整 E2E。
