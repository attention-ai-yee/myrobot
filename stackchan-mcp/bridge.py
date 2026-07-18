# -*- coding: utf-8 -*-
"""
StackChan <-> 这台电脑 的 MCP 桥接脚本
======================================
原理：作为 MCP Server 通过 WebSocket 连接小智 MCP 接入点，
把这台电脑上的工具暴露给 StackChan 的 AI 智能体调用。

运行：.venv/Scripts/python.exe bridge.py
日志：同目录 bridge.log
配置：同目录 config.json（endpoint = 小智接入点地址）
"""

import asyncio
import ctypes
import getpass
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets

try:  # BLE 直连机器人（可选能力，bleak 缺失时自动禁用）
    import robot_ble
    BLE_AVAILABLE = True
except Exception as _ble_err:
    robot_ble = None
    BLE_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "bridge.log")
ROBOT_INBOX = os.path.join(BASE_DIR, "robot_inbox.jsonl")

# 机器人最近一次通过 MCP 连上桥接的时间戳（0 表示从未连接）。
# 飞书回执用它判断"留言大概要等多久才会被机器人收到"。
LAST_MCP_ACTIVITY = 0.0


def touch_mcp_activity():
    global LAST_MCP_ACTIVITY
    LAST_MCP_ACTIVITY = time.time()


