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
через vision-LLM — два варианта, --ocr-provider выбирает:

    # OpenRouter — бесплатные (:free) модели, нужен только OPENROUTER_API_KEY,
    # без доп. pip-пакетов (см. src/ocr/openrouter_vision_provider.py):
    python run.py --side-a-pdf scan_a.pdf --side-b-pdf scan_b.pdf --ocr-provider openrouter

    # Claude — платно, нужны `pip install anthropic` и ANTHROPIC_API_KEY:
    python run.py --side-a-pdf scan_a.pdf --side-b-pdf scan_b.pdf --ocr-provider claude

Это ЗАМЕНЯЕТ --side-a/--side-b: сначала PDF превращается в TSV нужного формата
(сохраняется в data/raw/, чтобы не гонять OCR повторно при отладке пайплайна),
затем обрабатывается как обычно. Офлайн без API — Tesseract
(src/ocr/tesseract_provider.py), доступен только через код, качество на
кириллице без пакета tesseract-ocr-rus не гарантировано (см. тех.разбор).
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.pipeline import run_pipeline

DEFAULT_MODEL = {"claude": "claude-sonnet-4-6", "openrouter": "nvidia/nemotron-nano-12b-v2-vl:free",
                  "tesseract": "rus+eng"}
REQUIRED_ENV = {"claude": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}  # tesseract — без ключа, офлайн


def main():
    p = argparse.ArgumentParser(description="Сверка двух актов взаимных расчётов")
    p.add_argument("--side-a", default="data/raw/side_a_raw.tsv",
                    help="TSV с распознанными строками Стороны А (Покупатель)")
    p.add_argument("--side-b", default="data/raw/side_b_raw.tsv",
                    help="TSV с распознанными строками Стороны Б (Поставщик)")
    p.add_argument("--side-a-pdf", default=None,
                    help="ИЛИ: путь к новому PDF-скану Стороны А — вместо --side-a запускает OCR")
    p.add_argument("--side-b-pdf", default=None,
                    help="ИЛИ: путь к новому PDF-скану Стороны Б — вместо --side-b запускает OCR")
    p.add_argument("--ocr-provider", default="openrouter", choices=["claude", "openrouter", "tesseract"],
                    help="какой OCR использовать для --side-a-pdf/--side-b-pdf (tesseract — офлайн, без ключа)")
    p.add_argument("--ocr-model", default=None, help="переопределить модель провайдера по умолчанию")
    p.add_argument("--ocr-sleep", type=float, default=None,
                    help="пауза между страницами, сек (по умолчанию 0 для claude, 4 для openrouter — rate limits)")
    p.add_argument("--reference", default="data/reference/Итоги_для_проверки.xlsx",
                    help="Эталонный файл для расчёта метрик")
    p.add_argument("--output", default="output/Отчёт_сверки.xlsx",
                    help="Путь для итогового Excel-отчёта")
    p.add_argument("--mode", default="standard", choices=["strict", "standard", "flexible"],
                    help="Режим проверки (ТЗ п.6.5)")
    args = p.parse_args()

    side_a_path, side_b_path = args.side_a, args.side_b

    if args.side_a_pdf or args.side_b_pdf:
        from src.ocr_ingest import extract_pdf_to_tsv, build_provider

        env_var = REQUIRED_ENV.get(args.ocr_provider)
        if env_var and not os.environ.get(env_var):
            print(f"Указан --side-a-pdf/--side-b-pdf с --ocr-provider {args.ocr_provider}, "
                  f"но {env_var} не задан.\nexport {env_var}=...\n"
                  f"(или используйте --side-a/--side-b с уже готовым TSV, либо --ocr-provider tesseract "
                  f"для офлайн-распознавания без ключа)", file=sys.stderr)
            sys.exit(1)

        provider_kwargs = {}
        if args.ocr_provider == "tesseract":
            if args.ocr_model:
                provider_kwargs["lang"] = args.ocr_model  # для tesseract "модель" = языковой пакет, напр. rus+eng
        else:
            if args.ocr_model:
                provider_kwargs["model"] = args.ocr_model
            if args.ocr_sleep is not None:
                provider_kwargs["sleep_between_calls"] = args.ocr_sleep
            elif args.ocr_provider == "openrouter":
                provider_kwargs["sleep_between_calls"] = 4.0  # см. предупреждение о rate limits

        provider = build_provider(args.ocr_provider, **provider_kwargs)
        model_used = getattr(provider, "model", DEFAULT_MODEL.get(args.ocr_provider, "?"))

        if args.side_a_pdf:
            side_a_path = "data/raw/side_a_raw.tsv"
            print(f"[ocr:{args.ocr_provider}] Сторона А: распознаю {args.side_a_pdf} ({model_used}) ...")
            n = extract_pdf_to_tsv(args.side_a_pdf, "A", side_a_path, provider=provider)
            print(f"[ocr:{args.ocr_provider}] Сторона А: получено {n} строк -> {side_a_path}")
        if args.side_b_pdf:
            side_b_path = "data/raw/side_b_raw.tsv"
            print(f"[ocr:{args.ocr_provider}] Сторона Б: распознаю {args.side_b_pdf} ({model_used}) ...")
            n = extract_pdf_to_tsv(args.side_b_pdf, "B", side_b_path, provider=provider)
            print(f"[ocr:{args.ocr_provider}] Сторона Б: получено {n} строк -> {side_b_path}")

        print("[ocr] ВАЖНО: распознанные TSV — вероятностный вывод модели, не заверенный "
              "источник. Перед тем как доверять итоговому отчёту, сверьте несколько строк "
              "вручную со сканом (README, раздел «Проверка результата»).")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    run_pipeline(side_a_path, side_b_path, args.reference, args.output, mode=args.mode)


if __name__ == "__main__":
    main()
