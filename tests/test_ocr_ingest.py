# -*- coding: utf-8 -*-
"""Тестируем детерминированную часть src/ocr_ingest.py — разбор/фильтрацию
ответа модели и выбор провайдера. Сеть не дёргаем: вместо реального
Claude/OpenRouter передаём фейковый объект с .ask_image(...)."""
import unittest
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.ocr_ingest import extract_pdf_to_tsv, build_provider, render_pdf_pages


class FakeProvider:
    """Минимальная реализация контракта .ask_image(path, prompt) -> str."""
    name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def ask_image(self, image_path, prompt):
        self.calls.append((image_path, prompt))
        return self.responses.pop(0) if self.responses else ""


FAKE_PAGE_A = (
    "19.02.2021\tПлатежное поручение №1022 от 19.02.2021\t674000.00\t674000.00\t-\n"
    "это не похоже на строку таблицы, а на случайный текст модели\n"
    "\n"
    "01.04.2021\tАкт №172 от 22.01.2021 (Счет-фактура №172 от 22.01.2021)\t35000.00\t-\t35000.00"
)

FAKE_PAGE_B = (
    "19.01.21\tПриход (171 от 19.01.2021)\t-\t37000.00\n"
    "мусорная строка без табуляций вообще"
)


class TestOcrIngest(unittest.TestCase):
    def _fake_render(self, monkeypatch_target, n_pages=1):
        import src.ocr_ingest as mod
        original = mod.render_pdf_pages
        mod.render_pdf_pages = lambda pdf_path, out_dir, dpi=300: [f"fake_page_{i}.png" for i in range(n_pages)]
        self.addCleanup(setattr, mod, "render_pdf_pages", original)

    def test_side_a_filters_malformed_lines_via_fake_provider(self):
        self._fake_render(self)
        provider = FakeProvider([FAKE_PAGE_A])
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "side_a.tsv")
            n = extract_pdf_to_tsv("dummy.pdf", "A", out, provider=provider)
            self.assertEqual(n, 2)  # только 2 валидные строки с 4 табуляциями (5 колонок)
            with open(out, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("acc_date\tdoc_text\tsum_doc\tmy_debit\tmy_credit", content)
            self.assertNotIn("случайный текст", content)
            self.assertEqual(len(provider.calls), 1)  # 1 страница -> 1 вызов

    def test_side_b_filters_malformed_lines_via_fake_provider(self):
        self._fake_render(self)
        provider = FakeProvider([FAKE_PAGE_B])
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "side_b.tsv")
            n = extract_pdf_to_tsv("dummy.pdf", "B", out, provider=provider)
            self.assertEqual(n, 1)
            with open(out, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("acc_date\tdoc_text\tdebit\tcredit", content)
            self.assertNotIn("мусорная", content)

    def test_output_is_parseable_by_pipeline(self):
        """Круговой тест: то, что пишет ocr_ingest, должно без ошибок
        читаться той же функцией, что читает штатные data/raw/*.tsv."""
        from src.pipeline import load_side_a
        self._fake_render(self)
        provider = FakeProvider([FAKE_PAGE_A])
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "side_a.tsv")
            extract_pdf_to_tsv("dummy.pdf", "A", out, provider=provider)
            cards = load_side_a(out)
            self.assertEqual(len(cards), 2)
            self.assertEqual(cards[1].document_number_normalized, "172")

    def test_multi_page_calls_provider_once_per_page(self):
        self._fake_render(self, n_pages=3)
        provider = FakeProvider([FAKE_PAGE_A, "", FAKE_PAGE_B.replace("\t-\t", "\t-\t")])
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "side_a.tsv")
            extract_pdf_to_tsv("dummy.pdf", "A", out, provider=provider)
            self.assertEqual(len(provider.calls), 3)

    def test_build_provider_rejects_unknown_name(self):
        with self.assertRaises(ValueError):
            build_provider("chatgpt")

    def test_build_provider_openrouter_requires_api_key(self):
        old = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                build_provider("openrouter")
        finally:
            if old is not None:
                os.environ["OPENROUTER_API_KEY"] = old

    def test_build_provider_claude_requires_api_key(self):
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                build_provider("claude")
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
