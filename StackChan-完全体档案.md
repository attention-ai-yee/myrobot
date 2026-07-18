# StackChan 完全体档案

> 建立时间：2026-07-18 凌晨 · 由 Kimi 与主人共同搭建
> 一句话：一台 M5Stack StackChan 机器人 + 这台电脑 + 飞书 = 会聊天、会动、会看、能控电脑、能被飞书远程操控、拥有 Kimi K3 专家大脑的桌面 AI 伙伴。

---

## 一、设备档案

| 项目 | 值 |
|---|---|
| 设备 | M5Stack StackChan (K151)，CoreS3 主机（ESP32-S3, 16MB Flash + 8MB PSRAM）|
| 固件 | stack-chan **v1.4.4**（小智 xiaozhi 架构，ESP-IDF v5.5.4）|
| 串口 | **COM5**（USB 原生串口，115200 波特率）|
| WiFi | Redmi_03EA（2.4GHz）· 机器人 IP 192.168.31.201 |
| 蓝牙 | 设备名 `StackChan`，MAC `44:1B:F6:E4:01:A2`（BLE 服务仅 Dance 模式开放）|
| 唤醒词 | **"Hi, StackChan"**（不可自定义；也可单击屏幕唤醒）|
| 指示灯 | 🟢绿=聆听中 / 🔵蓝=讲话中 / ⚫灭=待机 |
| 省电 | 300 秒无交互休眠，900 秒自动关机 |

## 二、系统架构与数据链路

```
你说话 → 🤖机器人(麦克风/降噪) → WiFi → ☁️小智云端(ASR→大模型→TTS) → 机器人喇叭
                                          │
                          大模型决定调工具时 ↓ WebSocket (api.xiaozhi.me/mcp)
                                          │
📱飞书「飞书 CLI」 → feishu_listener.py → K3 意图解析(只填参数不写代码) ──┤
                                          ↓
                      💻这台电脑 bridge.py（MCP 工具服务器 + 本地 HTTP 8766）
                          ├─ pc.* 本地技能（开网页/音量/锁屏…）
                          ├─ win.* Windows-MCP 套件（看屏幕/点击/打字…）
                          ├─ ai.ask_expert → HTTPS → ☁️Kimi K3
                          └─ robot.* → 🔵蓝牙直连 → 🤖机器人转头/灯带（需 Dance 模式）

飞书"让机器人说 xxx" → robot_inbox.jsonl 队列 → 注入下次对话的工具结果 → 机器人先播报收尾
```

**电脑的角色**：工具引擎 + 飞书网关 + 蓝牙遥控器。机器人聊天直连小智云不经过电脑；电脑关机 → 失去 28 个扩展技能和飞书遥控，开机自动恢复。

## 三、技能清单（28 个扩展技能 + 自带能力）

### 自带（无需电脑）
语音对话（内置大模型）、个性人设、记忆、表情头像、摸头/摇晃互动、转头（双舵机）、拍照视觉问答、提醒闹钟、RGB 灯光、自身音量/亮度、OTA 升级。

### 手机 app（StackChan World）
Avatar 分身、监控摄像头、Motion 遥控、Dance 跳舞编排、对话记录、AI 模型与音色设置。

### 电脑技能（bridge.py 提供，需电脑开机+解锁）

| 类别 | 技能 | 说法举例 |
|---|---|---|
| 网页 | pc.open_url / search_video / search_web | "打开B站"、"在腾讯视频找《xxx》"、"搜一下xxx" |
| 遥控 | pc.set_volume / volume_adjust / mute / lock_screen | "电脑音量调到30%"、"静音"、"锁屏" |
| 应用 | pc.open_app | "打开记事本/计算器" |
| 信息 | pc.get_time / get_system_status | "电脑几点了"、"查电脑状态" |
| 桌面 | win.Snapshot/Screenshot/Click/Type/Shortcut/Scroll/App | "点开第一个视频"、"暂停"、"全屏"、"看看屏幕上是什么" |
| 内容 | win.Scrape / Clipboard / Notification | "这网页讲了什么"、"复制给我"、"发个通知" |
| 系统 | win.PowerShell / FileSystem / Process / Registry | "哪些程序最占内存"、"读桌面上的xxx文件"、"关掉Chrome" |
| 专家 | **ai.ask_expert（Kimi K3）** | "问问专家：xxx"、"问问Kimi：xxx" |
| 机器人 | robot.head_move / robot.rgb_light | "让机器人左转/摇头/亮红灯"（蓝牙直连，需 Dance 模式）|

