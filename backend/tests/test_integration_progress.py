"""Progresso persistido no PostgreSQL, API e vínculos de sessão."""

import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from notely.api.app import create_app
from notely.core.services import NotelyService
from notely.db.models import StudySessionViewedPageRow
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
            await conn.execute(text("select 1 from study_session_viewed_pages limit 1"))
            await conn.execute(text("delete from reader_events"))
            await conn.execute(text("delete from outbox_events"))
            await conn.execute(text("delete from documents"))
            await conn.execute(text("delete from study_sessions"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Progress integration database is unavailable: {exc}")
    yield sessions
    await engine.dispose()


async def make_document(service: NotelyService, pages: int):
    return await service.register_document(
        sha256=uuid4().hex * 2, title=f"Documento {pages}", filename=f"{uuid4()}.pdf",
        page_count=pages, storage_uri=f"s3://notely-documents/{uuid4()}.pdf",
    )


async def test_distinct_pages_session_weighting_detach_and_global_union(context) -> None:
    service = NotelyService(lambda: SqlAlchemyUnitOfWork(context))
    first = await make_document(service, 4)
    second = await make_document(service, 6)
    session = await service.create_study_session(theme="Estudo")
    other = await service.create_study_session(theme="Outro")
    await service.attach_document(session_id=session.id, document_id=first.id)
    await service.attach_document(session_id=session.id, document_id=second.id)
    await service.attach_document(session_id=other.id, document_id=first.id)

    for page in (1, 4, 1):
        update = await service.record_viewed_page(
            session_id=session.id, document_id=first.id, page_number=page
        )
    assert update.session.documents[first.id].percent == 50
    for page in (1, 3, 6):
        await service.record_viewed_page(
            session_id=session.id, document_id=second.id, page_number=page
        )
    snapshot = await service.get_reading_progress()
    assert snapshot.sessions[session.id].progress.percent == 50
    assert snapshot.sessions[session.id].progress.viewed_pages == 5
    assert snapshot.sessions[other.id].progress.percent == 0

    for page in (2, 4):
        await service.record_viewed_page(session_id=other.id, document_id=first.id, page_number=page)
    snapshot = await service.get_reading_progress()
    assert snapshot.documents[first.id].viewed_pages == 3
    assert snapshot.documents[first.id].percent == 75
    assert snapshot.sessions[other.id].documents[first.id].percent == 50

    await service.detach_document(session_id=session.id, document_id=first.id)
    assert (await service.get_reading_progress()).sessions[session.id].progress.viewed_pages == 3
    await service.attach_document(session_id=session.id, document_id=first.id)
    assert (await service.get_reading_progress()).sessions[session.id].progress.viewed_pages == 5
    async with context() as db:
        count = await db.scalar(select(func.count()).select_from(StudySessionViewedPageRow))
    assert count == 7

    app = create_app(lambda: service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/reading-progress")
        assert response.status_code == 200
        assert response.json()["documents"][str(first.id)]["percent"] == 75
        response = await client.put(
            f"/api/study-sessions/{session.id}/documents/{first.id}/viewed-pages/1"
        )
        assert response.status_code == 200
        assert response.json()["session"]["progress"]["percent"] == 50


async def test_invalid_page_session_and_unlinked_document_do_not_write(context) -> None:
    service = NotelyService(lambda: SqlAlchemyUnitOfWork(context))
    document = await make_document(service, 4)
    session = await service.create_study_session()
    app = create_app(lambda: service)
    base = f"/api/study-sessions/{session.id}/documents/{document.id}/viewed-pages"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put(f"{base}/1")).status_code == 404
        await service.attach_document(session_id=session.id, document_id=document.id)
        assert (await client.put(f"{base}/0")).status_code == 422
        assert (await client.put(f"{base}/5")).status_code == 422
        assert (await client.put(f"/api/study-sessions/{uuid4()}/documents/{document.id}/viewed-pages/1")).status_code == 404
        assert (await client.put(f"/api/study-sessions/{session.id}/documents/{uuid4()}/viewed-pages/1")).status_code == 404
    async with context() as db:
        assert await db.scalar(select(func.count()).select_from(StudySessionViewedPageRow)) == 0
