# -*- coding: utf-8 -*-
"""
Автоматическое извлечение таблицы из PDF-скана через vision-LLM.

ЗАЧЕМ ЭТОТ ФАЙЛ: без него программа принимает на вход только уже готовые
TSV (data/raw/side_a_raw.tsv, side_b_raw.tsv) — а они получены при
подготовке решения (см. ТЕХНИЧЕСКИЙ_РАЗБОР.md, §6). Этот модуль
автоматизирует тот шаг: даёт команду, которая берёт НОВУЮ пару PDF и сама
получает из неё TSV нужного формата.

ДВА ПРОВАЙДЕРА, ОБЩИЙ ИНТЕРФЕЙС (.ask_image(path, prompt) -> str):
    - src/ocr/claude_vision_provider.py     — платный, Anthropic API
    - src/ocr/openrouter_vision_provider.py — бесплатный (:free), OpenRouter

Выбор — параметром provider при вызове extract_pdf_to_tsv(...) или флагом
--ocr-provider {claude,openrouter} в run.py / --provider в CLI этого файла.
Сам цикл по страницам (ниже) не знает, какой именно провайдер использует —
это и есть архитектурная развязка, ради которой оба провайдера имеют
одинаковый .ask_image().

Почему vision-LLM, а не классический Tesseract — см. tesseract_provider.py
и тех.разбор: на этих сканах (наклон, рукописные пометки, замазанные поля)
Tesseract без tesseract-ocr-rus даёт нечитаемые даты и путает цифры.

ИСПОЛЬЗОВАНИЕ:
    # Claude (платно, нужен ANTHROPIC_API_KEY):
    python -m src.ocr_ingest --pdf scan_a.pdf --side A --out data/raw/side_a_raw.tsv --provider claude

    # OpenRouter (бесплатно, нужен OPENROUTER_API_KEY):
    python -m src.ocr_ingest --pdf scan_a.pdf --side A --out data/raw/side_a_raw.tsv --provider openrouter

    # или сразу из run.py:
    python run.py --side-a-pdf scan_a.pdf --side-b-pdf scan_b.pdf --ocr-provider openrouter
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import tempfile
from typing import List, Literal, Protocol, Union

SIDE_A_PROMPT = """Перед тобой страница скана «Акт сверки взаимных расчётов».
Таблица имеет колонки: Дата | Документ | Валюта документа | Сумма документа | Дебет (МЫ) | Кредит (МЫ) | Сумма документа (ОНИ) | Дебет (ОНИ) | Кредит (ОНИ).

Выведи КАЖДУЮ строку таблицы с этой страницы (кроме "Сальдо начальное/конечное" и "Обороты за период") одной строкой TSV (разделитель — TAB), СТРОГО в порядке колонок:
дата_проводки<TAB>текст_документа<TAB>сумма_документа<TAB>дебет_МЫ<TAB>кредит_МЫ

Пустая ячейка/прочерк — пиши "-". Числа без разделителей тысяч, точка как разделитель дробной части (35000.00). Никаких заголовков, пояснений, markdown — только строки TSV, каждая с новой строки. Если на странице нет таблицы (титул/подписи) — не выводи ничего."""

SIDE_B_PROMPT = """Перед тобой страница скана «Акт сверки взаимных расчётов». Таблица двухсторонняя: слева "По данным ОНИ" (Дата, Документ, Дебет, Кредит), справа "По данным МЫ" (Дата, Документ, Дебет, Кредит) — для одной и той же операции в одной строке.

Нужны данные ТОЛЬКО из правой части ("По данным МЫ"). Выведи каждую строку TSV:
дата<TAB>текст_документа<TAB>дебет<TAB>кредит

