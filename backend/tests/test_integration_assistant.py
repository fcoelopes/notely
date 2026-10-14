"""Integração do assistente: contexto ancorado, chave cifrada e caminhos de erro."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from auth_helpers import TEST_USER_ID, ensure_test_user, owned_uow
from notely.core.models import AIModelProvider, AnnotationType
from notely.core.security import SecretCipher
from notely.core.services import NotelyService, StudySessionNotFoundError
from notely.core.study_assistant import (
    AssistantNotConfiguredError,
    AssistantUnavailableError,
    StudyAssistant,
)
from notely.db.models import AIModelConfigRow, AnnotationRow
from notely.providers.chat_model import ChatModelUnavailableError
from notely.providers.pdf_text import EXTRACTOR_VERSION

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

PROVIDER_KEY = "chave-secreta-do-provider"
ANCHOR_QUOTE = "A materialidade depende da natureza ou magnitude das informações"
PAGE_TEXT = (
    "A materialidade depende da natureza ou magnitude das informações contábeis "
    "para influenciar decisões econômicas dos usuários das demonstrações."
)


class ScriptedChat:
    """Chat provider roteirizado: captura o prompt e devolve a resposta fixada."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, response: str | Exception = "Resposta ancorada [página 1].") -> None:
        self.response = response
        self.prompts: list[str] = []

    async def complete(self, *, system, messages, max_tokens=512, temperature=0.0) -> str:
        self.prompts.append(messages[0].content)
        self.system = system
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class ScriptedAssistant(StudyAssistant):
    """Assistente com provider injetado, mantendo a recuperação de contexto real."""

    def __init__(self, uow_factory, *, chat: ScriptedChat, cipher=None) -> None:
        super().__init__(uow_factory, cipher=cipher)
        self.chat = chat

    async def _provider(self):
        config = await self.get_config()
        if config is None or not config.enabled or not config.api_key_encrypted:
            # Sem configuração, o caminho real decide e reporta o erro próprio.
            return await super()._provider()
        return self.chat, config


class IntegrationContext:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.service = NotelyService(lambda: owned_uow(self.sessions))


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
            await db.execute(text("delete from outbox_events"))
            await db.execute(text("delete from documents"))
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"Assistant integration database is unavailable: {exc}")
    yield context
    await engine.dispose()


def assistant(context: IntegrationContext, chat: ScriptedChat, cipher: SecretCipher) -> ScriptedAssistant:
    return ScriptedAssistant(
        lambda: owned_uow(context.sessions), chat=chat, cipher=cipher
    )


async def configure(
    target: StudyAssistant, *, base_url: str | None = "https://api.exemplo.com/v1/chat/completions"
) -> None:
    await target.save_config(
        provider=AIModelProvider.OPENAI_COMPATIBLE,
        model="gpt-exemplo",
        api_key=PROVIDER_KEY,
        base_url=base_url,
    )


async def index_corpus(context: IntegrationContext, document_id: UUID, sha256: str) -> None:
    """Grava o corpus já extraído: é o que a recuperação do assistente lê."""
    async with context.sessions() as db:
        await db.execute(
            text(
                "insert into document_corpus_index "
                "(document_id, source_sha256, extractor_version, status, actual_page_count, indexed_at) "
                "values (:id, :sha, :version, 'ready', 1, now())"
            ),
            {"id": document_id, "sha": sha256, "version": EXTRACTOR_VERSION},
        )
        await db.execute(
            text(
                "insert into document_corpus_pages "
                "(document_id, page_number, text_content, content_sha256, extractor_version) "
                "values (:id, 1, :content, :digest, :version)"
            ),
            {
                "id": document_id,
                "content": PAGE_TEXT,
                "digest": uuid4().hex + uuid4().hex,
                "version": EXTRACTOR_VERSION,
            },
        )
        await db.execute(
            text(
                "insert into document_corpus_chunks "
                "(document_id, page_number, chunk_number, start_offset, end_offset, "
                "text_content, content_sha256) values (:id, 1, 0, 0, :end, :content, :digest)"
            ),
            {
                "id": document_id,
                "end": len(PAGE_TEXT),
                "content": PAGE_TEXT,
                "digest": uuid4().hex + uuid4().hex,
            },
        )
        await db.commit()


