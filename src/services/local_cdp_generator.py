"""Direct Chrome DevTools Protocol automation for Google Flow native generation.

Used for local Chrome instances with --remote-debugging-port (e.g. 9222, 9224).
Provides trusted physical key and mouse events directly to Google Flow canvas.
"""

import asyncio
import json
import time
from typing import Optional
import urllib.request
import websockets

from ..core.logger import debug_logger


async def execute_local_cdp_generate(
    prompt: str,
    port: int = 9224,
    timeout: int = 75,
) -> Optional[str]:
    """通过本地 Chrome CDP 端口直接驱动 Google Flow 页面原生生成并返回图片直链。"""
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2)
        tabs = json.loads(req.read().decode("utf-8"))
    except Exception as e:
        debug_logger.log_info(f"[Local CDP] 端口 {port} 不可用或未启动: {e}")
        return None

    target = None
    for t in tabs:
        url = t.get("url", "")
        if "flow.google.com/project" in url and "404" not in url:
            target = t
            break

    if not target:
        for t in tabs:
            url = t.get("url", "")
            if "flow.google.com" in url and "404" not in url:
                target = t
                break

    if not target or not target.get("webSocketDebuggerUrl"):
        debug_logger.log_info(f"[Local CDP] 端口 {port} 未找到打开的 Flow 页面")
        return None

    ws_url = target["webSocketDebuggerUrl"]
    debug_logger.log_info(f"[Local CDP] 连接端口 {port} 页面 {target['id']}: {target.get('title')}")

    try:
        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
            # 1. 获取已有图片路径
            msg = {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": """
                    (() => {
                        const imgs = Array.from(document.querySelectorAll('img[src*="flow-content.google/image/"]')).map(i => i.src.split('?')[0]);
                        return Array.from(new Set(imgs));
                    })()
                    """,
                    "returnByValue": True,
                },
            }
            await ws.send(json.dumps(msg))
            res = json.loads(await ws.recv())
            existing = set(res.get("result", {}).get("result", {}).get("value") or [])

            # 2. 定位输入框坐标并物理点击聚焦
            msg = {
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": """
                    (() => {
                        const el = document.querySelector('flow-prompt-box .ProseMirror') || document.querySelector('.ProseMirror');
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                    })()
                    """,
                    "returnByValue": True,
                },
            }
            await ws.send(json.dumps(msg))
            res = json.loads(await ws.recv())
            coords = res.get("result", {}).get("result", {}).get("value")
            if not coords:
                debug_logger.log_warning("[Local CDP] 未能定位到 ProseMirror 输入框坐标")
                return None

            # 物理点击聚焦
            await ws.send(json.dumps({
                "id": 3,
                "method": "Input.dispatchMouseEvent",
                "params": {"type": "mousePressed", "x": coords["x"], "y": coords["y"], "button": "left", "clickCount": 1},
            }))
            await ws.recv()
            await ws.send(json.dumps({
                "id": 4,
                "method": "Input.dispatchMouseEvent",
                "params": {"type": "mouseReleased", "x": coords["x"], "y": coords["y"], "button": "left", "clickCount": 1},
            }))
            await ws.recv()

            # 3. 物理键盘逐字输入
            for ch in prompt:
                await ws.send(json.dumps({
                    "id": 5,
                    "method": "Input.dispatchKeyEvent",
                    "params": {"type": "keyDown", "text": ch, "unmodifiedText": ch},
                }))
                await ws.recv()
                await ws.send(json.dumps({
                    "id": 6,
                    "method": "Input.dispatchKeyEvent",
                    "params": {"type": "keyUp", "text": ch, "unmodifiedText": ch},
                }))
                await ws.recv()
                await asyncio.sleep(0.015)

            await asyncio.sleep(0.3)

            # 4. 物理回车
            await ws.send(json.dumps({
                "id": 7,
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "rawKeyDown", "windowsVirtualKeyCode": 13, "unmodifiedText": "\r", "text": "\r", "key": "Enter", "code": "Enter"},
            }))
            await ws.recv()
            await ws.send(json.dumps({
                "id": 8,
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "keyUp", "windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter"},
            }))
            await ws.recv()
            debug_logger.log_info(f"[Local CDP] 物理回车已发送，开始监听图片生成...")

            # 5. 轮询等待新图片
            deadline = time.time() + timeout
            while time.time() < deadline:
                await asyncio.sleep(1.0)
                msg = {
                    "id": 9,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": """
                        (() => {
                            const imgs = Array.from(document.querySelectorAll('img[src*="flow-content.google/image/"]')).map(i => i.src);
                            return imgs;
                        })()
                        """,
                        "returnByValue": True,
                    },
                }
                await ws.send(json.dumps(msg))
                res = json.loads(await ws.recv())
                current = res.get("result", {}).get("result", {}).get("value") or []
                for src in current:
                    clean = src.split("?")[0]
                    if clean not in existing:
                        debug_logger.log_info(f"[Local CDP] 捕获到新生成的图片: {src}")
                        return src

            debug_logger.log_warning(f"[Local CDP] 等待生成超时 ({timeout}s)")
            return None
    except Exception as e:
        debug_logger.log_warning(f"[Local CDP] 执行异常: {e}")
        return None