Зачёркнутые/помеченные от руки строки выводи как обычные (пометки не расшифровывай и не описывай их смысл). Пустая ячейка/прочерк — "-". Точка как разделитель дробной части. Без заголовков и пояснений — только строки TSV."""


class VisionProvider(Protocol):
    """Общий контракт обоих провайдеров — см. docstring файла."""
    def ask_image(self, image_path: str, prompt: str) -> str: ...


def build_provider(name: str, **kwargs) -> "VisionProvider":
    """Единая точка выбора провайдера — фабрика, а не if/else в бизнес-логике."""
    if name == "claude":
        from .ocr.claude_vision_provider import ClaudeVisionProvider
        return ClaudeVisionProvider(**kwargs)
    if name == "openrouter":
        from .ocr.openrouter_vision_provider import OpenRouterVisionProvider
        return OpenRouterVisionProvider(**kwargs)
    if name == "tesseract":
        from .ocr.tesseract_table_provider import TesseractTableProvider
        return TesseractTableProvider(**kwargs)
    raise ValueError(f"Неизвестный провайдер: {name!r}. Допустимо: 'claude', 'openrouter', 'tesseract'.")


def render_pdf_pages(pdf_path: str, out_dir: str, dpi: int = 300) -> List[str]:
    prefix = os.path.join(out_dir, "pg")
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf_path, prefix],
                    check=True, capture_output=True)
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".png"))


def extract_pdf_to_tsv(pdf_path: str, side: str, out_tsv_path: str,
                         provider: Union["VisionProvider", str] = "claude",
                         dpi: int = 300, verbose: bool = True, **provider_kwargs) -> int:
    """Читает pdf_path постранично через vision-провайдер, пишет результат в
    out_tsv_path в формате, который parse_raw_line_side_a/b (src/parsing.py)
    ожидают на входе. Возвращает число извлечённых строк.

    provider — либо готовый объект с .ask_image(...), либо строка
    'claude'/'openrouter' (тогда создаётся через build_provider(**provider_kwargs))."""
    if isinstance(provider, str):
        provider = build_provider(provider, **provider_kwargs)

    prompt = SIDE_A_PROMPT if side == "A" else SIDE_B_PROMPT
    header = "acc_date\tdoc_text\tsum_doc\tmy_debit\tmy_credit" if side == "A" else "acc_date\tdoc_text\tdebit\tcredit"
    expected_cols = 5 if side == "A" else 4

    rows: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        pages = render_pdf_pages(pdf_path, tmp, dpi=dpi)
        for i, page in enumerate(pages, start=1):
            text = provider.ask_image(page, prompt)
            page_rows = [l.strip() for l in text.splitlines()
                          if l.strip() and l.count("\t") == expected_cols - 1]
            noisy = [l for l in text.splitlines() if l.strip()]
            if verbose:
                print(f"  [ocr_ingest:{getattr(provider, 'name', provider)}] страница {i}/{len(pages)}: "
                      f"{len(page_rows)} строк ({len(noisy) - len(page_rows)} отброшено — не {expected_cols} колонок)")
            rows.extend(page_rows)

    os.makedirs(os.path.dirname(out_tsv_path) or ".", exist_ok=True)
    with open(out_tsv_path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        f.write("\n".join(rows) + ("\n" if rows else ""))
    return len(rows)


def main():
    p = argparse.ArgumentParser(description="OCR-приём PDF-скана акта сверки через vision-LLM")
    p.add_argument("--pdf", required=True, help="путь к PDF-скану")
    p.add_argument("--side", required=True, choices=["A", "B"], help="A = Покупатель, B = Поставщик")
    p.add_argument("--out", required=True, help="куда записать TSV")
    p.add_argument("--provider", default="claude", choices=["claude", "openrouter", "tesseract"])
    p.add_argument("--model", default=None, help="переопределить модель провайдера по умолчанию")
    p.add_argument("--sleep", type=float, default=None,
                    help="пауза между страницами, сек (по умолчанию: 0 для claude, 4 для openrouter)")
    p.add_argument("--dpi", type=int, default=300)
    args = p.parse_args()

    kwargs = {}
    if args.model:
        kwargs["model"] = args.model
    if args.sleep is not None:
        kwargs["sleep_between_calls"] = args.sleep
    elif args.provider == "openrouter":
        kwargs["sleep_between_calls"] = 4.0  # см. предупреждение о rate limits в openrouter_vision_provider.py

    try:
        provider = build_provider(args.provider, **kwargs)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    n = extract_pdf_to_tsv(args.pdf, args.side, args.out, provider=provider, dpi=args.dpi)
    print(f"Готово: {n} строк -> {args.out}")


if __name__ == "__main__":
    main()
