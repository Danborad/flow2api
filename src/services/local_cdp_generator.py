"""Clean reCAPTCHA Enterprise token minting & live cookie sync via local Chrome CDP.

Bypasses Google Flow frontend's kUa extension_hijack_detected poison pill by patching
Object.assign before invoking grecaptcha.enterprise.execute.
Does NOT click DOM or scrape DOM images — all generation goes through pure HTTP RPC.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import urllib.request
import websockets

from ..core.logger import debug_logger


async def mint_clean_recaptcha_via_local_cdp(
    action: str = "IMAGE_GENERATION",
    ports: tuple[int, ...] = (9222, 9224),
) -> Optional[Dict[str, Any]]:
    """通过本地 Chrome CDP 端口注入毒丸净化补丁并获取 100% 纯净的 reCAPTCHA Enterprise Token。"""
    site_key = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
    for port in ports:
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.5)
            tabs = json.loads(req.read().decode("utf-8"))
        except Exception:
            continue

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
            continue

        ws_url = target["webSocketDebuggerUrl"]
        try:
            async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
                js_code = f"""
                (async () => {{
                    if (!window.__origObjectAssign) {{
                        window.__origObjectAssign = Object.assign;
                        Object.assign = function(target, ...sources) {{
                            const cleanSources = sources.map(s => (s && typeof s === "object" && s.action === "extension_hijack_detected") ? {{}} : s);
                            return window.__origObjectAssign.call(this, target, ...cleanSources);
                        }};
                    }}
                    if (!(window.grecaptcha && window.grecaptcha.enterprise)) {{
                        return null;
                    }}
                    const token = await window.grecaptcha.enterprise.execute("{site_key}", {{ action: "{action}" }});
                    const wiz = window.WIZ_global_data || {{}};
                    return {{
                        token: token || "",
                        user_agent: navigator.userAgent || "",
                        wiz_at: wiz.SNlM0e || "",
                        wiz_sid: wiz.FdrFJe || "",
                        wiz_bl: wiz.cfb2h || "",
                        email: wiz.oPEP7c || "",
                    }};
                }})()
                """
                await ws.send(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": js_code,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }))
                res = json.loads(await ws.recv())
                val = res.get("result", {}).get("result", {}).get("value")
                if isinstance(val, dict) and val.get("token"):
                    # 顺便获取该浏览器最新的 cookies
                    try:
                        await ws.send(json.dumps({
                            "id": 2,
                            "method": "Network.getCookies",
                            "params": {"urls": ["https://flow.google.com/", "https://google.com/"]},
                        }))
                        c_res = json.loads(await ws.recv())
                        cookies = c_res.get("result", {}).get("cookies")
                        if cookies:
                            val["google_cookies"] = json.dumps(cookies, ensure_ascii=False, separators=(",", ":"))
                    except Exception:
                        pass
                    return val
        except Exception as e:
            debug_logger.log_info(f"[Clean CDP Captcha] Port {port} 跳过: {e}")
            continue

    return None
