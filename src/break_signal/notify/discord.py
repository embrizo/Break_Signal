"""Discord webhook notifier. Posts multipart when a chart is present, else JSON."""
from __future__ import annotations

import json
import logging

import aiohttp

log = logging.getLogger(__name__)


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url: str):
        self._url = webhook_url

    async def send(self, text: str, image: bytes | None = None) -> None:
        # Discord content limit is 2000 chars; our messages are far shorter.
        content = f"```\n{text}\n```"
        async with aiohttp.ClientSession() as session:
            if image:
                form = aiohttp.FormData()
                form.add_field("payload_json", json.dumps({"content": content}))
                form.add_field("file", image, filename="chart.png", content_type="image/png")
                async with session.post(self._url, data=form, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    await _check(r)
            else:
                async with session.post(
                    self._url, json={"content": content}, timeout=aiohttp.ClientTimeout(total=30)
                ) as r:
                    await _check(r)


async def _check(resp: aiohttp.ClientResponse) -> None:
    if resp.status >= 400:
        body = await resp.text()
        raise RuntimeError(f"Discord webhook failed [{resp.status}]: {body[:200]}")
