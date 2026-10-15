"""Integração real: `GET /api/concepts` sobre conceitos extraídos no PostgreSQL.

Requer PostgreSQL com AGE + TimescaleDB e as migrations aplicadas (`0010` em diante):

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -U notely -d postgres -c "create database notely_test owner notely"
backend/.venv/bin/python scripts/migrate.py --database notely_test
```

Cada teste limpa o grafo e as tabelas de domínio antes de rodar. Sem esse banco os testes
são pulados, para que a suíte continue rodando em qualquer ambiente.

O que se verifica aqui é a **leitura**: contagem de trechos por conceito, os documentos que
os contêm (para a tela filtrar pela sessão aberta) e as associações `RELATED_TO` existentes
em qualquer das duas pontas. Nada nesta rota promove conceito a conhecimento confirmado.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from auth_helpers import TEST_USER_ID, authenticate, ensure_test_user
from notely.api.app import create_app
from notely.core.models import AnnotationType, User, utc_now
from notely.core.services import NotelyService
from notely.db.graph import AgeGraph
from notely.db.models import ConceptRow
from notely.db.outbox import SqlAlchemyOutboxStore
from notely.db.uow import SqlAlchemyUnitOfWork
from notely.providers.concept_extraction import (
    ConceptExtractionContext,
    ExtractedConcept,
)
from notely.workers import concepts as concepts_worker

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

MATERIALITY_QUOTE = "A materialidade depende da natureza ou magnitude das informações"
RELEVANCE_QUOTE = "A relevância orienta o que é divulgado nas demonstrações"


class QuoteConceptProvider:
    """Provider roteirizado por trecho: determinístico e sem rede.

    O trecho é o que distingue as anotações aqui, então é ele que decide quais conceitos
    cada uma recebe — é assim que a contagem por conceito ganha significado no teste.
    """

    name = "scripted"
    model = "scripted-1"

    def __init__(self, by_quote: dict[str, list[str]]) -> None:
        self.by_quote = by_quote

    async def extract(self, context: ConceptExtractionContext) -> list[ExtractedConcept]:
        return [
            ExtractedConcept(name=name, confidence=0.5)
            for name in self.by_quote.get(context.quote, [])
        ]


class IntegrationContext:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.store = SqlAlchemyOutboxStore(self.sessions)
        self.graph = AgeGraph()

    def service(self, user_id: UUID = TEST_USER_ID) -> NotelyService:
        return NotelyService(lambda: SqlAlchemyUnitOfWork(self.sessions, user_id=user_id))


async def _reset_database(context: IntegrationContext) -> None:
    """Limpa o que esta suíte escreve, antes e depois de cada teste.

    Antes, para que o resultado não dependa do resíduo de outra execução. Depois, porque o
    `--rebuild` do worker de grafo conta o que existe no PostgreSQL: um conceito deixado
    aqui mudaria a expectativa de `test_integration_graph.py`, que roda em seguida.
    """
    async with context.sessions() as db:
        await context.graph.truncate(session=db)
        await db.execute(text("delete from outbox_events"))
        await db.execute(text("delete from claim_relations"))
        await db.execute(text("delete from claims"))
        await db.execute(text("delete from annotation_concepts"))
        await db.execute(text("delete from concepts"))
        await db.execute(text("delete from documents"))
        await db.commit()


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
            await connection.execute(text("select 1 from concepts limit 1"))
            await connection.execute(text("select 1 from annotation_concepts limit 1"))
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"Concepts integration database is unavailable: {exc}")

    await _reset_database(context)
    await ensure_test_user(context.sessions)
    yield context
    await _reset_database(context)
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


async def _register_document(
    service: NotelyService, *, title: str = "IAS 1 - apresentação"
) -> UUID:
    document = await service.register_document(
        sha256=uuid4().hex + uuid4().hex,
        title=title,
        filename=f"{title}.pdf",
        page_count=38,
        storage_uri=f"s3://notely-documents/documents/{uuid4().hex}.pdf",
    )
    return document.id


async def _create_annotation(
    service: NotelyService, document_id: UUID, quote: str, *, page_number: int = 6
) -> UUID:
    annotation = await service.create_annotation(
        document_id=document_id,
        page_number=page_number,
        annotation_type=AnnotationType.HIGHLIGHT,
        quote=quote,
        comment=None,
        position={
            "rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}],
            "textQuoteSelector": {"exact": quote},
        },
    )
    return annotation.id


async def _extract(
    context: IntegrationContext, provider: QuoteConceptProvider
) -> dict[str, int]:
    return await concepts_worker.run_batch(
        store=context.store, sessions=context.sessions, provider=provider
    )


async def _get_concepts(app) -> list[dict]:  # noqa: ANN001 - FastAPI app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/concepts")
        assert response.status_code == 200
        return response.json()


async def _relate(app, source_id: UUID, target_id: UUID) -> dict:  # noqa: ANN001
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/concept-relations",
            json={
                "source_concept_id": str(source_id),
                "target_concept_id": str(target_id),
            },
        )
        assert response.status_code == 201
        return response.json()


def _by_name(entries: list[dict], name: str) -> dict:
    for entry in entries:
        if entry["name"] == name:
            return entry
    raise AssertionError(f"concept {name!r} is not in the listing")


async def test_reading_a_concept_counts_its_evidence_and_documents(
    context: IntegrationContext,
) -> None:
    """A lista diz quantos trechos e em quais documentos o conceito aparece.

    Sem isso a tela mostraria uma nuvem de termos, e a associação seria feita sem lastro.
    """
    service = context.service()
    document_id = await _register_document(service)
    second_document_id = await _register_document(service, title="IAS 8 - políticas")
    await _create_annotation(service, document_id, MATERIALITY_QUOTE)
    await _create_annotation(service, document_id, RELEVANCE_QUOTE, page_number=7)
    await _create_annotation(service, second_document_id, MATERIALITY_QUOTE)
    await _extract(
        context,
        QuoteConceptProvider(
            {
                MATERIALITY_QUOTE: ["Materialidade"],
                RELEVANCE_QUOTE: ["Relevância"],
            }
        ),
    )

    app = authenticate(create_app(lambda user_id: context.service(user_id)))
    listed = await _get_concepts(app)

    materiality = _by_name(listed, "Materialidade")
    assert materiality["annotation_count"] == 2
    assert {
        (item["document_id"], item["document_title"], item["annotation_count"])
        for item in materiality["documents"]
    } == {
        (str(document_id), "IAS 1 - apresentação", 1),
        (str(second_document_id), "IAS 8 - políticas", 1),
    }
    assert materiality["associations"] == []

    relevance = _by_name(listed, "Relevância")
    assert relevance["annotation_count"] == 1
    assert [item["document_id"] for item in relevance["documents"]] == [str(document_id)]

    # A ordem é a do lastro: o conceito mais referenciado vem primeiro.
    assert [entry["name"] for entry in listed][0] == "Materialidade"


async def test_associations_appear_in_both_ends_with_their_direction(
    context: IntegrationContext,
) -> None:
    """A associação existente é visível nas duas pontas, e a ordem do par é significativa."""
    service = context.service()
    document_id = await _register_document(service)
    await _create_annotation(service, document_id, MATERIALITY_QUOTE)
    await _create_annotation(service, document_id, RELEVANCE_QUOTE, page_number=7)
    await _extract(
        context,
        QuoteConceptProvider(
            {MATERIALITY_QUOTE: ["Materialidade"], RELEVANCE_QUOTE: ["Relevância"]}
        ),
    )

    app = authenticate(create_app(lambda user_id: context.service(user_id)))
    listed = await _get_concepts(app)
    materiality_id = UUID(_by_name(listed, "Materialidade")["id"])
    relevance_id = UUID(_by_name(listed, "Relevância")["id"])

    edge = await _relate(app, materiality_id, relevance_id)

    listed = await _get_concepts(app)
    materiality = _by_name(listed, "Materialidade")
    relevance = _by_name(listed, "Relevância")

    assert len(materiality["associations"]) == 1
    outgoing = materiality["associations"][0]
    assert outgoing["relation_id"] == edge["id"]
    assert outgoing["outgoing"] is True
    assert outgoing["source_concept_id"] == str(materiality_id)
    assert outgoing["target_concept_id"] == str(relevance_id)
    assert outgoing["other_concept_id"] == str(relevance_id)
    assert outgoing["other_concept_name"] == "Relevância"
    # A associação é autoral: quem a afirmou foi a pessoa, não a extração.
    assert outgoing["provenance"] == "user_authored"
    assert outgoing["status"] == "accepted"

    assert len(relevance["associations"]) == 1
    incoming = relevance["associations"][0]
    assert incoming["relation_id"] == edge["id"]
    assert incoming["outgoing"] is False
    assert incoming["other_concept_name"] == "Materialidade"

    # Remover a aresta tira a associação das duas pontas e não remove os conceitos.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.delete(f"/api/claim-relations/{edge['id']}")).status_code == 200

    listed = await _get_concepts(app)
    assert _by_name(listed, "Materialidade")["associations"] == []
    assert _by_name(listed, "Relevância")["associations"] == []
    assert _by_name(listed, "Materialidade")["annotation_count"] == 1


async def test_concepts_of_another_account_are_not_listed_nor_counted(
    context: IntegrationContext,
) -> None:
    """Lista e contagem são escopadas por dono: nada da outra conta aparece."""
    owner = context.service()
    owner_document = await _register_document(owner, title="Documento do dono")
    owner_other_document = await _register_document(owner, title="Outro documento do dono")
    await _create_annotation(owner, owner_document, MATERIALITY_QUOTE)
    await _create_annotation(owner, owner_other_document, RELEVANCE_QUOTE)

    intruder = await _foreign_user(context)
    intruder_service = context.service(intruder.id)
    intruder_document = await _register_document(intruder_service, title="Documento alheio")
    await _create_annotation(intruder_service, intruder_document, MATERIALITY_QUOTE)

    await _extract(
        context,
        QuoteConceptProvider(
            {MATERIALITY_QUOTE: ["Materialidade"], RELEVANCE_QUOTE: ["Relevância"]}
        ),
    )

    owner_app = authenticate(create_app(lambda user_id: context.service(user_id)))
    intruder_app = authenticate(
        create_app(lambda user_id: context.service(user_id)), intruder
    )

    # O nome coincide entre as contas, o conceito não: cada uma tem o seu, com o seu lastro.
    owner_listed = await _get_concepts(owner_app)
    intruder_listed = await _get_concepts(intruder_app)
    assert [entry["name"] for entry in owner_listed] == ["Materialidade", "Relevância"]
    assert [entry["name"] for entry in intruder_listed] == ["Materialidade"]
    assert _by_name(owner_listed, "Materialidade")["id"] != _by_name(
        intruder_listed, "Materialidade"
    )["id"]
    assert [item["document_id"] for item in _by_name(owner_listed, "Materialidade")["documents"]] == [
        str(owner_document)
    ]
    assert [
        item["document_id"] for item in _by_name(intruder_listed, "Materialidade")["documents"]
    ] == [str(intruder_document)]

    # Uma associação do dono não é sequer vista pela outra conta.
    await _relate(
        owner_app,
        UUID(_by_name(owner_listed, "Materialidade")["id"]),
        UUID(_by_name(owner_listed, "Relevância")["id"]),
    )

    assert len(_by_name(await _get_concepts(owner_app), "Materialidade")["associations"]) == 1
    assert (await _get_concepts(intruder_app))[0]["associations"] == []


async def test_a_concept_without_any_reference_is_still_listed(
    context: IntegrationContext,
) -> None:
    """Conceito sem trecho aparece com contagem zero, em vez de sumir da leitura."""
    async with context.sessions() as db:
        db.add(
            ConceptRow(
                id=uuid4(),
                user_id=TEST_USER_ID,
                name="Materialidade",
                normalized_name="materialidade",
                created_at=utc_now(),
            )
        )
        await db.commit()

    app = authenticate(create_app(lambda user_id: context.service(user_id)))
    listed = await _get_concepts(app)

    assert len(listed) == 1
    assert listed[0]["annotation_count"] == 0
    assert listed[0]["documents"] == []
    assert listed[0]["associations"] == []
