"""Camada de chat com modelo, compartilhada por extração de conceitos e assistente.

BYOK: endpoint, modelo e chave vêm de configuração e pertencem ao usuário. Os adapters
falam HTTP puro, como `providers/topic.py`, para não introduzir dependência nova. Esta é a
camada que a issue #8 generaliza para o assistente ancorado.

A listagem de modelos (issue #25) usa os mesmos headers de cada adapter: o cliente da tela
pede a lista ao próprio provedor, com a credencial do dono, em vez de manter uma lista fixa
no código que envelhece em semanas.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

ANTHROPIC_API_ROOT = "https://api.anthropic.com/v1"
ANTHROPIC_MESSAGES_ENDPOINT = f"{ANTHROPIC_API_ROOT}/messages"
ANTHROPIC_MODELS_ENDPOINT = f"{ANTHROPIC_API_ROOT}/models"
ANTHROPIC_API_VERSION = "2023-06-01"

CHAT_COMPLETIONS_SUFFIX = "/chat/completions"
MODELS_SUFFIX = "/models"


class ChatModelUnavailableError(Exception):
    """O provedor não respondeu ou respondeu fora do contrato esperado."""


class ChatModelListingError(Exception):
    """A lista de modelos do provedor não veio, ou veio fora do contrato esperado."""


class InvalidModelEndpointError(Exception):
    """O endpoint gravado não tem a forma que a listagem de modelos exige."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatModelInfo:
    """Modelo oferecido pelo provedor: o id que se grava e, quando houver, o nome exibido."""

    id: str
    display_name: str | None = None


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
        return _openai_compatible_headers(self.api_key)


class AnthropicChatProvider:
    """Adapter para a Messages API da Anthropic (Claude)."""

    name = "anthropic"
    DEFAULT_ENDPOINT = ANTHROPIC_MESSAGES_ENDPOINT
    API_VERSION = ANTHROPIC_API_VERSION

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
        return _anthropic_headers(self.api_key)


def _openai_compatible_headers(api_key: str) -> dict[str, str]:
    """Headers do adapter compatível com OpenAI, usados também na listagem de modelos."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _anthropic_headers(api_key: str) -> dict[str, str]:
    """Headers da Anthropic, usados também na listagem de modelos."""
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
    }


async def list_anthropic_models(
    *, api_key: str, timeout_seconds: float = 30.0
) -> tuple[ChatModelInfo, ...]:
    """Lista os modelos da Anthropic no endpoint oficial, com a chave do dono."""
    return await _list_models(
        endpoint=ANTHROPIC_MODELS_ENDPOINT,
        headers=_anthropic_headers(api_key),
        timeout_seconds=timeout_seconds,
    )


async def list_openai_compatible_models(
    *, endpoint: str, api_key: str = "", timeout_seconds: float = 30.0
) -> tuple[ChatModelInfo, ...]:
    """Lista os modelos de um endpoint compatível a partir da URL de `/chat/completions`."""
    return await _list_models(
        endpoint=models_endpoint_for(endpoint),
        headers=_openai_compatible_headers(api_key),
        timeout_seconds=timeout_seconds,
    )


def models_endpoint_for(endpoint: str) -> str:
    """Troca o sufixo `/chat/completions` por `/models`.

    O adapter chama a URL completa de chat completions, então é dela que a lista de modelos
    é derivada. Sem esse sufixo, recusar é melhor do que adivinhar a URL de um provedor que
    não conhecemos.
    """
    normalized = endpoint.strip().rstrip("/")
    if not normalized.endswith(CHAT_COMPLETIONS_SUFFIX):
        raise InvalidModelEndpointError(
            "an OpenAI-compatible endpoint must be the full URL of /chat/completions "
            "(for example http://localhost:11434/v1/chat/completions) to find the model list"
        )
    return normalized[: -len(CHAT_COMPLETIONS_SUFFIX)] + MODELS_SUFFIX


async def _list_models(
    *, endpoint: str, headers: dict[str, str], timeout_seconds: float
) -> tuple[ChatModelInfo, ...]:
    try:
        document = await asyncio.to_thread(_get, endpoint, headers, timeout_seconds)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ChatModelListingError("the model provider did not return a model list") from exc
    return _model_list(document)


def _get(endpoint: str, headers: dict[str, str], timeout_seconds: float) -> object:
    request = urllib.request.Request(  # noqa: S310 - endpoint vem de configuração
        endpoint, headers=headers, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _model_list(document: object) -> tuple[ChatModelInfo, ...]:
    """Lê `{"data": [{"id", "display_name"?}]}`, o formato dos dois provedores.

    Entrada sem id utilizável é ignorada: a tela só pode gravar o que o provedor aceita
    depois como nome de modelo. Lista vazia é resposta legítima — a tela cai no campo livre.
    """
    entries = document.get("data") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise ChatModelListingError("the model provider returned an unexpected payload")
    models: dict[str, ChatModelInfo] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        name = entry.get("display_name") or entry.get("name")
        display_name = name.strip() if isinstance(name, str) and name.strip() else None
        models.setdefault(
            identifier.strip(),
            ChatModelInfo(id=identifier.strip(), display_name=display_name),
        )
    return tuple(models.values())


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
