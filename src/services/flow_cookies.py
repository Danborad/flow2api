"""Cookie Jar for Google Flow with domain/path scoping, Set-Cookie updates, and CAS export."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .browser_cookie_utils import (
    cookie_pairs_for_url,
    parse_browser_cookie_payload,
    serialize_cookie_header_for_url,
)


class FlowCookieJar:
    """Retains domain/path scopes, duplicate cookies, server rotation, and exports snapshots."""

    def __init__(self, raw_cookies: Any = None):
        self._cookies: List[Dict[str, Any]] = []
        if raw_cookies:
            self.load_storage(raw_cookies)

    def load_storage(self, raw_cookies: Any) -> None:
        self._cookies = parse_browser_cookie_payload(raw_cookies)

    def export_storage(self) -> str:
        """Export compact structured JSON string."""
        return json.dumps(self._cookies, ensure_ascii=False, separators=(",", ":"))

    def get_cookie_header_for_url(self, target_url: str) -> str:
        """Get 'name=value; ...' header matching target_url."""
        return serialize_cookie_header_for_url(self._cookies, target_url)

    def get_cookie_pairs_for_url(self, target_url: str) -> List[Tuple[str, str]]:
        return cookie_pairs_for_url(self._cookies, target_url)

    def apply_set_cookie(self, set_cookie_headers: List[str] | str, request_url: str) -> bool:
        """Update jar from response Set-Cookie headers. Returns True if any change occurred."""
        if not set_cookie_headers:
            return False

        headers = [set_cookie_headers] if isinstance(set_cookie_headers, str) else set_cookie_headers
        parsed_url = urlparse(request_url)
        default_domain = parsed_url.hostname or "flow.google.com"
        default_path = "/"
        now = time.time()
        changed = False

        for header_line in headers:
            for part in str(header_line).splitlines():
                part = part.strip()
                if not part:
                    continue
                segments = part.split(";")
                nv = segments[0].strip()
                if "=" not in nv:
                    continue
                c_name, c_val = nv.split("=", 1)
                c_name = c_name.strip()
                c_val = c_val.strip()
                if not c_name:
                    continue

                attrs: Dict[str, Any] = {
                    "name": c_name,
                    "value": c_val,
                    "domain": default_domain,
                    "hostOnly": True,
                    "path": default_path,
                    "secure": request_url.startswith("https://"),
                    "httpOnly": False,
                }

                max_age = None
                is_expired = False

                for attr in segments[1:]:
                    attr = attr.strip()
                    if not attr:
                        continue
                    if "=" in attr:
                        k, v = attr.split("=", 1)
                        k = k.strip().lower()
                        v = v.strip()
                        if k == "domain":
                            attrs["domain"] = v
                            attrs["hostOnly"] = not v.startswith(".")
                        elif k == "path":
                            attrs["path"] = v or "/"
                        elif k == "max-age":
                            try:
                                max_age = float(v)
                                attrs["expirationDate"] = now + max_age
                                if max_age <= 0:
                                    is_expired = True
                            except Exception:
                                pass
                        elif k == "expires":
                            try:
                                # 若未被 max-age 覆盖
                                if max_age is None:
                                    from email.utils import parsedate_to_datetime
                                    dt = parsedate_to_datetime(v)
                                    exp_ts = dt.timestamp()
                                    attrs["expirationDate"] = exp_ts
                                    if exp_ts <= now:
                                        is_expired = True
                            except Exception:
                                pass
                        elif k == "samesite":
                            attrs["sameSite"] = v.capitalize()
                    else:
                        flag = attr.lower()
                        if flag == "secure":
                            attrs["secure"] = True
                        elif flag == "httponly":
                            attrs["httpOnly"] = True

                # 如果 Cookie 被删除 / 已过期，从 Jar 中移除对应项
                target_domain = attrs["domain"].lstrip(".").lower()
                target_path = attrs["path"]

                idx_to_remove = []
                for i, existing in enumerate(self._cookies):
                    e_name = existing.get("name")
                    e_domain = str(existing.get("domain") or "").lstrip(".").lower()
                    e_path = existing.get("path") or "/"
                    if e_name == c_name and e_domain == target_domain and e_path == target_path:
                        idx_to_remove.append(i)

                if is_expired:
                    if idx_to_remove:
                        for idx in reversed(idx_to_remove):
                            self._cookies.pop(idx)
                        changed = True
                    continue

                if idx_to_remove:
                    # 更新已有 Cookie
                    first_idx = idx_to_remove[0]
                    # 继承原有属性（如 sameSite 等）
                    orig = self._cookies[first_idx]
                    merged = {**orig, **attrs}
                    self._cookies[first_idx] = merged
                    # 若有多个重复项，清除多余的
                    for idx in reversed(idx_to_remove[1:]):
                        self._cookies.pop(idx)
                    changed = True
                else:
                    self._cookies.append(attrs)
                    changed = True

        return changed

    def update_from_curl_cookielist(self, cookielist: List[str]) -> bool:
        """从 curl 返回的 COOKIELIST 行数据更新。"""
        if not cookielist:
            return False
        changed = False
        now = time.time()
        for line in cookielist:
            parts = line.strip().split("\t")
            if len(parts) < 7:
                continue
            domain, include_subdomains, path, secure, expires, name, value = parts[:7]
            if not name:
                continue
            exp_ts = float(expires) if expires and expires != "0" else None
            if exp_ts and exp_ts < now:
                continue

            cookie_dict = {
                "name": name,
                "value": value,
                "domain": domain,
                "hostOnly": not domain.startswith(".") and include_subdomains.upper() != "TRUE",
                "path": path or "/",
                "secure": secure.upper() == "TRUE",
                "httpOnly": False,
            }
            if exp_ts:
                cookie_dict["expirationDate"] = exp_ts

            # 匹配并替换
            found = False
            for i, existing in enumerate(self._cookies):
                if (
                    existing.get("name") == name
                    and str(existing.get("domain") or "").lstrip(".").lower() == domain.lstrip(".").lower()
                    and (existing.get("path") or "/") == path
                ):
                    self._cookies[i] = {**existing, **cookie_dict}
                    found = True
                    changed = True
                    break
            if not found:
                self._cookies.append(cookie_dict)
                changed = True

        return changed
