"""Protocol login for labs.google NextAuth with exported Google cookies."""

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote

from curl_cffi.requests import AsyncSession

from ..core.logger import debug_logger


LABS_BASE = "https://labs.google/fx"
SESSION_COOKIE_NAME = "__Secure-next-auth.session-token"
IMPERSONATE = "chrome124"
GOOGLE_COOKIE_NAMES = (
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC",
)


def _parse_google_cookies(raw: str) -> Dict[str, str]:
    """Parse Google cookies from JSON export or name=value text."""
    text = (raw or "").strip()
    if not text:
        return {}

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        data = None

    if isinstance(data, list):
        result: Dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name and value:
                result[name] = value
        if result:
            return result

    if isinstance(data, dict):
        cookies_list = data.get("cookies")
        if isinstance(cookies_list, list):
            result: Dict[str, str] = {}
            for item in cookies_list:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if name and value:
                    result[name] = value
            if result:
                return result

        result = {
            str(key).strip(): str(value).strip()
            for key, value in data.items()
            if isinstance(value, str) and str(key).strip() and value.strip()
        }
        if result:
            return result

    result: Dict[str, str] = {}
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name and value:
            result[name] = value
    return result


def _build_cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if name and value)


def _get_set_cookies(headers: Any) -> List[str]:
    if hasattr(headers, "getlist"):
        return headers.getlist("set-cookie") or headers.getlist("Set-Cookie") or []
    if hasattr(headers, "get_list"):
        return headers.get_list("set-cookie") or headers.get_list("Set-Cookie") or []
    value = headers.get("set-cookie") or headers.get("Set-Cookie")
    return [value] if value else []


def _merge_cookies(cookies: Dict[str, str], headers: Any) -> None:
    for line in _get_set_cookies(headers):
        first = str(line).split(";", 1)[0].strip()
        if "=" not in first:
            continue
        name, _, value = first.partition("=")
        if name.strip():
            cookies[name.strip()] = value.strip()


def _extract_session_token(headers: Any) -> Optional[str]:
    for line in _get_set_cookies(headers):
        first = str(line).split(";", 1)[0].strip()
        if not first.startswith(f"{SESSION_COOKIE_NAME}="):
            continue
        return first.split("=", 1)[1].strip()
    return None


