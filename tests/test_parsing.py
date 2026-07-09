# -*- coding: utf-8 -*-
import sys, os, datetime, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.parsing import parse_raw_line_side_a, parse_raw_line_side_b, split_number, classify_doc_type, parse_date


class TestParsing(unittest.TestCase):

    def test_side_a_act_with_related_document(self):
        c = parse_raw_line_side_a("f.tsv", 1, "01.04.2021",
                                   "Акт №172 от 22.01.2021 (Счет-фактура №172 от 22.01.2021)",
                                   "35000.00", "-", "35000.00")
        self.assertEqual(c.doc_type_raw, "Акт")
        self.assertEqual(c.doc_type_group, "Отгрузка")
        self.assertEqual(c.document_number_normalized, "172")
        self.assertEqual(c.document_date, datetime.date(2021, 1, 22))
        # дата строки (проводки) НЕ должна перепутаться с датой документа (ТЗ п.12)
        self.assertEqual(c.accounting_date, datetime.date(2021, 4, 1))
        self.assertEqual(c.amount, 35000.0)
        self.assertEqual(c.direction, "credit")
        self.assertIn("Счет-фактура", c.related_document_text)

    def test_side_a_payment_order(self):
        c = parse_raw_line_side_a("f.tsv", 2, "19.02.2021", "Платежное поручение №1022 от 19.02.2021",
                                   "674000.00", "674000.00", "-")
        self.assertEqual(c.doc_type_group, "Оплата")
        self.assertEqual(c.amount, 674000.0)
        self.assertEqual(c.direction, "debit")

    def test_side_b_parenthetical_format(self):
        c = parse_raw_line_side_b("g.tsv", 1, "19.01.21", "Приход (171 от 19.01.2021)", "-", "37000.00")
        self.assertEqual(c.doc_type_group, "Отгрузка")
        self.assertEqual(c.document_number_normalized, "171")
        self.assertEqual(c.document_date, datetime.date(2021, 1, 19))

    def test_number_with_prefix_are_not_confused(self):
        """ТЗ п.12: АБ-172 и РН-172 — разные документы, префикс не игнорируется."""
        n1, p1, core1 = split_number("АБ-172")
        n2, p2, core2 = split_number("РН-172")
        self.assertNotEqual(n1, n2)
        self.assertNotEqual(p1, p2)
        self.assertEqual(core1, core2)  # ядро одинаковое — это лишь признак-кандидат (уровень E)

    def test_number_without_hash_sign(self):
        """Сторона Б пишет номер без «№» — парсер должен справляться."""
        c = parse_raw_line_side_b("g.tsv", 1, "01.03.21", "Продажа (700 от 01.03.2021)", "80000.00", "-")
        self.assertEqual(c.document_number_normalized, "700")

    def test_doc_type_grouping_maps_different_labels_to_same_group(self):
        self.assertEqual(classify_doc_type("Акт")[1], "Отгрузка")
        self.assertEqual(classify_doc_type("Продажа")[1], "Отгрузка")
        self.assertEqual(classify_doc_type("УПД")[1], "Отгрузка")
        self.assertEqual(classify_doc_type("Приход")[1], "Отгрузка")
        self.assertEqual(classify_doc_type("Оплата")[1], "Оплата")
        self.assertEqual(classify_doc_type("Платежное поручение")[1], "Оплата")

    def test_broken_date_does_not_crash(self):
        """OCR-артефакт вида 20.03.0202 (реальный случай в исходном скане) должен
        явно НЕ распознаться, а не тихо дать неверную дату."""
        self.assertIsNone(parse_date("20.03.0202"))


if __name__ == "__main__":
    unittest.main()