def pending_inbox_count():
    if not os.path.exists(ROBOT_INBOX):
        return 0
    try:
        with open(ROBOT_INBOX, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def drain_robot_inbox():
    """取出主人从飞书留给机器人的指令（若有），返回提示前缀文本。

    机器人无法被远程主动唤醒，只能在它下次对话调用工具时，
    把飞书指令注入工具结果，让智能体优先播报/照做。
    """
    if not os.path.exists(ROBOT_INBOX):
        return ""
    try:
        processing = ROBOT_INBOX + ".processing"
        os.replace(ROBOT_INBOX, processing)
        with open(processing, encoding="utf-8") as f:
            items = [json.loads(line) for line in f if line.strip()]
        os.unlink(processing)
    except Exception as e:
        log.warning("读取飞书指令队列失败: %s", e)
        return ""
    if not items:
        return ""
    log.info("注入 %d 条飞书指令给机器人", len(items))
    lines = [f"{i + 1}) {it.get('instruction', '')}" for i, it in enumerate(items)]
    return (
        "【主人从飞书发来的指令，请先用语音播报并照做，然后再回应本次请求：\n"
        + "\n".join(lines)
        + "\n】\n\n"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bridge")

# ---------------------------------------------------------------- 工具定义

APPS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "cmd": ["cmd.exe", "/c", "start", "cmd.exe"],
    "paint": ["mspaint.exe"],
}

# 各视频站搜索结果页地址模板，{q} 为关键词
VIDEO_SITES = {
    "tencent": ("腾讯视频", "https://v.qq.com/x/search/?q={q}"),
    "bilibili": ("哔哩哔哩", "https://search.bilibili.com/all?keyword={q}"),
    "iqiyi": ("爱奇艺", "https://so.iqiyi.com/so/q_{q}"),
    "youku": ("优酷", "https://so.youku.com/search_video/q_{q}"),
    "mgtv": ("芒果TV", "https://so.mgtv.com/so?k={q}"),
    "youtube": ("YouTube", "https://www.youtube.com/results?search_query={q}"),
}

TOOLS = [
    {
        "name": "pc.open_url",
        "description": "在这台 Windows 电脑的默认浏览器中打开一个网页。当用户想让你在电脑上打开网站、网页、链接时使用，例如“打开电脑上的B站”。参数 url 可以是网址或域名。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要打开的网址，例如 https://www.bilibili.com"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "pc.search_video",
        "description": "在电脑浏览器中打开指定视频网站的搜索结果页，帮用户找想看的电影、电视剧、综艺、动漫等视频。当用户说“帮我找/搜/我想看 xxx”时使用。site 取值：tencent(腾讯视频)、bilibili(哔哩哔哩/B站)、iqiyi(爱奇艺)、youku(优酷)、mgtv(芒果TV)、youtube；用户没指定网站时默认用 tencent。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "视频名称或搜索关键词，例如 藏海传"},
                "site": {
                    "type": "string",
                    "enum": list(VIDEO_SITES.keys()),
                    "description": "视频网站标识，默认 tencent",
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "pc.search_web",
        "description": "在电脑浏览器中用百度搜索一个关键词并打开结果页。当用户想让你在电脑上搜索资料、查东西时使用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "pc.set_volume",
        "description": "把这台 Windows 电脑的扬声器音量调到指定百分比（0-100）。当用户说“把电脑的音量调到 xx%”时使用。注意：这是控制电脑的音量，不是机器人自身的音量。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "音量百分比，0-100"}
            },
            "required": ["level"],
        },
    },
    {
        "name": "pc.volume_adjust",
        "description": "把这台 Windows 电脑的音量调大或调小一档（约10%）。当用户说“电脑声音大一点/小一点”时使用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "up=调大，down=调小"}
            },
            "required": ["direction"],
        },
    },
    {
        "name": "pc.mute",
        "description": "切换这台 Windows 电脑的静音状态：当前有声音则静音，当前静音则恢复声音。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pc.lock_screen",
        "description": "锁定这台 Windows 电脑的屏幕（回到登录界面）。当用户说“锁屏”“锁定电脑”时使用。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pc.open_app",
        "description": "在这台 Windows 电脑上启动一个应用程序。可选值：notepad(记事本)、calculator(计算器)、explorer(文件资源管理器)、cmd(命令提示符)、paint(画图)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "enum": list(APPS.keys()),
                    "description": "要启动的应用程序标识",
                }
            },
            "required": ["app"],
        },
    },
    {
        "name": "pc.get_time",
        "description": "获取这台 Windows 电脑当前的日期和时间。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pc.get_system_status",
        "description": "获取这台 Windows 电脑的基本状态：计算机名、当前登录用户、操作系统版本、CPU 核心数。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ai.ask_expert",
        "description": "把问题转发给更聪明的专家大模型（Kimi K3 旗舰模型）来回答。当用户说“问问专家”“问问 Kimi”“用更聪明的模型回答”时使用；遇到复杂问题、代码、数学、深度分析也建议使用。回答会以语音朗读，通常较简短。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要请教专家模型的完整问题"}
            },
            "required": ["question"],
        },
    },
    {
        "name": "robot.head_move",
        "description": "[蓝牙直连机器人] 控制桌上的 StackChan 机器人转头/俯仰，立即执行。当用户说“让机器人左转/右转/抬头/低头/摇头/看向我”时使用。前提：机器人需在 Dance 跳舞模式（AI Agent 模式下蓝牙服务关闭，会连不上）。yaw 为左右转角（-128~128 度，正数右转、负数左转），pitch 为俯仰角（0~90 度，0 最低、45 平视），speed 为速度（100~1000，默认 600）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "yaw": {"type": "integer", "minimum": -128, "maximum": 128, "description": "左右转角，度"},
                "pitch": {"type": "integer", "minimum": 0, "maximum": 90, "description": "俯仰角，度"},
                "speed": {"type": "integer", "minimum": 100, "maximum": 1000, "description": "转动速度"},
            },
        },
    },
    {
        "name": "robot.rgb_light",
        "description": "[蓝牙直连机器人] 设置桌上的 StackChan 机器人两侧 RGB 灯带颜色，立即执行。当用户说“让机器人亮红灯/灯光换成蓝色/关灯”时使用。颜色格式 #rrggbb，#000000 为关灯。前提：机器人需在 Dance 跳舞模式。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "left": {"type": "string", "description": "左侧灯带颜色，如 #ff0000"},
                "right": {"type": "string", "description": "右侧灯带颜色，如 #0000ff"},
            },
        },
    },
]


def tool_open_url(args):
    url = (args.get("url") or "").strip()
    if not url:
        return "没有提供网址"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    ok = webbrowser.open(url)
    return f"已在电脑浏览器中打开 {url}" if ok else f"尝试打开 {url}，但浏览器没有响应"


def tool_open_app(args):
    app = (args.get("app") or "").strip()
    cmd = APPS.get(app)
    if not cmd:
        return f"不支持的应用：{app}，可选：{', '.join(APPS)}"
    try:
        subprocess.Popen(cmd, close_fds=True)
        return f"已在电脑上启动 {app}"
    except Exception as e:
        return f"启动 {app} 失败：{e}"


