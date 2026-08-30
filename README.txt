项目名称：NJU Coding Agent
Git 仓库：https://github.com/LuciferTGQ/coding-agent.git

运行：Windows 从 GitHub Releases 下载并解压 CodingAgent-windows-x64.zip，运行 CodingAgent.exe；源码版需 Python 3.11+，安装仓库依赖后运行 python start_gui.py。调用模型前设置环境变量 DEEPSEEK_API_KEY。

本项目基于 DeepSeek Chat Completions 与原生 Tool Calling 自行实现本地 Coding Agent Harness，不使用 Agent 框架或服务端工具。对话历史与 Context、模型输出解析、Tool 定义与校验、本地执行、错误反馈和循环终止均由项目实现。Agent 循环执行模型调用、本地 Tool、Tool Result 回传和下一步决策，可浏览、搜索、分段读取、写入、精确编辑并运行命令；失败和超时也反馈给模型。修改后，Verification Guard 要求执行测试、构建或程序检查，确无合适验证时才允许说明原因后结束。

桌面 Session 持久化完整 transcript、模型 Context、Workspace 和设置，重启后可继续多轮任务。长会话通过旧只读 Tool Result 压缩与 Rolling Summary 控制增长，并保留近期完整 turn 和协议配对。不同 Session 可独立并行；Stop 在安全边界生效，并保留有效 Context。

复杂任务可按 Session 开启 Sub-Agent：Main 最多并行委派 4 个独立 Context 的临时只读 Child；Child 只返回精简 findings，修改与最终验证仍由 Main 统一完成。路径和命令 cwd 受 Workspace 边界约束；API Key 只进入模型客户端，不写入 Session 或传给子进程。命令采用 shell=False、超时、输出截断和凭据过滤；这些是基础防护，不是操作系统级 Sandbox。
