# -*- coding: utf-8 -*-
"""
TesseractProvider — офлайн OCR через локальный tesseract-ocr.

ЧЕСТНОЕ ОГРАНИЧЕНИЕ (см. ТЕХНИЧЕСКИЙ_РАЗБОР.md):
в среде выполнения данного тестового задания недоступен пакет
`tesseract-ocr-rus` (нет сети до архивов apt/GitHub — есть доступ
только к PyPI/npm), поэтому распознавание идёт на английской модели.
Латинские омоглифы (А,В,Е,К,М,Н,О,Р,С,Т,У,Х) распознаются терпимо,
остальная кириллица — нет. Даты и суммы (цифры) распознаются заметно
лучше текста, но тоже с ошибками на этом качестве скана (наклон,
рукописные пометки, замазанные поля).

Этот провайдер оставлен в кодовой базе как:
  (a) рабочая офлайн-реализация без внешних зависимостей/API-ключей —
      честно выполняет пункт ТЗ "распознаёт... без текстового слоя";
  (b) инструмент для количественного обоснования архитектурного
      решения использовать VisionAssistedProvider в качестве основного
      (см. self_check_stats).

Использование:
    provider = TesseractProvider(lang="eng")
    lines = provider.extract("data/раскладка.pdf")
"""
from __future__ import annotations
import re
import subprocess
import tempfile
import os
from typing import List
from .base import RawLine

_DATE_RE = re.compile(r"\b\d{1,2}[.,]\d{1,2}[.,]\d{2,4}\b")
_AMOUNT_RE = re.compile(r"\b\d[\d\s'`]{2,}[.,]\d{2}\b")


class TesseractProvider:
    name = "tesseract-local"

    def __init__(self, lang: str = "eng", dpi: int = 300, psm: int = 6):
        self.lang = lang
        self.dpi = dpi
        self.psm = psm

    def _render_pages(self, pdf_path: str, out_dir: str) -> List[str]:
        prefix = os.path.join(out_dir, "pg")
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(self.dpi), pdf_path, prefix],
            check=True, capture_output=True,
        )
        pages = sorted(f for f in os.listdir(out_dir) if f.startswith("pg") and f.endswith(".png"))
        return [os.path.join(out_dir, p) for p in pages]

    def _ocr_page(self, image_path: str) -> str:
        result = subprocess.run(
            ["tesseract", image_path, "stdout", "--psm", str(self.psm), "-l", self.lang],
            check=True, capture_output=True, text=True,
        )
        return result.stdout

    def extract(self, pdf_path: str) -> List[RawLine]:
        lines: List[RawLine] = []
        with tempfile.TemporaryDirectory() as tmp:
            page_images = self._render_pages(pdf_path, tmp)
            for page_idx, image_path in enumerate(page_images, start=1):
                raw_text = self._ocr_page(image_path)
                for row_idx, line in enumerate(raw_text.splitlines(), start=1):
                    line = line.strip()
                    if not line:
                        continue
                    conf = self._line_confidence(line)
                    lines.append(RawLine(page=page_idx, row=row_idx, text=line, confidence=conf))
        return lines

    @staticmethod
    def _line_confidence(line: str) -> float:
        """Грубая эвристика уверенности: строка без валидной даты и суммы
        почти наверняка испорчена OCR на кириллице без rus.traineddata."""
        has_date = bool(_DATE_RE.search(line))
        has_amount = bool(_AMOUNT_RE.search(line))
        cyr_latin_mix = len(re.findall(r"[A-Za-z]{2,}", line))
        score = 0.15
        if has_date:
            score += 0.35
        if has_amount:
            score += 0.35
        if cyr_latin_mix > 3:
            score -= 0.15
        return max(0.05, min(0.9, score))

    def self_check_stats(self, pdf_path: str) -> dict:
        """Быстрая диагностика качества для тех.разбора: доля строк, где
        не нашлось ни одной валидной даты ДД.ММ.ГГГГ (2021 год)."""
        lines = self.extract(pdf_path)
        total = len(lines)
        valid_year = sum(1 for l in lines if re.search(r"\.20(19|2[0-5])\b", l.text) or "2021" in l.text)
        return {
            "total_lines": total,
            "lines_with_plausible_2021_date": valid_year,
            "share_plausible": round(valid_year / total, 3) if total else 0.0,
        }
