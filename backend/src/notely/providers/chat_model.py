"""Camada de chat com modelo, compartilhada por extração de conceitos e assistente.

BYOK: endpoint, modelo e chave vêm de configuração e pertencem ao usuário. Os adapters
falam HTTP puro, como `providers/topic.py`, para não introduzir dependência nova. Esta é a
camada que a issue #8 generaliza para o assistente ancorado.

A listagem de modelos (issue #25) usa os mesmos headers de cada adapter: o cliente da tela
pede a lista ao próprio provedor, com a credencial do dono, em vez de manter uma lista fixa
no código que envelhece em semanas.

As três falhas de listagem são distintas porque exigem ações distintas de quem está na tela:
credencial recusada (`401`/`403`), provedor inalcançável (DNS, timeout, conexão recusada) e
resposta fora do formato de lista. Nenhuma delas repete a chave: o motivo vem do tipo do
erro, não do texto livre da exceção de transporte.
"""

from __future__ import annotations

import asyncio
import json
import socket
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

REJECTED_KEY_STATUSES = frozenset({401, 403})


class ChatModelUnavailableError(Exception):
    """O provedor não respondeu ou respondeu fora do contrato esperado."""


class ChatModelListingError(Exception):
    """O endpoint respondeu, mas não com uma lista de modelos."""

    def __init__(
        self, *, endpoint: str, detail: str = "the payload is not a list of models"
    ) -> None:
        self.endpoint = endpoint
        self.detail = detail
        super().__init__(f"{endpoint} did not return a model list: {detail}")


class ChatModelCredentialsRejectedError(Exception):
    """O provedor respondeu `401`/`403`: a chave não serve para listar os modelos."""

    def __init__(self, *, endpoint: str, status: int) -> None:
        self.endpoint = endpoint
        self.status = status
        super().__init__(f"the provider refused the api key with HTTP {status} at {endpoint}")


class ChatModelUnreachableError(Exception):
    """O provedor não foi alcançado: DNS, timeout, conexão recusada."""

    def __init__(self, *, endpoint: str, reason: str) -> None:
        self.endpoint = endpoint
        self.reason = reason
        super().__init__(f"could not reach {endpoint}: {reason}")


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
    except urllib.error.HTTPError as exc:
        # `HTTPError` é subclasse de `URLError`: precisa vir antes.
        if exc.code in REJECTED_KEY_STATUSES:
            raise ChatModelCredentialsRejectedError(endpoint=endpoint, status=exc.code) from exc
        raise ChatModelListingError(
            endpoint=endpoint, detail=f"the provider answered HTTP {exc.code}"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ChatModelListingError(
            endpoint=endpoint, detail="the answer was not valid json"
        ) from exc
    except OSError as exc:
        # Cobre `URLError`, `socket.gaierror`, `TimeoutError` e conexão recusada; `HTTPError`
        # é subclasse de `URLError` e já foi tratada acima, por isso vem primeiro.
        raise ChatModelUnreachableError(
            endpoint=endpoint, reason=_unreachable_reason(exc)
        ) from exc
    return _model_list(document, endpoint)


def _unreachable_reason(exc: BaseException) -> str:
    """Motivo curto e estável da falha de transporte, sem repetir texto do sistema.

    O texto vem do tipo da exceção, não de `str(exc)`: uma mensagem de sistema é livre o
    bastante para carregar o que não deve sair daqui.
    """
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        return _unreachable_reason(exc.reason)
    if isinstance(exc, socket.gaierror):
        return "the host name could not be resolved"
    if isinstance(exc, ConnectionRefusedError):
        return "the connection was refused"
    if isinstance(exc, TimeoutError):
        return "the request timed out"
    return "the connection failed"


def _get(endpoint: str, headers: dict[str, str], timeout_seconds: float) -> object:
    request = urllib.request.Request(  # noqa: S310 - endpoint vem de configuração
        endpoint, headers=headers, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _model_list(document: object, endpoint: str) -> tuple[ChatModelInfo, ...]:
    """Lê `{"data": [{"id", "display_name"?}]}`, o formato dos dois provedores.

    Entrada sem id utilizável é ignorada: a tela só pode gravar o que o provedor aceita
    depois como nome de modelo. Lista vazia é resposta legítima — o provedor respondeu,
    apenas não oferece nada que se possa escolher.
    """
    entries = document.get("data") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise ChatModelListingError(
            endpoint=endpoint, detail="the payload is not a list of models"
        )
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
