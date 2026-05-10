"""Thin httpx-based Telegram Bot API client (only sendMessage)."""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx
from loguru import logger

from .models import TelegramSendResult


class TelegramClient:
    """Wrap Telegram Bot API sendMessage with manual retry + 429 Retry-After honor.

    We do not use tenacity here because we need fine-grained control over
    Retry-After header parsing (a tenacity wait function would also work but
    adds indirection for a single call site).
    """

    def __init__(
        self,
        token: str,
        parse_mode: str = "Markdown",
        api_base: str = "https://api.telegram.org",
        timeout_s: float = 10.0,
        max_retries: int = 3,
        base_backoff: float = 2.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._token = token
        self._parse_mode = parse_mode
        self._api_base = api_base.rstrip("/")
        self._timeout = httpx.Timeout(timeout_s)
        self._max_retries = max_retries
        self._base_backoff = base_backoff
        self._transport = transport  # for tests; normally None

    @property
    def base_url(self) -> str:
        return f"{self._api_base}/bot{self._token}"

    async def send_message(self, chat_id: str, text: str) -> TelegramSendResult:
        """POST sendMessage with retry on 429 (Retry-After) + 5xx (exponential backoff)."""
        url = f"{self.base_url}/sendMessage"
        body = {"chat_id": chat_id, "text": text, "parse_mode": self._parse_mode}

        last_error: Optional[str] = None

        client_kwargs = {"timeout": self._timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(url, json=body)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = f"network: {type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    await asyncio.sleep(self._base_backoff * (2 ** (attempt - 1)))
                    continue
                break

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return TelegramSendResult(
                        success=True,
                        message_id=data.get("result", {}).get("message_id"),
                    )
                except Exception as exc:
                    last_error = f"bad response body: {exc}"
                    break

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "1"))
                last_error = f"429 rate limited (Retry-After={retry_after})"
                if attempt < self._max_retries:
                    await asyncio.sleep(retry_after)
                    continue
                break

            if 500 <= resp.status_code < 600:
                last_error = f"HTTP {resp.status_code}"
                if attempt < self._max_retries:
                    await asyncio.sleep(self._base_backoff * (2 ** (attempt - 1)))
                    continue
                break

            # 4xx other than 429 — non-retryable (bad request / unauthorized / etc)
            try:
                txt = resp.text[:200]
            except Exception:
                txt = "<unreadable>"
            last_error = f"HTTP {resp.status_code}: {txt}"
            break

        logger.warning("telegram send_message failed: {}", last_error)
        return TelegramSendResult(success=False, error=last_error or "unknown error")
