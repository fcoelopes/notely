"""Integração real: afirmação autoral → PostgreSQL → projeção no Apache AGE.

Requer PostgreSQL com AGE + TimescaleDB e as migrations aplicadas, incluindo a `0015`:

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U notely -d postgres -c "create database notely_test owner notely"
backend/.venv/bin/python scripts/migrate.py --database notely_test
```

Cada teste limpa o grafo e as tabelas de domínio antes de rodar. Sem esse banco os testes
são pulados, para que a suíte continue rodando em qualquer ambiente.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from auth_helpers import TEST_USER_ID, authenticate, ensure_test_user
from notely.api.app import create_app
from notely.core.models import (
    AnnotationType,
    ClaimRelationKind,
    RelationProvenance,
    User,
    utc_now,
)
from notely.core.services import (
    ClaimNotFoundError,
    ClaimRelationNotFoundError,
    NotelyService,
)
from notely.db.graph import AgeGraph
from notely.db.models import ClaimRelationRow, ClaimRow, ConceptRow
from notely.db.outbox import SqlAlchemyOutboxStore
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.workers import graph as graph_worker

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

ANCHOR_QUOTE = "A materialidade depende da natureza ou magnitude das informações"
ANSWER_QUOTE = "Julgamento profissional considera a natureza da informação"
CLAIM_TEXT = "A materialidade é julgada com julgamento profissional"


class IntegrationContext:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.store = SqlAlchemyOutboxStore(self.sessions)
        self.graph = AgeGraph()

    def service(self, user_id: UUID = TEST_USER_ID) -> NotelyService:
        return NotelyService(lambda: SqlAlchemyUnitOfWork(self.sessions, user_id=user_id))


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
            await connection.execute(text("select 1 from claims limit 1"))
            await connection.execute(text("select 1 from claim_relations limit 1"))
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"Claims integration database is unavailable: {exc}")

    async with context.sessions() as db:
        await context.graph.truncate(session=db)
        await db.execute(text("delete from outbox_events"))
        await db.execute(text("delete from claims"))
        await db.execute(text("delete from annotation_concepts"))
        await db.execute(text("delete from concepts"))
        await db.execute(text("delete from documents"))
        await db.commit()

    await ensure_test_user(context.sessions)
    yield context
    await engine.dispose()


async def _foreign_user(context: IntegrationContext) -> User:
    """Conta separada, para verificar que nada alheio é alcançado."""
    user_id = uuid4()
    async with context.sessions() as db:
        await db.execute(
            text(
                "insert into users (id, email, normalized_email, totp_enabled, created_at, "
                "updated_at) values (:id, :email, :email, false, now(), now())"
            ),
            {"id": user_id, "email": f"{user_id}@example.com"},
        )
        await db.commit()
    return User(
        id=user_id, email=f"{user_id}@example.com", normalized_email=f"{user_id}@example.com"
    )


async def _register_document(context: IntegrationContext, service: NotelyService) -> UUID:
    document = await service.register_document(
        sha256=uuid4().hex + uuid4().hex,
        title="IAS 1 - apresentação",
        filename="ias-1.pdf",
        page_count=38,
        storage_uri="s3://notely-documents/documents/7c/ias-1.pdf",
    )
    return document.id


async def _create_annotation(
    context: IntegrationContext,
    service: NotelyService,
    document_id: UUID,
    annotation_type: AnnotationType,
    *,
    quote: str = ANCHOR_QUOTE,
    comment: str | None = None,
) -> UUID:
    annotation = await service.create_annotation(
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


async def _concept(context: IntegrationContext, name: str) -> UUID:
    """Conceito do dono. Hoje eles nascem da extração; aqui a linha é criada direto."""
    async with context.sessions() as db:
        concept = ConceptRow(
            id=uuid4(),
            user_id=TEST_USER_ID,
            name=name,
            normalized_name=name.casefold(),
            created_at=utc_now(),
        )
        db.add(concept)
        await db.commit()
        return concept.id


async def _project(context: IntegrationContext) -> dict[str, int]:
    return await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=context.graph
    )


async def _counts(context: IntegrationContext) -> dict[str, int]:
    async with context.sessions() as db:
        return {
            "claims": await context.graph.count(session=db, label="Claim"),
            "supports": await context.graph.count_labelled_edges(session=db, label="SUPPORTS"),
            "contradicts": await context.graph.count_labelled_edges(
                session=db, label="CONTRADICTS"
            ),
            "answered_by": await context.graph.count_labelled_edges(
                session=db, label="ANSWERED_BY"
            ),
            "related_to": await context.graph.count_labelled_edges(
                session=db, label="RELATED_TO"
            ),
        }


async def _node_properties(context: IntegrationContext, label: str) -> list[dict]:
    async with context.sessions() as db:
        return await context.graph.node_properties(session=db, label=label)


async def _edge_properties(context: IntegrationContext, label: str) -> list[dict]:
    async with context.sessions() as db:
        return await context.graph.edge_properties(session=db, label=label)


async def test_asserting_from_an_annotation_projects_claim_and_edge(
    context: IntegrationContext,
) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    annotation_id = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )

    evidence = await service.create_claim(
        annotation_id=annotation_id,
        text=CLAIM_TEXT,
        relation=ClaimRelationKind.SUPPORTS,
    )

    # Persistência autoral, distinguível de inferência.
    assert evidence.claim.provenance.value == "user_authored"
    assert evidence.claim.status.value == "accepted"
    assert evidence.claim.accepted_at is not None
    assert evidence.claim.provider is None and evidence.claim.model is None
    assert evidence.relation.relation is ClaimRelationKind.SUPPORTS
    assert evidence.relation.provenance.value == "user_authored"
    assert evidence.quote == ANCHOR_QUOTE
    assert evidence.page_number == 6
    assert evidence.document_title == "IAS 1 - apresentação"

    stats = await _project(context)
    assert stats["failed"] == 0

    counts = await _counts(context)
    assert counts["claims"] == 1
    assert counts["supports"] == 1

    claims = await _node_properties(context, "Claim")
    assert len(claims) == 1
    # Nada existe apenas no AGE: o nó guarda a referência ao registro de origem.
    assert claims[0]["source_id"] == str(evidence.claim.id)
    assert claims[0]["text"] == CLAIM_TEXT
    assert claims[0]["provenance"] == "user_authored"
    assert claims[0]["status"] == "accepted"

    edges = await _edge_properties(context, "SUPPORTS")
    assert edges[0]["provenance"] == "user_authored"
    assert edges[0]["status"] == "accepted"


async def test_asserting_the_same_text_reuses_the_claim_and_adds_evidence(
    context: IntegrationContext,
) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    first = await _create_annotation(context, service, document_id, AnnotationType.HIGHLIGHT)
    second = await _create_annotation(
        context,
        service,
        document_id,
        AnnotationType.NOTE,
        quote=ANSWER_QUOTE,
        comment="mesma ideia, outro trecho",
    )

    await service.create_claim(
        annotation_id=first, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    # O mesmo texto com outra caixa e espaçamento é a mesma afirmação.
    repeated = await service.create_claim(
        annotation_id=second,
        text=f"  {CLAIM_TEXT.upper()}  ",
        relation=ClaimRelationKind.SUPPORTS,
    )

    async with context.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ClaimRow)) == 1
        assert await db.scalar(select(func.count()).select_from(ClaimRelationRow)) == 2
        stored = (await db.scalars(select(ClaimRow))).one()
    assert stored.id == repeated.claim.id
    # O reuso não reescreve a linha existente: o texto exibido continua o primeiro, e a
    # proveniência não é trocada. Reescrever destruiria a origem de uma sugestão de modelo que
    # o usuário tivesse aceitado, e deixaria o grafo com o texto antigo.
    assert stored.text == CLAIM_TEXT
    assert stored.provenance == RelationProvenance.USER_AUTHORED.value
    assert stored.accepted_at is not None

    await _project(context)
    counts = await _counts(context)
    assert counts["claims"] == 1
    assert counts["supports"] == 2
    # As duas camadas concordam sobre o texto: nada ficou obsoleto no grafo.
    nodes = await _node_properties(context, "Claim")
    assert [node["text"] for node in nodes] == [stored.text]

    listed = await service.list_claim_evidence()
    assert len(listed) == 2
    assert {entry.quote for entry in listed} == {ANCHOR_QUOTE, ANSWER_QUOTE}
    assert all(entry.relation.relation is ClaimRelationKind.SUPPORTS for entry in listed)


async def test_contradiction_is_a_distinct_edge(context: IntegrationContext) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    annotation_id = await _create_annotation(
        context, service, document_id, AnnotationType.DISAGREEMENT
    )

    await service.create_claim(
        annotation_id=annotation_id,
        text="A materialidade é sempre quantitativa",
        relation=ClaimRelationKind.CONTRADICTS,
    )
    await _project(context)

    counts = await _counts(context)
    assert counts["contradicts"] == 1
    assert counts["supports"] == 0
    edges = await _edge_properties(context, "CONTRADICTS")
    assert edges[0]["provenance"] == "user_authored"


async def test_answered_by_links_question_and_answer(context: IntegrationContext) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    question_id = await _create_annotation(
        context,
        service,
        document_id,
        AnnotationType.QUESTION,
        comment="Como isso se aplica ao consolidado?",
    )
    answer_id = await _create_annotation(
        context, service, document_id, AnnotationType.NOTE, quote=ANSWER_QUOTE
    )

    relation = await service.answer_question(
        question_annotation_id=question_id, answer_annotation_id=answer_id
    )

    assert relation.relation is ClaimRelationKind.ANSWERED_BY
    assert relation.provenance.value == "user_authored"
    assert relation.question_annotation_id == question_id
    assert relation.answer_annotation_id == answer_id

    # Repetir a mesma aresta não cria linha nova.
    again = await service.answer_question(
        question_annotation_id=question_id, answer_annotation_id=answer_id
    )
    assert again.id == relation.id

    await _project(context)
    counts = await _counts(context)
    assert counts["answered_by"] == 1
    edges = await _edge_properties(context, "ANSWERED_BY")
    assert edges[0]["provenance"] == "user_authored"
    assert edges[0]["status"] == "accepted"


async def test_related_to_links_two_owned_concepts(context: IntegrationContext) -> None:
    service = context.service()
    first = await _concept(context, "Materialidade")
    second = await _concept(context, "Relevância")

    relation = await service.relate_concepts(
        source_concept_id=first, target_concept_id=second
    )
    assert relation.relation is ClaimRelationKind.RELATED_TO
    # Repetir devolve a mesma aresta: a idempotência vale no banco, não só no `MERGE` do grafo,
    # que daria contagem 1 mesmo com duas linhas gravadas.
    again = await service.relate_concepts(source_concept_id=first, target_concept_id=second)
    assert again.id == relation.id
    async with context.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ClaimRelationRow)) == 1

    await _project(context)
    counts = await _counts(context)
    assert counts["related_to"] == 1
    edges = await _edge_properties(context, "RELATED_TO")
    assert edges[0]["provenance"] == "user_authored"


async def test_reprocessing_does_not_duplicate_node_or_edge(
    context: IntegrationContext,
) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    annotation_id = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )
    await service.create_claim(
        annotation_id=annotation_id, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    await _project(context)
    before = await _counts(context)

    # Simula retry: a entrega volta a ficar pendente para o worker de grafo.
    async with context.sessions() as db:
        await db.execute(
            text(
                "update outbox_deliveries set processed_at = null, claimed_at = null, "
                "attempt_count = 0 where consumer = 'graph'"
            )
        )
        await db.commit()

    retry = await _project(context)

    assert retry["claimed"] > 0
    assert retry["failed"] == 0
    assert await _counts(context) == before


async def test_rebuild_reproduces_claims_and_the_four_edges(
    context: IntegrationContext,
) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    evidence_id = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )
    question_id = await _create_annotation(
        context,
        service,
        document_id,
        AnnotationType.QUESTION,
        comment="Como isso se aplica?",
    )
    answer_id = await _create_annotation(
        context, service, document_id, AnnotationType.NOTE, quote=ANSWER_QUOTE
    )
    disagreement_id = await _create_annotation(
        context, service, document_id, AnnotationType.DISAGREEMENT
    )
    await service.create_claim(
        annotation_id=evidence_id, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    await service.create_claim(
        annotation_id=disagreement_id,
        text="A materialidade é sempre quantitativa",
        relation=ClaimRelationKind.CONTRADICTS,
    )
    await service.answer_question(
        question_annotation_id=question_id, answer_annotation_id=answer_id
    )
    await service.relate_concepts(
        source_concept_id=await _concept(context, "Materialidade"),
        target_concept_id=await _concept(context, "Relevância"),
    )

    await _project(context)
    incremental = await _counts(context)
    assert incremental == {
        "claims": 2,
        "supports": 1,
        "contradicts": 1,
        "answered_by": 1,
        "related_to": 1,
    }

    async with context.sessions() as db:
        await context.graph.truncate(session=db)
        await db.commit()
    assert (await _counts(context))["claims"] == 0

    await graph_worker.rebuild(sessions=context.sessions, graph=context.graph)

    assert await _counts(context) == incremental


async def test_rebuild_ignores_soft_deleted_claims(context: IntegrationContext) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    annotation_id = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )
    evidence = await service.create_claim(
        annotation_id=annotation_id, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    await service.delete_claim(evidence.claim.id)

    await graph_worker.rebuild(sessions=context.sessions, graph=context.graph)

    counts = await _counts(context)
    assert counts["claims"] == 0
    assert counts["supports"] == 0
    async with context.sessions() as db:
        # A afirmação continua no PostgreSQL: a exclusão é suave.
        row = await db.get(ClaimRow, evidence.claim.id)
    assert row is not None and row.deleted_at is not None


async def test_deleting_a_claim_removes_node_and_edges_from_the_graph(
    context: IntegrationContext,
) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    annotation_id = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )
    evidence = await service.create_claim(
        annotation_id=annotation_id, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    await _project(context)
    assert (await _counts(context))["claims"] == 1

    await service.delete_claim(evidence.claim.id)
    await _project(context)

    counts = await _counts(context)
    assert counts["claims"] == 0
    assert counts["supports"] == 0
    assert await service.list_claim_evidence() == []
    # Excluir de novo responde como inexistente.
    with pytest.raises(ClaimNotFoundError):
        await service.delete_claim(evidence.claim.id)


async def test_removing_an_edge_keeps_the_nodes(context: IntegrationContext) -> None:
    service = context.service()
    document_id = await _register_document(context, service)
    question_id = await _create_annotation(
        context, service, document_id, AnnotationType.QUESTION, comment="Como aplicar?"
    )
    answer_id = await _create_annotation(
        context, service, document_id, AnnotationType.NOTE, quote=ANSWER_QUOTE
    )
    relation = await service.answer_question(
        question_annotation_id=question_id, answer_annotation_id=answer_id
    )
    await _project(context)
    assert (await _counts(context))["answered_by"] == 1

    removed = await service.delete_claim_relation(relation.id)
    assert removed.relation is ClaimRelationKind.ANSWERED_BY
    await _project(context)

    counts = await _counts(context)
    assert counts["answered_by"] == 0
    async with context.sessions() as db:
        # As duas anotações continuam no grafo: só a aresta saiu.
        assert await context.graph.count(session=db, label="Question") == 1
    with pytest.raises(ClaimRelationNotFoundError):
        await service.delete_claim_relation(relation.id)


async def test_worker_failure_keeps_the_claim(context: IntegrationContext) -> None:
    """Falha do worker não afeta a afirmação: a entrega fica pendente com backoff."""
    service = context.service()
    document_id = await _register_document(context, service)
    annotation_id = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )
    evidence = await service.create_claim(
        annotation_id=annotation_id, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )

    class BrokenGraph(AgeGraph):
        async def project_claim(self, **kwargs: object) -> None:  # type: ignore[override]
            raise RuntimeError("AGE is unavailable")

    stats = await graph_worker.run_batch(
        store=context.store, sessions=context.sessions, graph=BrokenGraph()
    )

    assert stats["failed"] > 0
    # A afirmação continua persistida e legível.
    assert [entry.claim.id for entry in await service.list_claim_evidence()] == [
        evidence.claim.id
    ]
    async with context.sessions() as db:
        delivery = await db.scalar(
            text(
                "select attempt_count from outbox_deliveries "
                "where consumer = 'graph' and attempt_count > 0"
            )
        )
        claim = await db.get(ClaimRow, evidence.claim.id)
    assert delivery == 1
    assert claim is not None and claim.deleted_at is None


async def test_foreign_annotation_relation_and_claim_are_not_reachable(
    context: IntegrationContext,
) -> None:
    """Documento, anotação, conceito e afirmação de outra conta respondem como inexistentes."""
    owner = context.service()
    document_id = await _register_document(context, owner)
    evidence_annotation = await _create_annotation(
        context, owner, document_id, AnnotationType.HIGHLIGHT
    )
    # A dúvida e a que responde, ambas do dono.
    question_id = await _create_annotation(
        context, owner, document_id, AnnotationType.QUESTION, comment="Como aplicar?"
    )
    answer_id = await _create_annotation(
        context, owner, document_id, AnnotationType.NOTE, quote=ANSWER_QUOTE
    )
    concept_id = await _concept(context, "Materialidade")
    other_concept_id = await _concept(context, "Relevância")
    evidence = await owner.create_claim(
        annotation_id=evidence_annotation,
        text=CLAIM_TEXT,
        relation=ClaimRelationKind.SUPPORTS,
    )
    edge = await owner.relate_concepts(
        source_concept_id=concept_id, target_concept_id=other_concept_id
    )

    intruder_user = await _foreign_user(context)
    service_factory = lambda user_id: context.service(user_id)  # noqa: E731
    owner_app = authenticate(create_app(service_factory))
    intruder_app = authenticate(create_app(service_factory), intruder_user)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=intruder_app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/claims")).json() == []
        assert (
            await client.post(
                "/api/claims",
                json={
                    "annotation_id": str(evidence_annotation),
                    "text": "alheio",
                    "relation": "supports",
                },
            )
        ).status_code == 404
        assert (await client.delete(f"/api/claims/{evidence.claim.id}")).status_code == 404
        assert (
            await client.post(
                f"/api/questions/{question_id}/answers",
                json={"answer_annotation_id": str(answer_id)},
            )
        ).status_code == 404
        assert (
            await client.post(
                "/api/concept-relations",
                json={
                    "source_concept_id": str(concept_id),
                    "target_concept_id": str(other_concept_id),
                },
            )
        ).status_code == 404
        assert (await client.delete(f"/api/claim-relations/{edge.id}")).status_code == 404

    # Nada foi criado nem removido pelo caminho alheio.
    assert [entry.claim.id for entry in await owner.list_claim_evidence()] == [
        evidence.claim.id
    ]
    async with context.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ClaimRow)) == 1

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=owner_app), base_url="http://test"
    ) as client:
        listed = (await client.get("/api/claims")).json()
        assert len(listed) == 1
        assert listed[0]["claim"]["id"] == str(evidence.claim.id)
        assert listed[0]["quote"] == ANCHOR_QUOTE
        assert listed[0]["relation"] == "supports"
        assert listed[0]["document_title"] == "IAS 1 - apresentação"


async def test_routes_create_claim_answered_by_and_related_to(
    context: IntegrationContext,
) -> None:
    service_factory = lambda user_id: context.service(user_id)  # noqa: E731
    app = authenticate(create_app(service_factory))
    service = context.service()
    document_id = await _register_document(context, service)
    question_id = await _create_annotation(
        context, service, document_id, AnnotationType.QUESTION, comment="Como aplicar?"
    )
    answer_id = await _create_annotation(
        context, service, document_id, AnnotationType.NOTE, quote=ANSWER_QUOTE
    )
    first_concept = await _concept(context, "Materialidade")
    second_concept = await _concept(context, "Relevância")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/claims",
            json={
                "annotation_id": str(question_id),
                "text": CLAIM_TEXT,
                "relation": "supports",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["claim"]["text"] == CLAIM_TEXT
        assert body["claim"]["provenance"] == "user_authored"
        assert body["annotation_id"] == str(question_id)

        # Relação inválida para uma afirmação é recusada no limite da API.
        assert (
            await client.post(
                "/api/claims",
                json={
                    "annotation_id": str(question_id),
                    "text": "outra",
                    "relation": "related_to",
                },
            )
        ).status_code == 422

        answered = await client.post(
            f"/api/questions/{question_id}/answers",
            json={"answer_annotation_id": str(answer_id)},
        )
        assert answered.status_code == 201
        assert answered.json()["relation"] == "answered_by"

        related = await client.post(
            "/api/concept-relations",
            json={
                "source_concept_id": str(first_concept),
                "target_concept_id": str(second_concept),
            },
        )
        assert related.status_code == 201
        assert related.json()["relation"] == "related_to"

        # Um conceito não se associa consigo mesmo.
        assert (
            await client.post(
                "/api/concept-relations",
                json={
                    "source_concept_id": str(first_concept),
                    "target_concept_id": str(first_concept),
                },
            )
        ).status_code == 422

        # Responder a uma anotação que não é dúvida não cria `ANSWERED_BY`.
        assert (
            await client.post(
                f"/api/questions/{answer_id}/answers",
                json={"answer_annotation_id": str(question_id)},
            )
        ).status_code == 404

        removed = await client.delete(f"/api/claim-relations/{related.json()['id']}")
        assert removed.status_code == 200
        assert removed.json()["relation"] == "related_to"

        assert (await client.delete(f"/api/claims/{body['claim']['id']}")).status_code == 204

    await _project(context)
    counts = await _counts(context)
    assert counts["claims"] == 0  # a afirmação foi removida antes da projeção
    assert counts["answered_by"] == 1
    assert counts["related_to"] == 0


async def test_rebuild_requires_postgresql_as_the_source_of_truth(
    context: IntegrationContext,
) -> None:
    """Apagar o PostgreSQL e manter o grafo não é estado válido: o grafo é derivado."""
    service = context.service()
    document_id = await _register_document(context, service)
    annotation_id = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )
    await service.create_claim(
        annotation_id=annotation_id, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    await _project(context)
    assert (await _counts(context))["claims"] == 1

    async with context.sessions() as db:
        await db.execute(text("delete from claims"))
        await db.commit()

    await graph_worker.rebuild(sessions=context.sessions, graph=context.graph)

    counts = await _counts(context)
    assert counts["claims"] == 0
    assert counts["supports"] == 0


async def test_claim_without_evidence_stays_listed(context: IntegrationContext) -> None:
    """A afirmação é do usuário mesmo sem trecho apontando para ela.

    Remover a única evidência não pode esconder o dado autoral da listagem: a afirmação segue
    ativa no PostgreSQL e precisa continuar alcançável, com a evidência vazia.
    """
    service = context.service()
    document_id = await _register_document(context, service)
    annotation_id = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )
    evidence = await service.create_claim(
        annotation_id=annotation_id, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    assert len(await service.list_claim_evidence()) == 1

    await service.delete_claim_relation(evidence.relation.id)

    listed = await service.list_claim_evidence()
    assert len(listed) == 1
    assert listed[0].claim.id == evidence.claim.id
    assert listed[0].relation is None
    async with context.sessions() as db:
        stored = (await db.scalars(select(ClaimRow))).one()
    assert stored.deleted_at is None


async def test_asserting_the_same_text_after_removal_creates_a_new_claim(
    context: IntegrationContext,
) -> None:
    """O índice de unicidade é parcial: a afirmação removida não bloqueia a nova.

    É a razão de o índice existir — sem o `WHERE deleted_at IS NULL`, afirmar de novo o mesmo
    texto depois de remover a afirmação anterior falharia no conflito.
    """
    service = context.service()
    document_id = await _register_document(context, service)
    first_annotation = await _create_annotation(
        context, service, document_id, AnnotationType.HIGHLIGHT
    )
    second_annotation = await _create_annotation(
        context,
        service,
        document_id,
        AnnotationType.NOTE,
        quote=ANSWER_QUOTE,
        comment="outro trecho",
    )

    original = await service.create_claim(
        annotation_id=first_annotation, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    await service.delete_claim(original.claim.id)

    again = await service.create_claim(
        annotation_id=second_annotation, text=CLAIM_TEXT, relation=ClaimRelationKind.SUPPORTS
    )
    assert again.claim.id != original.claim.id
    async with context.sessions() as db:
        active = await db.scalar(
            select(func.count())
            .select_from(ClaimRow)
            .where(ClaimRow.deleted_at.is_(None))
        )
    assert active == 1
