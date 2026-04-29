import pathlib
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from web.inspector import Inspector
from web.store import DataStore


class FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"ok\n", None


class WebInspectorTests(unittest.IsolatedAsyncioTestCase):
    def _make_store(self) -> DataStore:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return DataStore(temp_dir.name)

    async def test_run_inspection_passes_dry_run_to_cli(self):
        store = self._make_store()
        inspector = Inspector(pathlib.Path(__file__).resolve().parents[1])
        captured_args = None

        async def fake_create_subprocess_exec(*args, **kwargs):
            nonlocal captured_args
            captured_args = args
            return FakeProcess()

        with patch("web.inspector.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            with patch.object(inspector, "refresh_tokens", new=AsyncMock()):
                output = await inspector.run_inspection(store, {}, dry_run=True)

        self.assertEqual(output, "ok\n")
        self.assertIn("--once", captured_args)
        self.assertIn("--dry-run", captured_args)

    async def test_refresh_tokens_passes_retry_config_to_clients(self):
        store = self._make_store()
        inspector = Inspector()
        config = {
            "CPA_ENDPOINT": "https://example.com",
            "CPA_TOKEN": "secret",
            "CPA_HTTP_TIMEOUT": "31",
            "CPA_USAGE_TIMEOUT": "17",
            "CPA_MAX_RETRIES": "4",
            "CPA_WORKER_THREADS": "2",
        }

        with patch("web.inspector.CPAApi") as cpa_cls, patch("web.inspector.OpenAIApi") as openai_cls:
            cpa = cpa_cls.return_value
            cpa.list_tokens = AsyncMock(return_value=[])
            cpa.close = AsyncMock()
            openai = openai_cls.return_value
            openai.close = AsyncMock()

            await inspector.refresh_tokens(store, config)

        cpa_cls.assert_called_once_with(
            "https://example.com",
            "secret",
            proxy=None,
            timeout=31,
            max_retries=4,
        )
        openai_cls.assert_called_once_with(proxy=None, timeout=17, max_retries=4)

