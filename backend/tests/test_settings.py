import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from backend.app import SETTINGS_PATH, chrome_extension_id, create_app


class DeepSeekSettingsTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.state_dir = Path(directory)
        self.key_path = self.state_dir / "deepseek-api-key"
        extension_id = chrome_extension_id()
        self.headers = {
            "X-Seed-Client": extension_id,
            "Origin": f"chrome-extension://{extension_id}",
        }
        self.client = self.new_client()

    def new_client(self):
        return self.enterContext(
            TestClient(
                create_app(self.state_dir),
                base_url="http://127.0.0.1:48729",
                headers=self.headers,
            )
        )

    def test_save_replace_reopen_and_clear(self):
        self.assertEqual(self.client.get(SETTINGS_PATH).json(), {"configured": False})
        for key in ("local-test-value", "replacement-test-value"):
            response = self.client.put(SETTINGS_PATH, json={"api_key": key})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"configured": True})
            self.assertEqual(self.key_path.read_text(encoding="utf-8"), key)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.key_path.stat().st_mode), 0o600)

        other_session = self.new_client()
        self.assertEqual(other_session.get(SETTINGS_PATH).json(), {"configured": True})
        self.assertEqual(other_session.delete(SETTINGS_PATH).json(), {"configured": False})
        self.assertFalse(self.key_path.exists())
        self.assertEqual(self.client.delete(SETTINGS_PATH).json(), {"configured": False})

    def test_invalid_payload_does_not_echo_the_key(self):
        response = self.client.put(
            SETTINGS_PATH,
            json={"api_key": "temporary-test-secret", "extra": "temporary-test-secret"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("temporary-test-secret", response.text)
        self.assertFalse(self.key_path.exists())

    def test_other_origins_cannot_change_settings(self):
        response = self.client.put(
            SETTINGS_PATH,
            headers={"Origin": "https://github.com"},
            json={"api_key": "local-test-value"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.key_path.exists())
        response = self.client.get(SETTINGS_PATH, headers={"X-Seed-Client": ""})
        self.assertEqual(response.status_code, 403)
        response = self.client.options(
            SETTINGS_PATH,
            headers={
                "Origin": "https://github.com",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type,x-seed-client",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)


if __name__ == "__main__":
    unittest.main()
