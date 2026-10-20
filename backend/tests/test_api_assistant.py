"""Contrato HTTP do assistente: a chave nunca sai, e os erros viram status claros."""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest

from auth_helpers import authenticate
from notely.api.app import create_app
from notely.core.models import AIModelConfig, AIModelProvider
from notely.core.study_assistant import (
    AssistantCredentialsRejectedError,
    AssistantNotConfiguredError,
    AssistantSource,
    AssistantUnavailableError,
    AssistantAnswer,
)
from notely.providers.chat_model import ChatModelInfo

CONFIG = AIModelConfig(
    provider=AIModelProvider.ANTHROPIC,
    model="claude-exemplo",
    api_key_encrypted="cifrado",
    base_url="https://api.anthropic.com/v1/messages",
)


class StubAssistant:
    """Duplo do assistente: devolve o desfecho ou o erro configurado."""

    def __init__(
        self,
        *,
        config: AIModelConfig | None = CONFIG,
        error: Exception | None = None,
        grounded: bool = True,
        listing: tuple[ChatModelInfo, ...] = (),
        listing_error: Exception | None = None,
    ) -> None:
        self.config = config
        self.error = error
        self.grounded = grounded
        self.listing = listing
        self.listing_error = listing_error
        self.saved: dict[str, object] = {}
        self.cleared = False

    async def get_config(self) -> AIModelConfig | None:
        return self.config

    async def save_config(self, **values: object) -> AIModelConfig:
        self.saved = values
        self.config = CONFIG
        return CONFIG

    async def clear_config(self) -> None:
        self.cleared = True
        self.config = None

    async def list_models(self, **values: object) -> tuple[ChatModelInfo, ...]:
        # A listagem não grava: quem recebe a chave aqui é só a chamada ao provedor.
        self.listed = values
        if self.listing_error is not None:
            raise self.listing_error
        return self.listing

    async def ask(self, *, message: str, study_session_id: UUID | None, document_id: UUID | None) -> AssistantAnswer:
        self.saved = {"message": message, "session": study_session_id, "document": document_id}
        if self.error is not None:
            raise self.error
        return AssistantAnswer(
            answer="A materialidade depende da magnitude [página 1].",
            provider="anthropic",
            model="claude-exemplo",
            grounded=self.grounded,
            sources=(
                AssistantSource(
                    document_id=uuid4(),
                    document_title="IAS 1",
                    page_number=1,
                    excerpt="A materialidade depende da natureza ou magnitude",
                ),
            )
            if self.grounded
            else (),
        )


def build(assistant: StubAssistant) -> object:
    app = create_app(
        service_factory=lambda user_id: object(),  # type: ignore[arg-type]
        assistant_factory=lambda user_id: assistant,  # type: ignore[arg-type]
    )
    return authenticate(app)  # type: ignore[arg-type]


def client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]


async def test_config_response_never_returns_the_key() -> None:
    assistant = StubAssistant()
    async with client(build(assistant)) as http:
        response = await http.get("/api/ai-config")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "provider": "anthropic",
        "model": "claude-exemplo",
        "base_url": "https://api.anthropic.com/v1/messages",
        "enabled": True,
        "has_key": True,
    }
    assert "cifrado" not in response.text
    assert "api_key" not in body


async def test_writing_the_config_passes_the_key_through() -> None:
    assistant = StubAssistant()
    async with client(build(assistant)) as http:
        response = await http.put(
            "/api/ai-config",
            json={
                "provider": "openai_compatible",
                "model": "gpt-exemplo",
                "api_key": "chave-do-usuario",
                "base_url": "https://api.exemplo.com/v1/chat/completions",
            },
        )

    assert response.status_code == 200
    assert assistant.saved["api_key"] == "chave-do-usuario"
    assert assistant.saved["model"] == "gpt-exemplo"
    assert response.json()["has_key"] is True


