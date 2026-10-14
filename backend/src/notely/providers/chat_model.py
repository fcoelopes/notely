"""Camada de chat com modelo, compartilhada por extração de conceitos e assistente.

BYOK: endpoint, modelo e chave vêm de configuração e pertencem ao usuário. Os adapters
falam HTTP puro, como `providers/topic.py`, para não introduzir dependência nova. Esta é a
camada que a issue #8 generaliza para o assistente ancorado.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class ChatModelUnavailableError(Exception):
    """O provedor não respondeu ou respondeu fora do contrato esperado."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


class ChatModelProvider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str: ...


class OpenAICompatibleChatProvider:
    """Adapter para endpoints compatíveis com `/chat/completions` (GPT e afins)."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not endpoint.strip() or not model.strip():
            raise ValueError("endpoint and model are required for the OpenAI-compatible provider")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        payload: dict[str, object] = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                *(  # noqa: COM812 - legibilidade da montagem da lista
                    {"role": message.role, "content": message.content}
                    for message in messages
                ),
            ],
        }
        try:
            document = await asyncio.to_thread(self._post, payload)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ChatModelUnavailableError("chat provider is unavailable") from exc
        return _openai_compatible_text(document)

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(  # noqa: S310 - endpoint vem de configuração
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


class AnthropicChatProvider:
    """Adapter para a Messages API da Anthropic (Claude)."""

    name = "anthropic"
    DEFAULT_ENDPOINT = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not model.strip() or not api_key.strip():
            raise ValueError("model and api_key are required for the Anthropic provider")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
        }
        try:
            document = await asyncio.to_thread(self._post, payload)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ChatModelUnavailableError("chat provider is unavailable") from exc
        return _anthropic_text(document)

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(  # noqa: S310 - endpoint vem de configuração
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
        }


def _openai_compatible_text(document: dict[str, object]) -> str:
    choices = document.get("choices") or []
    if not isinstance(choices, list) or not choices:
        raise ChatModelUnavailableError("chat provider returned no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ChatModelUnavailableError("chat provider returned an unexpected choice")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ChatModelUnavailableError("chat provider returned no message")
    return str(message.get("content", ""))


def _anthropic_text(document: dict[str, object]) -> str:
    blocks = document.get("content") or []
    if not isinstance(blocks, list):
        raise ChatModelUnavailableError("chat provider returned an unexpected payload")
    text = "".join(
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if not text.strip():
        raise ChatModelUnavailableError("chat provider returned no text")
    return text
