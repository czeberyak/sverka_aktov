# -*- coding: utf-8 -*-
"""
Автоматическое извлечение таблицы из PDF-скана через vision-LLM (Claude API).

ЗАЧЕМ ЭТОТ ФАЙЛ: без него программа принимает на вход только уже готовые
TSV (data/raw/side_a_raw.tsv, side_b_raw.tsv) — а они были получены путём
ручного считывания сканов при подготовке решения (см. ТЕХНИЧЕСКИЙ_РАЗБОР.md,
§6 "Как получены данные"). Этот модуль автоматизирует именно тот шаг: даёт
команду, которая берёт НОВУЮ пару PDF и сама получает из неё TSV нужного
формата — то есть закрывает разрыв "программе не скормить два новых файла".

Почему vision-LLM, а не классический Tesseract — см.
src/ocr/tesseract_provider.py и тех.разбор: на этих сканах (наклон от руки,
рукописные пометки, замазанные поля) Tesseract без пакета tesseract-ocr-rus
(в среде, где готовилось решение, недоступен — нет сети до apt/GitHub)
даёт нечитаемые даты и путает цифры. TesseractProvider в проекте оставлен
как честный офлайн-фолбэк и как код для количественного обоснования этого
решения, а не как основной путь.

ТРЕБОВАНИЯ:
    pip install anthropic
    export ANTHROPIC_API_KEY=...

СТОИМОСТЬ/ПРИВАТНОСТЬ (ТЗ, п.9 — раскрывать явно, а не молчать):
    - Один вызов API на страницу: на паре файлов из задания это 15+13=28
      вызовов. Порядок стоимости — единицы центов за страницу при обычных
      тарифах на дату написания; для больших объёмов дешевле пакетный API.
    - Скан уходит во внешний облачный сервис. Если это реальные акты с
      контрагентами (а не обезличенный пример) — нужен NDA/DPA с
      провайдером модели, либо self-hosted vision-модель вместо облачного
      API. Для тестового задания с уже частично замазанными данными это
      приемлемо, для продакшна — решение архитектора уровнем выше.

ИСПОЛЬЗОВАНИЕ:
    python -m src.ocr_ingest --pdf data/scan_a.pdf --side A --out data/raw/side_a_raw.tsv
    python -m src.ocr_ingest --pdf data/scan_b.pdf --side B --out data/raw/side_b_raw.tsv
    # или сразу из run.py:
    python run.py --side-a-pdf data/scan_a.pdf --side-b-pdf data/scan_b.pdf
"""
from __future__ import annotations
import argparse
import base64
import os
import subprocess
import sys
import tempfile
from typing import List, Literal

SIDE_A_PROMPT = """Перед тобой страница скана «Акт сверки взаимных расчётов».
Таблица имеет колонки: Дата | Документ | Валюта документа | Сумма документа | Дебет (МЫ) | Кредит (МЫ) | Сумма документа (ОНИ) | Дебет (ОНИ) | Кредит (ОНИ).

Выведи КАЖДУЮ строку таблицы с этой страницы (кроме "Сальдо начальное/конечное" и "Обороты за период") одной строкой TSV (разделитель — TAB), СТРОГО в порядке колонок:
дата_проводки<TAB>текст_документа<TAB>сумма_документа<TAB>дебет_МЫ<TAB>кредит_МЫ

Пустая ячейка/прочерк — пиши "-". Числа без разделителей тысяч, точка как разделитель дробной части (35000.00). Никаких заголовков, пояснений, markdown — только строки TSV, каждая с новой строки. Если на странице нет таблицы (титул/подписи) — не выводи ничего."""

SIDE_B_PROMPT = """Перед тобой страница скана «Акт сверки взаимных расчётов». Таблица двухсторонняя: слева "По данным ОНИ" (Дата, Документ, Дебет, Кредит), справа "По данным МЫ" (Дата, Документ, Дебет, Кредит) — для одной и той же операции в одной строке.

Нужны данные ТОЛЬКО из правой части ("По данным МЫ"). Выведи каждую строку TSV:
дата<TAB>текст_документа<TAB>дебет<TAB>кредит

Зачёркнутые/помеченные от руки строки выводи как обычные (пометки не расшифровывай и не описывай их смысл). Пустая ячейка/прочерк — "-". Точка как разделитель дробной части. Без заголовков и пояснений — только строки TSV."""


def render_pdf_pages(pdf_path: str, out_dir: str, dpi: int = 300) -> List[str]:
    prefix = os.path.join(out_dir, "pg")
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf_path, prefix],
                    check=True, capture_output=True)
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".png"))


def _call_claude_vision(image_path: str, prompt: str, model: str) -> str:
    """Один вызов API на одну страницу — намеренно не батчится, чтобы
    стоимость (см. заголовок файла) считалась по числу вызовов буквально."""
    import anthropic  # локальный импорт: зависимость нужна только этому пути
    client = anthropic.Anthropic()
    with open(image_path, "rb") as f:
        img_b64 = base64.standard_b64encode(f.read()).decode()
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return "".join(getattr(block, "text", "") for block in resp.content)


def extract_pdf_to_tsv(pdf_path: str, side: Literal["A", "B"], out_tsv_path: str,
                         model: str = "claude-sonnet-4-6", dpi: int = 300, verbose: bool = True) -> int:
    """Читает pdf_path постранично через vision-API, пишет результат в
    out_tsv_path в формате, который parse_raw_line_side_a/b (src/parsing.py)
    ожидают на входе. Возвращает число извлечённых строк."""
    prompt = SIDE_A_PROMPT if side == "A" else SIDE_B_PROMPT
    header = "acc_date\tdoc_text\tsum_doc\tmy_debit\tmy_credit" if side == "A" else "acc_date\tdoc_text\tdebit\tcredit"
    expected_cols = 5 if side == "A" else 4

    rows: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        pages = render_pdf_pages(pdf_path, tmp, dpi=dpi)
        for i, page in enumerate(pages, start=1):
            text = _call_claude_vision(page, prompt, model=model)
            page_rows = [l.strip() for l in text.splitlines()
                          if l.strip() and l.count("\t") == expected_cols - 1]
            skipped = [l for l in text.splitlines() if l.strip()] 
            if verbose:
                print(f"  [ocr_ingest] страница {i}/{len(pages)}: {len(page_rows)} строк "
                      f"({len(skipped) - len(page_rows)} отброшено — не {expected_cols} колонок)")
            rows.extend(page_rows)

    os.makedirs(os.path.dirname(out_tsv_path) or ".", exist_ok=True)
    with open(out_tsv_path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        f.write("\n".join(rows) + ("\n" if rows else ""))
    return len(rows)


def main():
    p = argparse.ArgumentParser(description="OCR-приём PDF-скана акта сверки через Claude vision API")
    p.add_argument("--pdf", required=True, help="путь к PDF-скану")
    p.add_argument("--side", required=True, choices=["A", "B"], help="A = Покупатель, B = Поставщик")
    p.add_argument("--out", required=True, help="куда записать TSV")
    p.add_argument("--model", default="claude-sonnet-4-6")
    p.add_argument("--dpi", type=int, default=300)
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY не задан. export ANTHROPIC_API_KEY=... и повторите.", file=sys.stderr)
        sys.exit(1)

    n = extract_pdf_to_tsv(args.pdf, args.side, args.out, model=args.model, dpi=args.dpi)
    print(f"Готово: {n} строк -> {args.out}")


if __name__ == "__main__":
    main()
