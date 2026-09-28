import json
import subprocess
import unittest
from pathlib import Path


POLICY_SCRIPT = Path(__file__).parent.parent / "extension" / "recaptcha_policy.js"


def run_policy(expression: str):
    script = (
        f"const policy = require({json.dumps(str(POLICY_SCRIPT))});"
        f"console.log(JSON.stringify({expression}));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class ExtensionRecaptchaPolicyTests(unittest.TestCase):
    def test_mint_page_uses_flow_origin(self):
        self.assertEqual(
            run_policy("policy.MINT_PAGE_URL"),
            "https://flow.google.com/about",
        )

    def test_enterprise_script_enables_trusted_types(self):
        result = run_policy('policy.getEnterpriseScriptUrl("site-key")')

        self.assertEqual(
            result,
            "https://www.google.com/recaptcha/enterprise.js?trustedtypes=true&render=site-key",
        )

    def test_manifest_registers_main_world_hook_at_document_start(self):
        manifest_path = Path(__file__).parent.parent / "extension" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        hooks = [
            script
            for script in manifest.get("content_scripts", [])
            if "recaptcha_hook.js" in script.get("js", [])
        ]

        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0].get("run_at"), "document_start")
        self.assertEqual(hooks[0].get("world"), "MAIN")
        self.assertIn("https://flow.google.com/*", hooks[0].get("matches", []))

    def test_background_defines_mint_runtime_functions(self):
        background_path = Path(__file__).parent.parent / "extension" / "background.js"
        source = background_path.read_text(encoding="utf-8")

        self.assertIn("async function ensureMintTab()", source)
        self.assertIn("async function dropMintTab(reason)", source)
        self.assertIn("async function mintRecaptchaToken(tabId, action, timeoutMs)", source)


if __name__ == "__main__":
    unittest.main()
