"""Telegram Bot API notifier. Uses sendPhoto when a chart is present, else sendMessage."""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)


class TelegramNotifier:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str):
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._chat_id = str(chat_id)

    async def send(self, text: str, image: bytes | None = None) -> None:
        async with aiohttp.ClientSession() as session:
            if image:
                form = aiohttp.FormData()
                form.add_field("chat_id", self._chat_id)
                form.add_field("caption", text)
                form.add_field("photo", image, filename="chart.png", content_type="image/png")
                url = f"{self._base}/sendPhoto"
                async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    await _check(r, "sendPhoto")
            else:
                url = f"{self._base}/sendMessage"
                payload = {"chat_id": self._chat_id, "text": text}
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    await _check(r, "sendMessage")


async def _check(resp: aiohttp.ClientResponse, what: str) -> None:
    if resp.status >= 400:
        body = await resp.text()
        raise RuntimeError(f"Telegram {what} failed [{resp.status}]: {body[:200]}")