def tool_search_video(args):
    keyword = (args.get("keyword") or "").strip()
    if not keyword:
        return "没有提供搜索关键词"
    site = (args.get("site") or "tencent").strip().lower()
    site_name, template = VIDEO_SITES.get(site, VIDEO_SITES["tencent"])
    url = template.format(q=urllib.parse.quote(keyword))
    ok = webbrowser.open(url)
    if ok:
        return f"已在电脑上打开{site_name}搜索“{keyword}”的结果页，请用户在屏幕上挑选想看的视频"
    return f"尝试打开{site_name}搜索页，但浏览器没有响应"


def tool_search_web(args):
    keyword = (args.get("keyword") or "").strip()
    if not keyword:
        return "没有提供搜索关键词"
    url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(keyword)
    ok = webbrowser.open(url)
    return f"已在电脑上打开“{keyword}”的百度搜索结果" if ok else "尝试打开百度搜索，但浏览器没有响应"


def tool_get_time(_args):
    now = datetime.now()
    week = "一二三四五六日"[now.weekday()]
    return f"电脑当前时间：{now:%Y年%m月%d日 %H:%M:%S}，星期{week}"


def tool_get_system_status(_args):
    return (
        f"计算机名：{platform.node()}\n"
        f"当前用户：{getpass.getuser()}\n"
        f"系统：{platform.system()} {platform.release()} ({platform.version()})\n"
        f"CPU 核心数：{os.cpu_count()}"
    )


VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
_KEYEVENTF_KEYUP = 2


def _press_media_key(vk, times=1):
    """模拟按媒体键，每按一次音量变化 2%"""
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)


def tool_set_volume(args):
    try:
        level = int(args.get("level", 50))
    except (TypeError, ValueError):
        return "音量百分比无效"
    level = max(0, min(100, level))
    _press_media_key(VK_VOLUME_DOWN, 50)  # 先归零
    _press_media_key(VK_VOLUME_UP, level // 2)
    return f"已把电脑音量调到约 {level}%"


def tool_volume_adjust(args):
    direction = (args.get("direction") or "up").strip().lower()
    if direction == "up":
        _press_media_key(VK_VOLUME_UP, 5)
        return "已把电脑音量调大约 10%"
    _press_media_key(VK_VOLUME_DOWN, 5)
    return "已把电脑音量调小约 10%"


def tool_mute(_args):
    _press_media_key(VK_VOLUME_MUTE)
    return "已切换电脑的静音状态"


def tool_lock_screen(_args):
    try:
        subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"], close_fds=True)
        return "已锁定电脑屏幕"
    except Exception as e:
        return f"锁屏失败：{e}"


def _call_expert_model(question, cfg):
    """调用 OpenAI 兼容的 chat completions 接口（阻塞式，需放入线程执行）。"""
    import urllib.error
    import urllib.request

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": cfg.get("system_prompt", "你是专家助手。")},
            {"role": "user", "content": question},
        ],
        "max_completion_tokens": cfg.get("max_completion_tokens", 1024),
    }
    if cfg.get("reasoning_effort"):
        payload["reasoning_effort"] = cfg["reasoning_effort"]
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg["api_key"],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout_seconds", 90)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return f"专家模型接口报错（HTTP {e.code}）：{body}"
    except Exception as e:
        return f"调用专家模型失败：{e}"
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        return "专家模型返回了无法解析的响应"


async def tool_ask_expert(args):
    question = (args.get("question") or "").strip()
    if not question:
        return "没有问题内容"
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f).get("expert_model", {})
    except Exception as e:
        return f"读取专家模型配置失败：{e}"
    if not cfg.get("api_key"):
        return "专家模型还没配置 API key，请先告诉主人在 config.json 里填入 Kimi 开放平台的密钥"
    log.info("转发给专家模型(%s): %s", cfg.get("model"), question[:80])
    answer = await asyncio.to_thread(_call_expert_model, question, cfg)
    log.info("专家模型回答: %s", answer[:120])
    return answer


# ------------------------------------------------ BLE 直连机器人

_ble_lock = asyncio.Lock()