async def test_missing_config_is_a_404_and_clearing_is_a_204() -> None:
    assistant = StubAssistant(config=None)
    async with client(build(assistant)) as http:
        missing = await http.get("/api/ai-config")
        cleared = await http.delete("/api/ai-config")

    assert missing.status_code == 404
    assert cleared.status_code == 204
    assert assistant.cleared is True


async def test_grounded_answer_reports_sources_and_provenance() -> None:
    assistant = StubAssistant()
    session_id = uuid4()
    async with client(build(assistant)) as http:
        response = await http.post(
            "/api/study-assistant/chat",
            json={"message": "O que é materialidade?", "study_session_id": str(session_id)},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is True
    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-exemplo"
    assert body["sources"][0]["page_number"] == 1
    assert body["sources"][0]["document_title"] == "IAS 1"
    assert assistant.saved["session"] == session_id


async def test_free_chat_is_allowed_without_a_session() -> None:
    assistant = StubAssistant(grounded=False)
    async with client(build(assistant)) as http:
        response = await http.post(
            "/api/study-assistant/chat", json={"message": "Quanto é 10 dividido por 2?"}
        )

    assert response.status_code == 200
    assert response.json()["grounded"] is False
    assert response.json()["sources"] == []


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (AssistantNotConfiguredError("configure a model provider before asking"), 409),
        (AssistantUnavailableError("the model provider is unavailable"), 502),
    ),
)
async def test_assistant_errors_map_to_status_codes(error: Exception, expected: int) -> None:
    assistant = StubAssistant(error=error)
    async with client(build(assistant)) as http:
        response = await http.post("/api/study-assistant/chat", json={"message": "Olá"})

    assert response.status_code == expected
    assert response.json()["detail"] == str(error)


async def test_assistant_routes_require_a_session() -> None:
    app = create_app(
        service_factory=lambda user_id: object(),  # type: ignore[arg-type]
        assistant_factory=lambda user_id: StubAssistant(),  # type: ignore[arg-type]
    )
    async with client(app) as http:
        configure = await http.get("/api/ai-config")
        chat = await http.post("/api/study-assistant/chat", json={"message": "Olá"})

    assert configure.status_code == 401
    assert chat.status_code == 401


async def test_listing_models_returns_what_the_provider_offers() -> None:
    assistant = StubAssistant(
        listing=(
            ChatModelInfo(id="claude-sonnet-4-5", display_name="Claude Sonnet 4.5"),
            ChatModelInfo(id="claude-haiku-4-5"),
        )
    )
    async with client(build(assistant)) as http:
        response = await http.post("/api/ai-config/models", json={"provider": "anthropic"})

    assert response.status_code == 200
    assert response.json() == {
        "models": [
            {"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"},
            {"id": "claude-haiku-4-5", "display_name": None},
        ]
    }
    # A rota só lista: nada é gravado por pedir a lista.
    assert assistant.saved == {}


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (
            AssistantCredentialsRejectedError(
                "the Anthropic provider refused the api key with HTTP 401: check the key "
                "saved for this provider before listing models"
            ),
            422,
        ),
        (
            AssistantUnavailableError(
                "could not reach http://localhost:11434/v1/models: the connection was refused"
            ),
            502,
        ),
        (
            AssistantUnavailableError(
                "http://localhost:11434/v1/models did not return a model list: the payload "
                "is not a list of models"
            ),
            502,
        ),
        (AssistantNotConfiguredError("type the provider key before listing models"), 409),
    ),
)
async def test_a_listing_failure_arrives_with_its_reason(error: Exception, expected: int) -> None:
    """A tela precisa do motivo, não de uma mensagem genérica: o detalhe vem do domínio."""
    assistant = StubAssistant(listing_error=error)
    async with client(build(assistant)) as http:
        response = await http.post("/api/ai-config/models", json={"provider": "anthropic"})

    assert response.status_code == expected
    assert response.json()["detail"] == str(error)
    assert "chave-do-usuario" not in response.text

