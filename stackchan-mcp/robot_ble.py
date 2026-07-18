# -*- coding: utf-8 -*-
"""
StackChan BLE 直连控制（无需云、无需配对）
==========================================
原理：官方固件在 DANCE 应用里会启动一个无配对的 BLE GATT 服务，
电脑用 bleak 直连写 JSON 即可控制舵机/表情/RGB。

前提：机器人在主界面打开 **Dance 跳舞** 应用（AI Agent 模式下 BLE 服务不启动）。

用法：
  python robot_ble.py scan                          # 只扫描，确认能发现机器人
  python robot_ble.py move --yaw 300 --pitch 450    # 转头（单位 0.1°）
  python robot_ble.py rgb --left "#ff0000" --right "#0000ff"
  python robot_ble.py demo                          # 左右摇头 + 回中 + 灯光 demo

协议（来自 firmware/main/stackchan/json/json_helper.cpp）：
  Motion: {"yawServo":{"angle":-1280~1280,"speed":0~1000},
           "pitchServo":{"angle":0~900,"speed":0~1000}}
  RGB:    {"leftRgbColor":"#rrggbb","rightRgbColor":"#rrggbb","leftRgbDuration":秒}
"""

import argparse
import asyncio
import json
import sys

import bleak  # noqa: F401  仅确认安装
from bleak import BleakClient, BleakScanner

SERVICE_UUID = "e2e5e5e0-1234-5678-1234-56789abcdef0"
CHAR_MOTION = "e2e5e5e1-1234-5678-1234-56789abcdef0"
CHAR_AVATAR = "e2e5e5e2-1234-5678-1234-56789abcdef0"
CHAR_RGB = "e2e5e5e4-1234-5678-1234-56789abcdef0"


async def find_robot(timeout=12, quiet=False):
    """扫描广播了 StackChan 服务 UUID 的设备。"""
    if not quiet:
        print(f"扫描中（{timeout}s）…")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for addr, (dev, adv) in devices.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if SERVICE_UUID in uuids:
            if not quiet:
                print(f"✅ 发现机器人: {dev.name} [{addr}] RSSI={adv.rssi}")
            return addr
        # 兜底：按名字找
        if dev.name and "stack" in dev.name.lower():
            if not quiet:
                print(f"✅ 按名称发现疑似机器人: {dev.name} [{addr}] RSSI={adv.rssi} 服务={uuids}")
            return addr
    if not quiet:
        print("❌ 没发现机器人。确认：①机器人已打开 Dance 应用 ②距离不远 ③电脑蓝牙已开")
    return None


async def ble_write(addr, char_uuid, payload):
    """连接并写入一段 JSON 指令（供其他模块复用，不打印）。"""
    async with BleakClient(addr, timeout=20) as client:
        await client.write_gatt_char(char_uuid, json.dumps(payload).encode("utf-8"), response=False)


async def write_json(addr, char_uuid, payload):
    await ble_write(addr, char_uuid, payload)
    print(f"已发送 -> {char_uuid[4:8]}: {json.dumps(payload)}")


async def cmd_scan(_args):
    await find_robot()


async def cmd_move(args):
    addr = await find_robot()
    if not addr:
        return
    payload = {
        "yawServo": {"angle": args.yaw, "speed": args.speed},
        "pitchServo": {"angle": args.pitch, "speed": args.speed},
    }
    await write_json(addr, CHAR_MOTION, payload)
    print(f"转头完成：yaw={args.yaw / 10}° pitch={args.pitch / 10}°")


async def cmd_rgb(args):
    addr = await find_robot()
    if not addr:
        return
    payload = {"leftRgbColor": args.left, "rightRgbColor": args.right, "leftRgbDuration": 0, "rightRgbDuration": 0}
    await write_json(addr, CHAR_RGB, payload)
    print("灯光已设置")


async def cmd_demo(_args):
    addr = await find_robot()
    if not addr:
        return
    async with BleakClient(addr, timeout=20) as client:
        async def move(yaw, pitch, speed=600):
            payload = {
                "yawServo": {"angle": yaw, "speed": speed},
                "pitchServo": {"angle": pitch, "speed": speed},
            }
            await client.write_gatt_char(CHAR_MOTION, json.dumps(payload).encode(), response=False)

        async def rgb(left, right):
            payload = {"leftRgbColor": left, "rightRgbColor": right, "leftRgbDuration": 0, "rightRgbDuration": 0}
            await client.write_gatt_char(CHAR_RGB, json.dumps(payload).encode(), response=False)

        print("demo：左 -> 右 -> 抬头 -> 回中，配灯光")
        await rgb("#ff0000", "#ff0000")
        await move(600, 450)
        await asyncio.sleep(1.2)
        await rgb("#0000ff", "#0000ff")
        await move(-600, 450)
        await asyncio.sleep(1.2)
        await rgb("#00ff00", "#00ff00")
        await move(0, 200)
        await asyncio.sleep(1.2)
        await move(0, 450)
        await rgb("#000000", "#000000")
    print("demo 完成 ✅ 机器人归位")


def main():
    parser = argparse.ArgumentParser(description="StackChan BLE 直连控制")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="扫描机器人")

    p_move = sub.add_parser("move", help="转头/俯仰（单位 0.1°）")
    p_move.add_argument("--yaw", type=int, default=0, help="-1280~1280，左右")
    p_move.add_argument("--pitch", type=int, default=450, help="0~900，俯仰")
    p_move.add_argument("--speed", type=int, default=600, help="0~1000")

    p_rgb = sub.add_parser("rgb", help="设置两侧 RGB 灯颜色")
    p_rgb.add_argument("--left", default="#ff0000")
    p_rgb.add_argument("--right", default="#ff0000")

    sub.add_parser("demo", help="摇头+灯光演示")

    args = parser.parse_args()
    handlers = {"scan": cmd_scan, "move": cmd_move, "rgb": cmd_rgb, "demo": cmd_demo}
    asyncio.run(handlers[args.cmd](args))


if __name__ == "__main__":
    sys.exit(main())
