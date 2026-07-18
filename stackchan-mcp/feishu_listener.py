# -*- coding: utf-8 -*-
"""
飞书 -> 这台电脑 的遥控监听进程
================================
原理：通过飞书开放平台 WebSocket 长连接接收发给应用机器人的消息，
用 Kimi K3 把自然语言映射成桥接工具调用，走 bridge.py 的本地 HTTP
端点（127.0.0.1:8766）执行，结果（文字/截图）回传到飞书。

运行：.venv/Scripts/python.exe feishu_listener.py
自检：.venv/Scripts/python.exe feishu_listener.py --selftest "打开电脑上的B站"
日志：同目录 feishu_listener.log
配置：config.json 的 feishu / expert_model 两段
"""

import base64
import json
import logging
import os
import sys
import tempfile
import threading
import time
import urllib.request

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "feishu_listener.log")
ROBOT_INBOX = os.path.join(BASE_DIR, "robot_inbox.jsonl")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("feishu")

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)
FEISHU_CFG = CONFIG.get("feishu", {})
EXPERT_CFG = CONFIG.get("expert_model", {})
BRIDGE_HTTP = FEISHU_CFG.get("bridge_http", "http://127.0.0.1:8766")

APP_ID = FEISHU_CFG.get("app_id", "")
APP_SECRET = FEISHU_CFG.get("app_secret", "")
ALLOWED_OPEN_IDS = set(FEISHU_CFG.get("allowed_open_ids") or [])

# ---------------------------------------------------------------- 飞书发送端

lark_client = (
    lark.Client.builder()
    .app_id(APP_ID)
    .app_secret(APP_SECRET)
    .log_level(lark.LogLevel.WARNING)
    .build()
)


def send_text(chat_id, text):
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("text")
        .content(json.dumps({"text": text}, ensure_ascii=False))
        .build()
    )
    req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    resp = lark_client.im.v1.message.create(req)
    if not resp.success():
        log.warning("发送飞书文本失败: %s %s", resp.code, resp.msg)


def send_image(chat_id, image_bytes):
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(image_bytes)
            path = tmp.name
        with open(path, "rb") as img:
            body = CreateImageRequestBody.builder().image_type("message").image(img).build()
            req = CreateImageRequest.builder().request_body(body).build()
            resp = lark_client.im.v1.image.create(req)
        if not resp.success():
            log.warning("上传飞书图片失败: %s %s", resp.code, resp.msg)
            return False
        image_key = resp.data.image_key
        body2 = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("image")
            .content(json.dumps({"image_key": image_key}))
            .build()
        )
        req2 = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body2).build()
        resp2 = lark_client.im.v1.message.create(req2)
        if not resp2.success():
            log.warning("发送飞书图片消息失败: %s %s", resp2.code, resp2.msg)
            return False
        return True
    except Exception as e:
        log.warning("发送图片异常: %s", e)
        return False
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------- 桥接调用

_tools_cache = {"at": 0, "brief": "（工具清单暂未加载）"}