async def _ble_send(char_uuid, payload):
    if not BLE_AVAILABLE:
        return None, "蓝牙功能未就绪（bleak 未安装）"
    async with _ble_lock:
        addr = await robot_ble.find_robot(quiet=True)
        if not addr:
            return None, "没找到机器人的蓝牙信号：请确认机器人已进入 Dance 跳舞模式（AI Agent 模式下蓝牙遥控是关闭的）"
        await robot_ble.ble_write(addr, char_uuid, payload)
    return addr, None


async def tool_robot_head_move(args):
    try:
        yaw = max(-128, min(128, int(args.get("yaw", 0))))
        pitch = max(0, min(90, int(args.get("pitch", 45))))
        speed = max(100, min(1000, int(args.get("speed", 600))))
    except (TypeError, ValueError):
        return "角度参数无效"
    payload = {
        "yawServo": {"angle": yaw * 10, "speed": speed},
        "pitchServo": {"angle": pitch * 10, "speed": speed},
    }
    try:
        _addr, err = await _ble_send(robot_ble.CHAR_MOTION if BLE_AVAILABLE else None, payload)
    except Exception as e:
        return f"蓝牙发送失败：{e}"
    if err:
        return err
    return f"已通过蓝牙让机器人转头：左右 {yaw}°，俯仰 {pitch}°"


async def tool_robot_rgb_light(args):
    left = (args.get("left") or "#ff0000").strip()
    right = (args.get("right") or left).strip()
    payload = {
        "leftRgbColor": left,
        "rightRgbColor": right,
        "leftRgbDuration": 0,
        "rightRgbDuration": 0,
    }
    try:
        _addr, err = await _ble_send(robot_ble.CHAR_RGB if BLE_AVAILABLE else None, payload)
    except Exception as e:
        return f"蓝牙发送失败：{e}"
    if err:
        return err
    return f"已通过蓝牙设置机器人灯带：左 {left} 右 {right}"


HANDLERS = {
    "pc.open_url": tool_open_url,
    "pc.search_video": tool_search_video,
    "pc.search_web": tool_search_web,
    "pc.set_volume": tool_set_volume,
    "pc.volume_adjust": tool_volume_adjust,
    "pc.mute": tool_mute,
    "pc.lock_screen": tool_lock_screen,
    "pc.open_app": tool_open_app,
    "pc.get_time": tool_get_time,
    "pc.get_system_status": tool_get_system_status,
    "ai.ask_expert": tool_ask_expert,
    "robot.head_move": tool_robot_head_move,
    "robot.rgb_light": tool_robot_rgb_light,
}

# ---------------------------------------------------------------- 公共套件聚合


class StdioMcpServer:
    """以 stdio 方式接入一个公共 MCP 服务器（能力套件），把它的工具合并进桥接。"""

    def __init__(self, name, command, allow_tools=None):
        self.name = name
        self.command = command
        self.allow_tools = set(allow_tools or [])
        self.process = None
        self.tools = []       # 过滤、加前缀后的工具定义
        self._pending = {}    # 请求 id -> Future
        self._next_id = 1
        self.ready = False

    async def start(self):
        self.tools = []
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=BASE_DIR,
            limit=16 * 1024 * 1024,  # 默认 64KB 会被截图类大消息撑爆管道
        )
        asyncio.create_task(self._read_loop())
        await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "stackchan-pc-bridge", "version": "0.6.1"},
        })
        await self._notify("notifications/initialized")
        result = await self._request("tools/list", {})
        for t in result.get("tools", []):
            orig = t["name"]
            if self.allow_tools and orig not in self.allow_tools:
                continue
            merged = dict(t)
            merged["name"] = f"{self.name}.{orig}"
            merged["description"] = "[操作这台Windows电脑] " + (t.get("description") or "")
            self.tools.append(merged)
        self.ready = True
        log.info("套件 %s 已接入，提供 %d 个工具", self.name, len(self.tools))

    async def _read_loop(self):
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                fut = self._pending.pop(msg.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(msg)
        except Exception:
            log.exception("套件 %s 读取循环异常", self.name)
        self.ready = False
        log.warning("套件 %s 的连接已断开", self.name)
        # 终止进程，让守护循环拉起一个干净的新实例
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except Exception:
                pass

    async def _request(self, method, params, timeout=120):
        rid = self._next_id
        self._next_id += 1
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        self.process.stdin.write(payload.encode("utf-8") + b"\n")
        await self.process.stdin.drain()
        msg = await asyncio.wait_for(fut, timeout)
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", "远程工具错误"))
        return msg.get("result", {})

    async def _notify(self, method):
        payload = json.dumps({"jsonrpc": "2.0", "method": method})
        self.process.stdin.write(payload.encode("utf-8") + b"\n")
        await self.process.stdin.drain()

    async def call_tool(self, orig_name, arguments):
        return await self._request("tools/call", {"name": orig_name, "arguments": arguments})

    def stop(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()


REMOTE_SERVERS = []

MAX_REMOTE_TEXT = 50000  # 单个套件工具返回文本上限，防止超大响应拖垮云端连接


def sanitize_remote_result(result):
    """清理套件返回：图片块替换为文字说明（云端无法利用 base64 图片且会撑爆连接），
    超长文本截断。"""
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return result
    cleaned = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            cleaned.append({
                "type": "text",
                "text": "[截图图片已省略：请使用返回的界面结构文字信息来定位元素]",
            })
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text") or ""
            if len(text) > MAX_REMOTE_TEXT:
                text = text[:MAX_REMOTE_TEXT] + f"\n…[内容过长已截断，原文 {len(text)} 字符]"
            cleaned.append({**block, "text": text})
        else:
            cleaned.append(block)
    return {**result, "content": cleaned}


def make_remote_handler(server, orig_name):
    async def _handler(args):
        try:
            result = await server.call_tool(orig_name, args)
            return sanitize_remote_result(result)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"套件工具 {orig_name} 执行失败: {e}"}], "isError": True}
    return _handler


