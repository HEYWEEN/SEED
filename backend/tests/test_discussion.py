import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.app import SETTINGS_PATH, chrome_extension_id, create_app

THREAD = "company/project#12"
DISCUSSION = {
    "title": "Scheduler feedback",
    "messages": [{"message_id": "M1", "kind": "body", "text": "Binding was slow.\n\nExample:\n  keep this line."}],
}


class DiscussionFlowTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.extract = Mock(return_value={
            "evaluations": [{
                "source_id": "S12",
                "sentiment": "negative",
                "sentence": "Binding was slow.",
                "aspect": {"text": "Binding"},
                "opinion": {"text": "slow"},
            }],
        })
        extension_id = chrome_extension_id()
        self.client = self.enterContext(TestClient(
            create_app(self.directory, extract=self.extract),
            base_url="http://127.0.0.1:48729",
            headers={
                "Origin": f"chrome-extension://{extension_id}",
                "X-Seed-Client": extension_id,
                "X-Seed-Thread": THREAD,
            },
        ))

    def finish(self, path):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.01)
        self.fail("Local job did not finish")

    def save_test_key(self):
        response = self.client.put(SETTINGS_PATH, json={"api_key": "local-placeholder-only"})
        self.assertEqual(response.json(), {"configured": True})

    def test_direct_summary_works_before_extraction_and_preserves_full_text(self):
        self.save_test_key()
        with patch("backend.deepseek.completion", return_value=self.reply("M1")) as provider:
            response = self.client.post("/v1/summaries", json={"mode": "full", **DISCUSSION})
            self.assertEqual(response.status_code, 202, response.text)
            job = self.finish("/v1/summaries/" + response.json()["summary_id"])
            self.assertEqual(job["result"]["mode"], "full")
            self.extract.assert_not_called()
            provider.assert_called_once()
            messages = json.loads(provider.call_args.args[0].data)["messages"]
            self.assertIn('"source_ids":["M1"]', messages[0]["content"])
            self.assertNotIn('"S1"', messages[0]["content"])
            context = json.loads(messages[1]["content"])
            self.assertEqual(context, {
                "title": DISCUSSION["title"],
                "messages": [{"source_id": "M1", "text": DISCUSSION["messages"][0]["text"]}],
            })

    def test_evaluation_summary_requires_this_threads_completed_analysis(self):
        self.save_test_key()
        with patch("backend.deepseek.completion", return_value=self.reply("S12")) as provider:
            missing = self.client.post("/v1/summaries", json={"mode": "evaluations", "analysis_id": "0" * 32})
            self.assertEqual(missing.json()["detail"]["code"], "analysis_required")
            created = self.client.post("/v1/analyses", json=DISCUSSION)
            self.assertEqual(created.status_code, 202, created.text)
            analysis_id = created.json()["analysis_id"]
            self.assertEqual(self.finish("/v1/analyses/" + analysis_id)["status"], "completed")
            provider.assert_not_called()
            payload = {"mode": "evaluations", "analysis_id": analysis_id}
            other = self.client.post("/v1/summaries", json=payload, headers={"X-Seed-Thread": "company/project#13"})
            self.assertEqual(other.status_code, 409)
            response = self.client.post("/v1/summaries", json=payload)
            self.assertEqual(response.status_code, 202, response.text)
            job = self.finish("/v1/summaries/" + response.json()["summary_id"])
            self.assertEqual(job["result"]["mode"], "evaluations")
            messages = json.loads(provider.call_args.args[0].data)["messages"]
            self.assertIn('"source_ids":["S12"]', messages[0]["content"])
            context = json.loads(messages[1]["content"])
            self.assertEqual(context, {
                "title": "",
                "evaluations": [{
                    "source_id": "S12", "sentiment": "negative", "aspect": "Binding",
                    "opinion": "slow", "sentence": "Binding was slow.",
                }],
            })

    def test_recovers_from_empty_truncated_and_invalid_citations(self):
        self.save_test_key()
        failures = {
            "blank": self.reply(content="   "),
            "truncated": self.reply(content='{"sentences":[', finish_reason="length"),
            "invalid JSON": self.reply(content="not JSON"),
            "invalid citation": self.reply("M999"),
        }
        for label, failure in failures.items():
            with self.subTest(label=label), patch(
                "backend.deepseek.completion", side_effect=[failure, self.reply("M1")]
            ) as provider:
                response = self.client.post("/v1/summaries", json={"mode": "full", **DISCUSSION, "title": label})
                self.assertEqual(response.status_code, 202, response.text)
                job = self.finish("/v1/summaries/" + response.json()["summary_id"])
                self.assertEqual(job["status"], "completed", job)
                self.assertEqual(job["result"]["sentences"][0]["source_ids"], ["M1"])
                self.assertEqual(provider.call_count, 2)
                initial, retry = [json.loads(call.args[0].data) for call in provider.call_args_list]
                self.assertNotEqual(initial["messages"][0], retry["messages"][0])
                self.assertEqual(initial["messages"][1], retry["messages"][1])
        self.extract.assert_not_called()

    def test_accepts_fenced_json_and_normalizes_known_citations(self):
        self.save_test_key()
        reply = self.reply(content=(
            '```json\n{"sentences":[{"text":"测试摘要。","source_ids":" [m1] "}]}\n```'
        ))
        with patch("backend.deepseek.completion", return_value=reply) as provider:
            response = self.client.post("/v1/summaries", json={"mode": "full", **DISCUSSION})
            self.assertEqual(response.status_code, 202, response.text)
            job = self.finish("/v1/summaries/" + response.json()["summary_id"])
            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(job["result"]["sentences"][0]["source_ids"], ["M1"])
            provider.assert_called_once()

    def test_no_key_and_invalid_evidence_are_explicit_failures(self):
        content = '{"sentences":[{"text":"测试摘要。","source_ids":["M1","S404"]}]}'
        with patch("backend.deepseek.completion", return_value=self.reply(content=content)) as provider:
            payload = {"mode": "full", **DISCUSSION}
            response = self.client.post("/v1/summaries", json=payload)
            self.assertEqual(response.json()["detail"]["code"], "key_missing")
            provider.assert_not_called()
            self.save_test_key()
            response = self.client.post("/v1/summaries", json=payload)
            job = self.finish("/v1/summaries/" + response.json()["summary_id"])
            self.assertEqual((job["status"], job["code"]), ("failed", "deepseek_response"))
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(list((self.directory / "results").glob("*.json")), [])

    @staticmethod
    def reply(source=None, *, content=None, finish_reason="stop"):
        if content is None:
            content = json.dumps({"sentences": [{"text": "测试摘要。", "source_ids": [source]}]})
        return {"choices": [{
            "finish_reason": finish_reason, "message": {"content": content},
        }]}
