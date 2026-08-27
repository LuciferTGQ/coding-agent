# 可重复 Demo

这个 Demo 使用一个小型 Python 文本统计项目验证完整的 Agent 反馈循环。样例包含多个源文件和测试，初始实现有一个会导致 3 个测试失败的缺陷。

## 准备工作区

在仓库根目录运行：

```powershell
python scripts/prepare_demo.py
```

脚本会将 `examples/buggy_project` 复制到独立的 `.demo-workspace`。Agent 的修改只发生在这份副本中，模板本身不受影响。

如果需要先确认初始状态：

```powershell
Push-Location .demo-workspace
python -m pytest -q
Pop-Location
```

预期结果为 `3 failed, 6 passed`。

## 使用桌面端运行

```powershell
python -m coding_agent.gui
```

创建会话时选择 `.demo-workspace`，发送：

> Inspect this project, identify why its tests fail, fix the bug without weakening the tests, and verify the result.

界面会流式显示模型回复；reasoning 默认折叠，文件读取、搜索、精确编辑和 pytest 输出显示为带状态的工具卡片。任务完成后可继续发送：

> Add a focused regression test for the bug you fixed and run the relevant tests again.

关闭并重新打开应用，选择同一会话，再发送：

> Summarize the original defect, both changes, and the verification results.

最后一轮应能引用此前任务、修改和工具结果，说明 provider context 已从磁盘恢复。完整 transcript 会一直保留在界面中，即使较旧的模型上下文因预算而被裁剪。

## 使用 CLI 运行

```powershell
python -m coding_agent --workspace .demo-workspace --max-steps 20 `
  "Inspect this project, identify why its tests fail, fix the bug without weakening the tests, and verify the result."
```

该任务没有指定缺陷所在文件或工具顺序。Agent 需要根据项目结构和执行结果自行决定下一步。

## 预期轨迹

每次的读取顺序可能不同，但完整过程通常包含：

```text
list_files / read_file 了解项目
→ run_command 执行 pytest
→ 观察 exit code 1 和失败断言
→ search_text / read_file 定位相关逻辑
→ edit_file 返回 unified diff
→ run_command 重新执行 pytest
→ 观察 exit code 0 和 9 passed
→ Final Answer 总结修改与验证结果
```

这条轨迹同时覆盖了文件观察、执行失败反馈、精确编辑和修改后验证。如果模型在编辑后试图直接结束，Verification Guard 会要求补充一次可执行的验证。

## 重置

重新运行准备脚本即可删除当前副本并恢复初始缺陷：

```powershell
python scripts/prepare_demo.py
```

`.demo-workspace` 已被 Git 忽略，不会污染仓库状态。