def get_tools_brief():
    if time.time() - _tools_cache["at"] < 600:
        return _tools_cache["brief"]
    try:
        with urllib.request.urlopen(BRIDGE_HTTP + "/tools", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        lines = []
        for t in data.get("tools", []):
            schema = t.get("inputSchema") or {}
            props = {k: v.get("description", "") for k, v in (schema.get("properties") or {}).items()}
            lines.append(json.dumps({
                "name": t.get("name"),
                "description": (t.get("description") or "")[:200],
                "required": schema.get("required") or [],
                "properties": props,
            }, ensure_ascii=False))
        _tools_cache["brief"] = "\n".join(lines)
        _tools_cache["at"] = time.time()
        log.info("工具清单已刷新，共 %d 个", len(lines))
    except Exception as e:
        log.warning("拉取工具清单失败: %s", e)
    return _tools_cache["brief"]


def call_bridge_tool(name, arguments):
    payload = json.dumps({"name": name, "arguments": arguments, "raw": True}).encode("utf-8")
    req = urllib.request.Request(
        BRIDGE_HTTP + "/call", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- K3 指令解析

MAPPER_PROMPT = """你是飞书遥控指令解析器。主人在飞书里发消息，遥控一台 Windows 电脑（桌上 StackChan 机器人的桥接电脑）。

可用工具（每行一个 JSON，含名称/描述/必填参数/参数说明）：
%s

请判断主人意图，严格输出一行 JSON（不要 markdown 代码块、不要任何额外文字）：
1. 指挥电脑、操作屏幕/程序/文件、查电脑状态、截屏等 -> {"action":"tool","name":"工具名","arguments":{参数}}
2. 让机器人**做动作**（转头、抬头、摇头、亮灯、灯光颜色等）-> {"action":"tool","name":"robot.head_move 或 robot.rgb_light","arguments":{参数}}（蓝牙直连，立即执行；若工具返回说连不上，如实转告主人机器人不在 Dance 模式）
3. 让机器人**说话/提醒/拍照/传话**（提到"机器人"/"StackChan"且不是动作）-> {"action":"robot","instruction":"改写成对机器人的直接指令"}（进队列，下次对话时执行）
4. 闲聊、知识问答、无法映射到工具的请求 -> {"action":"chat","reply":"你的回答，100字以内口语化"}
5. 意图不清 -> {"action":"chat","reply":"反问主人一句想让我做什么"}

注意：arguments 必须符合工具的参数要求；主人没指定的参数合理取默认值。机器人本体不能被远程唤醒，robot 指令会在它下次对话时执行。"""


def map_to_action(user_text):
    prompt = MAPPER_PROMPT % get_tools_brief()
    payload = {
        "model": EXPERT_CFG.get("model", "k3"),
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text},
        ],
        "max_completion_tokens": 512,
    }
    if EXPERT_CFG.get("reasoning_effort"):
        payload["reasoning_effort"] = EXPERT_CFG["reasoning_effort"]
    req = urllib.request.Request(
        EXPERT_CFG["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + EXPERT_CFG["api_key"],
        },
    )
    with urllib.request.urlopen(req, timeout=EXPERT_CFG.get("timeout_seconds", 90)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"].strip()
    # 容错：模型可能用 ```json 包裹
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ---------------------------------------------------------------- 消息处理

_processed = {}
_processed_lock = threading.Lock()


def is_duplicate(message_id):
    with _processed_lock:
        now = time.time()
        for mid, ts in list(_processed.items()):
            if now - ts > 3600:
                _processed.pop(mid, None)
        if message_id in _processed:
            return True
        _processed[message_id] = now
        return False


def handle_user_text(user_text):
    """核心处理：返回 [("text", str), ("image", bytes), ...]"""
    try:
        action = map_to_action(user_text)
    except Exception as e:
        log.warning("K3 解析失败: %s", e)
        return [("text", "我没能理解这条指令（指令解析出错），换种说法试试？")]
    log.info("指令解析结果: %s", json.dumps(action, ensure_ascii=False))

    if action.get("action") == "chat":
        return [("text", action.get("reply") or "我在，想让我控制电脑做什么？")]

    if action.get("action") == "robot":
        instruction = (action.get("instruction") or "").strip()
        if not instruction:
            return [("text", "想让我转告机器人做什么？")]
        try:
            with open(ROBOT_INBOX, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "instruction": instruction}, ensure_ascii=False) + "\n")
            log.info("飞书指令已入队给机器人: %s", instruction)
            return [("text", f"已转达给机器人：{instruction}\n（它无法被远程唤醒，下次你喊它说话时会先播报/照做这条指令）")]
        except Exception as e:
            return [("text", f"写入机器人指令队列失败：{e}")]

    name = action.get("name", "")
    arguments = action.get("arguments") or {}
    try:
        result = call_bridge_tool(name, arguments)
    except Exception as e:
        log.warning("调用桥接失败: %s", e)
        return [("text", f"执行失败：电脑上的桥接服务没响应（{e}）。可能电脑关机或桥接挂了。")]

    parts = []
    is_error = result.get("isError")
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            parts.append(("text", block["text"]))
        elif block.get("type") == "image" and block.get("data"):
            try:
                parts.append(("image", base64.b64decode(block["data"])))
            except Exception:
                pass
    if not parts:
        parts = [("text", "执行完了，但没有返回内容。")]
    if is_error:
        parts.insert(0, ("text", "⚠️ 执行出现异常："))
    return parts


def on_message(data: P2ImMessageReceiveV1):
    try:
        msg = data.event.message
        sender = data.event.sender
        open_id = sender.sender_id.open_id if sender and sender.sender_id else ""
        log.info("收到飞书消息: chat=%s type=%s sender=%s", msg.chat_id, msg.message_type, open_id)

        if ALLOWED_OPEN_IDS and open_id not in ALLOWED_OPEN_IDS:
            send_text(msg.chat_id, "抱歉，我只执行主人的指令 🙅")
            log.warning("非授权用户 %s 的指令已拒绝", open_id)
            return
        if is_duplicate(msg.message_id):
            return
        if msg.message_type != "text":
            send_text(msg.chat_id, "我现在只看得懂文字消息哦")
            return

        content = json.loads(msg.content or "{}")
        text = (content.get("text") or "").replace("@_user_1", "").strip()
        if not text:
            return

        parts = handle_user_text(text)
        for kind, payload in parts:
            if kind == "text":
                send_text(msg.chat_id, payload)
            else:
                if not send_image(msg.chat_id, payload):
                    send_text(msg.chat_id, "（截图发送失败）")
    except Exception:
        log.exception("处理飞书消息异常")


# ---------------------------------------------------------------- 入口


def selftest(text):
    print(">>> 指令:", text)
    for kind, payload in handle_user_text(text):
        if kind == "text":
            print("<<< 文字:", payload)
        else:
            out = os.path.join(BASE_DIR, "selftest_shot.png")
            with open(out, "wb") as f:
                f.write(payload)
            print("<<< 图片已保存:", out)


def main():
    if not APP_ID or not APP_SECRET:
        log.error("config.json 里 feishu.app_id / app_secret 未配置")
        sys.exit(1)
    get_tools_brief()
    builder = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message)
    try:  # 已读事件无需处理，注册空处理器避免 SDK 报错刷日志
        builder = builder.register_p2_im_message_message_read_v1(lambda _d: None)
    except Exception:
        pass
    handler = builder.build()
    ws_client = lark.ws.Client(
        APP_ID, APP_SECRET,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    log.info("飞书长连接启动，等待消息…（允许用户: %s）", ALLOWED_OPEN_IDS or "所有人")
    ws_client.start()  # 阻塞，SDK 内部自动重连


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        selftest(" ".join(sys.argv[2:]))
    else:
        main()
