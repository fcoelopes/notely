"""Integração da lista de modelos: a rota lê a configuração gravada e nada é persistido.

Requer PostgreSQL com as migrations aplicadas. Sem o banco o teste é pulado. O transporte
HTTP do provedor é dublado — nada sai para a rede —, então o que se verifica aqui é a rota
de ponta a ponta: a chave gravada e cifrada é decifrada para a chamada, o endpoint guardado
decide a URL da lista, e os erros chegam como `409`, `422` e `502` sem repetir a chave.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from auth_helpers import TEST_USER_ID, authenticate, ensure_test_user, owned_uow
from notely.api.app import create_app
from notely.core.models import AIModelProvider
from notely.core.security import SecretCipher
from notely.core.study_assistant import StudyAssistant
from notely.db.models import AIModelConfigRow

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

PROVIDER_KEY = "chave-ficticia-do-provedor"
TYPED_KEY = "chave-ficticia-digitada"
CHAT_ENDPOINT = "http://localhost:11434/v1/chat/completions"
MODELS_ENDPOINT = "http://localhost:11434/v1/models"


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

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
    error: Exception | None = None,
) -> list[urllib.request.Request]:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> FakeResponse:
        requests.append(request)
        if error is not None:
            raise error
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


def headers_of(request: urllib.request.Request) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


class IntegrationContext:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def context() -> AsyncIterator[IntegrationContext]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    context = IntegrationContext(engine)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("select 1 from ai_model_configs limit 1"))
        await ensure_test_user(context.sessions)
        async with context.sessions() as db:
            await db.execute(
                text("delete from ai_model_configs where user_id = :user"), {"user": TEST_USER_ID}
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"Model listing integration database is unavailable: {exc}")
    yield context
    async with context.sessions() as db:
        await db.execute(
            text("delete from ai_model_configs where user_id = :user"), {"user": TEST_USER_ID}
        )
        await db.commit()
    await engine.dispose()


def assistant(context: IntegrationContext, cipher: SecretCipher) -> StudyAssistant:
    return StudyAssistant(lambda: owned_uow(context.sessions), cipher=cipher)


def app_for(context: IntegrationContext, target: StudyAssistant) -> object:
    app = create_app(
        service_factory=lambda user_id: object(),  # type: ignore[arg-type]
        assistant_factory=lambda user_id: target,  # type: ignore[arg-type]
    )
    return authenticate(app)  # type: ignore[arg-type]


async def post_models(app: object, body: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"  # type: ignore[arg-type]
    ) as http:
        return await http.post("/api/ai-config/models", json=body)


async def test_listing_uses_the_stored_key_and_the_stored_endpoint(
    context: IntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    target = assistant(context, cipher)
    await target.save_config(
        provider=AIModelProvider.OPENAI_COMPATIBLE,
        model="llama3.1",
        api_key=PROVIDER_KEY,
        base_url=CHAT_ENDPOINT,
    )
    requests = stub_transport(
        monkeypatch,
        payload={"data": [{"id": "llama3.1"}, {"id": "qwen2.5", "display_name": "Qwen 2.5"}]},
    )

    response = await post_models(app_for(context, target), {"provider": "openai_compatible"})

    assert response.status_code == 200
    assert response.json() == {
        "models": [
            {"id": "llama3.1", "display_name": None},
            {"id": "qwen2.5", "display_name": "Qwen 2.5"},
        ]
    }
    # A chave devolvida para a chamada é a decifrada, e o endpoint vem da configuração.
    assert requests[0].full_url == MODELS_ENDPOINT
    assert headers_of(requests[0])["authorization"] == f"Bearer {PROVIDER_KEY}"
    # A listagem não grava nada: a configuração continua a mesma.
    config = await target.get_config()
    assert config is not None and config.model == "llama3.1"
    assert PROVIDER_KEY not in response.text


async def test_the_anthropic_listing_goes_to_the_official_endpoint(
    context: IntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    target = assistant(context, cipher)
    await target.save_config(
        provider=AIModelProvider.ANTHROPIC, model="claude-exemplo", api_key=PROVIDER_KEY
    )
    requests = stub_transport(monkeypatch, payload={"data": [{"id": "claude-sonnet-4-5"}]})

    response = await post_models(app_for(context, target), {"provider": "anthropic"})

    assert response.status_code == 200
    assert response.json()["models"] == [{"id": "claude-sonnet-4-5", "display_name": None}]
    assert requests[0].full_url == "https://api.anthropic.com/v1/models"
    assert headers_of(requests[0])["x-api-key"] == PROVIDER_KEY


async def test_a_typed_key_is_used_without_being_stored(
    context: IntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = assistant(context, SecretCipher(SecretCipher.generate_key()))
    requests = stub_transport(monkeypatch, payload={"data": [{"id": "claude-sonnet-4-5"}]})

    response = await post_models(
        app_for(context, target), {"provider": "anthropic", "api_key": TYPED_KEY}
    )

    assert response.status_code == 200
    assert headers_of(requests[0])["x-api-key"] == TYPED_KEY
    # Nada foi gravado: listar não é configurar.
    async with context.sessions() as db:
        rows = (await db.scalars(select(AIModelConfigRow))).all()
    assert rows == []


async def test_listing_without_a_configuration_and_a_key_is_a_409(
    context: IntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = stub_transport(monkeypatch, payload={"data": []})
    target = assistant(context, SecretCipher(SecretCipher.generate_key()))

    response = await post_models(app_for(context, target), {"provider": "anthropic"})

    assert response.status_code == 409
    assert "detail" in response.json()
    assert requests == []


async def test_a_stored_endpoint_without_the_chat_completions_suffix_is_a_422(
    context: IntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quem já guardou a base URL recebe o formato esperado, em vez de uma URL adivinhada."""
    cipher = SecretCipher(SecretCipher.generate_key())
    target = assistant(context, cipher)
    await target.save_config(
        provider=AIModelProvider.OPENAI_COMPATIBLE,
        model="llama3.1",
        api_key=PROVIDER_KEY,
        base_url="http://localhost:11434/v1",
    )
    requests = stub_transport(monkeypatch, payload={"data": []})

    response = await post_models(app_for(context, target), {"provider": "openai_compatible"})

    assert response.status_code == 422
    assert "/chat/completions" in response.json()["detail"]
    assert PROVIDER_KEY not in response.text
    assert requests == []


async def test_a_provider_failure_is_a_502_without_exposing_the_key(
    context: IntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    target = assistant(context, cipher)
    await target.save_config(
        provider=AIModelProvider.ANTHROPIC, model="claude-exemplo", api_key=PROVIDER_KEY
    )
    stub_transport(
        monkeypatch,
        error=urllib.error.HTTPError(
            "https://api.anthropic.com/v1/models", 401, "unauthorized", {}, None
        ),
    )

    response = await post_models(app_for(context, target), {"provider": "anthropic"})

    assert response.status_code == 502
    assert "model list" in response.json()["detail"]
    assert PROVIDER_KEY not in response.text


async def test_the_models_route_requires_a_session(context: IntegrationContext) -> None:
    target = assistant(context, SecretCipher(SecretCipher.generate_key()))
    app = create_app(
        service_factory=lambda user_id: object(),  # type: ignore[arg-type]
        assistant_factory=lambda user_id: target,  # type: ignore[arg-type]
    )

    response = await post_models(app, {"provider": "anthropic"})

    assert response.status_code == 401
