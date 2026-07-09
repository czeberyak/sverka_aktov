# -*- coding: utf-8 -*-
"""
Контракт OCR-провайдера.

Архитектурное решение (см. ТЕХНИЧЕСКИЙ_РАЗБОР.md, раздел "OCR"):
распознавание вынесено за интерфейс OCRProvider, чтобы можно было
подключить любую реализацию, не меняя остальной пайплайн (парсинг,
нормализацию, сопоставление, отчёт). В проекте есть две реализации:

  1. TesseractProvider     — локальный, бесплатный, офлайн OCR.
  2. VisionAssistedProvider — использует заранее подготовленный (одним
     из мультимодальных LLM) построчный текстовый слой сканов.

Обе возвращают один и тот же формат: список RawLine на страницу.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Protocol


@dataclass
class RawLine:
    page: int
    row: int
    text: str          # исходная строка целиком, как она "прочитана"
    confidence: float  # 0..1, оценка уверенности распознавания строки


class OCRProvider(Protocol):
    name: str

    def extract(self, pdf_path: str) -> List[RawLine]:
        """Возвращает построчный текстовый слой документа."""
        ...
