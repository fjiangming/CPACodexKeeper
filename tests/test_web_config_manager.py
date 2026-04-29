import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from web.config_manager import ConfigManager


class WebConfigManagerTests(unittest.TestCase):
    def _make_env_file(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env_path = Path(temp_dir.name) / ".env"
        env_path.write_text(content, encoding="utf-8")
        return env_path

    def test_environment_variables_override_env_file_values(self):
        env_file = self._make_env_file(
            "CPA_ENDPOINT=https://file.example.com\n"
            "CPA_TOKEN=file-token\n"
            "CPA_INTERVAL=120\n"
        )

        with patch.dict(
            os.environ,
            {
                "CPA_ENDPOINT": "https://env.example.com",
                "CPA_TOKEN": "env-token",
            },
            clear=True,
        ):
            values = ConfigManager(env_file).read_all()
            items = ConfigManager(env_file).read_with_meta()

        self.assertEqual(values["CPA_ENDPOINT"], "https://env.example.com")
        self.assertEqual(values["CPA_TOKEN"], "env-token")
        self.assertEqual(values["CPA_INTERVAL"], "120")

        item_by_key = {item["key"]: item for item in items}
        self.assertEqual(item_by_key["CPA_ENDPOINT"]["source"], "environment")
        self.assertEqual(item_by_key["CPA_TOKEN"]["source"], "environment")
        self.assertEqual(item_by_key["CPA_INTERVAL"]["source"], "file")

