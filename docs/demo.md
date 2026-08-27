# Demo 与两分钟视频流程

## 1. 准备

安装项目并用环境变量配置 DeepSeek Key。不要在终端执行会显示 Key 的 `echo`、`env` 或
`Get-ChildItem Env:`；录制画面中不要打开 `api.txt`、`.env` 或凭据设置历史。

从模板重建独立工作区：

```powershell
python scripts/prepare_demo.py
```

该脚本只会替换仓库内被 `.gitignore` 排除的 `.demo-workspace`。原始
`examples/buggy_project` 保持不变，因此演示后再次运行即可 reset。

可在录制前确认初始状态：

```powershell
Push-Location .demo-workspace
python -m pytest -q
Pop-Location
```

预期是 3 failed、6 passed；这一步可不放进最终视频，以节省时间。

## 2. 推荐任务和命令

```powershell
python -m coding_agent --workspace .demo-workspace --max-steps 20 `
  "Inspect this project, identify why its tests fail, fix the bug without weakening the tests, and verify the result."
```

这是普通自然语言任务。Harness 和 system prompt 中没有 Demo 文件名、bug 位置或固定工具序列。

## 3. 理想展示路径

```text
用户任务
→ list_files / read_file 了解项目
→ run_command 执行 pytest
→ 观察真实失败与 exit code 1
→ 定位统计逻辑
→ edit_file 返回 unified diff
→ run_command 再次执行 pytest
→ 观察 exit code 0 与 9 passed
→ Final Answer 总结修改和验证
```

模型可能选择 search 或不同读取顺序，这正说明它在使用通用 Harness，而不是走固定脚本。

## 4. 两分钟建议剪辑

- 0:00-0:12：一句话介绍“自研 Harness + DeepSeek 原生 tool calling”，展示任务；
- 0:12-0:45：加速展示 list/read 和首次 pytest，停留在 3 failed；
- 0:45-1:15：展示错误反馈、精确 edit 与 unified diff；
- 1:15-1:35：展示第二次 pytest 的 9 passed 和 Final Answer；
- 1:35-1:58：用架构图口述 AgentRunner、Registry、Workspace、Context block 与 Guard；
- 留 2 秒结束余量，视频必须小于 2 分钟、mp4、小于 200 MB。

建议终端宽度约 110-130 列、字号足够大，关闭通知，先清屏。CLI 默认会限制长输出；如需更多
细节才使用 `--verbose`。可以剪辑和加速，但保留“失败输出 → 修改 → 成功输出”的因果链。

## 5. 录制后检查

1. 画面和音频没有 Key、`api.txt` 内容或环境变量值；
2. 能看清 task、工具名、失败 exit code、diff、最终 9 passed；
3. 没有修改或删除测试；
4. 时长小于 2 分钟，mp4 小于 200 MB；
5. 最终提交 zip 只包含 `README.txt` 和视频，并以本人姓名命名；
6. 截止时间后不要再向公开仓库推送。
