"""Apoio dos testes de API: app autenticado e unidade de trabalho com dono.

A sessão real é coberta por `test_integration_auth.py`; aqui só interessa que as rotas
existam atrás de um dono fixo, com o mesmo usuário usado para gravar os dados.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notely.api.app import get_current_user
from notely.core.models import User
from notely.db.uow import SqlAlchemyUnitOfWork

TEST_USER_ID = UUID("00000000-0000-0000-0000-0000000a0001")
TEST_USER_EMAIL = "teste@exemplo.com"


async def ensure_test_user(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    """Garante a conta do dono usado nos testes.

    As tabelas de domínio exigem dono existente (FK para `users`), então a conta precisa
    existir antes de qualquer escrita escopada.
    """
    async with sessions() as db:
        await db.execute(
            text(
                "insert into users (id, email, normalized_email, totp_enabled, created_at, updated_at) "
                "values (:id, :email, :email, false, now(), now()) on conflict (id) do nothing"
            ),
            {"id": TEST_USER_ID, "email": TEST_USER_EMAIL},
        )
        await db.commit()
    return TEST_USER_ID


def test_user() -> User:
    return User(
        id=TEST_USER_ID,
        email=TEST_USER_EMAIL,
        normalized_email=TEST_USER_EMAIL,
    )


def owned_uow(
    sessions: async_sessionmaker[AsyncSession], user_id: UUID = TEST_USER_ID
) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(sessions, user_id=user_id)


def authenticate(app: FastAPI, user: User | None = None) -> FastAPI:
    """Sobrescreve a dependência de sessão com um usuário fixo."""
    fixed = user or test_user()
    app.dependency_overrides[get_current_user] = lambda: fixed
    return app
