"""Integração real: anotação → conceitos no PostgreSQL → projeção no Apache AGE.

Requer PostgreSQL com AGE + TimescaleDB e as migrations aplicadas, incluindo a `0010`:

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U notely -d postgres -c "create database notely_test owner notely"
backend/.venv/bin/python scripts/migrate.py --database notely_test
```
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from notely.core.models import AIModelConfig, AIModelProvider, AnnotationType
from notely.core.security import SecretCipher
from notely.core.services import NotelyService
from notely.core.study_assistant import StudyAssistant, chat_provider_for
from notely.db.graph import AgeGraph
from notely.db.models import AnnotationConceptRow, AnnotationRow, ConceptRow
from notely.db.outbox import CONSUMER_CONCEPTS, SqlAlchemyOutboxStore
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.providers.chat_model import ChatModelProvider, ChatModelUnavailableError
from notely.providers.concept_extraction import (
    ConceptExtractionContext,
    ConceptExtractionUnavailableError,
    ExtractedConcept,
)
from notely.workers import concepts as concepts_worker
from notely.workers import graph as graph_worker
from auth_helpers import TEST_USER_ID, ensure_test_user

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

ANCHOR_QUOTE = "A materialidade depende da natureza ou magnitude das informações"

# Chaves de cifra: a do servidor decifra a chave que o dono cadastrou.
SERVER_SECRET_KEY = SecretCipher.generate_key()
PROVIDER_KEY = "chave-secreta-do-provider"
OWNER_MODEL = "gpt-do-usuario"
OTHER_USER_ID = UUID("00000000-0000-0000-0000-0000000a0002")
OWNER_ANSWERS = json.dumps(
    {"concepts": [{"name": "Materialidade", "confidence": 0.8}, {"name": "Relevância"}]}
)


class ScriptedConceptProvider:
    """Provider roteirizado: resultados determinísticos, sem rede."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, concepts: list[ExtractedConcept] | Exception) -> None:
        self.concepts = concepts
        self.contexts: list[ConceptExtractionContext] = []

    async def extract(self, context: ConceptExtractionContext) -> list[ExtractedConcept]:
        self.contexts.append(context)
        if isinstance(self.concepts, Exception):
            raise self.concepts
        return list(self.concepts)


class ScriptedChat:
    """Chat provider roteirizado do dono: devolve o JSON combinado, sem rede.

    O nome e o modelo imitam o que o adapter real informaria, para que a proveniência
    gravada possa ser comparada com o provider e o modelo reais do usuário.
    """

    def __init__(
        self,
        *,
        name: str = AIModelProvider.OPENAI_COMPATIBLE.value,
        model: str = OWNER_MODEL,
        response: str | Exception = OWNER_ANSWERS,
    ) -> None:
        self.name = name
        self.model = model
        self.response = response
        self.prompts: list[str] = []

    async def complete(
        self, *, system: str, messages, max_tokens: int = 512, temperature: float = 0.0
    ) -> str:  # noqa: ANN001 - mesma assinatura do protocolo
        self.prompts.append(messages[0].content)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class ScriptedChatFactory:
    """Monta o chat do dono e registra a configuração e a chave decifrada recebidas."""

    def __init__(self, chat: ScriptedChat) -> None:
        self.chat = chat
        self.configs: list[AIModelConfig] = []
        self.api_keys: list[str] = []

    def __call__(self, config: AIModelConfig, api_key: str) -> ChatModelProvider:
        self.configs.append(config)
        self.api_keys.append(api_key)
        return self.chat


class IntegrationContext:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.store = SqlAlchemyOutboxStore(self.sessions)
        self.service = NotelyService(lambda: SqlAlchemyUnitOfWork(self.sessions, user_id=TEST_USER_ID))
        self.graph = AgeGraph()


@pytest.fixture
async def context() -> AsyncIterator[IntegrationContext]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    context = IntegrationContext(engine)
    try:
        async with engine.begin() as connection:
            found = await connection.scalar(
                text("select count(*) from ag_catalog.ag_graph where name = 'notely'")
            )
            if not found:
                raise RuntimeError("AGE graph 'notely' not found")
            await connection.execute(text("select 1 from annotation_concepts limit 1"))
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"Concepts integration database is unavailable: {exc}")

    async with context.sessions() as db:
        await context.graph.truncate(session=db)
        await db.execute(text("delete from outbox_events"))
        await db.execute(text("delete from concepts"))
        await db.execute(text("delete from documents"))
        # A configuração de modelo alimenta a extração: um resíduo de outro teste mudaria
        # qual provider cada dono usa.
        await db.execute(text("delete from ai_model_configs"))
        await db.commit()

    await ensure_test_user(context.sessions)
    await _ensure_owner(context, OTHER_USER_ID, "outro@exemplo.com")
    yield context
    await engine.dispose()