async def session_with_material(context: IntegrationContext) -> tuple[UUID, UUID]:
    """Sessão de estudo com um PDF indexado e uma anotação do usuário."""
    document = await context.service.register_document(
        sha256=uuid4().hex + uuid4().hex,
        title="IAS 1 - apresentação",
        filename="ias-1.pdf",
        page_count=4,
        storage_uri="s3://notely-documents/ias-1.pdf",
    )
    session = await context.service.create_study_session(theme="Demonstrações contábeis")
    await context.service.attach_document(session_id=session.id, document_id=document.id)
    await context.service.create_annotation(
        document_id=document.id,
        page_number=1,
        annotation_type=AnnotationType.HIGHLIGHT,
        quote=ANCHOR_QUOTE,
        comment="Preciso entender quando isso muda a decisão do usuário.",
        position={"rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}]},
        study_session_id=session.id,
    )
    await index_corpus(context, document.id, document.sha256)
    return session.id, document.id


async def test_config_key_is_stored_encrypted_and_kept_on_update(
    context: IntegrationContext,
) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    target = assistant(context, ScriptedChat(), cipher)

    await configure(target)

    config = await target.get_config()
    assert config is not None
    assert config.provider is AIModelProvider.OPENAI_COMPATIBLE
    assert config.api_key_encrypted is not None
    # A chave não fica em claro no banco e volta a ser legível com a chave do servidor.
    assert PROVIDER_KEY not in config.api_key_encrypted
    assert cipher.decrypt(config.api_key_encrypted) == PROVIDER_KEY

    # Sem nova chave, a guardada é mantida ao trocar o modelo.
    updated = await target.save_config(
        provider=AIModelProvider.ANTHROPIC, model="claude-exemplo"
    )
    assert updated.model == "claude-exemplo"
    assert updated.api_key_encrypted == config.api_key_encrypted

    # Limpar remove de fato: a configuração deixa de existir.
    await target.clear_config()
    assert await target.get_config() is None


async def test_grounded_answer_uses_the_corpus_and_the_user_notes(
    context: IntegrationContext,
) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    chat = ScriptedChat("A materialidade depende da magnitude [IAS 1, página 1].")
    target = assistant(context, chat, cipher)
    await configure(target)
    session_id, _ = await session_with_material(context)

    answer = await target.ask(message="O que é materialidade?", study_session_id=session_id)

    assert answer.grounded is True
    assert answer.answer.startswith("A materialidade")
    assert answer.provider == "openai_compatible"
    assert answer.model == "gpt-exemplo"
    assert [source.page_number for source in answer.sources] == [1]
    assert answer.sources[0].document_title == "IAS 1 - apresentação"
    assert "materialidade" in answer.sources[0].excerpt

    prompt = chat.prompts[0]
    # O contexto cita a evidência e distingue a voz do estudante.
    assert "IAS 1 - apresentação, página 1" in prompt
    assert ANCHOR_QUOTE in prompt
    assert "Preciso entender quando isso muda a decisão" in prompt
    assert "Anotações do estudante" in prompt
    assert "Trechos do material" in prompt
    # O documento integral não vai para o provider.
    assert "demonstrações" in prompt and len(prompt) < 4000
    # O sistema exige citação, proíbe supor e separa a resposta da voz do usuário.
    assert "cite o documento e a página" in chat.system
    assert "não há evidência suficiente" in chat.system
    assert "sugestão de estudo" in chat.system


async def test_free_chat_has_no_grounding_and_no_sources(
    context: IntegrationContext,
) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    chat = ScriptedChat("Dividir é subtrair.")
    target = assistant(context, chat, cipher)
    await configure(target)
    await session_with_material(context)

    answer = await target.ask(message="Quanto é 10 dividido por 2?")

    assert answer.grounded is False
    assert answer.sources == ()
    assert "Não há contexto do material" in chat.prompts[0]


async def test_unknown_session_is_rejected(context: IntegrationContext) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    target = assistant(context, ScriptedChat(), cipher)
    await configure(target)

    with pytest.raises(StudySessionNotFoundError):
        await target.ask(message="O que é materialidade?", study_session_id=uuid4())


async def test_asking_without_configuration_is_reported(
    context: IntegrationContext,
) -> None:
    target = assistant(context, ScriptedChat(), SecretCipher(SecretCipher.generate_key()))

    with pytest.raises(AssistantNotConfiguredError):
        await target.ask(message="O que é materialidade?")


async def test_provider_failure_does_not_touch_the_annotations(
    context: IntegrationContext,
) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    target = assistant(context, ScriptedChat(ChatModelUnavailableError("down")), cipher)
    await configure(target)
    session_id, document_id = await session_with_material(context)

    with pytest.raises(AssistantUnavailableError):
        await target.ask(message="O que é materialidade?", study_session_id=session_id)

    # A resposta do modelo nunca vira anotação: o dado autoral permanece o mesmo.
    annotations = await context.service.list_document_annotations(document_id)
    assert len(annotations) == 1
    async with context.sessions() as db:
        stored = (await db.scalars(select(AnnotationRow))).all()
    assert len(stored) == 1


async def test_config_is_per_user(context: IntegrationContext) -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    target = assistant(context, ScriptedChat(), cipher)
    await configure(target)

    async with context.sessions() as db:
        rows = (await db.scalars(select(AIModelConfigRow))).all()

    assert len(rows) == 1
    assert rows[0].user_id == TEST_USER_ID
    assert rows[0].api_key_encrypted != PROVIDER_KEY
