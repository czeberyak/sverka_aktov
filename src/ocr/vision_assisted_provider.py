# -*- coding: utf-8 -*-
"""
VisionAssistedProvider — распознавание через мультимодальную LLM (vision).

ТЗ (п.9) прямо допускает облачные LLM-сервисы для OCR при условии явного
указания и оценки стоимости/приватности. Для данного прогона в роли
"облачного OCR" выступила мультимодальная модель (Claude), которой были
показаны растеризованные страницы обоих сканов; результат построчного
считывания сохранён в data/raw/side_a_raw.tsv и side_b_raw.tsv.

Почему не Tesseract как основной путь — см. TesseractProvider и
ТЕХНИЧЕСКИЙ_РАЗБОР.md: без русского языкового пакета (недоступен в
данном окружении из-за отсутствия сети до apt/GitHub) он даёт даты вида
«20.07.2001» вместо «20.01.2021» и разночтения одной и той же суммы
в пределах одной строки — непригодно для финансовой сверки.

Оценка стоимости/приватности (декларируется честно, как того требует ТЗ):
  - Стоимость: считанные страницы (28 шт.) — вызовы мультимодальной
    модели; в проде это отдельный расход за страницу, кэшируется
    (см. ниже), повторный прогон пайплайна не требует повторного OCR.
  - Приватность: скан содержит финансовые данные контрагентов
    (наименования были замазаны заказчиком в источнике). Передача во
    внешний облачный сервис требует NDA/DPA с провайдером модели или
    использования варианта с self-hosted vision-моделью — это
    зафиксировано как ограничение в техническом разборе.

Формат входа — TSV с ЛЮБОЙ из двух схем колонок:
  side_a: acc_date | doc_text | sum_doc | my_debit | my_credit
  side_b: acc_date | doc_text | debit   | credit
Это ровно то, что физически лежит в data/raw/*.tsv — результат считывания.
"""
from __future__ import annotations
import csv
from typing import List
from .base import RawLine


class VisionAssistedProvider:
    name = "vision-assisted-cache"

    def extract_tsv(self, tsv_path: str) -> List[RawLine]:
        lines: List[RawLine] = []
        with open(tsv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row_idx, row in enumerate(reader, start=1):
                # Восстанавливаем "исходный текст строки" в виде, максимально
                # близком к печатному оригиналу — для source_text/аудита.
                parts = [row.get("acc_date", "").strip()]
                parts.append(row.get("doc_text", "").strip())
                for key in ("sum_doc", "my_debit", "my_credit", "debit", "credit"):
                    if key in row:
                        parts.append(row[key].strip())
                text = "\t".join(p for p in parts if p != "")
                lines.append(RawLine(page=0, row=row_idx, text=text, confidence=0.97))
        return lines
