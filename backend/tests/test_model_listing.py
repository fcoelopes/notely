"""Lista de modelos por provedor, sem rede: o que o adapter pede e o que ele aceita de volta.

A lista é pedida ao próprio provedor porque uma lista fixa no código envelhece em semanas.
Aqui o transporte é dublado (`urllib.request.urlopen`), então o teste verifica a URL, os
headers — os mesmos dos adapters de chat — e a leitura da resposta, incluindo o caminho em
que o endpoint configurado não tem a forma que a listagem exige. A chave é fictícia e nunca
pode aparecer em mensagem de erro.

As três falhas de listagem são verificadas separadamente, porque a tela mostra o motivo ao
usuário: credencial recusada (`401`/`403`), provedor inalcançável (DNS, timeout, recusa) e
resposta fora do formato de lista.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

import pytest

from notely.core.models import AIModelConfig, AIModelProvider
from notely.core.security import SecretCipher
from notely.core.study_assistant import (
    AssistantCredentialsRejectedError,
    AssistantInvalidEndpointError,
    AssistantNotConfiguredError,
    AssistantUnavailableError,
    StudyAssistant,
)
from notely.providers.chat_model import (
    ANTHROPIC_MODELS_ENDPOINT,
    AnthropicChatProvider,
    ChatModelCredentialsRejectedError,
    ChatModelInfo,
    ChatModelListingError,
    ChatModelUnreachableError,
    InvalidModelEndpointError,
    list_anthropic_models,
    list_openai_compatible_models,
    models_endpoint_for,
)

PROVIDER_KEY = "chave-ficticia-do-provedor"
TYPED_KEY = "chave-ficticia-digitada"
CHAT_ENDPOINT = "http://localhost:11434/v1/chat/completions"
MODELS_ENDPOINT = "http://localhost:11434/v1/models"


class FakeResponse:
    """Resposta mínima de `urlopen`: só o que o adapter lê."""

    def __init__(self, payload: object, *, raw: bytes | None = None) -> None:
        self._body = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def stub_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: object = None,
    raw: bytes | None = None,
    error: Exception | None = None,
) -> list[urllib.request.Request]:
    """Dubla o transporte HTTP e devolve as requisições feitas."""
    requests: list[urllib.request.Request] = []

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> FakeResponse:
        requests.append(request)
        if error is not None:
            raise error
        return FakeResponse(payload, raw=raw)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


def headers_of(request: urllib.request.Request) -> dict[str, str]:
    """Headers com nomes em minúsculas: o `urllib` capitaliza como manda o protocolo."""
    return {name.lower(): value for name, value in request.header_items()}


def http_error(status: int, url: str = ANTHROPIC_MODELS_ENDPOINT) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, status, "provider refused the request", {}, None)


# -- adapters ----------------------------------------------------------------


async def test_anthropic_listing_uses_the_official_endpoint_and_the_adapter_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = stub_transport(
        monkeypatch,
        payload={
            "data": [
                {"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"},
                {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5"},
            ]
        },
    )

    models = await list_anthropic_models(api_key=PROVIDER_KEY)

    assert models == (
        ChatModelInfo(id="claude-sonnet-4-5", display_name="Claude Sonnet 4.5"),
        ChatModelInfo(id="claude-haiku-4-5", display_name="Claude Haiku 4.5"),
    )
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].full_url == ANTHROPIC_MODELS_ENDPOINT
    # Mesmos headers do adapter de chat: a listagem não inventa um segundo dialeto.
    assert headers_of(requests[0])["x-api-key"] == PROVIDER_KEY
    assert headers_of(requests[0])["anthropic-version"] == "2023-06-01"


async def test_openai_compatible_listing_swaps_the_chat_completions_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = stub_transport(
        monkeypatch,
        payload={"data": [{"id": "llama3.1"}, {"id": "qwen2.5", "name": "Qwen 2.5"}]},
    )

    models = await list_openai_compatible_models(endpoint=CHAT_ENDPOINT, api_key=PROVIDER_KEY)

    assert models == (
        ChatModelInfo(id="llama3.1"),
        ChatModelInfo(id="qwen2.5", display_name="Qwen 2.5"),
    )
    assert requests[0].full_url == MODELS_ENDPOINT
    assert headers_of(requests[0])["authorization"] == f"Bearer {PROVIDER_KEY}"


def test_a_trailing_slash_does_not_change_the_derived_endpoint() -> None:
    assert models_endpoint_for(f"{CHAT_ENDPOINT}/") == MODELS_ENDPOINT
    assert models_endpoint_for(f"  {CHAT_ENDPOINT}  ") == MODELS_ENDPOINT


async def test_an_endpoint_without_the_chat_completions_suffix_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = stub_transport(monkeypatch, payload={"data": []})

    with pytest.raises(InvalidModelEndpointError) as failure:
        await list_openai_compatible_models(
            endpoint="http://localhost:11434/v1", api_key=PROVIDER_KEY
        )

    # A URL não é adivinhada: a mensagem diz o formato esperado e nada é chamado.
    assert "/chat/completions" in str(failure.value)
    assert PROVIDER_KEY not in str(failure.value)
    assert requests == []


@pytest.mark.parametrize("status", (401, 403))
async def test_a_rejected_key_is_reported_as_a_rejected_key(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """`401`/`403` não é "provedor fora": é a chave que o provedor recusou."""
    stub_transport(monkeypatch, error=http_error(status))

    with pytest.raises(ChatModelCredentialsRejectedError) as failure:
        await list_anthropic_models(api_key=PROVIDER_KEY)

    assert failure.value.status == status
    assert failure.value.endpoint == ANTHROPIC_MODELS_ENDPOINT
    assert str(status) in str(failure.value)
    assert PROVIDER_KEY not in str(failure.value)


@pytest.mark.parametrize(
    ("error", "reason"),
    (
        (
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
            "the connection was refused",
        ),
        (
            urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
            "the host name could not be resolved",
        ),
        (TimeoutError("timed out"), "the request timed out"),
        (ConnectionRefusedError(111, "Connection refused"), "the connection was refused"),
    ),
)
async def test_an_unreachable_provider_is_reported_with_the_endpoint(
    monkeypatch: pytest.MonkeyPatch, error: Exception, reason: str
) -> None:
    stub_transport(monkeypatch, error=error)

    with pytest.raises(ChatModelUnreachableError) as failure:
        await list_openai_compatible_models(endpoint=CHAT_ENDPOINT, api_key=PROVIDER_KEY)

    message = str(failure.value)
    # O host tentado vai na mensagem: é o que diz ao dono qual endpoint conferir.
    assert MODELS_ENDPOINT in message
    assert reason in message
    assert PROVIDER_KEY not in message


@pytest.mark.parametrize(
    "transport",
    (
        {"payload": {"object": "list"}},
        {"payload": ["lista", "sem", "envelope"]},
        {"raw": b"isto nao e json"},
        {"raw": b"\xff\xfe nao e utf-8"},
        {"error": http_error(500)},
    ),
)
async def test_a_payload_out_of_shape_is_reported_without_the_key(
    monkeypatch: pytest.MonkeyPatch, transport: dict[str, object]
) -> None:
    stub_transport(monkeypatch, **transport)  # type: ignore[arg-type]

    with pytest.raises(ChatModelListingError) as failure:
        await list_anthropic_models(api_key=PROVIDER_KEY)

    message = str(failure.value)
    assert ANTHROPIC_MODELS_ENDPOINT in message
    assert "did not return a model list" in message
    assert PROVIDER_KEY not in message


async def test_entries_without_an_usable_id_are_skipped_and_duplicates_collapse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_transport(
        monkeypatch,
        payload={
            "data": [
                {"id": "modelo-a"},
                {"id": "modelo-a", "display_name": "Modelo A"},
                {"display_name": "sem id"},
                {"id": "   "},
                "entrada que nao e objeto",
            ]
        },
    )

    models = await list_anthropic_models(api_key=PROVIDER_KEY)

    # A primeira ocorrência do id vence: a lista não repete o mesmo modelo.
    assert models == (ChatModelInfo(id="modelo-a"),)


async def test_an_empty_list_is_a_legitimate_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provedor local pode não devolver nada: a tela cai no nome digitado."""
    stub_transport(monkeypatch, payload={"data": []})

    assert await list_anthropic_models(api_key=PROVIDER_KEY) == ()