def _extract_redirect_from_html(text: str) -> Optional[str]:
    body = text or ""
    match = re.search(
        r'content\s*=\s*["\']?\d+\s*;\s*url\s*=\s*([^"\'>\s]+)',
        body,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.search(
        r'location(?:\.(?:href|replace))?\s*(?:\(|=)\s*["\']([^"\']+)',
        body,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.search(r'<form[^>]*action\s*=\s*["\']([^"\']+)', body, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(r'(https://labs\.google/fx/api/auth/callback/google[^"\'<>\s]*)', body)
    if match:
        return match.group(1)

    match = re.search(r'[&?]continue=([^"\'<>\s&]+)', body)
    if match:
        return unquote(match.group(1))

    return None


def _normalize_proxy_url(proxy_url: Optional[str]) -> Optional[str]:
    raw = (proxy_url or "").strip()
    if not raw:
        return None

    st5_match = re.match(r"^st5\s+(.+)$", raw, re.IGNORECASE)
    if st5_match:
        rest = st5_match.group(1).strip()
        if "@" in rest:
            return f"socks5://{rest}"
        parts = rest.split(":")
        if len(parts) >= 4 and parts[1].isdigit():
            return f"socks5://{parts[2]}:{':'.join(parts[3:])}@{parts[0]}:{parts[1]}"
        return None

    if "://" not in raw:
        if "@" in raw:
            return f"http://{raw}"
        parts = raw.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            return f"http://{parts[0]}:{parts[1]}"
        if len(parts) >= 4 and parts[1].isdigit():
            return f"http://{parts[2]}:{':'.join(parts[3:])}@{parts[0]}:{parts[1]}"
        return None

    if re.match(r"^(http|https|socks5h?|socks5)://", raw, re.IGNORECASE):
        if "@" in raw:
            return raw
        scheme, _, rest = raw.partition("://")
        parts = rest.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            return raw
        if len(parts) >= 4 and parts[1].isdigit():
            return f"{scheme}://{parts[2]}:{':'.join(parts[3:])}@{parts[0]}:{parts[1]}"
    return None


def _append_login_hint(target_url: str, email: Optional[str]) -> str:
    email = (email or "").strip()
    if not email:
        return target_url
    parsed = urlparse(target_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["login_hint"] = email
    return urlunparse(parsed._replace(query=urlencode(query)))


class ProtocolLogin:
    """Login to labs.google/fx through NextAuth Google OAuth using Google cookies."""

    async def login(
        self,
        google_cookies_raw: str,
        proxy: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        google_cookies = _parse_google_cookies(google_cookies_raw)
        if not any(name in google_cookies for name in GOOGLE_COOKIE_NAMES):
            return {
                "success": False,
                "error": "未找到有效的 Google cookie（需要 SID/SAPISID 或 __Secure-*PSID/__Secure-*PAPISID 等登录 Cookie）",
            }

        session_kwargs: Dict[str, Any] = {"impersonate": IMPERSONATE, "trust_env": False}
        proxy_url = _normalize_proxy_url(proxy)
        if proxy_url:
            session_kwargs["proxy"] = proxy_url

        async with AsyncSession(**session_kwargs) as session:
            try:
                csrf_resp = await session.get(f"{LABS_BASE}/api/auth/csrf")
                if csrf_resp.status_code != 200:
                    return {"success": False, "error": f"CSRF 失败: HTTP {csrf_resp.status_code}"}
                csrf_token = (csrf_resp.json() or {}).get("csrfToken")
                if not csrf_token:
                    return {"success": False, "error": "CSRF 响应缺少 csrfToken"}

                labs_cookies: Dict[str, str] = {}
                _merge_cookies(labs_cookies, csrf_resp.headers)

                signin_resp = await session.post(
                    f"{LABS_BASE}/api/auth/signin/google",
                    data={
                        "csrfToken": csrf_token,
                        "callbackUrl": LABS_BASE,
                        "json": "true",
                    },
                    headers={
                        "Referer": LABS_BASE,
                        "Origin": "https://labs.google",
                        "Cookie": _build_cookie_header(labs_cookies),
                    },
                    allow_redirects=False,
                )
                if signin_resp.status_code != 200:
                    return {"success": False, "error": f"Signin 失败: HTTP {signin_resp.status_code}"}

                _merge_cookies(labs_cookies, signin_resp.headers)
                signin_data = signin_resp.json() or {}
                redirect_url = signin_data.get("redirect") or signin_data.get("url")
                if not redirect_url:
                    return {"success": False, "error": f"无重定向 URL: {json.dumps(signin_data)[:200]}"}
                redirect_url = _append_login_hint(redirect_url, email)

                google_cookie_header = _build_cookie_header(google_cookies)
                callback_url = ""
                current_url = redirect_url

                for attempt in range(10):
                    oauth_resp = await session.get(
                        current_url,
                        headers={
                            "Cookie": google_cookie_header,
                            "Referer": "https://labs.google/" if attempt == 0 else "https://accounts.google.com/",
                        },
                        allow_redirects=False,
                    )
                    location = (oauth_resp.headers.get("location") or "").strip()
                    if location:
                        location = urljoin(current_url, location)
                        if "labs.google/fx/api/auth/callback/google" in location:
                            callback_url = location
                            break
                        current_url = location
                        continue

                    body = oauth_resp.text or ""
                    if "signin/rejected" in body.lower():
                        return {"success": False, "error": "Google 拒绝登录，Cookies 可能已过期或被风控"}

                    if oauth_resp.status_code == 200:
                        html_redirect = _extract_redirect_from_html(body)
                        if html_redirect:
                            html_redirect = urljoin(current_url, html_redirect)
                            if "labs.google/fx/api/auth/callback/google" in html_redirect:
                                callback_url = html_redirect
                                break
                            current_url = html_redirect
                            continue

                    return {
                        "success": False,
                        "error": f"Google OAuth 未返回重定向（HTTP {oauth_resp.status_code}）",
                    }

                if not callback_url:
                    return {"success": False, "error": "Google OAuth 流程中未获得 callback URL"}

                callback_resp = await session.get(
                    callback_url,
                    headers={
                        "Cookie": _build_cookie_header(labs_cookies),
                        "Referer": "https://accounts.google.com/",
                    },
                    allow_redirects=False,
                )

                session_token = _extract_session_token(callback_resp.headers)
                _merge_cookies(labs_cookies, callback_resp.headers)

                for _ in range(5):
                    if session_token:
                        break
                    location = (callback_resp.headers.get("location") or "").strip()
                    if not location or callback_resp.status_code not in (301, 302, 303, 307, 308):
                        break
                    callback_resp = await session.get(
                        urljoin(callback_url, location),
                        headers={"Cookie": _build_cookie_header(labs_cookies)},
                        allow_redirects=False,
                    )
                    _merge_cookies(labs_cookies, callback_resp.headers)
                    session_token = _extract_session_token(callback_resp.headers)

                if not session_token:
                    return {"success": False, "error": "未获取到 session token，Google session 可能已过期"}
                return {"success": True, "session_token": session_token}
            except Exception as exc:
                debug_logger.log_error(f"[PROTOCOL_LOGIN] 协议登录异常: {exc}")
                return {"success": False, "error": str(exc)}


protocol_loginer = ProtocolLogin()
