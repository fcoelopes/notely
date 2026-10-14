"""Marcador autoral persistido por documento e por usuário."""

import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from auth_helpers import TEST_USER_ID, authenticate, ensure_test_user
from notely.api.app import create_app
from notely.core.models import User
from notely.core.services import NotelyService
from notely.db.models import DocumentBookmarkRow, StudySessionViewedPageRow
from notely.db.uow import SqlAlchemyUnitOfWork

DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL", "postgresql+asyncpg://notely:notely@localhost:5432/notely_test"
)


@pytest.fixture
async def context():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("select 1 from document_bookmarks limit 1"))
            await conn.execute(text("delete from reader_events"))
            await conn.execute(text("delete from outbox_events"))
            await conn.execute(text("delete from documents"))
            await conn.execute(text("delete from study_sessions"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Bookmark integration database is unavailable: {exc}")
    await ensure_test_user(sessions)
    yield sessions
    await engine.dispose()


async def make_document(service: NotelyService):
    return await service.register_document(
        sha256=uuid4().hex * 2, title="Documento de estudo", filename="estudo.pdf",
        page_count=5, storage_uri=f"s3://notely-documents/{uuid4()}.pdf",
    )


async def test_bookmark_is_replaced_and_removed_without_changing_progress(context) -> None:
    service = NotelyService(lambda: SqlAlchemyUnitOfWork(context, user_id=TEST_USER_ID))
    document = await make_document(service)
    app = authenticate(create_app(lambda user_id: service))
    path = f"/api/documents/{document.id}/bookmark"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/document-bookmarks")).json() == []
        first = await client.put(path, json={"page_number": 3})
        assert first.status_code == 200
        assert first.json()["page_number"] == 3
        assert first.json()["document_id"] == str(document.id)
        assert (await client.put(path, json={"page_number": 3})).status_code == 200
        replaced = await client.put(path, json={"page_number": 5})
        assert replaced.status_code == 200
        assert replaced.json()["page_number"] == 5
        assert replaced.json()["created_at"] == first.json()["created_at"]
        assert [item["page_number"] for item in (await client.get("/api/document-bookmarks")).json()] == [5]
        async with context() as db:
            assert await db.scalar(select(func.count()).select_from(DocumentBookmarkRow)) == 1
            assert await db.scalar(select(func.count()).select_from(StudySessionViewedPageRow)) == 0
        assert (await client.delete(path)).status_code == 204
        assert (await client.delete(path)).status_code == 204
        assert (await client.get("/api/document-bookmarks")).json() == []


async def test_invalid_and_foreign_documents_cannot_change_bookmarks(context) -> None:
    owner = NotelyService(lambda: SqlAlchemyUnitOfWork(context, user_id=TEST_USER_ID))
    document = await make_document(owner)
    foreign_user = uuid4()
    async with context() as db:
        await db.execute(text(
            "insert into users (id, email, normalized_email, totp_enabled, created_at, updated_at) "
            "values (:id, :email, :email, false, now(), now())"
        ), {"id": foreign_user, "email": f"{foreign_user}@example.com"})
        await db.commit()
    service_factory = lambda user_id: NotelyService(
        lambda: SqlAlchemyUnitOfWork(context, user_id=user_id)
    )
    owner_app = authenticate(create_app(service_factory))
    foreign_app = authenticate(create_app(service_factory), User(
        id=foreign_user,
        email=f"{foreign_user}@example.com",
        normalized_email=f"{foreign_user}@example.com",
    ))
    path = f"/api/documents/{document.id}/bookmark"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner_app), base_url="http://test") as client:
        for page in (0, 6):
            assert (await client.put(path, json={"page_number": page})).status_code == 422
        assert (await client.put(f"/api/documents/{uuid4()}/bookmark", json={"page_number": 1})).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=foreign_app), base_url="http://test") as client:
        assert (await client.get("/api/document-bookmarks")).json() == []
        assert (await client.put(path, json={"page_number": 2})).status_code == 404
        assert (await client.delete(path)).status_code == 404
    async with context() as db:
        assert await db.scalar(select(func.count()).select_from(DocumentBookmarkRow)) == 0
        # Mesmo um registro inconsistente importado diretamente não expõe outro acervo.
        await db.execute(text(
            "insert into document_bookmarks "
            "(user_id, document_id, page_number, created_at, updated_at) "
            "values (:user_id, :document_id, 2, now(), now())"
        ), {"user_id": foreign_user, "document_id": document.id})
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=foreign_app), base_url="http://test") as client:
        assert (await client.get("/api/document-bookmarks")).json() == []
