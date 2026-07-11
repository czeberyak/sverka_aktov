# -*- coding: utf-8 -*-
"""
TesseractTableProvider — офлайн-провайдер для --ocr-provider tesseract.

ПОЧЕМУ НЕ ПО КООРДИНАТАМ СЛОВ (word bounding boxes): первая попытка была
именно такой (см. git-историю решения) — Tesseract действительно отдаёт
координаты каждого слова (`tesseract img stdout tsv`). Но эмпирическая
проверка на реальной странице показала: соседние узкие числовые колонки
(Сумма документа / Дебет МЫ / Кредит МЫ / Сумма ОНИ / Дебет ОНИ / Кредит
ОНИ — 6 колонок на сравнительно узком пространстве) Tesseract слипает в
одно "слово", например строка с тремя одинаковыми "30 000,00" была
распознана как единый токен `30.000,00]30.000,00)"30.000,00`. Разбить это
обратно на 6 колонок позиционно — значит рисковать ТИХО перепутать
местами дебет/кредит, что для финансовой сверки хуже, чем просто не
распознать строку.

ПОЭТОМУ ЗДЕСЬ ДРУГАЯ, БОЛЕЕ УСТОЙЧИВАЯ СТРАТЕГИЯ — построчный текст
(`tesseract img stdout --psm 6`, БЕЗ tsv) + якоря:
    Сторона А: "RUB" — надёжно распознаётся (латиница), делит строку на
               "дата + текст документа" (слева) и "числовой хвост" (справа).
               Из числового хвоста берётся ПЕРВОЕ найденное число — при
               отсутствии расхождения между Стороной А и Б все 6 колонок
               содержат одно и то же значение, так что "взять первое"
               эквивалентно "взять правильное" в подавляющем большинстве
               строк. Направление (дебет/кредит) не читается из колонки,
               а ВЫВОДИТСЯ из типа документа (Платёжное поручение ->
               дебет, Акт/Счёт-фактура -> кредит) — та же логика, что уже
               проверена в src/parsing.py: DOC_TYPE_GROUPS.
    Сторона Б: якоря куда менее надёжны — нет аналога "RUB", а строка
               устроена как ДВА самостоятельных блока (По данным ОНИ | По
               данным МЫ) без чёткого разделителя при плохом OCR. Реализован
               эвристический сплит (см. _split_side_b_line), но он ЗАВЕДОМО
               менее надёжен стороны А — см. README/тех.разбор, честно
               помечено как экспериментальное.

ЧЕСТНО О ПРОВЕРЕННОМ И НЕПРОВЕРЕННОМ:
    Я тестировал эту логику ТОЛЬКО на английской модели tesseract (в среде
    разработки не было сети для tesseract-ocr-rus) — на ней кириллица
    читается как визуально похожая латиница ("Акт" -> "Akt", "Приход" ->
    "Mpixog" и т.п.), сама структура строки (дата, "RUB", числа) остаётся
    узнаваемой, и на этом регэкспы уже проверены (tests/test_tesseract_table_provider.py,
    фикстуры — реальный вывод tesseract с этих самых сканов). Но КАЧЕСТВО
    итогового doc_text (типа документа и номера) на настоящей rus-модели
    не проверялось мной ни разу — это и есть то, что стоит верифицировать
    в первую очередь на вашей машине, раз у вас есть tesseract-ocr-rus.
"""
from __future__ import annotations
import re
import subprocess
import sys
from typing import Optional, Tuple

_LEAD_DATE_RE = re.compile(r"^\D{0,3}(\d{1,2}[.,]\d{1,2}[.,]\d{2,4})")
_AMOUNT_RE = re.compile(r"(?:\d{1,3}(?:[ .]\d{3})*|\d{4,})[.,]\d{2}")
_RUB_RE = re.compile(r"\bR[UI][B8]\b", re.IGNORECASE)  # RUB с типовыми OCR-заменами
_TYPE_KEYWORDS_A = ("акт", "akt", "счет", "счёт", "cuet", "платежн", "nnatex", "плат", "nnat")


