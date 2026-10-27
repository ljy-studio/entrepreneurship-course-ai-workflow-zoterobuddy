import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import zotero


def response(status, body=None, headers=None):
    headers = dict(headers or {})
    text = "" if body is None else json.dumps(body)
    if body is not None:
        headers.setdefault("Content-Type", "application/json")
    return zotero.Response(status, headers, text)


def parsed_item(key, item_type="journalArticle", collections=None, tags=None, version=4):
    return {
        "key": key,
        "version": version,
        "library": {"id": 12},
        "data": {
            "key": key,
            "version": version,
            "itemType": item_type,
            "title": "A test paper",
            "collections": list(collections or []),
            "tags": list(tags or []),
        },
    }


class ClassificationTests(unittest.TestCase):
    def cli_args(self, *extra):
        return zotero.build_parser().parse_args(
            [
                "classify",
                "ATTACH01",
                "--collection-key",
                "TARGET01",
                "--tag",
                "#method",
                "--yes",
                *extra,
            ]
        )

    def write_manifest(self, rows):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "classifications.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        return str(path)

    def test_zotero_major_version(self):
        self.assertEqual(zotero.zotero_major_version("10.0.2"), 10)
        self.assertEqual(zotero.zotero_major_version("9.0.6"), 9)
        self.assertIsNone(zotero.zotero_major_version(None))

    @patch.object(zotero, "request", return_value=response(200))
    @patch.object(zotero.subprocess, "Popen")
    @patch.object(zotero.subprocess, "run", return_value=argparse.Namespace(returncode=0))
    @patch.object(zotero.time, "sleep")
    @patch.object(zotero.platform, "system", return_value="Windows")
    def test_windows_restart_requests_graceful_close(self, system, sleep, run, popen, request):
        self.assertTrue(zotero.restart_zotero())
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["powershell.exe", "-NoProfile"])
        self.assertNotIn("taskkill", " ".join(args).lower())
        self.assertNotIn("/f", " ".join(args).lower())
        popen.assert_called_once_with(
            ["zotero.exe"], stdout=zotero.subprocess.DEVNULL, stderr=zotero.subprocess.DEVNULL
        )

    @patch.object(zotero, "request")
    @patch.object(zotero.subprocess, "Popen")
    @patch.object(zotero.subprocess, "run", return_value=argparse.Namespace(returncode=4))
    @patch.object(zotero.platform, "system", return_value="Windows")
    def test_windows_restart_does_not_force_close_unresponsive_app(
        self, system, run, popen, request
    ):
        self.assertFalse(zotero.restart_zotero())
        popen.assert_not_called()
        request.assert_not_called()

    def test_merge_preserves_existing_tags_and_collections(self):
        current = {
            "collections": ["OLD00001", "OTHER001"],
            "tags": [{"tag": "/unread", "type": 1}, {"tag": "#existing"}],
        }
        result = zotero.merged_classification(
            current, "TARGET01", ["#existing", "#new"], "OLD00001"
        )
        self.assertEqual(result["collections"], ["OTHER001", "TARGET01"])
        self.assertEqual(
            result["tags"],
            [{"tag": "/unread", "type": 1}, {"tag": "#existing"}, {"tag": "#new"}],
        )

    @patch.object(zotero, "request")
    @patch.object(zotero, "api_get")
    @patch.object(zotero, "status_payload")
    def test_cli_requires_explicit_write_flag(self, status, api_get, req):
        args = zotero.build_parser().parse_args(
            ["classify", "ITEM0001", "--collection-key", "COLL0001", "--tag", "#topic"]
        )
        with self.assertRaisesRegex(SystemExit, "without --yes"):
            zotero.cmd_classify(args)
        status.assert_not_called()
        api_get.assert_not_called()
        req.assert_not_called()

    @patch.object(zotero, "request")
    @patch.object(zotero, "api_get")
    @patch.object(zotero, "status_payload")
    def test_zotero_9_refuses_before_library_reads_or_writes(self, status, api_get, req):
        status.return_value = {
            "api_running": True,
            "local_api_enabled_pref": True,
            "zotero_version": "9.0.6",
            "server_id": None,
        }
        with self.assertRaisesRegex(SystemExit, "Zotero 10 or later"):
            zotero.cmd_classify(self.cli_args())
        api_get.assert_not_called()
        req.assert_not_called()

    @patch.object(zotero, "request")
    @patch.object(zotero, "api_get")
    @patch.object(zotero, "status_payload")
    def test_attachment_classifies_parent_and_verifies_preserved_data(self, status, api_get, req):
        status.return_value = {
            "api_running": True,
            "local_api_enabled_pref": True,
            "zotero_version": "10.0.0",
            "server_id": "SERVER01",
        }
        attachment = {
            "key": "ATTACH01",
            "library": {"id": 12},
            "data": {"key": "ATTACH01", "itemType": "attachment", "parentItem": "PARENT01"},
        }
        parent = parsed_item(
            "PARENT01",
            collections=["UNCAT001", "OTHER001"],
            tags=[{"tag": "/unread", "type": 1}],
        )
        target = {"key": "TARGET01", "library": {"id": 12}, "data": {"name": "Topic"}}
        source = {"key": "UNCAT001", "library": {"id": 12}, "data": {"name": "Unsorted"}}
        verified = {
            "key": "PARENT01",
            "data": {
                "key": "PARENT01",
                "title": "A test paper",
                "collections": ["OTHER001", "TARGET01"],
                "tags": [{"tag": "/unread", "type": 1}, {"tag": "#method"}],
            },
        }
        api_get.side_effect = [attachment, parent, target, source, verified]
        req.side_effect = [
            response(200, {"key": "ONETIME"}),
            response(204),
        ]

        with patch.object(zotero, "dump_json"):
            zotero.cmd_classify(
                self.cli_args("--remove-collection-key", "UNCAT001")
            )

        write_call = req.call_args_list[1]
        self.assertEqual(write_call.args[0], "/api/users/0/items/PARENT01")
        self.assertEqual(write_call.kwargs["method"], "PATCH")
        self.assertEqual(
            write_call.kwargs["data"],
            {
                "version": 4,
                "collections": ["OTHER001", "TARGET01"],
                "tags": [{"tag": "/unread", "type": 1}, {"tag": "#method"}],
            },
        )
        self.assertEqual(write_call.kwargs["headers"]["Zotero-API-Key"], "ONETIME")
        self.assertEqual(write_call.kwargs["headers"]["Zotero-Server-ID"], "SERVER01")
        self.assertEqual(api_get.call_args_list[-1], call("/api/users/0/items/PARENT01"))

    @patch.object(zotero, "request")
    @patch.object(zotero, "api_get")
    @patch.object(zotero, "status_payload")
    def test_authorization_denial_does_not_attempt_patch(self, status, api_get, req):
        status.return_value = {
            "api_running": True,
            "local_api_enabled_pref": True,
            "zotero_version": "10.0.0",
            "server_id": "SERVER01",
        }
        api_get.side_effect = [
            parsed_item("ATTACH01", collections=[], tags=[]),
            {"key": "TARGET01", "library": {"id": 12}, "data": {"name": "Topic"}},
        ]
        req.return_value = response(403, {"denied": True})
        with self.assertRaisesRegex(SystemExit, "authorization was denied"):
            zotero.cmd_classify(self.cli_args())
        self.assertEqual(req.call_count, 1)

    @patch.object(zotero, "request")
    @patch.object(zotero, "api_get")
    @patch.object(zotero, "status_payload")
    def test_version_conflict_is_not_retried(self, status, api_get, req):
        status.return_value = {
            "api_running": True,
            "local_api_enabled_pref": True,
            "zotero_version": "10.0.0",
            "server_id": "SERVER01",
        }
        api_get.side_effect = [
            parsed_item("ATTACH01", collections=[], tags=[]),
            {"key": "TARGET01", "library": {"id": 12}, "data": {"name": "Topic"}},
        ]
        req.side_effect = [response(200, {"key": "ONETIME"}), response(412)]
        with self.assertRaisesRegex(SystemExit, "changed during classification"):
            zotero.cmd_classify(self.cli_args())
        self.assertEqual(req.call_count, 2)

    @patch.object(zotero, "request")
    @patch.object(zotero, "api_get")
    @patch.object(zotero, "status_payload")
    def test_verification_mismatch_is_reported(self, status, api_get, req):
        status.return_value = {
            "api_running": True,
            "local_api_enabled_pref": True,
            "zotero_version": "10.0.0",
            "server_id": "SERVER01",
        }
        api_get.side_effect = [
            parsed_item("ATTACH01", collections=[], tags=[]),
            {"key": "TARGET01", "library": {"id": 12}, "data": {"name": "Topic"}},
            {"key": "ATTACH01", "data": {"collections": [], "tags": []}},
        ]
        req.side_effect = [response(200, {"key": "ONETIME"}), response(204)]
        with patch.object(zotero, "dump_json"), self.assertRaisesRegex(
            SystemExit, "verification did not match"
        ):
            zotero.cmd_classify(self.cli_args())
        self.assertEqual(req.call_count, 2)

    @patch.object(zotero, "request")
    @patch.object(zotero, "api_get")
    @patch.object(zotero, "status_payload")
    def test_batch_authorizes_once_and_verifies_each_item(self, status, api_get, req):
        status.return_value = {
            "api_running": True,
            "local_api_enabled_pref": True,
            "zotero_version": "10.0.0",
            "server_id": "SERVER01",
        }
        first = parsed_item("ITEM0001", collections=["UNCAT001"], tags=[{"tag": "/unread", "type": 1}])
        second = parsed_item("ITEM0002", collections=["UNCAT001"], tags=[{"tag": "/unread", "type": 1}])
        collection = {"key": "TARGET01", "library": {"id": 12}, "data": {"name": "Target"}}
        source = {"key": "UNCAT001", "library": {"id": 12}, "data": {"name": "Unsorted"}}
        first_verified = {"data": {"collections": ["TARGET01"], "tags": [{"tag": "/unread", "type": 1}, {"tag": "#first"}]}}
        second_verified = {"data": {"collections": ["TARGET01"], "tags": [{"tag": "/unread", "type": 1}, {"tag": "#second"}]}}
        api_get.side_effect = [
            first, collection, source,
            second, collection, source,
            first_verified, second_verified,
        ]
        req.side_effect = [
            response(200, {"key": "ONETIME"}),
            response(200, {"successful": {"0": {"key": "ITEM0001"}, "1": {"key": "ITEM0002"}}, "failed": {}}),
        ]
        path = self.write_manifest(
            [
                {"item_key": "ITEM0001", "collection_key": "TARGET01", "tags": ["#first"], "remove_collection_key": "UNCAT001"},
                {"item_key": "ITEM0002", "collection_key": "TARGET01", "tags": ["#second"], "remove_collection_key": "UNCAT001"},
            ]
        )
        args = zotero.build_parser().parse_args(["classify-batch", "--file", path, "--yes"])
        with patch.object(zotero, "dump_json") as output:
            zotero.cmd_classify_batch(args)
        self.assertEqual(req.call_count, 2)
        self.assertEqual(req.call_args_list[1].args[0], "/api/users/0/items")
        self.assertEqual(req.call_args_list[1].kwargs["method"], "POST")
        self.assertEqual(len(req.call_args_list[1].kwargs["data"]), 2)
        self.assertEqual(req.call_args_list[1].kwargs["headers"]["Zotero-API-Key"], "ONETIME")
        self.assertEqual(output.call_args.args[0]["status"], "verified")

    @patch.object(zotero, "request")
    @patch.object(zotero, "api_get")
    @patch.object(zotero, "status_payload")
    def test_batch_aborts_if_expected_membership_changed(self, status, api_get, req):
        status.return_value = {
            "api_running": True,
            "local_api_enabled_pref": True,
            "zotero_version": "10.0.0",
            "server_id": "SERVER01",
        }
        api_get.return_value = parsed_item("ITEM0001", collections=["OTHER001"], tags=[])
        path = self.write_manifest(
            [
                {
                    "item_key": "ITEM0001",
                    "collection_key": "TARGET01",
                    "tags": ["#topic"],
                    "expected_collection_keys": [],
                }
            ]
        )
        args = zotero.build_parser().parse_args(["classify-batch", "--file", path, "--yes"])
        with self.assertRaisesRegex(SystemExit, "membership changed"):
            zotero.cmd_classify_batch(args)
        req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
