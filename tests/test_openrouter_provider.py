# -*- coding: utf-8 -*-
"""Тестируем OpenRouterVisionProvider без реальной сети: подменяем
urllib.request.urlopen. Реальный вызов openrouter.ai из среды разработки
недоступен (egress-прокси блокирует домен, см. ТЕХНИЧЕСКИЙ_РАЗБОР.md) —
поэтому здесь проверяется ТОЛЬКО код (payload, заголовки, ретраи), не
качество распознавания. Это явное ограничение, а не то, что забыли
проверить."""
import unittest
from unittest.mock import patch, MagicMock
import io
import json
import os
import tempfile
import urllib.error
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.ocr.openrouter_vision_provider import OpenRouterVisionProvider


def _fake_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestOpenRouterVisionProvider(unittest.TestCase):
    def setUp(self):
        self._old_key = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "test-key-123"
        self._tmp_image = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        self._tmp_image.write(b"\x89PNG\r\n\x1a\nfake")
        self._tmp_image.close()

    def tearDown(self):
        if self._old_key is not None:
            os.environ["OPENROUTER_API_KEY"] = self._old_key
        else:
            os.environ.pop("OPENROUTER_API_KEY", None)
        os.unlink(self._tmp_image.name)

    def test_missing_api_key_raises(self):
        os.environ.pop("OPENROUTER_API_KEY")
        with self.assertRaises(RuntimeError):
            OpenRouterVisionProvider()

    def test_default_model_is_ocr_focused_vl(self):
        p = OpenRouterVisionProvider(sleep_between_calls=0)
        self.assertEqual(p.model, "nvidia/nemotron-nano-12b-v2-vl:free")

    @patch("src.ocr.openrouter_vision_provider.urllib.request.urlopen")
    def test_ask_image_builds_correct_payload_and_parses_reply(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(
            {"choices": [{"message": {"content": "19.01.21\tПриход (171 от 19.01.2021)\t-\t37000.00"}}]}
        )
        p = OpenRouterVisionProvider(model="test/model:free", sleep_between_calls=0)
        result = p.ask_image(self._tmp_image.name, "распознай таблицу")

        self.assertIn("37000.00", result)
        sent_request = mock_urlopen.call_args[0][0]
        self.assertEqual(sent_request.full_url, OpenRouterVisionProvider.API_URL)
        self.assertEqual(sent_request.get_header("Authorization"), "Bearer test-key-123")
        body = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(body["model"], "test/model:free")
        content = body["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    @patch("time.sleep", return_value=None)  # не ждать реально в тесте
    @patch("src.ocr.openrouter_vision_provider.urllib.request.urlopen")
    def test_retries_on_429_then_succeeds(self, mock_urlopen, mock_sleep):
        err = urllib.error.HTTPError(
            url=OpenRouterVisionProvider.API_URL, code=429, msg="Too Many Requests",
            hdrs=None, fp=io.BytesIO(b'{"error":"rate limited"}'),
        )
        ok = _fake_response({"choices": [{"message": {"content": "готово"}}]})
        mock_urlopen.side_effect = [err, ok]

        p = OpenRouterVisionProvider(sleep_between_calls=0, max_retries=3)
        result = p.ask_image(self._tmp_image.name, "prompt")

        self.assertEqual(result, "готово")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called()  # выдержана пауза перед повтором

    @patch("time.sleep", return_value=None)
    @patch("src.ocr.openrouter_vision_provider.urllib.request.urlopen")
    def test_gives_up_after_max_retries_on_persistent_429(self, mock_urlopen, mock_sleep):
        err = urllib.error.HTTPError(
            url=OpenRouterVisionProvider.API_URL, code=429, msg="Too Many Requests",
            hdrs=None, fp=io.BytesIO(b"{}"),
        )
        mock_urlopen.side_effect = [err, err, err]
        p = OpenRouterVisionProvider(sleep_between_calls=0, max_retries=3)
        with self.assertRaises(RuntimeError):
            p.ask_image(self._tmp_image.name, "prompt")
        self.assertEqual(mock_urlopen.call_count, 3)

    @patch("src.ocr.openrouter_vision_provider.urllib.request.urlopen")
    def test_non_429_http_error_raises_immediately_without_retry(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url=OpenRouterVisionProvider.API_URL, code=401, msg="Unauthorized",
            hdrs=None, fp=io.BytesIO(b'{"error":"invalid api key"}'),
        )
        mock_urlopen.side_effect = err
        p = OpenRouterVisionProvider(sleep_between_calls=0, max_retries=5)
        with self.assertRaises(RuntimeError) as ctx:
            p.ask_image(self._tmp_image.name, "prompt")
        self.assertIn("401", str(ctx.exception))
        self.assertEqual(mock_urlopen.call_count, 1)  # без повторов — ключ всё равно неверный


if __name__ == "__main__":
    unittest.main()
