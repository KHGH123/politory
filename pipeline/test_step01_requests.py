from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError

from google.api_core.exceptions import PreconditionFailed

from step01_collect_assembly import Collector


class CollectorRequestTest(unittest.TestCase):
    def test_retry_log_and_error_do_not_expose_api_key(self) -> None:
        collector = object.__new__(Collector)
        collector.cfg = SimpleNamespace(request_delay=0)
        stderr = io.StringIO()

        with (
            patch("step01_collect_assembly.urlopen", side_effect=URLError("offline")) as mocked,
            patch("step01_collect_assembly.time.sleep"),
            redirect_stderr(stderr),
        ):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts") as raised:
                collector.request(
                    "https://example.test/openapi",
                    {"KEY": "top-secret"},
                    timeout_seconds=20,
                    attempts=3,
                )

        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(mocked.call_args.kwargs["timeout"], 20)
        self.assertNotIn("top-secret", stderr.getvalue())
        self.assertNotIn("top-secret", str(raised.exception))

    def test_upload_is_create_only_and_reuses_existing_object(self) -> None:
        collector = object.__new__(Collector)
        blob = Mock()
        blob.upload_from_string.side_effect = PreconditionFailed("exists")
        collector.bucket = Mock()
        collector.bucket.blob.return_value = blob
        collector.cfg = SimpleNamespace(bucket="archive")

        uri = collector.upload("raw/file.json", b"{}", "application/json")

        self.assertEqual(uri, "gs://archive/raw/file.json")
        blob.upload_from_string.assert_called_once_with(
            b"{}", content_type="application/json", if_generation_match=0
        )


if __name__ == "__main__":
    unittest.main()
