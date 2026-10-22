"""Unit tests for Cookie-first authentication and flow.google.com batchexecute RPC."""

import json
import time
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from src.services.browser_cookie_utils import (
    cookie_pairs_for_url,
    parse_browser_cookie_payload,
    serialize_cookie_header_for_url,
    validate_flow_cookie_storage,
    build_cookie_signature,
)
from src.services.flow_cookies import FlowCookieJar
from src.services.flow_frontend import FlowFrontendTransport


class BrowserCookieUtilsTests(unittest.TestCase):
    def test_structured_and_array_input(self):
        cookies = [
            {"name": "OSID", "value": "val-osid", "domain": "flow.google.com", "path": "/", "secure": True},
            {"name": "SID", "value": "val-sid", "domain": ".google.com", "path": "/", "secure": True},
        ]
        parsed = parse_browser_cookie_payload(json.dumps(cookies))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["name"], "OSID")

    def test_host_only_and_domain_scope(self):
        cookies = [
            {"name": "OSID", "value": "osid", "domain": "flow.google.com", "hostOnly": True, "path": "/", "secure": True},
            {"name": "OTHER_HOST", "value": "x", "domain": "other.google.com", "hostOnly": True, "path": "/", "secure": True},
            {"name": "SID", "value": "sid", "domain": ".google.com", "hostOnly": False, "path": "/", "secure": True},
        ]
        pairs = cookie_pairs_for_url(json.dumps(cookies), "https://flow.google.com/projects")
        names = [p[0] for p in pairs]
        self.assertIn("OSID", names)
        self.assertIn("SID", names)
        self.assertNotIn("OTHER_HOST", names)

    def test_path_scoping_and_priority(self):
        cookies = [
            {"name": "ROOT", "value": "r", "domain": "flow.google.com", "path": "/"},
            {"name": "DEEP", "value": "d", "domain": "flow.google.com", "path": "/project/123"},
        ]
        pairs = cookie_pairs_for_url(json.dumps(cookies), "https://flow.google.com/project/123")
        self.assertEqual(pairs[0][0], "DEEP")
        self.assertEqual(pairs[1][0], "ROOT")

        pairs_root = cookie_pairs_for_url(json.dumps(cookies), "https://flow.google.com/other")
        names = [p[0] for p in pairs_root]
        self.assertIn("ROOT", names)
        self.assertNotIn("DEEP", names)

    def test_skips_partitioned_cookies(self):
        cookies = [
            {"name": "PART", "value": "p", "domain": "flow.google.com", "partitioned": True},
            {"name": "VALID", "value": "v", "domain": "flow.google.com"},
        ]
        parsed = parse_browser_cookie_payload(json.dumps(cookies))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "VALID")

    def test_validation_required_cookie_groups(self):
        # 缺少会话组
        no_session = [{"name": "SID", "value": "s", "domain": ".google.com", "path": "/", "secure": True}]
        with self.assertRaises(ValueError) as cm:
            validate_flow_cookie_storage(json.dumps(no_session))
        self.assertIn("缺少 Flow 会话 Cookie", str(cm.exception))

        # 缺少账号组
        no_account = [{"name": "OSID", "value": "o", "domain": "flow.google.com", "path": "/", "secure": True}]
        with self.assertRaises(ValueError) as cm2:
            validate_flow_cookie_storage(json.dumps(no_account))
        self.assertIn("缺少 Google 账号 Cookie", str(cm2.exception))

        # 完整通过
        full = [
            {"name": "OSID", "value": "o", "domain": "flow.google.com", "path": "/", "secure": True},
            {"name": "SID", "value": "s", "domain": ".google.com", "path": "/", "secure": True},
        ]
        res = validate_flow_cookie_storage(json.dumps(full))
        self.assertTrue(res.startswith("["))


class FlowCookieJarTests(unittest.TestCase):
    def test_apply_set_cookie_rotation_and_delete(self):
        initial = [
            {"name": "OSID", "value": "old-val", "domain": "flow.google.com", "path": "/", "secure": True},
        ]
        jar = FlowCookieJar(json.dumps(initial))
        
        # 轮换
        jar.apply_set_cookie("OSID=new-val; Domain=flow.google.com; Path=/; Secure", "https://flow.google.com/")
        header = jar.get_cookie_header_for_url("https://flow.google.com/")
        self.assertIn("OSID=new-val", header)

        # 删除
        jar.apply_set_cookie("OSID=deleted; Domain=flow.google.com; Path=/; Max-Age=0", "https://flow.google.com/")
        header_del = jar.get_cookie_header_for_url("https://flow.google.com/")
        self.assertNotIn("OSID", header_del)


class FlowFrontendTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_framed_response(self):
        transport = FlowFrontendTransport()
        framed_text = ')]}\'\n123\n[["wrb.fr","test_rpc","{\\"data\\":\\"hello\\"}"],["xsrf","new-xsrf-token"]]\n'
        payload, new_xsrf, has_er, er_detail = transport._parse_framed_response(framed_text, "test_rpc")
        self.assertEqual(payload, {"data": "hello"})
        self.assertEqual(new_xsrf, "new-xsrf-token")
        self.assertFalse(has_er)


if __name__ == "__main__":
    unittest.main()
