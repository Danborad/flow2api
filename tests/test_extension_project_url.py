import json
import subprocess
import unittest
from pathlib import Path


PROJECT_URL_SCRIPT = Path(__file__).parent.parent / "extension" / "project_url.js"


def run_project_url_helper(expression: str):
    script = (
        f"const helper = require({json.dumps(str(PROJECT_URL_SCRIPT))});"
        f"console.log(JSON.stringify({expression}));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class ExtensionProjectUrlTests(unittest.TestCase):
    def test_extracts_new_and_legacy_project_urls(self):
        urls = [
            "https://flow.google.com/project/12345678-1234-1234-1234-123456789abc",
            "https://flow.google.com/projects/23456789-2345-2345-2345-23456789abcd",
            "https://labs.google/fx/tools/flow/project/abcdefab-cdef-abcd-efab-cdefabcdefab",
            "https://flow.google.com/?projectId=3456789a-3456-3456-3456-3456789abcde",
            "https://flow.google.com/",
        ]

        result = run_project_url_helper(
            f"{json.dumps(urls)}.map(helper.getFlowProjectFromTabUrl)"
        )

        self.assertEqual(result[0], "12345678-1234-1234-1234-123456789abc")
        self.assertEqual(result[1], "23456789-2345-2345-2345-23456789abcd")
        self.assertEqual(result[2], "abcdefab-cdef-abcd-efab-cdefabcdefab")
        self.assertEqual(result[3], "3456789a-3456-3456-3456-3456789abcde")
        self.assertEqual(result[4], "")

    def test_prefers_active_then_most_recent_project_tab(self):
        tabs = [
            {
                "url": "https://flow.google.com/project/11111111-1111-1111-1111-111111111111",
                "title": "Older project",
                "active": False,
                "lastAccessed": 200,
            },
            {
                "url": "https://flow.google.com/project/22222222-2222-2222-2222-222222222222",
                "title": "Active project - Google Flow",
                "active": True,
                "lastAccessed": 100,
            },
        ]

        result = run_project_url_helper(
            f"helper.selectCurrentFlowProject({json.dumps(tabs)})"
        )

        self.assertEqual(result["projectId"], "22222222-2222-2222-2222-222222222222")
        self.assertEqual(result["projectName"], "Active project")

    def test_reads_pending_url_during_project_navigation(self):
        tabs = [
            {
                "url": "https://flow.google.com/",
                "pendingUrl": "https://flow.google.com/project/33333333-3333-3333-3333-333333333333",
                "title": "Google Flow",
                "active": True,
                "lastAccessed": 300,
            },
        ]

        result = run_project_url_helper(
            f"helper.selectCurrentFlowProject({json.dumps(tabs)})"
        )

        self.assertEqual(result["projectId"], "33333333-3333-3333-3333-333333333333")

    def test_builds_current_flow_create_project_payload(self):
        result = run_project_url_helper(
            'helper.buildCreateProjectEnvelope("Flow2API Project")'
        )

        self.assertEqual(result[0][0][0], "jHPbke")
        self.assertEqual(
            json.loads(result[0][0][1]),
            ["projects/*", [None, ["Flow2API Project"]], [None, 22]],
        )
        self.assertEqual(result[0][0][3], "generic")

    def test_extracts_created_project_id_from_batch_response(self):
        response = (
            ")]}'\n\n123\n"
            '[["wrb.fr","jHPbke","[\\"44444444-4444-4444-4444-444444444444\\",[\\"Flow2API Project\\"]]",null,null,null,"generic"]]'
        )

        result = run_project_url_helper(
            f"helper.extractCreatedProjectId({json.dumps(response)})"
        )

        self.assertEqual(result, "44444444-4444-4444-4444-444444444444")


if __name__ == "__main__":
    unittest.main()
