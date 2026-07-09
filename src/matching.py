# -*- coding: utf-8 -*-
"""
Многоуровневое сопоставление операций (ТЗ, п.6.4-6.6).

Подход: не "первый попавшийся", а явная балльная оценка (0..100) по трём
независимым признакам — номер, дата, сумма — плюс проверка экономической
группы документа как обязательного условия входа в рассмотрение.
Итоговый уровень (A-F) и статус выводятся ИЗ баллов и того, какие именно
признаки совпали, а не назначаются отдельно, чтобы решение было
воспроизводимо и объяснимо (ТЗ, п.6.7).

Баллы (из 100):
    номер, точное совпадение (normalized)        -> 40
    номер, совпадение только по цифровому ядру    -> 20
    дата документа, точное совпадение             -> 35
    дата документа, расхождение 1-3 дня            -> 18
    сумма, точное совпадение (±0.01 руб)           -> 25
    направление (дебет/кредит) совпало             -> бонус, не блокирует

Пороги уверенности (ТЗ п.6.5): >=90 подтверждено, 75-89 высоковероятно,
55-74 возможно, <55 не сопоставлено.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
import datetime
import itertools
from .models import OperationCard

AMOUNT_TOL = 0.01
DATE_TOL_DAYS = 3

SCORE_NUMBER_EXACT = 40
SCORE_NUMBER_CORE = 20
SCORE_DATE_EXACT = 35
SCORE_DATE_NEAR = 18
SCORE_AMOUNT_EXACT = 25


@dataclass
class MatchConfig:
    """Режимы проверки (ТЗ п.6.5): строгий/стандартный/гибкий/пользовательский.

    Меняет допуски и то, разрешён ли резерв "по ядру номера" (E) и
    требование совпадения экономической группы.
    """
    name: str = "standard"
    date_tol_days: int = DATE_TOL_DAYS
    amount_tol: float = AMOUNT_TOL
    allow_core_fallback: bool = True
    require_group_match: bool = True

    @staticmethod
    def strict() -> "MatchConfig":
        return MatchConfig(name="strict", date_tol_days=0, amount_tol=0.01,
                            allow_core_fallback=False, require_group_match=True)

    @staticmethod
    def standard() -> "MatchConfig":
        return MatchConfig(name="standard")

    @staticmethod
    def flexible() -> "MatchConfig":
        return MatchConfig(name="flexible", date_tol_days=5, amount_tol=1.0,
                            allow_core_fallback=True, require_group_match=False)


@dataclass
class MatchResult:
    level: str                 # "A".."F" | "-"
    status: str                 # одна из 12 категорий (ТЗ п.6.6)
    confidence: int              # 0..100
    side_a: Optional[OperationCard]
    side_b: Optional[OperationCard]
    side_b_group: Optional[List[OperationCard]] = None   # для уровня F (N строк)
    side_a_group: Optional[List[OperationCard]] = None
    explanation: str = ""
    candidates_considered: int = 0
    amount_diff: float = 0.0
    date_diff_days: Optional[int] = None


def _amount_eq(a: float, b: float) -> bool:
    return abs(a - b) < AMOUNT_TOL


def _score_pair(a: OperationCard, b: OperationCard, cfg: "MatchConfig") -> Tuple[int, dict]:
    """Считает баллы пары и возвращает (score, детали для объяснения)."""
    details = {"number": None, "date": None, "amount": None, "direction": None}
    score = 0

    a_numbers = {a.document_number_normalized, a.related_document_number_normalized} - {""}
    b_numbers = {b.document_number_normalized, b.related_document_number_normalized} - {""}
    if a_numbers & b_numbers:
        score += SCORE_NUMBER_EXACT
        details["number"] = "exact"
        details["number_via_related"] = a.document_number_normalized not in b_numbers
    elif cfg.allow_core_fallback and a.document_number_core and a.document_number_core == b.document_number_core:
        score += SCORE_NUMBER_CORE
        details["number"] = "core"
    else:
        details["number"] = "none"

    da, db = a.effective_date, b.effective_date
    if da and db:
        diff = abs((da - db).days)
        if diff == 0:
            score += SCORE_DATE_EXACT
            details["date"] = ("exact", 0)
        elif diff <= cfg.date_tol_days:
            score += SCORE_DATE_NEAR
            details["date"] = ("near", diff)
        else:
            details["date"] = ("far", diff)
    else:
        details["date"] = ("missing", None)

    if abs(a.amount - b.amount) < cfg.amount_tol:
        score += SCORE_AMOUNT_EXACT
        details["amount"] = "exact"
    else:
        details["amount"] = "diff"

    details["direction"] = "same" if a.direction == b.direction else "mirrored"
    if details["direction"] == "same":
        score += 5  # небольшой бонус, не блокирует расхождение (ТЗ п.12: направление может быть зеркальным)

    return score, details


def _level_from_details(details: dict, score: int) -> str:
    num, date, amount = details["number"], details["date"], details["amount"]
    date_kind = date[0] if isinstance(date, tuple) else date
    if amount == "exact" and num == "exact" and date_kind == "exact":
        return "A" if details["direction"] == "same" else "B"
    if amount == "exact" and num == "exact" and date_kind == "near":
        return "C"
    if amount == "exact" and num == "none" and date_kind == "exact":
        return "D"
    if amount == "exact" and num == "core" and date_kind == "exact":
        return "E"
    return "-"


def _explain(details: dict, level: str) -> str:
    num, date, amount, direction = details["number"], details["date"], details["amount"], details["direction"]
    parts = []
    if num == "exact":
        if details.get("number_via_related"):
            parts.append("номера совпали через связанный документ (напр. номер счёт-фактуры = номер у другой стороны)")
        else:
            parts.append("номера совпали")
    elif num == "core":
        parts.append("совпало цифровое ядро номера (разные префиксы/форматы)")
    else:
        parts.append("номер не распознан или не совпал")
    date_kind = date[0] if isinstance(date, tuple) else date
    if date_kind == "exact":
        parts.append("дата документа совпала")
    elif date_kind == "near":
        parts.append(f"дата отличается на {date[1]} дн. (в допуске)")
    elif date_kind == "far":
        parts.append(f"дата отличается на {date[1]} дн. (вне допуска)")
    else:
        parts.append("дата не распознана")
    parts.append("сумма совпала" if amount == "exact" else "сумма отличается")
    parts.append("направление дебет/кредит совпало" if direction == "same" else "направление зеркально/различается")
    return "Сопоставлено: " + "; ".join(parts) + "."


class Matcher:
    def __init__(self, side_a: List[OperationCard], side_b: List[OperationCard],
                 config: "MatchConfig" = None):
        self.side_a = side_a
        self.side_b = side_b
        self.cfg = config or MatchConfig.standard()
        # индекс кандидатов стороны B по экономической группе — сразу отсекает
        # заведомо несравнимые пары (Оплата vs Отгрузка), это не балл, а гейт
        # (если require_group_match=False в гибком режиме — единая группа "*")
        self._b_by_group: Dict[str, List[OperationCard]] = {}
        for c in side_b:
            key = c.doc_type_group if self.cfg.require_group_match else "*"
            self._b_by_group.setdefault(key, []).append(c)

    def run(self) -> Tuple[List[MatchResult], List[OperationCard], List[OperationCard]]:
        results: List[MatchResult] = []
        matched_a_ids, matched_b_ids = set(), set()

        # --- этап 1: прямое 1:1 сопоставление по баллам (уровни A-E) ---
        scored_pairs = []
        for a in self.side_a:
            group_key = a.doc_type_group if self.cfg.require_group_match else "*"
            candidates = self._b_by_group.get(group_key, [])
            for b in candidates:
                score, details = _score_pair(a, b, self.cfg)
                level = _level_from_details(details, score)
                if level != "-":
                    scored_pairs.append((score, a, b, details, level))

        # сортируем по убыванию баллов — сначала фиксируем самые надёжные пары,
        # это и есть отказ от "первого попавшегося" (ТЗ п.6.6): приоритет
        # получает объективно более сильное совпадение, а не порядок в файле
        scored_pairs.sort(key=lambda x: -x[0])

        candidates_by_a: Dict[str, List] = {}
        candidates_by_b: Dict[str, List] = {}
        for score, a, b, details, level in scored_pairs:
            candidates_by_a.setdefault(a.card_id, []).append((score, b, details, level))
            candidates_by_b.setdefault(b.card_id, []).append((score, a, details, level))

        for score, a, b, details, level in scored_pairs:
            if a.card_id in matched_a_ids or b.card_id in matched_b_ids:
                continue
            # неоднозначность: у a есть другой кандидат b' с тем же баллом (не строго меньше)
            rivals_a = [c for c in candidates_by_a[a.card_id] if c[1].card_id != b.card_id and c[0] >= score]
            rivals_b = [c for c in candidates_by_b[b.card_id] if c[1].card_id != a.card_id and c[0] >= score]
            confidence = min(100, score)
            if (rivals_a or rivals_b) and confidence < 100:
                status = "Неоднозначное совпадение — ручная проверка"
                results.append(MatchResult(level=level, status=status, confidence=confidence,
                                            side_a=a, side_b=b,
                                            explanation=_explain(details, level) + " Найдено несколько кандидатов с близкими баллами.",
                                            candidates_considered=1 + len(rivals_a) + len(rivals_b)))
                # карточка уже получила запись в отчёте (на ручную проверку) —
                # исключаем её из дальнейших этапов, иначе она задвоится
                # либо в "расхождениях", либо в итоге попадёт в "отсутствует",
                # что было бы неверно: кандидат-то есть, просто неоднозначный
                matched_a_ids.add(a.card_id)
                matched_b_ids.add(b.card_id)
                continue

            matched_a_ids.add(a.card_id)
            matched_b_ids.add(b.card_id)
            status = self._status_for_level(level, confidence)
            results.append(MatchResult(level=level, status=status, confidence=confidence,
                                        side_a=a, side_b=b, explanation=_explain(details, level),
                                        amount_diff=round(a.amount - b.amount, 2)))

        remaining_a = [c for c in self.side_a if c.card_id not in matched_a_ids]
        remaining_b = [c for c in self.side_b if c.card_id not in matched_b_ids]

        # --- этап 2: уровень F — разбивка суммы на несколько строк (1<->N) ---
        # ВАЖНО: идёт раньше "расхождений по номеру" — иначе жадное сравнение
        # 1:1 по совпавшему номеру перехватит одну из строк разбивки и объявит
        # её "расхождением по сумме", хотя на самом деле сумма сходится, просто
        # разнесена на несколько строк у другой стороны.
        f_results, remaining_a, remaining_b = self._match_multirow(remaining_a, remaining_b)
        results.extend(f_results)

        # --- этап 3: расхождения по сумме/дате при совпавшем номере (не F) ---
        results, remaining_a, remaining_b = self._match_same_number_conflicts(results, remaining_a, remaining_b)

        # --- этап 4: дубли внутри одной стороны среди оставшихся ---
        results, remaining_a, remaining_b = self._flag_duplicates(results, remaining_a, remaining_b)

        return results, remaining_a, remaining_b

    @staticmethod
    def _status_for_level(level: str, confidence: int) -> str:
        return {
            "A": "Полное совпадение",
            "B": "Совпадение по реквизитам (разные наименования)",
            "C": "Вероятное совпадение — расхождение по дате",
            "D": "Возможное совпадение — номер не распознан",
            "E": "Вероятное совпадение по цифровому ядру номера",
        }.get(level, "Требует ручной проверки")

    def _match_same_number_conflicts(self, results, remaining_a, remaining_b):
        """Номер и группа совпали, но сумма (и/или дата) — нет: это не 'нет
        документа', а содержательное расхождение, которое нужно показать
        отдельно, а не прятать среди 'отсутствует у стороны' (ТЗ п.12)."""
        by_num_b: Dict[Tuple[str, str], List[OperationCard]] = {}
        for b in remaining_b:
            for num in {b.document_number_normalized, b.related_document_number_normalized} - {""}:
                by_num_b.setdefault((b.doc_type_group, num), []).append(b)

        still_a, matched_b_ids = [], set()
        for a in remaining_a:
            a_nums = {a.document_number_normalized, a.related_document_number_normalized} - {""}
            cands = []
            for num in a_nums:
                cands.extend(b for b in by_num_b.get((a.doc_type_group, num), []) if b.card_id not in matched_b_ids)
            if not cands:
                still_a.append(a)
                continue
            b = cands[0]
            matched_b_ids.add(b.card_id)
            amount_diff = round(a.amount - b.amount, 2)
            date_diff = None
            if a.effective_date and b.effective_date:
                date_diff = (a.effective_date - b.effective_date).days
            if amount_diff != 0 and date_diff not in (0, None):
                status, conf = "Расхождение по сумме и дате одновременно", 40
            elif amount_diff != 0:
                status, conf = "Расхождение по сумме", 50
            else:
                status, conf = "Расхождение по дате", 55
            results.append(MatchResult(level="-", status=status, confidence=conf, side_a=a, side_b=b,
                                        amount_diff=amount_diff, date_diff_days=date_diff,
                                        explanation=f"Номер и группа совпали, но обнаружено расхождение "
                                                    f"(Δсумма={amount_diff}, Δдата={date_diff} дн.)."))
        remaining_b2 = [b for b in remaining_b if b.card_id not in matched_b_ids]
        return results, still_a, remaining_b2

    def _match_multirow(self, remaining_a, remaining_b, max_combo=3):
        """Уровень F: сумма одной стороны равна сумме N строк другой стороны
        с тем же номером/ядром и близкой датой — типичный случай 'один акт
        разбит поставщиком на несколько накладных' и наоборот."""
        results = []
        used_a, used_b = set(), set()

        def try_direction(one_side, many_side, one_is_a: bool):
            by_key: Dict[Tuple[str, str], List[OperationCard]] = {}
            for c in many_side:
                key = (c.doc_type_group, c.document_number_core)
                by_key.setdefault(key, []).append(c)
            for one in one_side:
                oid = one.card_id
                if oid in (used_a if one_is_a else used_b):
                    continue
                key = (one.doc_type_group, one.document_number_core)
                pool = [c for c in by_key.get(key, []) if c.card_id not in (used_b if one_is_a else used_a)]
                if len(pool) < 2:
                    continue
                for r in range(2, min(max_combo, len(pool)) + 1):
                    for combo in itertools.combinations(pool, r):
                        if abs(sum(c.amount for c in combo) - one.amount) < AMOUNT_TOL:
                            if one_is_a:
                                results.append(MatchResult(
                                    level="F", status="Разбивка суммы на несколько строк (1↔N)", confidence=70,
                                    side_a=one, side_b=None, side_b_group=list(combo),
                                    explanation=f"Сумма стороны А ({one.amount}) равна сумме {r} строк стороны Б "
                                                f"с тем же номером/ядром — требуется подтверждение."))
                                used_a.add(one.card_id)
                            else:
                                results.append(MatchResult(
                                    level="F", status="Разбивка суммы на несколько строк (N↔1)", confidence=70,
                                    side_a=None, side_b=one, side_a_group=list(combo),
                                    explanation=f"Сумма стороны Б ({one.amount}) равна сумме {r} строк стороны А "
                                                f"с тем же номером/ядром — требуется подтверждение."))
                                used_b.add(one.card_id)
                            for c in combo:
                                (used_b if one_is_a else used_a).add(c.card_id)
                            break
                    else:
                        continue
                    break

        try_direction(remaining_a, remaining_b, one_is_a=True)
        try_direction(remaining_b, remaining_a, one_is_a=False)

        still_a = [c for c in remaining_a if c.card_id not in used_a]
        still_b = [c for c in remaining_b if c.card_id not in used_b]
        return results, still_a, still_b

    @staticmethod
    def _flag_duplicates(results, remaining_a, remaining_b):
        """Одинаковые (группа, номер, дата, сумма) дважды на одной стороне —
        возможный дубль ввода, а не 'отсутствует у контрагента' (ТЗ п.6.6)."""
        def find_dupes(cards):
            seen: Dict[tuple, List[OperationCard]] = {}
            for c in cards:
                key = (c.doc_type_group, c.document_number_normalized, c.effective_date, round(c.amount, 2))
                seen.setdefault(key, []).append(c)
            return {k: v for k, v in seen.items() if len(v) > 1}

        dupes_a = find_dupes(remaining_a)
        dupes_b = find_dupes(remaining_b)
        dupe_ids_a = {c.card_id for group in dupes_a.values() for c in group}
        dupe_ids_b = {c.card_id for group in dupes_b.values() for c in group}

        for key, group in dupes_a.items():
            results.append(MatchResult(level="-", status="Возможный дубль (Сторона А)", confidence=60,
                                        side_a=group[0], side_b=None, side_a_group=group,
                                        explanation=f"{len(group)} одинаковые строки (номер/дата/сумма) у Стороны А."))
        for key, group in dupes_b.items():
            results.append(MatchResult(level="-", status="Возможный дубль (Сторона Б)", confidence=60,
                                        side_a=None, side_b=group[0], side_b_group=group,
                                        explanation=f"{len(group)} одинаковые строки (номер/дата/сумма) у Стороны Б."))

        still_a = [c for c in remaining_a if c.card_id not in dupe_ids_a]
        still_b = [c for c in remaining_b if c.card_id not in dupe_ids_b]
        return results, still_a, still_b
