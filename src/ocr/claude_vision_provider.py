# -*- coding: utf-8 -*-
"""
ClaudeVisionProvider — тот же интерфейс `.ask_image(image_path, prompt)`,
что и у OpenRouterVisionProvider (см. openrouter_vision_provider.py).

Вынесен из ocr_ingest.py в отдельный класс, чтобы переключение
Claude/OpenRouter в ocr_ingest.py было выбором ОБЪЕКТА при старте, а не
if/else в цикле обработки страниц.

ТРЕБОВАНИЯ: pip install anthropic; export ANTHROPIC_API_KEY=...
Платно (в отличие от OpenRouterVisionProvider) — см. README, раздел
"Стоимость/приватность".
"""
from __future__ import annotations
import base64
import os


class ClaudeVisionProvider:
    name = "claude-vision"

    def __init__(self, model: str = "claude-sonnet-4-6", sleep_between_calls: float = 0.0):
        self.model = model
        self.sleep_between_calls = sleep_between_calls
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY не задан.")

    def ask_image(self, image_path: str, prompt: str) -> str:
        import time
        import anthropic  # локальный импорт: зависимость нужна только этому провайдеру
        if self.sleep_between_calls > 0:
            time.sleep(self.sleep_between_calls)
        client = anthropic.Anthropic()
        with open(image_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()
        resp = client.messages.create(
            model=self.model,
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
