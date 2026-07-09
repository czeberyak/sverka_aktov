# -*- coding: utf-8 -*-
"""
Разбор наименования документа (ТЗ, п.6.2) и нормализация (п.6.3).

Идея (главный принцип ТЗ): строка не сравнивается целиком — из неё
извлекаются реквизиты. Здесь это и происходит: из сырого текста типа

    "Акт №172 от 22.01.2021 (Счет-фактура №172 от 22.01.2021)"
    "Приход (171 от 19.01.2021)"
    "Платежное поручение №1022 от 19.02.2021"

извлекаются: тип документа, номер в трёх видах (сырой/нормализованный/
ядро), дата ДОКУМЕНТА (а не дата строки/проводки — см. ТЗ п.12).
"""
from __future__ import annotations
import re
import datetime
from typing import Optional, Tuple
from .models import OperationCard

# --- словарь типов документов -> экономическая группа (ТЗ п.6.2, п.6.3) ---
# Порядок важен: более специфичные фразы проверяются раньше общих.
DOC_TYPE_GROUPS = [
    (r"плат[её]жн\w*\s+поручен\w*", "Оплата", "Платежное поручение"),
    (r"\bоплата\b", "Оплата", "Оплата"),
    (r"счет-фактур\w*", "Отгрузка", "Счет-фактура"),
    (r"\bупд\b", "Отгрузка", "УПД"),
    (r"наклад\w*", "Отгрузка", "Накладная"),
    (r"реализац\w*", "Отгрузка", "Реализация"),
    (r"\bпродажа\b", "Отгрузка", "Продажа"),
    (r"\bприход\b", "Отгрузка", "Приход"),
    (r"\bакт\b", "Отгрузка", "Акт"),
]

_NUMBER_TOKEN = r"[A-ZА-Я]*[-/]?\d[\d\-/A-ZА-Я]*"

# "Акт №172 от 22.01.2021 (Счет-фактура №172 от 22.01.2021)" — форма Стороны А
_RE_TYPE_NUM_DATE = re.compile(
    r"(?P<type>[А-Яа-яЁё]+(?:\s+[А-Яа-яЁё]+)?)\s*№\s*(?P<num>" + _NUMBER_TOKEN + r")\s*от\s*"
    r"(?P<date>\d{1,2}[.,]\d{1,2}[.,]\d{2,4})"
    r"(?:\s*\(\s*(?P<rel_type>[А-Яа-яЁё\-]+)\s*№\s*(?P<rel_num>" + _NUMBER_TOKEN + r")\s*от\s*"
    r"(?P<rel_date>\d{1,2}[.,]\d{1,2}[.,]\d{2,4})\s*\))?",
    re.IGNORECASE,
)

# "Приход (171 от 19.01.2021)" / "Оплата (1037 от 19.02.2021)" — форма Стороны Б
_RE_TYPE_PAREN_NUM_DATE = re.compile(
    r"(?P<type>[А-Яа-яЁё]+)\s*\(\s*(?P<num>" + _NUMBER_TOKEN + r")\s*от\s*"
    r"(?P<date>\d{1,2}[.,]\d{1,2}[.,]\d{2,4})\s*\)",
    re.IGNORECASE,
)

# запасной вариант: тип + номер без "от даты" (напр. "Платежное поручение №1022")
_RE_TYPE_NUM_ONLY = re.compile(
    r"(?P<type>[А-Яа-яЁё]+(?:\s+[А-Яа-яЁё]+)?)\s*№\s*(?P<num>" + _NUMBER_TOKEN + r")",
    re.IGNORECASE,
)


def classify_doc_type(type_text: str) -> Tuple[str, str]:
    """Возвращает (doc_type_raw_normalized, doc_type_group)."""
    t = type_text.strip().lower()
    for pattern, group, canon in DOC_TYPE_GROUPS:
        if re.search(pattern, t, re.IGNORECASE):
            return canon, group
    return type_text.strip(), "Неизвестно"


