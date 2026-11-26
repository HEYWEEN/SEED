import copy
import os
import stat
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.app import SETTINGS_PATH, chrome_extension_id, create_app
from backend.cache import discussion_fingerprint
from backend.jobs import JobError

THREAD = "demo/seed#8"
DISCUSSION = {
    "title": "Test 中文 🐛",
    "messages": [
        {"message_id": "M1", "kind": "body", "text": "The run was slow.\n\n保留原文"},
        {"message_id": "M2", "kind": "comment", "text": 'Input: "demo"\t🧪'},
    ],
}
EVALUATIONS = {
    "evaluations": [{
        "source_id": "S1",
        "sentiment": "negative",
        "sentence": "The run was slow.",
        "aspect": {"text": "run"},
        "opinion": {"text": "slow"},
    }],
}


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.state_dir = Path(self.enterContext(TemporaryDirectory()))
        ext_id = chrome_extension_id()
        self.auth = {
            "Origin": f"chrome-extension://{ext_id}",
            "X-Seed-Client": ext_id,
            "X-Seed-Thread": THREAD,
        }
        self.extract = Mock(return_value=EVALUATIONS)
        self.summarize = Mock(side_effect=lambda key, mode, title, rows: {
            "mode": mode,
            "sentences": [{"text": "测试摘要。", "source_ids": [rows[0]["source_id"]]}],
        })

    def application(self):
        return TestClient(
            create_app(self.state_dir, extract=self.extract, summarize=self.summarize),
            base_url="http://127.0.0.1:48729",
            headers=self.auth,
        )

    def submit(self, client, route, body):
        response = client.post("/v1/" + route, json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def wait_for(self, client, route, identifier):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = client.get(f"/v1/{route}/{identifier}")
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.01)
        self.fail("Cache test job did not complete")

    def lookup(self, client, discussion=DISCUSSION, **kwargs):
        checksum = discussion_fingerprint(discussion)
        response = client.post("/v1/cache/lookup", json={
            "analysis_fingerprint": checksum,
            "full_fingerprint": checksum,
        }, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_results_survive_restart_and_avoid_repeated_calls(self):
        with self.application() as client:
            client.put(SETTINGS_PATH, json={"api_key": "placeholder-only"})
            first = self.submit(client, "analyses", DISCUSSION)
            analysis_id = first["analysis_id"]
            self.assertFalse(first["cached"])
            self.assertEqual(self.wait_for(client, "analyses", analysis_id)["status"], "completed")
            modes = [
                {"mode": "full", **DISCUSSION},
                {"mode": "evaluations", "analysis_id": analysis_id},
            ]
            summaries = []
            for mode in modes:
                generated = self.submit(client, "summaries", mode)
                self.assertFalse(generated["cached"])
                summaries.append(generated["summary_id"])
                self.wait_for(client, "summaries", generated["summary_id"])

        with self.application() as client:
            client.delete(SETTINGS_PATH)
            result = self.lookup(client)
            self.assertEqual(result["analysis"]["id"], analysis_id)
            self.assertTrue(result["analysis"]["cached"])
            self.assertEqual(result["summary"]["data"]["mode"], "evaluations")
            self.assertEqual(result["summary"]["id"], summaries[-1])
            repeat = self.submit(client, "analyses", DISCUSSION)
            self.assertEqual(repeat, {"analysis_id": analysis_id, "cached": True})
            for mode, summary_id in zip(modes, summaries):
                repeat = self.submit(client, "summaries", mode)
                self.assertEqual(repeat, {"summary_id": summary_id, "cached": True})
                self.assertEqual(self.wait_for(client, "summaries", summary_id)["status"], "completed")

        self.assertEqual(self.extract.call_count, 1)
        self.assertEqual(self.summarize.call_count, 2)
        self.assertEqual(len(list((self.state_dir / "results").glob("*.json"))), 3)
        if os.name == "posix":
            self.assertEqual(
                stat.S_IMODE((self.state_dir / "results" / f"{analysis_id}.json").stat().st_mode),
                0o600,
            )

    def test_content_issue_and_model_changes_do_not_reuse_results(self):
        with self.application() as client:
            client.put(SETTINGS_PATH, json={"api_key": "placeholder-only"})
            first = self.submit(client, "analyses", DISCUSSION)
            self.wait_for(client, "analyses", first["analysis_id"])
            full = self.submit(client, "summaries", {"mode": "full", **DISCUSSION})
            self.wait_for(client, "summaries", full["summary_id"])
            changed = copy.deepcopy(DISCUSSION)
            changed["messages"][1]["text"] += "\nNow fast."
            self.assertEqual(self.lookup(client, changed), {"analysis": None, "summary": None})
            self.assertEqual(
                self.lookup(client, headers={"X-Seed-Thread": "demo/seed#9"}),
                {"analysis": None, "summary": None},
            )
            with patch("backend.app.sesame_signature", return_value=["different-runtime"]):
                self.assertIsNone(self.lookup(client)["analysis"])
                updated = self.submit(client, "analyses", DISCUSSION)
                self.assertNotEqual(updated["analysis_id"], first["analysis_id"])
                self.wait_for(client, "analyses", updated["analysis_id"])
            with patch.dict(os.environ, {"SEED_DEEPSEEK_MODEL": "different-model"}):
                self.assertIsNone(self.lookup(client)["summary"])
                updated = self.submit(client, "summaries", {"mode": "full", **DISCUSSION})
                self.assertNotEqual(updated["summary_id"], full["summary_id"])
                self.wait_for(client, "summaries", updated["summary_id"])
            self.assertEqual(self.extract.call_count, 2)
            self.assertEqual(self.summarize.call_count, 2)

    def test_inflight_retries_share_the_extraction_and_failures_are_retriable(self):
        gate = Event()
        self.addCleanup(gate.set)

        def fail_once(discussion, report):
            gate.wait(timeout=2)
            raise JobError("sesame_failed")

        def execute(discussion, report):
            if self.extract.call_count == 1:
                return fail_once(discussion, report)
            return EVALUATIONS

        self.extract.side_effect = execute
        with self.application() as client:
            first = self.submit(client, "analyses", DISCUSSION)
            repeated = self.submit(client, "analyses", DISCUSSION)
            self.assertEqual(repeated, first)
            gate.set()
            self.assertEqual(self.wait_for(client, "analyses", first["analysis_id"])["status"], "failed")
            self.assertIsNone(self.lookup(client)["analysis"])
            repeated = self.submit(client, "analyses", DISCUSSION)
            self.assertEqual(repeated, first)
            self.assertEqual(self.wait_for(client, "analyses", repeated["analysis_id"])["status"], "completed")
        self.assertEqual(self.extract.call_count, 2)
