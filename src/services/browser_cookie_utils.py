"""Helpers for normalizing, parsing, scoping, and validating browser cookies."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

DEFAULT_COOKIE_URL = "https://flow.google.com/"
DEFAULT_GOOGLE_COOKIE_TARGET_URLS = (
    "https://flow.google.com/",
    "https://www.google.com/",
)
_COOKIE_ATTRIBUTE_KEYS = {
    "path",
    "domain",
    "expires",
    "max-age",
    "secure",
    "httponly",
    "samesite",
    "priority",
    "partitioned",
}
_SESSION_TOKEN_COOKIE_NAMES = (
    "__Secure-next-auth.session-token",
    "next-auth.session-token",
)

# 必须存在于 flow.google.com / projects 作用域的 Cookie 组
FLOW_AUTH_SESSION_NAMES = {"OSID", "__Secure-OSID"}
GOOGLE_AUTH_ACCOUNT_NAMES = {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
}


def normalize_cookie_header_text(raw_cookie: Optional[str]) -> str:
    value = str(raw_cookie or "").strip()
    if not value:
        return ""
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    return value


def normalize_cookie_storage_text(raw_cookie: Any) -> str:
    if raw_cookie is None:
        return ""
    if isinstance(raw_cookie, (dict, list)):
        try:
            return json.dumps(raw_cookie, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return ""
    value = str(raw_cookie).strip()
    if not value:
        return ""
    if value[:1] in {"[", "{"}:
        try:
            payload = json.loads(value)
        except Exception:
            payload = None
        if isinstance(payload, (dict, list)):
            try:
                return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                return value
    return normalize_cookie_header_text(value)


def _normalize_same_site(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if raw == "strict":
        return "Strict"
    if raw == "none":
        return "None"
    if raw == "lax":
        return "Lax"
    return None


def _build_cookie_from_mapping(raw_cookie: Dict[str, Any], default_url: str) -> Optional[Dict[str, Any]]:
    name = str(raw_cookie.get("name") or "").strip()
    if not name:
        return None
    # 跳过 partitioned cookie
    if bool(raw_cookie.get("partitioned")) or bool(raw_cookie.get("partitionKey")):
        return None

    cookie: Dict[str, Any] = {
        "name": name,
        "value": str(raw_cookie.get("value") or ""),
    }
    domain = str(raw_cookie.get("domain") or "").strip()
    url = str(raw_cookie.get("url") or "").strip()
    path = str(raw_cookie.get("path") or "/").strip() or "/"

    if url:
        cookie["url"] = url
    elif domain:
        cookie["domain"] = domain
        cookie["path"] = path
    else:
        cookie["url"] = default_url
        cookie["path"] = path

    host_only = raw_cookie.get("hostOnly")
    if host_only is not None:
        cookie["hostOnly"] = bool(host_only)
    elif domain:
        cookie["hostOnly"] = not domain.startswith(".")

    same_site = _normalize_same_site(raw_cookie.get("sameSite"))
    if same_site:
        cookie["sameSite"] = same_site

    expires = raw_cookie.get("expirationDate") if "expirationDate" in raw_cookie else raw_cookie.get("expires")
    if expires not in (None, ""):
        try:
            cookie["expirationDate"] = float(expires)
        except Exception:
            pass

    for key in ("secure", "httpOnly"):
        if key in raw_cookie:
            cookie[key] = bool(raw_cookie.get(key))

    if name.startswith("__Secure-") or name.startswith("__Host-"):
        cookie["secure"] = True
    if name.startswith("__Host-"):
        cookie.pop("domain", None)
        cookie["hostOnly"] = True
        cookie["path"] = "/"
        if "url" not in cookie:
            cookie["url"] = default_url

    return cookie


def parse_browser_cookie_payload(raw_cookie: Any, default_url: str = DEFAULT_COOKIE_URL) -> List[Dict[str, Any]]:
    normalized = normalize_cookie_storage_text(raw_cookie)
    if not normalized:
        return []
    if normalized[:1] in {"[", "{"}:
        try:
            payload = json.loads(normalized)
        except Exception:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
            payload = payload["cookies"]
        elif isinstance(payload, dict):
            payload = [payload]
        if isinstance(payload, list):
            cookies: List[Dict[str, Any]] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                normalized_item = _build_cookie_from_mapping(item, default_url)
                if normalized_item:
                    cookies.append(normalized_item)
            return cookies

    cookies = []
    for chunk in normalized.split(";"):
        segment = chunk.strip()
        if not segment or "=" not in segment:
            continue
        name, value = segment.split("=", 1)
        cookie_name = name.strip()
        if not cookie_name or cookie_name.lower() in _COOKIE_ATTRIBUTE_KEYS:
            continue
        cookie: Dict[str, Any] = {
            "name": cookie_name,
            "value": value.strip(),
            "url": default_url,
            "path": "/",
            "secure": default_url.startswith("https://"),
            "hostOnly": True,
        }
        if cookie_name.startswith("__Secure-") or cookie_name.startswith("__Host-"):
            cookie["secure"] = True
        if cookie_name.startswith("__Host-"):
            cookie["path"] = "/"
        cookies.append(cookie)
    return cookies


def _matches_url_scope(cookie: Dict[str, Any], parsed_url, now: float) -> bool:
    # 检查过期
    exp = cookie.get("expirationDate") or cookie.get("expires")
    if exp is not None:
        try:
            if float(exp) < now:
                return False
        except Exception:
            pass

    # 检查安全通道
    if cookie.get("secure") and parsed_url.scheme != "https":
        return False

    hostname = (parsed_url.hostname or "").lower()

    # 检查域名
    cookie_domain = str(cookie.get("domain") or "").strip().lower()
    host_only = cookie.get("hostOnly")
    if not cookie_domain and cookie.get("url"):
        try:
            c_parsed = urlparse(cookie["url"])
            cookie_domain = (c_parsed.hostname or "").lower()
            host_only = True
        except Exception:
            pass

    if cookie_domain:
        dot_prefix = cookie_domain.startswith(".")
        clean_domain = cookie_domain.lstrip(".")
        if host_only or (not dot_prefix and not host_only):
            if hostname != clean_domain:
                return False
        else:
            if hostname != clean_domain and not hostname.endswith("." + clean_domain):
                return False

    # 检查路径
    cookie_path = str(cookie.get("path") or "/").strip() or "/"
    req_path = parsed_url.path or "/"
    if not req_path.startswith(cookie_path):
        if not (cookie_path.endswith("/") and req_path == cookie_path.rstrip("/")):
            return False

    return True


def cookie_pairs_for_url(raw_cookie: Any, target_url: str) -> List[Tuple[str, str]]:
    """根据目标 URL 规则严格筛选匹配的 Cookie (name, value) 列表，长 Path 优先，保留重复项。"""
    cookies = parse_browser_cookie_payload(raw_cookie, default_url=target_url)
    if not cookies:
        return []

    parsed_url = urlparse(target_url)
    now = time.time()

    matched: List[Dict[str, Any]] = []
    for c in cookies:
        if _matches_url_scope(c, parsed_url, now):
            matched.append(c)

    # 排序：长 path 优先，同 path 保持原始稳定顺序
    matched.sort(key=lambda x: len(str(x.get("path") or "/")), reverse=True)

    return [(str(c["name"]), str(c.get("value") or "")) for c in matched]


def serialize_cookie_header_for_url(raw_cookie: Any, target_url: str) -> str:
    """序列化适用于 target_url 请求的 Cookie 请求头字符串。"""
    pairs = cookie_pairs_for_url(raw_cookie, target_url)
    if not pairs:
        return ""
    return "; ".join(f"{name}={val}" for name, val in pairs)


def validate_flow_cookie_storage(raw_cookie: Any) -> str:
    """验证对 https://flow.google.com/projects 筛选后的 Cookie 是否包含必需的会话组和账号组。
    
    验证通过时返回紧凑规范化后的 JSON 字符串；否则抛出 ValueError。
    """
    pairs = cookie_pairs_for_url(raw_cookie, "https://flow.google.com/projects")
    matched_names = {name for name, _ in pairs}

    has_session = bool(matched_names & FLOW_AUTH_SESSION_NAMES)
    has_account = bool(matched_names & GOOGLE_AUTH_ACCOUNT_NAMES)

    if not has_session:
        raise ValueError(
            f"缺少 Flow 会话 Cookie ({'/'.join(sorted(FLOW_AUTH_SESSION_NAMES))})，"
            f"已匹配到的 Cookie: {', '.join(sorted(matched_names)) or '无'}"
        )
    if not has_account:
        raise ValueError(
            f"缺少 Google 账号 Cookie ({'/'.join(sorted(GOOGLE_AUTH_ACCOUNT_NAMES))})，"
            f"已匹配到的 Cookie: {', '.join(sorted(matched_names)) or '无'}"
        )

    all_cookies = parse_browser_cookie_payload(raw_cookie)
    return json.dumps(all_cookies, ensure_ascii=False, separators=(",", ":"))


def build_browser_cookie_targets(
    raw_cookie: Any,
    default_url: str = DEFAULT_COOKIE_URL,
    fallback_urls: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    cookies = parse_browser_cookie_payload(raw_cookie, default_url=default_url)
    if not cookies:
        return []
    targets = []
    urls = fallback_urls or [default_url]
    for c in cookies:
        for u in urls:
            targets.append({**c, "url": u})
    return targets


def merge_browser_cookie_payloads(
    base_cookie: Any,
    new_cookie_items: Any,
    default_url: str = DEFAULT_COOKIE_URL,
) -> str:
    merged: Dict[str, Dict[str, Any]] = {}

    def append_cookie_items(raw_cookie: Any):
        if raw_cookie is None:
            return
        if isinstance(raw_cookie, dict):
            payload = raw_cookie.get("cookies") if isinstance(raw_cookie.get("cookies"), list) else [raw_cookie]
        elif isinstance(raw_cookie, list):
            payload = raw_cookie
        else:
            payload = parse_browser_cookie_payload(raw_cookie, default_url=default_url)
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            domain = str(item.get("domain") or "").strip().lower()
            path = str(item.get("path") or "/").strip() or "/"
            if not name:
                continue
            key = f"{name}:{domain}:{path}"
            merged[key] = item

    append_cookie_items(base_cookie)
    append_cookie_items(new_cookie_items)
    if not merged:
        return ""
    return json.dumps(list(merged.values()), ensure_ascii=False, separators=(",", ":"))


def build_cookie_signature(raw_cookie: Any, default_url: str = DEFAULT_COOKIE_URL) -> str:
    """生成用于 CAS 比较的稳定哈希签名。"""
    cookies = parse_browser_cookie_payload(raw_cookie, default_url=default_url)
    if not cookies:
        return ""
    stable_payload = json.dumps(cookies, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable_payload.encode("utf-8")).hexdigest()