class TesseractTableProvider:
    name = "tesseract-table"

    def __init__(self, lang: str = "rus+eng", psm: int = 6, warn_if_no_rus: bool = True):
        self.lang = lang
        self.psm = psm
        if warn_if_no_rus and "rus" in lang:
            available = self._available_langs()
            if "rus" not in available:
                print(f"  [tesseract] ВНИМАНИЕ: языковой пакет 'rus' не найден "
                      f"(доступно: {sorted(available)}). Распознавание кириллицы будет "
                      f"нечитаемым — установите tesseract-ocr-rus. Продолжаю на {lang}.",
                      file=sys.stderr)

    @staticmethod
    def _available_langs() -> set:
        try:
            out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, check=True)
            return set(out.stdout.strip().splitlines()[1:])
        except Exception:
            return set()

    def ask_image(self, image_path: str, prompt: str) -> str:
        raw_text = self._ocr_text(image_path)
        side = "A" if ("Дебет (МЫ)" in prompt or "дебет_МЫ" in prompt) else "B"
        rows = []
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = self._parse_side_a_line(line) if side == "A" else self._parse_side_b_line(line)
            if parsed:
                rows.append(parsed)
        return "\n".join(rows)

    def _ocr_text(self, image_path: str) -> str:
        result = subprocess.run(
            ["tesseract", image_path, "stdout", "--psm", str(self.psm), "-l", self.lang],
            capture_output=True, text=True, check=True,
        )
        return result.stdout

    # ------------------------------------------------------------------
    # Сторона А: "дата ... RUB ... хвост_с_числами" -> 5 колонок TSV
    # ------------------------------------------------------------------
    def _parse_side_a_line(self, line: str) -> Optional[str]:
        date_m = _LEAD_DATE_RE.match(line)
        if not date_m:
            return None
        acc_date = date_m.group(1).replace(",", ".")

        rub_m = _RUB_RE.search(line)
        if rub_m:
            doc_text = line[date_m.end():rub_m.start()]
            tail = line[rub_m.end():]
        else:
            # запасной вариант: "RUB" не распознан — режем по первому числу
            first_amount = _AMOUNT_RE.search(line, date_m.end())
            if not first_amount:
                return None
            doc_text = line[date_m.end():first_amount.start()]
            tail = line[first_amount.start():]

        doc_text = self._clean_doc_text(doc_text)
        if not doc_text or len(doc_text) < 3:
            return None  # пустой/обрубленный текст документа — не похоже на строку таблицы

        amounts = _AMOUNT_RE.findall(tail)
        if not amounts:
            return None
        amount = _normalize_amount(amounts[0])

        direction = _infer_direction(doc_text)
        debit = amount if direction == "debit" else "-"
        credit = amount if direction == "credit" else "-"
        return f"{acc_date}\t{doc_text}\t{amount}\t{debit}\t{credit}"

    # ------------------------------------------------------------------
    # Сторона Б: эвристика — экспериментально, см. docstring файла
    # ------------------------------------------------------------------
    def _parse_side_b_line(self, line: str) -> Optional[str]:
        date_m = _LEAD_DATE_RE.match(line)
        if not date_m:
            return None
        acc_date = date_m.group(1).replace(",", ".")

        # ищем ВТОРОЕ вхождение похожей даты — граница "По данным МЫ"
        second_date = _LEAD_DATE_RE_ANY.search(line, date_m.end())
        search_from = second_date.start() if second_date else date_m.end()

        amounts = list(_AMOUNT_RE.finditer(line, search_from))
        if not amounts:
            return None
        last_amount = amounts[-1]  # правая часть строки = "По данным МЫ" по построению макета
        doc_text = self._clean_doc_text(line[search_from:last_amount.start()])
        if not doc_text:
            return None

        amount = _normalize_amount(last_amount.group(0))
        direction = _infer_direction(doc_text)
        debit = amount if direction == "debit" else "-"
        credit = amount if direction == "credit" else "-"
        return f"{acc_date}\t{doc_text}\t{debit}\t{credit}"

    @staticmethod
    def _clean_doc_text(text: str) -> str:
        text = re.sub(r"[\[\]|_\"'`]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" -.,")
        return text


_LEAD_DATE_RE_ANY = re.compile(r"\d{1,2}[.,]\d{1,2}[.,]\d{2,4}")


def _normalize_amount(raw: str) -> str:
    """'30.000,00' / '30 000,00' / '30000,00' -> '30000.00'."""
    raw = raw.strip()
    whole, _, frac = raw.replace(" ", "").replace(".", "").rpartition(",") if "," in raw else (raw.replace(" ", ""), "", "")
    if not whole:
        whole, frac = raw, "00"
    whole = re.sub(r"[^\d]", "", whole) or "0"
    frac = (re.sub(r"[^\d]", "", frac) or "00")[:2].ljust(2, "0")
    return f"{whole}.{frac}"


def _infer_direction(doc_text: str) -> str:
    """Платёжка/оплата -> списание (дебет у Стороны А), Акт/Продажа/Приход
    -> начисление (кредит) — то же правило, что подтверждено на 919+1182
    строках ручной транскрипции в src/parsing.py (DOC_TYPE_GROUPS).

    НАДЁЖНОСТЬ: на нормальном rus+eng распознавании кириллица читается
    достаточно устойчиво, и хотя бы один из вариантов ниже должен
    совпасть. На английской модели (fallback, см. класс-докстринг)
    транслитерация одного и того же слова непостоянна (например,
    "Платежное" в разных строках одного скана превращалось и в
    "Tinatexvoe", и в "Minareniioe" — общего устойчивого паттерна нет),
    поэтому там эта функция ненадёжна ПРИНЦИПИАЛЬНО, а не из-за
    недостаточно длинного списка вариантов — это ожидаемое ограничение,
    а не то, что можно исчерпывающе перечислить."""
    t = doc_text.lower()
    if re.search(r"плат|nnat|onnat|оплат|tinatex|minatex|ninatex", t):
        return "debit"
    return "credit"
