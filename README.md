# StackChan 完全体系统

一台 **M5Stack StackChan** 机器人 + 一台 Windows 电脑 + 飞书 = 会聊天、会动、会看、能控电脑、能被飞书远程操控、可外接专家大模型的桌面 AI 伙伴。

```
语音唤醒 → 小智云(ASR→LLM→TTS) → 机器人
                  ↓ MCP (WebSocket)
飞书「机器人」→ feishu_listener.py → K3 意图解析 ─┐
                  ↓                 ↓
        bridge.py（28 个工具 + 本地 HTTP :8766）
          ├─ pc.*   开网页 / 音量 / 锁屏 / 系统状态
          ├─ win.*  Windows-MCP 套件（看屏/点击/打字/PowerShell…）
          ├─ ai.ask_expert → 专家大模型（OpenAI 兼容接口）
          └─ robot.* → BLE 蓝牙直控机器人（转头/灯带，需 Dance 模式）
飞书"让机器人说 xxx" → robot_inbox.jsonl → 注入下次对话 → 机器人先播报
```

## 功能一览（28 个工具）

| 通路 | 玩法 |
|---|---|
| 语音 → 机器人 → 电脑 | "Hi, StackChan，打开电脑上的腾讯视频"、"问问专家：xxx" |
| 飞书 → 电脑 | 发"打开B站 / 截个屏 / 音量30%"，立即执行并回文字/截图 |
| 飞书 → 机器人（动作） | 发"让机器人左转30度/亮红灯"，BLE 直连立即执行（需 Dance 模式）|
| 飞书 → 机器人（语音） | 发"让机器人提醒我10点睡觉"，下次对话时它先播报 |

## 快速开始（新电脑部署）

1. **环境**：Windows + Python 3.12 + [uv](https://docs.astral.sh/uv/)（用于 Windows-MCP 套件）+ 电脑蓝牙（robot.* 工具需要）
2. **安装**：
   ```bash
   git clone https://github.com/attention-ai-yee/myrobot.git
   cd myrobot/stackchan-mcp
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
   ```
3. **配置**：复制 `config.example.json` 为 `config.json`，填入：
   - `endpoint`：小智 APP → MCP 接入点地址
   - `expert_model.api_key`：你的大模型 API Key（OpenAI 兼容接口均可）
   - `feishu.*`：飞书自建应用凭证（事件订阅选**长连接** + 添加「接收消息」事件，权限开通 im:message 系列）
4. **启动**：
   ```bash
   .venv\Scripts\pythonw.exe bridge.py            # MCP 桥接（28 个工具）
   .venv\Scripts\pythonw.exe feishu_listener.py   # 飞书遥控
   ```
   或双击 `重启桥接.bat`（一键重启 + 健康检查）。
5. **开机自启**：桥接见计划任务 `StackChanMCPBridge` 的做法；飞书监听放一个快捷方式到 `shell:startup`。

## 文件说明

| 文件 | 作用 |
|---|---|
| `stackchan-mcp/bridge.py` | MCP 桥接主程序：连小智云、聚合工具、本地 HTTP 控制端点 |
| `stackchan-mcp/feishu_listener.py` | 飞书长连接监听：消息 → K3 解析 → 执行 → 回复 |
| `stackchan-mcp/robot_ble.py` | BLE 直连机器人（可被 import，也可独立 CLI：`scan/move/rgb/demo`）|
| `stackchan-mcp/restart_bridge.ps1` + `重启桥接.bat` | 一键重启全部进程并打印健康状态 |
| `stackchan-mcp/查看日志.bat` | 实时滚动桥接日志 |
| `StackChan-完全体档案.md` | 完整文档：技能清单、配置位置、故障自救、安全须知、无线连接总览 |

## ⚠️ 安全须知

- `config.json` 含全部密钥，**已被 .gitignore 排除，永远不要提交**
- 声控无密码：能对机器人说话的人都能操作电脑
- BLE 无配对：Dance 模式下附近设备都能控机器人，公共场合注意
- `win.*` 套件含 PowerShell / 文件读写 / 注册表等高权限，请按需裁剪 `allow_tools`

## 已知限制（官方固件天花板）

- 机器人**无法被远程主动唤醒说话**（小智云无推送 API），"让机器人说"类指令只能下次对话时消费
- BLE 直控只在 **Dance 跳舞模式**下可用（AI Agent 模式固件会关闭 BLE 服务）
- 突破方案：改开源固件 / 自建 xiaozhi-esp32-server（见档案「后路」一节）