### 飞书遥控（feishu_listener.py，飞书里发消息即可）
对飞书机器人「**飞书 CLI**」私聊发文字即可遥控：自然语言 → Kimi K3 解析（只翻译参数，不生成代码）→ 调用全部 28 个技能 → 文字/截图回复。例："打开电脑上的B站"、"截个屏"。闲聊也可，由 K3 直接回答。仅白名单用户（主人）可用。
**指挥机器人说话/提醒**：说"让机器人 xxx"（非动作类）→ 指令进 `robot_inbox.jsonl` 队列 → 机器人下次被唤醒对话时先播报/照做再回应你。⚠️ 两个前提：①官方固件下机器人无法被远程主动唤醒；②**那次对话必须触发一次工具调用**（问时间、开网页等都算），纯闲聊不消耗队列。

### 蓝牙直控机器人（robot_ble.py，Dance 模式下立即执行）
机器人在 **Dance 跳舞模式**下开放无配对 BLE GATT 服务（固件源码实锤），电脑经 `robot.head_move` / `robot.rgb_light` 工具**蓝牙直连立即控制**，飞书说"让机器人左转 30 度/亮红灯"立即生效；本机 CLI：`python robot_ble.py scan / move --yaw --pitch / rgb / demo`。⚠️ AI Agent 模式下 BLE 服务关闭，会提示"没找到蓝牙信号"。

## 四、配置位置一览

