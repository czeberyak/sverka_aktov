# -*- coding: utf-8 -*-
"""Тестируем только детерминированную часть src/ocr_ingest.py — разбор и
фильтрацию TSV-ответа модели. Сам вызов API мокается: реальный вызов
Anthropic не детерминирован и не должен жить в юнит-тестах."""
import unittest
from unittest.mock import patch
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.ocr_ingest import extract_pdf_to_tsv


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
    @patch("src.ocr_ingest.render_pdf_pages")
    @patch("src.ocr_ingest._call_claude_vision")
    def test_side_a_filters_malformed_lines(self, mock_call, mock_render):
        mock_render.return_value = ["fake_page_1.png"]
        mock_call.return_value = FAKE_PAGE_A
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "side_a.tsv")
            n = extract_pdf_to_tsv("dummy.pdf", "A", out)
            self.assertEqual(n, 2)  # только 2 валидные строки с 4 табуляциями (5 колонок)
            with open(out, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("acc_date\tdoc_text\tsum_doc\tmy_debit\tmy_credit", content)
            self.assertNotIn("случайный текст", content)

    @patch("src.ocr_ingest.render_pdf_pages")
    @patch("src.ocr_ingest._call_claude_vision")
    def test_side_b_filters_malformed_lines(self, mock_call, mock_render):
        mock_render.return_value = ["fake_page_1.png"]
        mock_call.return_value = FAKE_PAGE_B
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "side_b.tsv")
            n = extract_pdf_to_tsv("dummy.pdf", "B", out)
            self.assertEqual(n, 1)
            with open(out, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("acc_date\tdoc_text\tdebit\tcredit", content)
            self.assertNotIn("мусорная", content)

    @patch("src.ocr_ingest.render_pdf_pages")
    @patch("src.ocr_ingest._call_claude_vision")
    def test_output_is_parseable_by_pipeline(self, mock_call, mock_render):
        """Круговой тест: то, что пишет ocr_ingest, должно без ошибок
        читаться той же функцией, что читает штатные data/raw/*.tsv."""
        from src.pipeline import load_side_a
        mock_render.return_value = ["fake_page_1.png"]
        mock_call.return_value = FAKE_PAGE_A
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "side_a.tsv")
            extract_pdf_to_tsv("dummy.pdf", "A", out)
            cards = load_side_a(out)
            self.assertEqual(len(cards), 2)
            self.assertEqual(cards[1].document_number_normalized, "172")


if __name__ == "__main__":
    unittest.main()