# -- regra de uso da configuração -------------------------------------------


class FakeUnitOfWork:
    """Unidade de trabalho mínima: a listagem só lê a configuração do modelo."""

    def __init__(self, config: AIModelConfig | None) -> None:
        self.config = config

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def get_ai_model_config(self) -> AIModelConfig | None:
        return self.config


def _cipher() -> SecretCipher:
    return SecretCipher(SecretCipher.generate_key())


def _config(cipher: SecretCipher, **overrides: object) -> AIModelConfig:
    fields: dict[str, object] = {
        "provider": AIModelProvider.OPENAI_COMPATIBLE,
        "model": "gpt-exemplo",
        "api_key_encrypted": cipher.encrypt(PROVIDER_KEY),
        "base_url": CHAT_ENDPOINT,
    }
    fields.update(overrides)
    return AIModelConfig(**fields)  # type: ignore[arg-type]


def assistant(
    config: AIModelConfig | None, cipher: SecretCipher
) -> StudyAssistant:
    return StudyAssistant(lambda: FakeUnitOfWork(config), cipher=cipher)


async def test_listing_without_a_configuration_and_without_a_key_is_a_missing_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_transport(monkeypatch, payload={"data": []})

    with pytest.raises(AssistantNotConfiguredError):
        await assistant(None, _cipher()).list_models(provider=AIModelProvider.ANTHROPIC)