| 内容 | 路径 |
|---|---|
| 桥接主程序 | `stackchan-mcp\bridge.py`（v0.6.0，含本地 HTTP 端点 `127.0.0.1:8766`）|
| 飞书监听 | `stackchan-mcp\feishu_listener.py`（飞书 CLI 机器人，App ID `cli_a944219ac1b9dcd6`）|
| 蓝牙控制模块 | `stackchan-mcp\robot_ble.py`（被 bridge.py 引用，也可独立运行）|
| 机器人指令队列 | `stackchan-mcp\robot_inbox.jsonl`（飞书→机器人留言，消费后自动清空）|
| 飞书日志 | `stackchan-mcp\feishu_listener.log`（飞书链路排查首选）|
| 配置文件 | `stackchan-mcp\config.json`（小智 token + Kimi K3 key + 飞书 app 凭证与白名单 + 套件开关）|
| 运行日志 | `stackchan-mcp\bridge.log`（排查首选）|
| **自救神器** | `stackchan-mcp\重启桥接.bat`（双击，桥接+飞书监听一起重启）|
| 日志查看 | `stackchan-mcp\查看日志.bat`（双击实时滚动 bridge.log）|
| Python 环境 | `stackchan-mcp\.venv\`（websockets + lark-oapi + bleak）|
| 套件 | Windows-MCP（uv 管理，`stackchan-mcp\bin\uv.exe`）|
| 开机自启 | 计划任务 `StackChanMCPBridge`（桥接）+ 启动文件夹 `stackchan-feishu-listener.bat`（飞书监听）|
| 开源调研 | `tree.json`（m5stack/StackChan 全仓库文件树，控制通道调研结论见对话记录）|
| issue 追踪 | 定时任务每天 09:47 检查 [issue #103](https://github.com/m5stack/StackChan/issues/103)，状态存 `stackchan-mcp\issue-103-state.json` |
| Bug 报告底稿 | `stackchan-mcp\bug-report-no-audio-after-photo.md` |
| 复现日志 | `stackchan-mcp\serial-repro2.log` |

## 五、故障自救指南

| 症状 | 处方 |
|---|---|
| 机器人说"找不到这个工具" | 退出 AI Agent 重新进入（工具清单在重连时刷新）|
| 电脑技能全部失灵 | 双击 `重启桥接.bat`，看到"28 个工具"即恢复 |
| 飞书机器人不回复 | ①双击 `重启桥接.bat` ②看 `feishu_listener.log` 是否有 connected 行 ③确认消息发给了「飞书 CLI」而不是 hermes-server |
| 飞书让机器人转头/亮灯没反应 | ①确认机器人在 **Dance 跳舞模式**（AI Agent 模式蓝牙服务关闭）②别离电脑太远 ③重启桥接 |
| 飞书让机器人说话没发生 | ①机器人要先被唤醒对话一次 ②那次对话要触发一次工具调用（问时间/开网页等），纯聊天不消耗队列 ③看 `bridge.log` 有无"注入 N 条飞书指令"行 |
| 飞书回复"我只听主人的指令" | 你的 open_id 不在白名单，找主人加进 `config.json` 的 allowed_open_ids |
| 单个套件工具失灵 | 不用管，守护进程 5 秒自愈；反复失败则重启桥接 |
| COM5 串口消失 | 机器人休眠/关机了，或 USB 松了；按电源键开机、重插线 |
| **读图后说话没声音** | 已知固件 bug（[issue #103](https://github.com/m5stack/StackChan/issues/103)），退出 AI Agent 重进即可恢复；等官方修复 |
| 喊不醒 | ①确认在 AI Agent 模式 ②靠近大声 ③或单击屏幕唤醒 |
| 电脑技能偶尔点歪 | 正常，AI 看屏点击非像素级精确，让它再试一次 |
| 电脑关机后技能没了 | 正常，开机后桥接自启，技能自动回来 |
| 想收回高权限 | 找 Kimi 改 `config.json` 的 allow_tools 白名单 |

## 六、安全须知

1. `config.json` 里有**小智接入点 token**、**Kimi API key**、**飞书 App Secret**——等同于密码，勿分享、勿截图本档案给外人
2. **声控=无密码**：任何能对机器人说话的人都能操作这台电脑，有客人时留意
3. **蓝牙无配对**：Dance 模式下 10 米内任何手机/电脑都能连上机器人的 BLE 服务控它转头——公共场合别把机器人留在 Dance 界面
4. 已放开的高危权限：PowerShell、文件读写、进程管理、注册表——重要文件请有备份

## 七、还能怎么玩（后路）

- 换人设/内置模型：StackChan World app → AI 智能体设置，改完重启机器人
- 调 K3 回答风格：`config.json` 的 expert_model（思考强度 low/high/max、字数）
- 挂更多公共 MCP 套件：`config.json` 的 mcp_servers 加一段即可
- 蓝牙控制更多：表情（Avatar 特征 `e2e5e5e2`）、舞蹈序列——`robot_ble.py` 里加几个函数的事
- 终极自定义：改开源固件在 AI Agent 模式常驻 BLE/HTTP 控制服务 + TTS 注入（仓库 m5stack/StackChan 全开源：firmware/app/server/remote），或自建 xiaozhi-esp32-server——能实现"随时主动开口"，代价是失去 M5Stack app 功能
- 关闭待机转头：官方暂无开关（GitHub 有 open 的 Feature Request），可改开源固件实现

## 八、无线连接方式总览

机器人有 4 种对外连接，各管各的事，别混淆：

| 方式 | 干什么用 | 怎么用 / 注意 |
|---|---|---|
| **Wi-Fi（主力）** | AI 对话、拍照问答、提醒、OTA 升级、app 视频/监控、电脑 MCP 桥接（26 个云侧技能）| 仅 2.4GHz；一切"智能"都走 Wi-Fi → 小智云端 |
| **蓝牙 BLE 5.0** | ①手机 app 的 Dance 跳舞编排 ②**本电脑 BLE 直控**（robot.head_move / robot.rgb_light）| 服务仅在 Dance 模式开放、无配对；❌不能当蓝牙音箱/耳机；AI Agent 模式关闭 |
| **ESP-NOW** | 官方遥控器固件专用（乐鑫私有协议，不经路由器）| 普通用户接触不到，DIY 遥控手柄才用 |
| **USB-C（COM5）** | 供电、刷固件、串口日志（115200）| 插线打开串口会让机器人复位重启，属正常现象 |

- 电脑对机器人的两条控制路：云侧（MCP 工具，对话中生效）+ 蓝牙侧（robot.* 直控，Dance 模式立即生效）
- 想自定义蓝牙玩法（BLE 遥控、手机直连发指令）需自刷固件：Arduino / UiFlow2 + 官方 `StackChan-BSP` 板级支持包，ESP32-S3 的 BLE 能力全开放
