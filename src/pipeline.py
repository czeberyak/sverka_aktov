# -*- coding: utf-8 -*-
"""
Пайплайн целиком: сырые данные (TSV, результат OCR/vision-считывания) ->
карточки операций -> сопоставление -> метрики против эталона -> Excel-отчёт.
"""
from __future__ import annotations
import csv
import time
from typing import List, Tuple
from .models import OperationCard
from .parsing import parse_raw_line_side_a, parse_raw_line_side_b
from .matching import Matcher, MatchConfig, MatchResult
from .metrics import evaluate, Metrics, quality_tier
from .report import build_report


def load_side_a(tsv_path: str) -> List[OperationCard]:
    cards = []
    with open(tsv_path, encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f, delimiter="\t"), 1):
            cards.append(parse_raw_line_side_a(
                tsv_path.split("/")[-1], i, row["acc_date"], row["doc_text"],
                row["sum_doc"], row["my_debit"], row["my_credit"]))
    return cards


def load_side_b(tsv_path: str) -> List[OperationCard]:
    cards = []
    with open(tsv_path, encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f, delimiter="\t"), 1):
            cards.append(parse_raw_line_side_b(
                tsv_path.split("/")[-1], i, row["acc_date"], row["doc_text"],
                row["debit"], row["credit"]))
    return cards


def run_pipeline(side_a_path: str, side_b_path: str, reference_path: str,
                  output_path: str, mode: str = "standard") -> Tuple[List[MatchResult], Metrics]:
    t0 = time.time()
    side_a = load_side_a(side_a_path)
    side_b = load_side_b(side_b_path)

    config = {"strict": MatchConfig.strict(), "standard": MatchConfig.standard(),
              "flexible": MatchConfig.flexible()}.get(mode, MatchConfig.standard())

    matcher = Matcher(side_a, side_b, config=config)
    results, missing_a, missing_b = matcher.run()

    metrics = evaluate(missing_b, reference_path)

    build_report(output_path, side_a, side_b, results, missing_a, missing_b, metrics, config.name)

    elapsed = round(time.time() - t0, 2)
    print(f"[reconcile] Сторона А: {len(side_a)} строк | Сторона Б: {len(side_b)} строк | режим: {config.name}")
    print(f"[reconcile] сопоставлений всего: {len(results)} | отсутствует у А: {len(missing_a)} | у Б: {len(missing_b)}")
    print(f"[reconcile] Precision={metrics.precision:.3f} Recall={metrics.recall:.3f} F1={metrics.f1:.3f} "
          f"-> {quality_tier(metrics.f1)}")
    print(f"[reconcile] сумма расхождений (найдено/эталон): {metrics.predicted_sum:,.2f} / {metrics.reference_sum:,.2f} руб.")
    print(f"[reconcile] отчёт сохранён: {output_path} ({elapsed} с)")
    return results, metrics