async def test_a_configuration_without_a_stored_key_still_needs_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_transport(monkeypatch, payload={"data": []})
    cipher = _cipher()

    with pytest.raises(AssistantNotConfiguredError):
        await assistant(_config(cipher, api_key_encrypted=None), cipher).list_models(
            provider=AIModelProvider.OPENAI_COMPATIBLE
        )


async def test_without_a_typed_key_the_stored_key_is_decrypted_and_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cipher = _cipher()
    requests = stub_transport(monkeypatch, payload={"data": [{"id": "llama3.1"}]})

    models = await assistant(_config(cipher), cipher).list_models(
        provider=AIModelProvider.OPENAI_COMPATIBLE
    )

    assert models == (ChatModelInfo(id="llama3.1"),)
    # O endpoint vem da configuração gravada e a chave é a decifrada, nunca a cifrada.
    assert requests[0].full_url == MODELS_ENDPOINT
    assert headers_of(requests[0])["authorization"] == f"Bearer {PROVIDER_KEY}"


async def test_a_typed_key_lists_models_before_anything_is_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = stub_transport(monkeypatch, payload={"data": [{"id": "claude-sonnet-4-5"}]})

    models = await assistant(None, _cipher()).list_models(
        provider=AIModelProvider.ANTHROPIC, api_key=f"  {TYPED_KEY}  "
    )

    assert models == (ChatModelInfo(id="claude-sonnet-4-5"),)
    assert headers_of(requests[0])["x-api-key"] == TYPED_KEY


async def test_the_endpoint_typed_in_the_screen_wins_over_the_stored_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cipher = _cipher()
    requests = stub_transport(monkeypatch, payload={"data": []})

    await assistant(_config(cipher), cipher).list_models(
        provider=AIModelProvider.OPENAI_COMPATIBLE,
        api_key=TYPED_KEY,
        base_url="http://outro-host:8000/v1/chat/completions",
    )

    assert requests[0].full_url == "http://outro-host:8000/v1/models"


async def test_openai_compatible_listing_requires_an_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = stub_transport(monkeypatch, payload={"data": []})
    cipher = _cipher()

    with pytest.raises(AssistantInvalidEndpointError):
        await assistant(_config(cipher, base_url=None), cipher).list_models(
            provider=AIModelProvider.OPENAI_COMPATIBLE
        )

    assert requests == []


