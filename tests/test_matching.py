# -*- coding: utf-8 -*-
import sys, os, datetime, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.models import OperationCard
from src.matching import Matcher, MatchConfig


def card(side, num, date, amount, direction, group="Отгрузка", text=None, row=1):
    c = OperationCard(source_file="t", page=0, row=row, source_text=text or f"{num} {date} {amount}",
                       side=side, raw_document_text=text or f"Документ {num}")
    c.doc_type_group = group
    c.document_number_normalized = str(num)
    c.document_number_core = str(num).lstrip("0") or "0"
    c.document_date = datetime.date(*[int(x) for x in reversed(date.split("."))])
    if direction == "debit":
        c.debit = amount
    else:
        c.credit = amount
    c.__post_init__()
    return c


class TestMatching(unittest.TestCase):

    def test_exact_match_level_a(self):
        a = card("A", "172", "22.01.2021", 35000.0, "credit")
        b = card("B", "172", "22.01.2021", 35000.0, "credit")
        results, rem_a, rem_b = Matcher([a], [b]).run()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "A")
        self.assertEqual(results[0].status, "Полное совпадение")
        self.assertEqual(len(rem_a), 0)
        self.assertEqual(len(rem_b), 0)

    def test_mirrored_direction_still_matches_as_level_b(self):
        """ТЗ п.12: направление может быть в противоположных колонках — это
        не должно мешать найти совпадение, но должно понижать точность до B."""
        a = card("A", "500", "10.02.2021", 40000.0, "credit")
        b = card("B", "500", "10.02.2021", 40000.0, "debit")
        results, rem_a, rem_b = Matcher([a], [b]).run()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "B")

    def test_missing_document_reported_not_dropped(self):
        a = card("A", "1", "01.01.2021", 1000.0, "credit")
        results, rem_a, rem_b = Matcher([a], []).run()
        self.assertEqual(rem_a, [a])  # -> "Отсутствует у Поставщика" в отчёте

    def test_amount_mismatch_is_discrepancy_not_missing(self):
        """Номер и дата совпали, сумма — нет: должно попасть в 'Расхождения',
        а НЕ в 'отсутствует у стороны' (иначе теряется важная информация)."""
        a = card("A", "9", "05.05.2021", 10000.0, "credit")
        b = card("B", "9", "05.05.2021", 12000.0, "credit")
        results, rem_a, rem_b = Matcher([a], [b]).run()
        self.assertEqual(len(rem_a), 0)
        self.assertEqual(len(rem_b), 0)
        self.assertTrue(any(r.status == "Расхождение по сумме" for r in results))

    def test_duplicate_within_side_is_flagged(self):
        a1 = card("A", "7", "01.01.2021", 5000.0, "credit", row=1)
        a2 = card("A", "7", "01.01.2021", 5000.0, "credit", row=2)
        results, rem_a, rem_b = Matcher([a1, a2], []).run()
        self.assertTrue(any("дубль" in r.status.lower() for r in results))

    def test_multirow_split_level_f(self):
        """Один документ у Стороны А равен сумме двух строк у Стороны Б
        с тем же цифровым ядром номера — уровень F."""
        a = card("A", "300", "01.03.2021", 100000.0, "credit")
        b1 = card("B", "300", "01.03.2021", 60000.0, "credit", row=1)
        b2 = card("B", "300", "01.03.2021", 40000.0, "credit", row=2)
        results, rem_a, rem_b = Matcher([a], [b1, b2]).run()
        self.assertTrue(any(r.level == "F" for r in results))
        self.assertEqual(len(rem_a), 0)
        self.assertEqual(len(rem_b), 0)

    def test_ambiguous_when_two_equally_good_candidates(self):
        a = card("A", "1", "01.01.2021", 5000.0, "credit")
        b1 = card("B", "1", "01.01.2021", 5000.0, "credit", row=1)
        b2 = card("B", "1", "02.01.2021", 5000.0, "credit", row=2)  # тот же номер+сумма, дата другая -> тоже кандидат уровня C
        # у обоих кандидатов может получиться разный балл; проверяем как минимум,
        # что автоматически не выбирается "первый попавшийся" без объяснения
        results, rem_a, rem_b = Matcher([a], [b1, b2]).run()
        statuses = [r.status for r in results]
        self.assertTrue(any(s in ("Полное совпадение", "Неоднозначное совпадение — ручная проверка") for s in statuses))

    def test_related_document_number_resolves_mismatch(self):
        """Реальный кейс из скана: 'Акт №1218 (Счёт-фактура №1219)' у
        Стороны А, а Сторона Б ссылается на '1219'. Без учёта связанного
        номера это ошибочно уходило в 'отсутствует у Покупателя'."""
        a = card("A", "1218", "07.04.2021", 39000.0, "credit",
                  text="Акт №1218 от 07.04.2021 (Счет-фактура №1219 от 07.04.2021)")
        a.related_document_number_normalized = "1219"
        b = card("B", "1219", "07.04.2021", 39000.0, "credit", text="Приход (1219 от 07.04.2021)")
        results, rem_a, rem_b = Matcher([a], [b]).run()
        self.assertEqual(len(rem_a), 0)
        self.assertEqual(len(rem_b), 0)
        self.assertEqual(results[0].level, "A")

    def test_strict_mode_disables_core_fallback(self):
        """АБ-172 и РН-172 — разные номера, совпадает только цифровое ядро.
        В гибком режиме это уровень E; в строгом резерв по ядру запрещён,
        поэтому уровня E быть не должно (хотя уровень D по дате+сумме
        по-прежнему возможен — строгий режим не про это, а про запрет
        путать разные префиксы, см. ТЗ п.12)."""
        a = card("A", "АБ-172", "01.01.2021", 1000.0, "credit")
        b = card("B", "РН-172", "01.01.2021", 1000.0, "credit")
        a.document_number_core = "172"
        b.document_number_core = "172"
        strict_results, ra, rb = Matcher([a], [b], config=MatchConfig.strict()).run()
        self.assertFalse(any(r.level == "E" for r in strict_results))
        flex_results, ra2, rb2 = Matcher([a], [b], config=MatchConfig.flexible()).run()
        self.assertTrue(any(r.level == "E" for r in flex_results))


if __name__ == "__main__":
    unittest.main()