async def _ensure_owner(
    context: IntegrationContext, user_id: UUID, email: str
) -> None:
    """Segundo dono nos testes de resolução por dono."""
    async with context.sessions() as db:
        await db.execute(
            text(
                "insert into users (id, email, normalized_email, totp_enabled, created_at, updated_at) "
                "values (:id, :email, :email, false, now(), now()) on conflict (id) do nothing"
            ),
            {"id": user_id, "email": email},
        )
        await db.commit()


def _service_for(context: IntegrationContext, user_id: UUID) -> NotelyService:
    return NotelyService(lambda: SqlAlchemyUnitOfWork(context.sessions, user_id=user_id))


async def _configure_owner(
    context: IntegrationContext,
    *,
    user_id: UUID = TEST_USER_ID,
    cipher_key: str = SERVER_SECRET_KEY,
    model: str = OWNER_MODEL,
    enabled: bool = True,
) -> None:
    """Cadastra a configuração de modelo do dono, com a chave cifrada em repouso."""
    assistant = StudyAssistant(
        lambda: SqlAlchemyUnitOfWork(context.sessions, user_id=user_id),
        cipher=SecretCipher(cipher_key),
    )
    await assistant.save_config(
        provider=AIModelProvider.OPENAI_COMPATIBLE,
        model=model,
        api_key=PROVIDER_KEY,
        base_url="https://api.exemplo.com/v1/chat/completions",
        enabled=enabled,
    )


async def _register_document(
    context: IntegrationContext, *, user_id: UUID = TEST_USER_ID
) -> UUID:
    document = await _service_for(context, user_id).register_document(
        sha256=uuid4().hex + uuid4().hex,
        title="IAS 1 - apresentação",
        filename="ias-1.pdf",
        page_count=38,
        storage_uri="s3://notely-documents/documents/7c/ias-1.pdf",
    )
    return document.id


async def _create_annotation(
    context: IntegrationContext,
    document_id: UUID,
    annotation_type: AnnotationType,
    *,
    quote: str = ANCHOR_QUOTE,
    comment: str | None = None,
    user_id: UUID = TEST_USER_ID,
) -> UUID:
    annotation = await _service_for(context, user_id).create_annotation(
        document_id=document_id,
        page_number=6,
        annotation_type=annotation_type,
        quote=quote,
        comment=comment,
        position={
            "rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}],
            "textQuoteSelector": {"exact": quote},
        },
    )
    return annotation.id


async def _run_concepts(
    context: IntegrationContext,
    provider: ScriptedConceptProvider | None = None,
    *,
    secret_key: str = "",
    chat_factory: concepts_worker.ChatProviderFactory = chat_provider_for,
) -> dict[str, int]:
    """Roda o lote. `provider` é o do servidor; `secret_key` é a chave que decifra a do dono."""
    return await concepts_worker.run_batch(
        store=context.store,
        sessions=context.sessions,
        provider=provider,
        secret_key=secret_key,
        chat_factory=chat_factory,
    )


async def _relations(context: IntegrationContext) -> list[AnnotationConceptRow]:
    async with context.sessions() as db:
        return list((await db.scalars(select(AnnotationConceptRow))).all())


async def _counts(context: IntegrationContext) -> dict[str, int]:
    async with context.sessions() as db:
        return {
            "documents": await context.graph.count(session=db, label="Document"),
            "annotations": await context.graph.count(session=db, label="Annotation"),
            "concepts": await context.graph.count(session=db, label="Concept"),
            "refers_to": await context.graph.count_labelled_edges(session=db, label="REFERS_TO"),
            "questions": await context.graph.count_labelled_edges(session=db, label="QUESTIONS"),
        }