async def test_openai_compatible_listing_requires_the_chat_completions_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quem guardou a base URL, e não a URL de `/chat/completions`, recebe o formato esperado."""
    stub_transport(monkeypatch, payload={"data": []})
    cipher = _cipher()

    with pytest.raises(AssistantInvalidEndpointError) as failure:
        await assistant(_config(cipher, base_url="http://localhost:11434/v1"), cipher).list_models(
            provider=AIModelProvider.OPENAI_COMPATIBLE
        )

    assert "/chat/completions" in str(failure.value)


@pytest.mark.parametrize(("status", "provider"), ((401, "Anthropic"), (403, "Anthropic")))
async def test_a_rejected_key_becomes_a_credentials_error_naming_the_provider(
    monkeypatch: pytest.MonkeyPatch, status: int, provider: str
) -> None:
    """A chave guardada pode estar revogada: a mensagem diz isso, e cita o provedor."""
    stub_transport(monkeypatch, error=http_error(status))
    cipher = _cipher()

    with pytest.raises(AssistantCredentialsRejectedError) as failure:
        await assistant(_config(cipher), cipher).list_models(
            provider=AIModelProvider.ANTHROPIC
        )

    message = str(failure.value)
    assert provider in message
    assert "refused the api key" in message
    assert str(status) in message
    assert PROVIDER_KEY not in message


async def test_a_rejected_typed_key_is_reported_for_the_compatible_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_transport(monkeypatch, error=http_error(401, MODELS_ENDPOINT))

    with pytest.raises(AssistantCredentialsRejectedError) as failure:
        await assistant(None, _cipher()).list_models(
            provider=AIModelProvider.OPENAI_COMPATIBLE,
            api_key=TYPED_KEY,
            base_url=CHAT_ENDPOINT,
        )

    message = str(failure.value)
    assert "OpenAI-compatible" in message
    assert TYPED_KEY not in message


async def test_an_unreachable_provider_is_unavailable_and_says_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_transport(monkeypatch, error=urllib.error.URLError(ConnectionRefusedError(111, "no")))
    cipher = _cipher()

    with pytest.raises(AssistantUnavailableError) as failure:
        await assistant(_config(cipher), cipher).list_models(
            provider=AIModelProvider.OPENAI_COMPATIBLE
        )

    message = str(failure.value)
    assert "could not reach" in message
    assert MODELS_ENDPOINT in message
    assert PROVIDER_KEY not in message


async def test_an_unexpected_payload_is_unavailable_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_transport(monkeypatch, payload={"object": "list"})
    cipher = _cipher()

    with pytest.raises(AssistantUnavailableError) as failure:
        await assistant(_config(cipher), cipher).list_models(
            provider=AIModelProvider.OPENAI_COMPATIBLE
        )

    message = str(failure.value)
    assert MODELS_ENDPOINT in message
    assert "did not return a model list" in message
    assert PROVIDER_KEY not in message


async def test_a_provider_server_error_is_not_mistaken_for_a_rejected_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Um `5xx` do provedor é indisponibilidade, não credencial recusada."""
    stub_transport(monkeypatch, error=http_error(500))
    cipher = _cipher()

    with pytest.raises(AssistantUnavailableError) as failure:
        await assistant(_config(cipher), cipher).list_models(
            provider=AIModelProvider.ANTHROPIC
        )

    assert "HTTP 500" in str(failure.value)
    assert PROVIDER_KEY not in str(failure.value)


def test_the_adapters_keep_their_public_constants() -> None:
    """A tela e os testes de configuração continuam vendo o endpoint oficial de chat."""
    assert AnthropicChatProvider.DEFAULT_ENDPOINT == "https://api.anthropic.com/v1/messages"
    assert AnthropicChatProvider.API_VERSION == "2023-06-01"
    # A listagem de modelos usa o mesmo root versionado, com outro caminho.
    assert models_endpoint_for(CHAT_ENDPOINT) == MODELS_ENDPOINT
    assert ANTHROPIC_MODELS_ENDPOINT == "https://api.anthropic.com/v1/models"
