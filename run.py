#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа. Запуск по умолчанию (на приложенных данных):

    python run.py

Другие режимы:

    python run.py --mode strict
    python run.py --mode flexible
    python run.py --side-a data/raw/side_a_raw.tsv --side-b data/raw/side_b_raw.tsv \
                   --reference data/reference/Итоги_для_проверки.xlsx --output output/report.xlsx

Сверка ДВУХ НОВЫХ PDF-сканов (не приложенных к заданию), автоматический OCR-приём
через Claude vision API (нужны `pip install anthropic` и ANTHROPIC_API_KEY;
см. src/ocr_ingest.py — там же честно про стоимость и приватность):

    python run.py --side-a-pdf path/to/scan_a.pdf --side-b-pdf path/to/scan_b.pdf

Это ЗАМЕНЯЕТ --side-a/--side-b: сначала PDF превращается в TSV нужного формата
(сохраняется в data/raw/, чтобы не гонять OCR повторно при отладке пайплайна),
затем обрабатывается как обычно. Без ANTHROPIC_API_KEY используйте офлайн
Tesseract (src/ocr/tesseract_provider.py) — доступен только через код,
качество на кириллице без пакета tesseract-ocr-rus не гарантировано
(см. ТЕХНИЧЕСКИЙ_РАЗБОР.md, §"OCR").
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.pipeline import run_pipeline


def main():
    p = argparse.ArgumentParser(description="Сверка двух актов взаимных расчётов")
    p.add_argument("--side-a", default="data/raw/side_a_raw.tsv",
                    help="TSV с распознанными строками Стороны А (Покупатель)")
    p.add_argument("--side-b", default="data/raw/side_b_raw.tsv",
                    help="TSV с распознанными строками Стороны Б (Поставщик)")
    p.add_argument("--side-a-pdf", default=None,
                    help="ИЛИ: путь к новому PDF-скану Стороны А — вместо --side-a запускает OCR (Claude API)")
    p.add_argument("--side-b-pdf", default=None,
                    help="ИЛИ: путь к новому PDF-скану Стороны Б — вместо --side-b запускает OCR (Claude API)")
    p.add_argument("--ocr-model", default="claude-sonnet-4-6", help="модель для --side-a-pdf/--side-b-pdf")
    p.add_argument("--reference", default="data/reference/Итоги_для_проверки.xlsx",
                    help="Эталонный файл для расчёта метрик")
    p.add_argument("--output", default="output/Отчёт_сверки.xlsx",
                    help="Путь для итогового Excel-отчёта")
    p.add_argument("--mode", default="standard", choices=["strict", "standard", "flexible"],
                    help="Режим проверки (ТЗ п.6.5)")
    args = p.parse_args()

    side_a_path, side_b_path = args.side_a, args.side_b

    if args.side_a_pdf or args.side_b_pdf:
        from src.ocr_ingest import extract_pdf_to_tsv
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("Указан --side-a-pdf/--side-b-pdf, но ANTHROPIC_API_KEY не задан.\n"
                  "export ANTHROPIC_API_KEY=... (нужен ключ Anthropic) или используйте --side-a/--side-b "
                  "с уже готовым TSV.", file=sys.stderr)
            sys.exit(1)
        if args.side_a_pdf:
            side_a_path = "data/raw/side_a_raw.tsv"
            print(f"[ocr] Сторона А: распознаю {args.side_a_pdf} через {args.ocr_model} ...")
            n = extract_pdf_to_tsv(args.side_a_pdf, "A", side_a_path, model=args.ocr_model)
            print(f"[ocr] Сторона А: получено {n} строк -> {side_a_path}")
        if args.side_b_pdf:
            side_b_path = "data/raw/side_b_raw.tsv"
            print(f"[ocr] Сторона Б: распознаю {args.side_b_pdf} через {args.ocr_model} ...")
            n = extract_pdf_to_tsv(args.side_b_pdf, "B", side_b_path, model=args.ocr_model)
            print(f"[ocr] Сторона Б: получено {n} строк -> {side_b_path}")
        print("[ocr] ВАЖНО: распознанные TSV — вероятностный вывод модели, не заверенный "
              "источник. Перед тем как доверять итоговому отчёту, сверьте несколько строк "
              "вручную со сканом (см. README, раздел «Проверка результата»).")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    run_pipeline(side_a_path, side_b_path, args.reference, args.output, mode=args.mode)


if __name__ == "__main__":
    main()

