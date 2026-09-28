import unittest

from src.services.flow_client import FlowClient


class FlowClientExtensionFingerprintTests(unittest.TestCase):
    def test_browser_fingerprint_overrides_legacy_labs_origin(self):
        client = FlowClient(proxy_manager=None)
        client._set_request_fingerprint({
            "origin": "https://flow.google.com",
            "referer": "https://flow.google.com/about",
        })
        headers = client._apply_runtime_browser_context_headers(
            {"Origin": "https://labs.google", "Referer": "https://labs.google/"},
            "https://aisandbox-pa.googleapis.com/v1/projects/project-1/flowMedia:batchGenerateImages",
            None,
        )

        self.assertEqual(headers["Origin"], "https://flow.google.com")
        self.assertEqual(headers["Referer"], "https://flow.google.com/about")


if __name__ == "__main__":
    unittest.main()