async def supervise_server(server):
    """套件进程守护：退出后自动重启，保证套件工具长期可用。"""
    while True:
        if server.process:
            await server.process.wait()
        server.ready = False
        log.warning("套件 %s 已退出，5 秒后自动重启", server.name)
        await asyncio.sleep(5)
        try:
            await server.start()
        except Exception as e:
            log.warning("套件 %s 重启失败，30 秒后重试: %s", server.name, e)
            await asyncio.sleep(30)


async def start_remote_servers(config):
    for spec in config.get("mcp_servers", []):
        server = StdioMcpServer(spec["name"], spec["command"], spec.get("allow_tools"))
        try:
            await server.start()
        except Exception as e:
            log.warning("套件 %s 启动失败（跳过，不影响本地工具）: %s", spec.get("name"), e)
            continue
        REMOTE_SERVERS.append(server)
        for t in server.tools:
            orig = t["name"].split(".", 1)[1]
            HANDLERS[t["name"]] = make_remote_handler(server, orig)
        asyncio.create_task(supervise_server(server))


def all_tools():
    tools = list(TOOLS)
    for s in REMOTE_SERVERS:
        tools.extend(s.tools)
    return tools


# ---------------------------------------------------- 本地 HTTP 控制端点
# 供本机其他进程（如飞书监听进程）复用全部工具，仅监听 127.0.0.1。

async def execute_tool(name, arguments, raw=False):
    """统一执行入口。raw=True 时套件工具返回原始结果（保留截图图片块）。"""
    if raw:
        for s in REMOTE_SERVERS:
            prefix = s.name + "."
            if name.startswith(prefix):
                orig = name[len(prefix):]
                if not s.ready:
                    return {"content": [{"type": "text", "text": f"套件 {s.name} 尚未就绪"}], "isError": True}
                try:
                    return await s.call_tool(orig, arguments)
                except Exception as e:
                    return {"content": [{"type": "text", "text": f"套件工具 {orig} 执行失败: {e}"}], "isError": True}
    handler = HANDLERS.get(name)
    if not handler:
        return {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True}
    result = handler(arguments)
    if asyncio.iscoroutine(result):
        result = await result
    if isinstance(result, dict):
        return result
    return {"content": [{"type": "text", "text": result}], "isError": False}