async def test_extraction_persists_inference_and_reaches_the_graph(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    annotation_id = await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    provider = ScriptedConceptProvider(
        [ExtractedConcept(name="Materialidade", confidence=0.8), ExtractedConcept(name="Relevância")]
    )

    stats = await _run_concepts(context, provider)

    assert stats == {"claimed": 1, "extracted": 1, "relations": 2, "failed": 0}
    # O contexto enviado ao provider é o mínimo: título, tipo, trecho e comentário.
    assert provider.contexts[0].document_title == "IAS 1 - apresentação"
    assert provider.contexts[0].annotation_type == "highlight"

    async with context.sessions() as db:
        relations = (await db.scalars(select(AnnotationConceptRow))).all()
        concepts = (await db.scalars(select(ConceptRow))).all()
    assert len(concepts) == 2
    assert len(relations) == 2
    # Inferência de modelo: nunca autoral enquanto não houver aceite.
    assert all(relation.provenance == "ai_inference" for relation in relations)
    assert all(relation.status == "suggested" for relation in relations)
    assert all(relation.accepted_at is None for relation in relations)
    assert all(relation.relation == "refers_to" for relation in relations)
    assert all(relation.provider == "scripted" and relation.model == "scripted-1" for relation in relations)
    assert {relation.concept_id for relation in relations} == {concept.id for concept in concepts}

    projected = await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )
    assert projected["projected"] == 3  # documento + anotação + conceitos extraídos

    counts = await _counts(context)
    assert counts["documents"] == 1
    assert counts["annotations"] == 1
    assert counts["concepts"] == 2
    assert counts["refers_to"] == 2


async def test_question_annotations_link_with_questions_edges(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(
        context,
        document_id,
        AnnotationType.QUESTION,
        comment="Como isso se aplica ao consolidado?",
    )
    provider = ScriptedConceptProvider([ExtractedConcept(name="Consolidação")])

    await _run_concepts(context, provider)
    await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )

    async with context.sessions() as db:
        relation = (await db.scalars(select(AnnotationConceptRow))).one()
    assert relation.relation == "questions"

    counts = await _counts(context)
    assert counts["concepts"] == 1
    assert counts["questions"] == 1
    assert counts["refers_to"] == 0


async def test_reprocessing_does_not_duplicate_concepts_or_edges(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    provider = ScriptedConceptProvider([ExtractedConcept(name="Materialidade")])

    await _run_concepts(context, provider)
    await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )
    before = await _counts(context)

    # Simula retry: a entrega volta a ficar pendente para o consumidor de conceitos.
    async with context.sessions() as db:
        await db.execute(
            text(
                "update outbox_deliveries set processed_at = null, claimed_at = null, "
                "attempt_count = 0 where consumer = 'concepts'"
            )
        )
        await db.commit()

    retry = await _run_concepts(context, provider)

    # A extração já existente é reaproveitada: nenhum conceito novo e nenhuma chamada
    # adicional ao provider.
    assert retry["relations"] == 0
    assert len(provider.contexts) == 1

    await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )
    assert await _counts(context) == before


