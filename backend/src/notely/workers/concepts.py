"""Extrai conceitos das anotações e publica o pedido de projeção no grafo.

Conceito extraído é inferência de modelo, nunca dado autoral: as relações nascem
`suggested` e com proveniência `ai_inference`, e só o aceite explícito do usuário as
promove (AGENTS.md §1). O worker é idempotente e reprocessar não gasta modelo de novo.

O provider é resolvido **por dono da anotação**, dentro do lote: a configuração de modelo
que o usuário cadastrou para o assistente (`ai_model_configs`) extrai os conceitos dele,
com a chave dele decifrada por `NOTELY_AI_SECRET_KEY`; sem configuração utilizável, valem
o provider do servidor (`NOTELY_CONCEPT_*`) e, por fim, o heurístico. A proveniência
gravada em `annotation_concepts` registra o provider e o modelo que de fato extraíram
(ADR 0005).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from notely.core.concepts import normalize_concept_name, relation_for
from notely.core.models import AIModelConfig, AnnotationType, OutboxEvent, utc_now
from notely.core.security import SecretCipher, SecurityError
from notely.core.study_assistant import (
    AssistantError,
    chat_provider_for,
    user_api_key,
)
from notely.db.models import (
    AnnotationConceptRow,
    AnnotationRow,
    ConceptRow,
    DocumentRow,
    OutboxDeliveryRow,
    OutboxEventRow,
)
from notely.db.outbox import (
    CONSUMER_CONCEPTS,
    DEFAULT_BATCH_SIZE,
    ClaimedEvent,
    SqlAlchemyOutboxStore,
)
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.providers.chat_model import (
    AnthropicChatProvider,
    ChatModelProvider,
    OpenAICompatibleChatProvider,
)
from notely.providers.concept_extraction import (
    ConceptExtractionContext,
    ConceptExtractionProvider,
    ConceptExtractionUnavailableError,
    ExtractedConcept,
    HeuristicConceptExtractionProvider,
    ModelConceptExtractionProvider,
)

logger = logging.getLogger("notely.workers.concepts")

REQUESTED_EVENT = "concepts.requested"
PUBLISHED_EVENT = "concepts.extracted"
EVENT_TYPES = frozenset({"annotation.created", REQUESTED_EVENT})
DEFAULT_DATABASE_URL = "postgresql+asyncpg://notely:notely@localhost:5432/notely"
DEFAULT_BACKFILL_LIMIT = 500

# Como a configuração do dono vira provider de chat. O padrão é a regra compartilhada com o
# assistente; é um colaborador injetável, como o `graph` ou o `storage` dos outros workers.
ChatProviderFactory = Callable[[AIModelConfig, str], ChatModelProvider]


async def extract_annotation(
    *,
    session: AsyncSession,
    provider: ConceptExtractionProvider,
    annotation: AnnotationRow,
) -> int:
    """Extrai conceitos de uma anotação e grava as relações sugeridas. Devolve quantas
    relações foram realmente criadas."""
    document = await session.get(DocumentRow, annotation.document_id)
    if document is None:
        return 0

    extracted = await provider.extract(
        ConceptExtractionContext(
            document_title=document.title,
            annotation_type=annotation.type,
            quote=annotation.quote,
            comment=annotation.comment,
        )
    )
    relation = relation_for(AnnotationType(annotation.type)).value
    created = 0
    for concept in extracted:
        concept_id = await _upsert_concept(session, concept, user_id=annotation.user_id)
        if await _add_relation(
            session=session,
            annotation_id=annotation.id,
            concept_id=concept_id,
            relation=relation,
            provider=provider.name,
            model=provider.model,
            confidence=concept.confidence,
        ):
            created += 1
    return created


async def _upsert_concept(
    session: AsyncSession, concept: ExtractedConcept, *, user_id: UUID
) -> UUID:
    normalized = normalize_concept_name(concept.name)
    statement = (
        insert(ConceptRow)
        .values(
            id=uuid4(),
            user_id=user_id,
            name=concept.name,
            normalized_name=normalized,
            created_at=utc_now(),
        )
        # O conceito pertence a um usuário: a unicidade é (user_id, normalized_name).
        .on_conflict_do_update(
            index_elements=["user_id", "normalized_name"], set_={"name": concept.name}
        )
        .returning(ConceptRow.id)
    )
    return (await session.execute(statement)).scalar_one()


async def _add_relation(
    *,
    session: AsyncSession,
    annotation_id: UUID,
    concept_id: UUID,
    relation: str,
    provider: str,
    model: str,
    confidence: float | None,
) -> bool:
    statement = (
        insert(AnnotationConceptRow)
        .values(
            id=uuid4(),
            annotation_id=annotation_id,
            concept_id=concept_id,
            relation=relation,
            provenance="ai_inference",
            status="suggested",
            provider=provider,
            model=model,
            confidence=confidence,
            created_at=utc_now(),
        )
        .on_conflict_do_nothing(index_elements=["annotation_id", "concept_id", "relation"])
        .returning(AnnotationConceptRow.id)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


async def _has_relations(session: AsyncSession, annotation_id: UUID) -> bool:
    return (
        await session.scalar(
            select(AnnotationConceptRow.id)
            .where(AnnotationConceptRow.annotation_id == annotation_id)
            .limit(1)
        )
    ) is not None


async def _publish_extraction(
    session: AsyncSession,
    annotation_id: UUID,
    *,
    user_id: UUID,
    provider: ConceptExtractionProvider,
) -> bool:
    """Publica `concepts.extracted` para o worker de grafo projetar os conceitos.

    A chave inclui o provider: reextrair com outro modelo volta a disparar a projeção. A
    projeção é idempotente por `MERGE`, e `--rebuild` reconcilia o grafo com o PostgreSQL.
    """
    statement = (
        insert(OutboxEventRow)
        .values(
            id=uuid4(),
            user_id=user_id,
            aggregate_type="annotation",
            aggregate_id=annotation_id,
            event_type=PUBLISHED_EVENT,
            payload={"annotation_id": str(annotation_id)},
            event_key=f"concepts:{annotation_id}:{provider.name}:{provider.model}",
            created_at=utc_now(),
            available_at=utc_now(),
            attempt_count=0,
        )
        .on_conflict_do_nothing()
        .returning(OutboxEventRow.id)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


def _annotation_id(event: ClaimedEvent) -> UUID:
    return UUID(str(event.payload.get("annotation_id", event.aggregate_id)))


def _label(provider: ConceptExtractionProvider) -> str:
    return f"{provider.name}/{provider.model}"


class OwnerConceptProviders:
    """Provider de extração por dono da anotação, com cache por lote.

    A precedência é a do ADR 0005: a configuração de modelo do dono (a mesma do
    assistente) → o provider do servidor → o heurístico. A chave cadastrada pelo usuário
    é decifrada com `NOTELY_AI_SECRET_KEY`; se a chave do servidor faltar ou não decifrar,
    o worker avisa e usa a alternativa em vez de deixar a entrega presa em retry por uma
    configuração de ambiente. A chave em claro não é registrada em log nem guardada além do
    provider que a usa.

    O cache vale para um lote: a configuração é lida uma vez por dono, e uma mudança de
    modelo passa a valer no lote seguinte.
    """

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        fallback: ConceptExtractionProvider,
        secret_key: str | None = None,
        chat_factory: ChatProviderFactory = chat_provider_for,
    ) -> None:
        self._sessions = sessions
        self._fallback = fallback
        self._secret_key = secret_key
        self._chat_factory = chat_factory
        self._providers: dict[UUID, ConceptExtractionProvider] = {}
        self._cipher: SecretCipher | None = None
        self._cipher_checked = False
        self._cipher_failure = ""

    async def for_user(self, user_id: UUID) -> ConceptExtractionProvider:
        cached = self._providers.get(user_id)
        if cached is not None:
            return cached
        provider = await self._resolve(user_id)
        self._providers[user_id] = provider
        return provider

    async def _resolve(self, user_id: UUID) -> ConceptExtractionProvider:
        async with SqlAlchemyUnitOfWork(self._sessions, user_id=user_id) as uow:
            config = await uow.get_ai_model_config()
        if config is None or not config.enabled:
            # Quem não cadastrou chave (ou desligou a configuração) não muda de
            # comportamento: segue o provider do servidor e, sem ele, o heurístico.
            return self._fallback
        if not config.api_key_encrypted:
            logger.warning(
                "ai model config of %s has no stored key; concept extraction falls back to %s",
                user_id,
                _label(self._fallback),
            )
            return self._fallback
        cipher = self._cipher_or_none()
        if cipher is None:
            logger.warning(
                "NOTELY_AI_SECRET_KEY is unusable (%s), so the model key of %s cannot be read; "
                "concept extraction falls back to %s",
                self._cipher_failure,
                user_id,
                _label(self._fallback),
            )
            return self._fallback
        try:
            chat = self._chat_factory(config, user_api_key(config, cipher))
        except (AssistantError, SecurityError) as exc:
            logger.warning(
                "ai model config of %s is not usable (%s); concept extraction falls back to %s",
                user_id,
                exc,
                _label(self._fallback),
            )
            return self._fallback
        logger.info("concept extraction of %s uses the owner model %s", user_id, config.model)
        return ModelConceptExtractionProvider(chat=chat)

    def _cipher_or_none(self) -> SecretCipher | None:
        """Uma única construção de `SecretCipher` por lote, com o motivo da falha guardado."""
        if not self._cipher_checked:
            self._cipher_checked = True
            key = (
                self._secret_key
                if self._secret_key is not None
                else os.environ.get("NOTELY_AI_SECRET_KEY", "")
            )
            if not key:
                self._cipher_failure = "NOTELY_AI_SECRET_KEY is not set"
            else:
                try:
                    self._cipher = SecretCipher(key)
                except SecurityError as exc:
                    self._cipher_failure = str(exc)
        return self._cipher


async def run_batch(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    provider: ConceptExtractionProvider | None = None,
    secret_key: str | None = None,
    chat_factory: ChatProviderFactory = chat_provider_for,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """Processa um lote do consumidor `concepts`.

    O provider é resolvido por dono da anotação: `provider` é o provider do **servidor**,
    usado quando o dono não tem configuração de modelo utilizável (sem ele, o heurístico
    de `build_provider()`). `secret_key` e `chat_factory` existem para injetar a chave do
    servidor que decifra e a montagem do provider de chat nos testes.
    """
    server_provider = provider if provider is not None else build_provider()
    owners = OwnerConceptProviders(
        sessions=sessions,
        fallback=server_provider,
        secret_key=secret_key,
        chat_factory=chat_factory,
    )
    stats = {"claimed": 0, "extracted": 0, "relations": 0, "failed": 0}
    async with sessions() as session:
        claimed = await store.claim(
            session=session,
            consumer=CONSUMER_CONCEPTS,
            batch_size=batch_size,
            event_types=EVENT_TYPES,
        )
        stats["claimed"] = len(claimed)
        if not claimed:
            await session.commit()
            return stats

        processed: list[UUID] = []
        for event in claimed:
            try:
                annotation = await session.get(AnnotationRow, _annotation_id(event))
                if annotation is None or annotation.deleted_at is not None:
                    processed.append(event.id)
                    continue
                owner_provider = await owners.for_user(annotation.user_id)
                if await _has_relations(session, annotation.id):
                    # Já extraído: reprocessar não gasta modelo nem duplica relação, mas
                    # republica para o grafo alcançar o estado do PostgreSQL.
                    await _publish_extraction(
                        session, annotation.id, user_id=annotation.user_id, provider=owner_provider
                    )
                else:
                    created = await extract_annotation(
                        session=session, provider=owner_provider, annotation=annotation
                    )
                    stats["extracted"] += 1
                    stats["relations"] += created
                    if created:
                        await _publish_extraction(
                            session,
                            annotation.id,
                            user_id=annotation.user_id,
                            provider=owner_provider,
                        )
            except ConceptExtractionUnavailableError as exc:
                # Falha do provider fica pendente para retry com backoff; a anotação
                # permanece intacta.
                stats["failed"] += 1
                await store.mark_failed(
                    session=session,
                    event_id=event.id,
                    consumer=CONSUMER_CONCEPTS,
                    error=str(exc),
                )
                continue
            processed.append(event.id)

        for event_id in processed:
            await store.mark_processed(
                session=session, event_id=event_id, consumer=CONSUMER_CONCEPTS
            )
        await session.commit()
    return stats


async def enqueue_missing_annotations(
    session: AsyncSession,
    *,
    provider: ConceptExtractionProvider,
    limit: int = DEFAULT_BACKFILL_LIMIT,
) -> int:
    """Enfileira extração para anotações ainda sem conceitos (backfill explícito).

    A pendência é checada na entrega do consumidor, e a chave do evento inclui o provider
    do servidor: trocar de modelo permite reextrair o acervo, sem duplicar um pedido já na
    fila. A chave é só do pedido — quem extrai é o provider do dono de cada anotação.
    """
    has_relations = exists().where(AnnotationConceptRow.annotation_id == AnnotationRow.id)
    undelivered = ~exists().where(
        and_(
            OutboxDeliveryRow.event_id == OutboxEventRow.id,
            OutboxDeliveryRow.consumer == CONSUMER_CONCEPTS,
        )
    )
    unresolved = exists().where(
        and_(
            OutboxDeliveryRow.event_id == OutboxEventRow.id,
            OutboxDeliveryRow.consumer == CONSUMER_CONCEPTS,
            OutboxDeliveryRow.processed_at.is_(None),
        )
    )
    pending = exists().where(
        OutboxEventRow.event_type == REQUESTED_EVENT,
        OutboxEventRow.aggregate_id == AnnotationRow.id,
        or_(undelivered, unresolved),
    )
    rows = (
        await session.execute(
            select(AnnotationRow.id, AnnotationRow.user_id)
            .where(AnnotationRow.deleted_at.is_(None), ~has_relations, ~pending)
            .order_by(AnnotationRow.created_at, AnnotationRow.id)
            .limit(limit)
        )
    ).all()
    queued = 0
    for annotation_id, user_id in rows:
        statement = (
            insert(OutboxEventRow)
            .values(
                id=uuid4(),
                user_id=user_id,
                aggregate_type="annotation",
                aggregate_id=annotation_id,
                event_type=REQUESTED_EVENT,
                payload={"annotation_id": str(annotation_id)},
                event_key=(
                    f"concepts-backfill:{annotation_id}:{provider.name}:{provider.model}:{uuid4()}"
                ),
                created_at=utc_now(),
                available_at=utc_now(),
                attempt_count=0,
            )
            .on_conflict_do_nothing()
            .returning(OutboxEventRow.id)
        )
        if (await session.execute(statement)).scalar_one_or_none() is not None:
            queued += 1
    return queued


async def run_forever(
    *,
    store: SqlAlchemyOutboxStore,
    sessions: async_sessionmaker[AsyncSession],
    provider: ConceptExtractionProvider | None = None,
    secret_key: str | None = None,
    chat_factory: ChatProviderFactory = chat_provider_for,
    batch_size: int = DEFAULT_BATCH_SIZE,
    interval_seconds: float = 2.0,
) -> None:
    while True:
        stats = await run_batch(
            store=store,
            sessions=sessions,
            provider=provider,
            secret_key=secret_key,
            chat_factory=chat_factory,
            batch_size=batch_size,
        )
        if stats["claimed"]:
            logger.info("batch processed %s", stats)
        else:
            await asyncio.sleep(interval_seconds)


def build_provider() -> ConceptExtractionProvider:
    """Provider do servidor por configuração. Sem ela, o heurístico mantém o fluxo local."""
    kind = os.environ.get("NOTELY_CONCEPT_PROVIDER", "heuristic")
    if kind == "heuristic":
        return HeuristicConceptExtractionProvider()
    model = os.environ.get("NOTELY_CONCEPT_MODEL", "")
    api_key = os.environ.get("NOTELY_CONCEPT_API_KEY", "")
    timeout = float(os.environ.get("NOTELY_CONCEPT_TIMEOUT_SECONDS", "30"))
    endpoint = os.environ.get("NOTELY_CONCEPT_ENDPOINT", "")
    if kind == "anthropic":
        chat = AnthropicChatProvider(
            model=model,
            api_key=api_key,
            endpoint=endpoint or AnthropicChatProvider.DEFAULT_ENDPOINT,
            timeout_seconds=timeout,
        )
    elif kind == "openai_compatible":
        chat = OpenAICompatibleChatProvider(
            endpoint=endpoint, model=model, api_key=api_key, timeout_seconds=timeout
        )
    else:
        raise ValueError(f"unsupported concept provider: {kind}")
    return ModelConceptExtractionProvider(chat=chat)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract concepts from annotations")
    parser.add_argument("--once", action="store_true", help="process a single batch and exit")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="enqueue extraction for annotations without concepts, then continue",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("NOTELY_OUTBOX_INTERVAL_SECONDS", "2")),
        help="seconds to wait between batches",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("NOTELY_OUTBOX_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("NOTELY_DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    arguments = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def run() -> None:
        server_provider = build_provider()
        engine = create_async_engine(arguments.database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlAlchemyOutboxStore(sessions)
        try:
            if arguments.backfill:
                async with sessions() as session:
                    queued = await enqueue_missing_annotations(
                        session, provider=server_provider
                    )
                    await session.commit()
                logger.info("concept backfill queued %s annotations", queued)
            if arguments.once:
                stats = await run_batch(
                    store=store,
                    sessions=sessions,
                    provider=server_provider,
                    batch_size=arguments.batch_size,
                )
                logger.info("batch processed %s", stats)
                return
            await run_forever(
                store=store,
                sessions=sessions,
                provider=server_provider,
                batch_size=arguments.batch_size,
                interval_seconds=arguments.interval,
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