class _LocalHttpHandler(BaseHTTPRequestHandler):
    loop = None

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            idle = None
            if LAST_MCP_ACTIVITY:
                idle = int(time.time() - LAST_MCP_ACTIVITY)
            self._send(200, {
                "ok": True,
                "tools": len(all_tools()),
                "last_mcp_activity": LAST_MCP_ACTIVITY or None,
                "mcp_idle_seconds": idle,
                "pending_inbox": pending_inbox_count(),
            })
        elif self.path == "/tools":
            self._send(200, {"tools": all_tools()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/call":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            name = payload.get("name", "")
            arguments = payload.get("arguments") or {}
            raw = bool(payload.get("raw"))
            log.info("本地 HTTP 调用: %s 参数: %s", name, json.dumps(arguments, ensure_ascii=False))
            fut = asyncio.run_coroutine_threadsafe(
                execute_tool(name, arguments, raw), type(self).loop
            )
            result = fut.result(timeout=180)
            self._send(200, result)
        except Exception as e:
            self._send(500, {"content": [{"type": "text", "text": f"本地调用失败: {e}"}], "isError": True})


def start_local_http(loop, port):
    _LocalHttpHandler.loop = loop
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _LocalHttpHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info("本地 HTTP 控制端点已启动: http://127.0.0.1:%d", port)


# ---------------------------------------------------------------- MCP 协议


def make_result(req_id, result):
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def make_error(req_id, code, message):
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


async def handle_request(msg):
    """处理一个 JSON-RPC 请求，返回需要发回的 JSON 字符串或 None。"""
    touch_mcp_activity()
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if req_id is None:  # 通知类消息无需回复
        log.info("收到通知: %s", method)
        return None

    if method == "initialize":
        client_version = params.get("protocolVersion", "2024-11-05")
        log.info("MCP initialize, 客户端协议版本: %s", client_version)
        return make_result(req_id, {
            "protocolVersion": client_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "stackchan-pc-bridge", "version": "0.6.1"},
        })

    if method == "ping":
        return make_result(req_id, {})

    if method == "tools/list":
        tools = all_tools()
        log.info("MCP tools/list，返回 %d 个工具", len(tools))
        return make_result(req_id, {"tools": tools})

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        log.info("MCP tools/call: %s 参数: %s", name, json.dumps(arguments, ensure_ascii=False))
        handler = HANDLERS.get(name)
        if not handler:
            return make_error(req_id, -32602, f"未知工具: {name}")
        try:
            result = handler(arguments)
            if asyncio.iscoroutine(result):
                result = await result
            inbox = drain_robot_inbox()
            if isinstance(result, dict):  # 远程套件：content/isError 原样透传
                if inbox:  # 飞书指令注入第一个文本块
                    content = result.get("content") or []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            block["text"] = inbox + (block.get("text") or "")
                            break
                    else:
                        content.insert(0, {"type": "text", "text": inbox})
                    result["content"] = content
                log.info("套件工具 %s 执行完成", name)
                return make_result(req_id, result)
            if inbox:
                result = inbox + result
            log.info("工具执行结果: %s", result)
            return make_result(req_id, {
                "content": [{"type": "text", "text": result}],
                "isError": False,
            })
        except Exception as e:
            log.exception("工具 %s 执行异常", name)
            return make_result(req_id, {
                "content": [{"type": "text", "text": f"工具执行异常: {e}"}],
                "isError": True,
            })

    log.warning("未支持的方法: %s", method)
    return make_error(req_id, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------- 主循环


async def serve(endpoint):
    async with websockets.connect(endpoint, ping_interval=20, ping_timeout=30, max_size=4 * 1024 * 1024) as ws:
        log.info("已连接小智 MCP 接入点，等待请求…")
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("收到非 JSON 消息: %.200s", raw)
                continue
            response = await handle_request(msg)
            if response is not None:
                await ws.send(response)


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    endpoint = config["endpoint"]
    wait = config.get("reconnect_min_seconds", 5)
    wait_max = config.get("reconnect_max_seconds", 60)

    log.info("桥接启动，接入点: %s…", endpoint[:60])

    async def runner():
        await start_remote_servers(config)
        start_local_http(asyncio.get_running_loop(), config.get("local_http_port", 8766))
        backoff = wait
        while True:
            try:
                await serve(endpoint)
                backoff = wait  # 正常断开后重置退避
                log.warning("连接已断开，%d 秒后重连…", backoff)
            except Exception as e:
                log.warning("连接异常: %s，%d 秒后重连…", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, wait_max)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
