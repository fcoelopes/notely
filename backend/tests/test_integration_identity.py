"""Integração da fundação de identidade: schema, dono e unicidade por usuário.

Requer PostgreSQL com as migrations aplicadas, incluindo a `0011`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

BOOTSTRAP_USER = UUID("00000000-0000-0000-0000-000000000001")

# Tabelas de domínio que passaram a ter dono.
OWNED_TABLES = (
    "documents",
    "annotations",
    "reading_sessions",
    "study_sessions",
    "study_session_documents",
    "study_session_viewed_pages",
    "ai_suggestions",
    "question_curation_requests",
    "question_curated_sources",
    "concepts",
    "outbox_events",
)

# Projeções derivadas: o dono é resolvido pelo registro de origem.
DERIVED_TABLES = (
    "document_corpus_index",
    "document_corpus_pages",
    "document_corpus_chunks",
    "reader_events",
    "annotation_concepts",
)


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            found = await connection.scalar(text("select count(*) from users"))
            if not found:
                raise RuntimeError("bootstrap user not found")
        async with factory() as db:
            await db.execute(
                text("delete from users where id <> :bootstrap"), {"bootstrap": BOOTSTRAP_USER}
            )
            await db.execute(text("delete from concepts"))
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"Identity integration database is unavailable: {exc}")
    yield factory
    await engine.dispose()


async def test_bootstrap_user_owns_the_existing_data_without_a_password(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as db:
        row = (
            await db.execute(
                text(
                    "select email, normalized_email, password_hash, totp_enabled "
                    "from users where id = :id"
                ),
                {"id": BOOTSTRAP_USER},
            )
        ).one()
    assert row.normalized_email == "owner@notely.local"
    # Ninguém entra com o dono provisório até o bootstrap definir a senha.
    assert row.password_hash is None
    assert row.totp_enabled is False


async def test_owned_tables_require_an_owner_and_derived_tables_do_not(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as db:
        owned = (
            await db.execute(
                text(
                    "select table_name, is_nullable from information_schema.columns "
                    "where table_schema = 'public' and column_name = 'user_id'"
                )
            )
        ).all()
    nullable = {row.table_name: row.is_nullable for row in owned}

    for table in OWNED_TABLES:
        # As tabelas de identidade também têm user_id, mas não são de domínio.
        assert table in nullable, f"{table} should own its rows"
        assert nullable[table] == "NO", f"{table}.user_id should be required"
    for table in DERIVED_TABLES:
        assert table not in nullable, f"{table} should resolve its owner from the origin"


async def test_writes_without_an_explicit_owner_fall_back_to_the_bootstrap_user(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """O default é transitório: mantém a aplicação funcionando durante a migração."""
    document_id = uuid4()
    async with sessions() as db:
        await db.execute(
            text(
                "insert into documents (id, sha256, title, filename, page_count, "
                "storage_uri, created_at, updated_at) values "
                "(:id, :sha, 'Sem dono explícito', 'x.pdf', 1, 'test://pdf', now(), now())"
            ),
            {"id": document_id, "sha": uuid4().hex + uuid4().hex},
        )
        await db.commit()
        owner = await db.scalar(
            text("select user_id from documents where id = :id"), {"id": document_id}
        )
        await db.execute(text("delete from documents where id = :id"), {"id": document_id})
        await db.commit()
    assert owner == BOOTSTRAP_USER


async def test_email_is_unique_when_normalized(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as db:
        await db.execute(
            text(
                "insert into users (id, email, normalized_email, totp_enabled, created_at, updated_at) "
                "values (:id, 'Dono@Exemplo.com', 'dono@exemplo.com', false, now(), now())"
            ),
            {"id": uuid4()},
        )
        await db.commit()
        with pytest.raises(Exception) as conflict:
            await db.execute(
                text(
                    "insert into users (id, email, normalized_email, totp_enabled, created_at, updated_at) "
                    "values (:id, 'outro@exemplo.com', 'dono@exemplo.com', false, now(), now())"
                ),
                {"id": uuid4()},
            )
        assert "normalized_email" in str(conflict.value)
        await db.rollback()


async def test_mfa_requires_a_stored_secret(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as db:
        with pytest.raises(Exception) as invalid:
            await db.execute(
                text(
                    "insert into users (id, email, normalized_email, totp_enabled, created_at, updated_at) "
                    "values (:id, 'mfa@exemplo.com', 'mfa@exemplo.com', true, now(), now())"
                ),
                {"id": uuid4()},
            )
        assert "totp_enabled" in str(invalid.value)
        await db.rollback()


async def test_the_same_concept_name_can_belong_to_two_users(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    other = uuid4()
    async with sessions() as db:
        await db.execute(
            text(
                "insert into users (id, email, normalized_email, totp_enabled, created_at, updated_at) "
                "values (:id, 'outro@exemplo.com', 'outro@exemplo.com', false, now(), now())"
            ),
            {"id": other},
        )
        for owner in (BOOTSTRAP_USER, other):
            await db.execute(
                text(
                    "insert into concepts (id, name, normalized_name, created_at, user_id) "
                    "values (:id, 'Materialidade', 'materialidade', now(), :owner)"
                ),
                {"id": uuid4(), "owner": owner},
            )
        await db.commit()
        total = await db.scalar(
            text("select count(*) from concepts where normalized_name = 'materialidade'")
        )
    assert total == 2


async def test_session_and_recovery_tables_follow_the_user(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    user_id = uuid4()
    async with sessions() as db:
        await db.execute(
            text(
                "insert into users (id, email, normalized_email, totp_enabled, created_at, updated_at) "
                "values (:id, 'sessao@exemplo.com', 'sessao@exemplo.com', false, now(), now())"
            ),
            {"id": user_id},
        )
        await db.execute(
            text(
                "insert into auth_sessions (id, user_id, token_hash, stage, expires_at, created_at) "
                "values (:id, :user, :token, 'active', now() + interval '1 day', now())"
            ),
            {"id": uuid4(), "user": user_id, "token": uuid4().hex},
        )
        await db.execute(
            text(
                "insert into recovery_codes (id, user_id, code_hash, created_at) "
                "values (:id, :user, :code, now())"
            ),
            {"id": uuid4(), "user": user_id, "code": uuid4().hex},
        )
        await db.commit()

        # Apagar o usuário remove sessões e códigos de recuperação.
        await db.execute(text("delete from users where id = :id"), {"id": user_id})
        await db.commit()
        remaining = await db.scalar(
            text(
                "select (select count(*) from auth_sessions where user_id = :id) "
                "+ (select count(*) from recovery_codes where user_id = :id)"
            ),
            {"id": user_id},
        )
    assert remaining == 0
