# -*- coding: utf-8 -*-
"""
Модель данных "Карточка операции" — единая структура, к которой приводится
каждая строка обоих актов до начала сопоставления (ТЗ, п.5).

Ключевой принцип задания: сопоставление идёт не по строкам, а по
реквизитам, извлечённым из строки. Поэтому карточка хранит и сырой текст
(для аудита/объяснимости), и разобранные атрибуты (для матчинга).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import datetime


@dataclass
class OperationCard:
    # --- происхождение записи (для журнала обработки и аудита) ---
    source_file: str
    page: int
    row: int
    source_text: str  # полный исходный текст строки — храним всегда, без изменений

    # --- сторона ---
    side: str  # "A" (Покупатель) | "B" (Поставщик)

    # --- наименование документа ---
    raw_document_text: str  # как напечатано в акте, напр. "Акт №172 от 22.01.2021 (Счет-фактура №172 от 22.01.2021)"
    doc_type_raw: str = ""        # первое слово-маркер: "Акт", "Продажа", "Приход", "Платежное поручение", "Оплата"...
    doc_type_group: str = ""      # экономическая группа: "Отгрузка" | "Оплата" | "Неизвестно"

    # --- номер документа (три уровня, п.6.2-6.3) ---
    document_number_raw: str = ""         # как найдено в тексте, включая префикс/суффикс/нули
    document_number_normalized: str = ""  # без пробелов/лишних разделителей, без ведущих нулей
    document_number_prefix: str = ""      # буквенный префикс/суффикс, если есть (АБ, РН, К...)
    document_number_core: str = ""        # только цифровое ядро — используется лишь как признак-кандидат (уровень E)

    # --- даты (приоритет — дата документа, не дата проводки, п.6.3) ---
    document_date: Optional[datetime.date] = None     # дата, стоящая в тексте рядом с номером ("от ДД.ММ.ГГГГ")
    accounting_date: Optional[datetime.date] = None    # дата строки/дата проводки по данным акта (может быть датой пакетной выгрузки)

    # --- суммы ---
    currency: str = "RUB"
    debit: Optional[float] = None
    credit: Optional[float] = None
    amount: float = 0.0     # abs(debit or credit) — суммарная величина операции
    direction: str = ""     # "debit" | "credit" — в какой колонке стоит сумма у данной стороны

    # --- сопутствующие документы (напр. второй номер в скобках: Счет-фактура) ---
    related_document_text: str = ""
    related_document_number_raw: str = ""
    related_document_number_normalized: str = ""

    # --- служебное поле для журнала обработки ---
    ocr_confidence: float = 1.0
    processing_notes: field(default_factory=list) = None

    def __post_init__(self):
        if self.processing_notes is None:
            self.processing_notes = []
        if self.debit and not self.credit:
            self.direction = "debit"
            self.amount = abs(self.debit)
        elif self.credit and not self.debit:
            self.direction = "credit"
            self.amount = abs(self.credit)

    @property
    def card_id(self) -> str:
        return f"{self.side}:{self.source_file}:p{self.page}:r{self.row}"

    @property
    def effective_date(self) -> Optional[datetime.date]:
        """Дата, которая используется для сопоставления — документная, а не учётная (ТЗ, подсказки п.12)."""
        return self.document_date or self.accounting_date
