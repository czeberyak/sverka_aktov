# -*- coding: utf-8 -*-
"""
Сравнение с эталоном (data/reference/Итоги_для_проверки.xlsx) и расчёт
Precision / Recall / F1 (ТЗ, п.8 "Критерии оценки" — 35% веса).

Эталон — список документов, не подтверждённых одной из сторон на
31.08.2021, в формате "<номер> от <дата>" + сумма. Сопоставляем его с
карточками, которые наш алгоритм отнёс к категориям "Отсутствует у
Стороны А/Б", по паре (цифровое ядро номера, дата документа).
"""
from __future__ import annotations
import re
import openpyxl
from dataclasses import dataclass
from typing import List, Dict, Tuple
from .models import OperationCard


@dataclass
class Metrics:
    true_positive: int
    false_negative: int
    false_positive: int
    precision: float
    recall: float
    f1: float
    reference_count: int
    reference_sum: float
    predicted_count: int
    predicted_sum: float
    missing_from_reference: List[Tuple[str, str]]
    extra_vs_reference: List[Tuple[str, str]]


def load_reference(path: str) -> Dict[Tuple[str, str], float]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    ref: Dict[Tuple[str, str], float] = {}
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row or not row[0]:
            continue
        doc, amount = row[0], row[1]
        m = re.match(r"(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})", str(doc))
        if m and amount is not None:
            key = (m.group(1).lstrip("0") or "0", m.group(2))
            ref[key] = float(amount)
    return ref


def _key_for_card(card: OperationCard) -> Tuple[str, str]:
    date_str = card.document_date.strftime("%d.%m.%Y") if card.document_date else ""
    return (card.document_number_core, date_str)


def evaluate(missing_cards: List[OperationCard], reference_path: str, date_tolerance_days: int = 1) -> Metrics:
    """Сравнение со списком-эталоном.

    ВАЖНО (найдено при разборе ошибок, см. ТЕХНИЧЕСКИЙ_РАЗБОР.md): часть
    расхождений на поверку оказывается одним и тем же документом с датой,
    отличающейся на 1 день (напр. №2613: эталон "25.07.2021", у нас —
    "26.07.2021") — то есть ровно тот случай, под который в самом алгоритме
    заведён уровень C (допуск по дате). Раньше сверка с эталоном сравнивала
    ключи строго и один и тот же документ засчитывался ОДНОВРЕМЕННО как
    FN (не нашли) и FP (нашли что-то лишнее), искусственно занижая метрику.
    Теперь сверка использует тот же допуск по дате, что и уровень C."""
    reference = load_reference(reference_path)
    predicted: Dict[Tuple[str, str], float] = {_key_for_card(c): c.amount for c in missing_cards}

    import datetime as _dt

    def _parse(d: str):
        try:
            return _dt.datetime.strptime(d, "%d.%m.%Y").date()
        except ValueError:
            return None

    ref_keys, pred_keys = set(reference), set(predicted)
    tp_keys, matched_pred = set(), set()
    unmatched_ref = []
    for rk in ref_keys:
        if rk in pred_keys:
            tp_keys.add(rk)
            matched_pred.add(rk)
            continue
        r_num, r_date_s = rk
        r_date = _parse(r_date_s)
        found = None
        if r_date:
            for pk in pred_keys - matched_pred:
                p_num, p_date_s = pk
                if p_num != r_num:
                    continue
                p_date = _parse(p_date_s)
                if p_date and abs((p_date - r_date).days) <= date_tolerance_days:
                    found = pk
                    break
        if found:
            tp_keys.add(rk)
            matched_pred.add(found)
        else:
            unmatched_ref.append(rk)

    fn_keys = set(unmatched_ref)
    fp_keys = pred_keys - matched_pred

    tp, fn, fp = len(tp_keys), len(fn_keys), len(fp_keys)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return Metrics(
        true_positive=tp, false_negative=fn, false_positive=fp,
        precision=round(precision, 4), recall=round(recall, 4), f1=round(f1, 4),
        reference_count=len(reference), reference_sum=round(sum(reference.values()), 2),
        predicted_count=len(predicted), predicted_sum=round(sum(predicted.values()), 2),
        missing_from_reference=sorted(fn_keys), extra_vs_reference=sorted(fp_keys),
    )


def quality_tier(f1: float) -> str:
    if f1 >= 0.90:
        return "Отлично"
    if f1 >= 0.75:
        return "Хорошо"
    if f1 >= 0.60:
        return "Базово"
    return "Ниже базового порога"