async def test_backfill_enqueues_missing_annotations_and_can_run_again(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    provider = ScriptedConceptProvider([ExtractedConcept(name="Materialidade")])

    async with context.sessions() as db:
        assert await concepts_worker.enqueue_missing_annotations(db, provider=provider) == 1
        await db.commit()
    # O pedido já está na fila: reexecutar não duplica.
    async with context.sessions() as db:
        assert await concepts_worker.enqueue_missing_annotations(db, provider=provider) == 0
        await db.commit()

    await _run_concepts(context, provider)
    # Com conceitos gravados, a anotação sai do backfill.
    async with context.sessions() as db:
        assert await concepts_worker.enqueue_missing_annotations(db, provider=provider) == 0
        await db.commit()

    # Reextração com outro provider volta a funcionar: a chave do pedido inclui o modelo.
    async with context.sessions() as db:
        await db.execute(text("delete from annotation_concepts"))
        await db.execute(text("delete from concepts"))
        await db.commit()
    async with context.sessions() as db:
        other = ScriptedConceptProvider([ExtractedConcept(name="Outro")])
        assert await concepts_worker.enqueue_missing_annotations(db, provider=other) == 1
        await db.commit()


async def test_provider_failure_keeps_the_annotation_and_retries_later(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    annotation_id = await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    provider = ScriptedConceptProvider(ConceptExtractionUnavailableError("provider down"))

    stats = await _run_concepts(context, provider)

    assert stats["failed"] == 1
    assert stats["relations"] == 0
    # A anotação continua intacta e nenhuma relação foi gravada.
    annotations = await context.service.list_document_annotations(document_id)
    assert [annotation.id for annotation in annotations] == [annotation_id]
    async with context.sessions() as db:
        assert (await db.scalars(select(AnnotationConceptRow))).all() == []
        delivery = await db.scalar(
            text(
                "select attempt_count from outbox_deliveries "
                "where consumer = :consumer and attempt_count > 0"
            ).bindparams(consumer=CONSUMER_CONCEPTS)
        )
    assert delivery == 1


async def test_owner_model_config_extracts_with_the_owner_key(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    await _configure_owner(context)
    chat = ScriptedChat()
    factory = ScriptedChatFactory(chat)
    server = ScriptedConceptProvider([ExtractedConcept(name="Servidor")])

    stats = await _run_concepts(
        context, server, secret_key=SERVER_SECRET_KEY, chat_factory=factory
    )

    assert stats == {"claimed": 1, "extracted": 1, "relations": 2, "failed": 0}
    # A chave do dono foi decifrada com a chave do servidor e chegou ao provider dele.
    assert factory.api_keys == [PROVIDER_KEY]
    assert factory.configs[0].model == OWNER_MODEL
    # O provider do servidor não foi chamado.
    assert server.contexts == []
    # A proveniência registra o provider e o modelo reais do dono, não um rótulo genérico.
    relations = await _relations(context)
    assert {relation.provider for relation in relations} == {"openai_compatible:concepts"}
    assert {relation.model for relation in relations} == {OWNER_MODEL}
    assert all(relation.provenance == "ai_inference" for relation in relations)
    # A projeção publicada também carrega o provider que extraiu.
    async with context.sessions() as db:
        event_key = await db.scalar(
            text(
                "select event_key from outbox_events "
                "where event_type = 'concepts.extracted'"
            )
        )
    assert "openai_compatible:concepts" in event_key


async def test_each_owner_uses_its_own_provider_in_the_same_batch(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    await _create_annotation(
        context, document_id, AnnotationType.HIGHLIGHT, quote="Relevância da informação"
    )
    other_document_id = await _register_document(context, user_id=OTHER_USER_ID)
    await _create_annotation(
        context, other_document_id, AnnotationType.HIGHLIGHT, user_id=OTHER_USER_ID
    )
    await _configure_owner(context)
    factory = ScriptedChatFactory(ScriptedChat())
    server = ScriptedConceptProvider([ExtractedConcept(name="Servidor")])

    stats = await _run_concepts(
        context, server, secret_key=SERVER_SECRET_KEY, chat_factory=factory
    )

    assert stats["claimed"] == 3
    # Uma leitura de configuração por dono no lote, mesmo com duas anotações do mesmo dono.
    assert len(factory.configs) == 1
    async with context.sessions() as db:
        rows = (
            await db.execute(
                select(
                    AnnotationRow.user_id,
                    AnnotationConceptRow.provider,
                    AnnotationConceptRow.model,
                ).join(AnnotationConceptRow, AnnotationConceptRow.annotation_id == AnnotationRow.id)
            )
        ).all()
    by_owner: dict[UUID, set[tuple[str, str]]] = {}
    for user_id, provider_name, model in rows:
        by_owner.setdefault(user_id, set()).add((provider_name, model))
    assert by_owner[TEST_USER_ID] == {("openai_compatible:concepts", OWNER_MODEL)}
    assert by_owner[OTHER_USER_ID] == {("scripted", "scripted-1")}


async def test_owner_without_configuration_uses_the_server_provider(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    factory = ScriptedChatFactory(ScriptedChat())
    server = ScriptedConceptProvider([ExtractedConcept(name="Servidor")])

    stats = await _run_concepts(context, server, chat_factory=factory)

    assert stats == {"claimed": 1, "extracted": 1, "relations": 1, "failed": 0}
    assert factory.configs == []
    relations = await _relations(context)
    assert {relation.provider for relation in relations} == {"scripted"}
    assert {relation.model for relation in relations} == {"scripted-1"}


async def test_owner_without_configuration_falls_back_to_the_heuristic(
    context: IntegrationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NOTELY_CONCEPT_PROVIDER", raising=False)
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)

    # Sem provider do servidor nem configuração do dono, resta o heurístico local.
    stats = await _run_concepts(context, None, secret_key=SERVER_SECRET_KEY)

    assert stats["extracted"] == 1
    relations = await _relations(context)
    assert relations
    assert {relation.provider for relation in relations} == {"heuristic"}
    assert {relation.model for relation in relations} == {"frequency-v1"}


async def test_missing_server_secret_key_warns_and_falls_back(
    context: IntegrationContext, caplog: pytest.LogCaptureFixture
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    await _configure_owner(context)
    factory = ScriptedChatFactory(ScriptedChat())
    server = ScriptedConceptProvider([ExtractedConcept(name="Servidor")])

    with caplog.at_level(logging.WARNING, logger="notely.workers.concepts"):
        stats = await _run_concepts(context, server, secret_key="", chat_factory=factory)

    # Nada derruba o worker: a extração acontece pela alternativa e a entrega é concluída.
    assert stats == {"claimed": 1, "extracted": 1, "relations": 1, "failed": 0}
    assert factory.configs == []
    warnings = [record.getMessage() for record in caplog.records]
    assert any(
        "NOTELY_AI_SECRET_KEY" in message and "falls back" in message for message in warnings
    )
    assert {relation.provider for relation in await _relations(context)} == {"scripted"}


async def test_owner_key_that_cannot_be_decrypted_warns_and_falls_back(
    context: IntegrationContext, caplog: pytest.LogCaptureFixture
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    # A configuração foi cifrada com a chave do servidor; decifrar com outra falha.
    await _configure_owner(context)
    factory = ScriptedChatFactory(ScriptedChat())
    server = ScriptedConceptProvider([ExtractedConcept(name="Servidor")])

    with caplog.at_level(logging.WARNING, logger="notely.workers.concepts"):
        stats = await _run_concepts(
            context,
            server,
            secret_key=SecretCipher.generate_key(),
            chat_factory=factory,
        )

    assert stats == {"claimed": 1, "extracted": 1, "relations": 1, "failed": 0}
    # A falha acontece na decifra: o provider do dono nem chega a ser montado.
    assert factory.configs == []
    warnings = [record.getMessage() for record in caplog.records]
    assert any("not usable" in message and "falls back" in message for message in warnings)
    # A chave em claro nunca aparece no log.
    assert not any(PROVIDER_KEY in message for message in warnings)
    assert {relation.provider for relation in await _relations(context)} == {"scripted"}


async def test_owner_provider_failure_keeps_the_annotation_and_retries_later(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    annotation_id = await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    await _configure_owner(context)
    factory = ScriptedChatFactory(ScriptedChat(response=ChatModelUnavailableError("sem cota")))

    stats = await _run_concepts(
        context, None, secret_key=SERVER_SECRET_KEY, chat_factory=factory
    )

    assert stats["failed"] == 1
    assert stats["relations"] == 0
    # A extração tentou com a chave do dono antes de falhar.
    assert factory.api_keys == [PROVIDER_KEY]
    # A anotação continua intacta e nenhuma relação foi gravada.
    annotations = await context.service.list_document_annotations(document_id)
    assert [annotation.id for annotation in annotations] == [annotation_id]
    assert await _relations(context) == []
    async with context.sessions() as db:
        delivery = await db.scalar(
            text(
                "select attempt_count from outbox_deliveries "
                "where consumer = :consumer and attempt_count > 0"
            ).bindparams(consumer=CONSUMER_CONCEPTS)
        )
    assert delivery == 1


async def test_disabled_configuration_keeps_the_server_provider(
    context: IntegrationContext,
) -> None:
    document_id = await _register_document(context)
    await _create_annotation(context, document_id, AnnotationType.HIGHLIGHT)
    # Configuração desligada pelo dono: é como não ter configuração, e sem aviso.
    await _configure_owner(context, enabled=False)
    factory = ScriptedChatFactory(ScriptedChat())
    server = ScriptedConceptProvider([ExtractedConcept(name="Servidor")])

    stats = await _run_concepts(
        context, server, secret_key=SERVER_SECRET_KEY, chat_factory=factory
    )

    assert stats == {"claimed": 1, "extracted": 1, "relations": 1, "failed": 0}
    assert factory.configs == []
    assert {relation.provider for relation in await _relations(context)} == {"scripted"}
