# -*- coding: utf-8 -*-
"""
OpenRouterVisionProvider — vision-OCR через бесплатные (:free) модели
OpenRouter вместо платного Anthropic API.

Почему так, а не класс-наследник OCRProvider (см. base.py): у OCRProvider
контракт `.extract(pdf_path) -> List[RawLine]` — "просто распознай", без
управляемого промпта. Здесь же нужен ИМЕННО промпт под конкретный формат
таблицы (Сторона А / Сторона Б — разные колонки), поэтому у этого
провайдера и у ClaudeVisionProvider единый, более узкий интерфейс:

    .ask_image(image_path: str, prompt: str) -> str

Это и есть "переключатель": src/ocr_ingest.py работает с любым объектом,
у которого есть .ask_image(...), и не знает/не спрашивает, Claude это или
OpenRouter — выбор провайдера происходит один раз, в момент создания
объекта (--ocr-provider в run.py), а не веткой if/else на каждой странице.

ТРЕБОВАНИЯ: только стандартная библиотека (urllib) — специально без
дополнительных pip-пакетов, в отличие от пути через Anthropic (там нужен
`pip install anthropic`). export OPENROUTER_API_KEY=...

ВЫБОР МОДЕЛИ (проверено веб-поиском на дату разработки, список бесплатных
моделей у OpenRouter меняется без предупреждения — актуальный перечень
всегда см. https://openrouter.ai/models?fmt=cards&input_modalities=image&max_price=0):

  - nvidia/nemotron-nano-12b-v2-vl:free  (ПО УМОЛЧАНИЮ)
    Явно обучена под OCR/document intelligence, заявлены лидирующие
    результаты на OCRBench v2. Из всех бесплатных vision-моделей на
    момент проверки — единственная с явным фокусом именно на
    распознавание текста/таблиц, а не на общее описание изображений.
    Русский язык отдельно не заявлен — нужна проверка на реальном скане.

  - google/gemma-4-31b-it:free  (ЗАПАСНОЙ ВАРИАНТ)
    Крупнее (31B), заявлена мультиязычность 140+ языков (должен покрывать
    русский) и "document understanding tasks". Более общая модель, не
    специализированная под OCR — как альтернатива, если Nemotron VL
    даёт слабый результат на конкретном скане.

  Я НЕ МОГУ вызвать OpenRouter API из среды разработки (сетевой прокси
  блокирует openrouter.ai, см. ТЕХНИЧЕСКИЙ_РАЗБОР.md), поэтому качество
  распознавания русского текста и таблиц ни одной из моделей эмпирически
  не проверено с моей стороны — только структура запроса/ретраев. Первый
  реальный прогон стоит сделать на 1-2 страницах, не на всём документе.

ЛИМИТЫ: бесплatные модели OpenRouter — как правило, 20 запросов/мин и
200/сутки (лимиты плавают по модели и провайдеру, официальный источник —
https://openrouter.ai/docs). На 15+13=28 страниц это не проблема по
дневному лимиту, но по частоте — да, нужна пауза между вызовами (задаётся
параметром sleep_between_calls, по умолчанию 4 сек, как и предложено).
При HTTP 429 provider делает экспоненциальную паузу и до 5 повторов,
а не падает сразу.
"""
from __future__ import annotations
import base64
import json
import os
import time
import urllib.request
import urllib.error
from typing import Optional


class OpenRouterVisionProvider:
    name = "openrouter-vision"
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    # см. докстринг файла — выбор обоснован там, не только "первое, что нашлось"
    DEFAULT_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"
    FALLBACK_MODEL = "google/gemma-4-31b-it:free"

    def __init__(self, model: str = DEFAULT_MODEL, max_retries: int = 5,
                 sleep_between_calls: float = 4.0, timeout: int = 120,
                 referer: str = "https://github.com/czeberyak/sverka_aktov"):
        self.model = model
        self.max_retries = max_retries
        self.sleep_between_calls = sleep_between_calls
        self.timeout = timeout
        self.referer = referer
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY не задан. Получить ключ: https://openrouter.ai/keys "
                "(регистрация без карты, бесплатные модели доступны сразу)."
            )

    def ask_image(self, image_path: str, prompt: str) -> str:
        """Один вызов модели на одно изображение. Возвращает текст ответа.
        Пауза self.sleep_between_calls выдерживается ПЕРЕД каждым вызовом
        (в т.ч. первым) — так проще гарантировать общий темп при обработке
        цикла страниц в ocr_ingest.py, не считая паузы отдельно там."""
        if self.sleep_between_calls > 0:
            time.sleep(self.sleep_between_calls)

        with open(image_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()

        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }],
        }
        request = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": self.referer,  # OpenRouter учитывает при ранжировании/статистике
                "X-Title": "sverka-aktov",
            },
            method="POST",
        )
        return self._send_with_retries(request)

    def _send_with_retries(self, request: urllib.request.Request) -> str:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                if e.code == 429 and attempt < self.max_retries:
                    wait = 10 * attempt  # 10, 20, 30, 40с — линейный бэкофф, не агрессивный
                    print(f"    [openrouter] 429 Too Many Requests, жду {wait}с "
                          f"(попытка {attempt}/{self.max_retries})...")
                    time.sleep(wait)
                    last_error = e
                    continue
                raise RuntimeError(f"OpenRouter API вернул {e.code}: {body[:400]}") from e
            except urllib.error.URLError as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(5 * attempt)
                    continue
                raise RuntimeError(f"OpenRouter недоступен после {self.max_retries} попыток: {e}") from e
        raise RuntimeError(f"Не удалось получить ответ от OpenRouter: {last_error}")