def parse_date(raw: str) -> Optional[datetime.date]:
    raw = raw.replace(",", ".").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            d = datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        # санити-чек года: в этом деле все документы 2019-2029; "20.03.0202"
        # (реальный OCR-артефакт из скана — перепутаны разряды 2021) формально
        # парсится strptime как год 202, но это явно мусор. Лучше явный
        # пропуск даты, чем тихо неверная дата (ТЗ п.12).
        if not (2000 <= d.year <= 2099):
            return None
        return d
    return None


def split_number(raw_num: str) -> Tuple[str, str, str]:
    """Возвращает (normalized, prefix, core).

    normalized — верхний регистр, без пробелов и без ведущих нулей ядра;
    prefix     — буквенно-символьная часть перед/после цифр (АБ-, /77, -К);
    core       — только цифры, без ведущих нулей (используется лишь как
                 признак для уровня E "цифровое ядро", не как самостоятельное
                 подтверждение совпадения — ТЗ прямо предостерегает от
                 путаницы АБ-172 и РН-172).
    """
    raw = raw_num.strip().upper()
    digits = re.findall(r"\d+", raw)
    core = digits[-1].lstrip("0") or "0" if digits else ""
    prefix_parts = re.sub(r"\d", "", raw)
    normalized = re.sub(r"[\s]", "", raw)
    return normalized, prefix_parts, core


def parse_raw_line_side_a(source_file: str, row_idx: int, acc_date: str, doc_text: str,
                            sum_doc: str, my_debit: str, my_credit: str) -> OperationCard:
    source_text = f"{acc_date}\t{doc_text}\t{sum_doc}\t{my_debit}\t{my_credit}"
    card = _parse_common(source_file, row_idx, acc_date, doc_text, side="A", source_text=source_text)
    card.debit = _to_float(my_debit)
    card.credit = _to_float(my_credit)
    card.__post_init__()
    return card


def parse_raw_line_side_b(source_file: str, row_idx: int, acc_date: str, doc_text: str,
                            debit: str, credit: str) -> OperationCard:
    source_text = f"{acc_date}\t{doc_text}\t{debit}\t{credit}"
    card = _parse_common(source_file, row_idx, acc_date, doc_text, side="B", source_text=source_text)
    card.debit = _to_float(debit)
    card.credit = _to_float(credit)
    card.__post_init__()
    return card


def _to_float(v: str) -> Optional[float]:
    v = (v or "").strip()
    if v in ("", "-", "—"):
        return None
    try:
        return float(v.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _parse_common(source_file: str, row_idx: int, acc_date: str, doc_text: str, side: str,
                    source_text: str) -> OperationCard:
    card = OperationCard(
        source_file=source_file, page=0, row=row_idx, source_text=source_text,
        side=side, raw_document_text=doc_text,
    )
    card.accounting_date = parse_date(acc_date)

    m = _RE_TYPE_NUM_DATE.search(doc_text) or _RE_TYPE_PAREN_NUM_DATE.search(doc_text)
    if m:
        card.doc_type_raw, card.doc_type_group = classify_doc_type(m.group("type"))
        card.document_number_raw = m.group("num")
        card.document_date = parse_date(m.group("date"))
        norm, prefix, core = split_number(m.group("num"))
        card.document_number_normalized = norm
        card.document_number_prefix = prefix
        card.document_number_core = core
        if "rel_num" in m.groupdict() and m.group("rel_num"):
            card.related_document_text = f'{m.group("rel_type")} №{m.group("rel_num")} от {m.group("rel_date")}'
            card.related_document_number_raw = m.group("rel_num")
            rel_norm, _, _ = split_number(m.group("rel_num"))
            card.related_document_number_normalized = rel_norm
    else:
        m2 = _RE_TYPE_NUM_ONLY.search(doc_text)
        if m2:
            card.doc_type_raw, card.doc_type_group = classify_doc_type(m2.group("type"))
            card.document_number_raw = m2.group("num")
            norm, prefix, core = split_number(m2.group("num"))
            card.document_number_normalized = norm
            card.document_number_prefix = prefix
            card.document_number_core = core
            card.processing_notes.append("дата документа не найдена в тексте — используется дата строки")
            card.document_date = None
        else:
            card.processing_notes.append("не удалось разобрать номер документа регулярными выражениями")
            card.ocr_confidence = 0.3

    return card
