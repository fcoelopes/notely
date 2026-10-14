"""Persistência de identidade: contas, sessões, recuperação e tentativas de login.

O token em claro nunca é gravado; só o hash. O segredo TOTP é guardado já cifrado.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notely.core.models import AuthSession, SessionStage, User
from notely.db.models import AuthSessionRow, LoginAttemptRow, RecoveryCodeRow, UserRow


class SqlAlchemyIdentityStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyIdentityStore:
        self.session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    def _active(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("identity store must be used as an async context manager")
        return self.session

    async def get_user(self, user_id: UUID) -> User | None:
        row = await self._active().get(UserRow, user_id)
        return _to_user(row) if row is not None and row.deleted_at is None else None

    async def get_user_by_email(self, normalized_email: str) -> User | None:
        row = await self._active().scalar(
            select(UserRow).where(UserRow.normalized_email == normalized_email)
        )
        return _to_user(row) if row is not None and row.deleted_at is None else None

    async def list_users(self) -> list[User]:
        rows = (
            await self._active().scalars(select(UserRow).order_by(UserRow.created_at, UserRow.id))
        ).all()
        return [_to_user(row) for row in rows]

    async def add_user(self, user: User) -> None:
        self._active().add(_user_row(user))

    async def update_user(self, user: User) -> None:
        row = await self._active().get(UserRow, user.id)
        if row is None:
            raise LookupError("user does not exist")
        row.email = user.email
        row.normalized_email = user.normalized_email
        row.password_hash = user.password_hash
        row.totp_secret_encrypted = user.totp_secret_encrypted
        row.totp_enabled = user.totp_enabled
        row.updated_at = user.updated_at
        row.deleted_at = user.deleted_at

    async def add_session(self, session: AuthSession) -> None:
        self._active().add(
            AuthSessionRow(
                id=session.id,
                user_id=session.user_id,
                token_hash=session.token_hash,
                stage=session.stage.value,
                expires_at=session.expires_at,
                created_at=session.created_at,
                revoked_at=session.revoked_at,
            )
        )

    async def get_session(self, token_hash: str) -> AuthSession | None:
        row = await self._active().scalar(
            select(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash)
        )
        return _to_session(row) if row is not None else None

    async def update_session(self, session: AuthSession) -> None:
        row = await self._active().get(AuthSessionRow, session.id)
        if row is None:
            raise LookupError("session does not exist")
        row.stage = session.stage.value
        row.expires_at = session.expires_at
        row.revoked_at = session.revoked_at

    async def replace_recovery_codes(
        self, *, user_id: UUID, code_hashes: list[str], created_at: datetime
    ) -> None:
        session = self._active()
        await session.execute(delete(RecoveryCodeRow).where(RecoveryCodeRow.user_id == user_id))
        for code_hash in code_hashes:
            session.add(
                RecoveryCodeRow(
                    id=uuid4(),
                    user_id=user_id,
                    code_hash=code_hash,
                    created_at=created_at,
                    used_at=None,
                )
            )

    async def consume_recovery_code(
        self, *, user_id: UUID, code_hash: str, used_at: datetime
    ) -> bool:
        """Marca o código como usado. O `WHERE used_at IS NULL` garante uso único."""
        result = await self._active().execute(
            update(RecoveryCodeRow)
            .where(
                RecoveryCodeRow.user_id == user_id,
                RecoveryCodeRow.code_hash == code_hash,
                RecoveryCodeRow.used_at.is_(None),
            )
            .values(used_at=used_at)
            .returning(RecoveryCodeRow.id)
        )
        return result.scalar_one_or_none() is not None

    async def record_login_attempt(
        self, *, normalized_email: str, succeeded: bool, at: datetime
    ) -> None:
        self._active().add(
            LoginAttemptRow(
                id=uuid4(),
                normalized_email=normalized_email,
                succeeded=succeeded,
                attempted_at=at,
            )
        )

    async def count_recent_failures(self, *, normalized_email: str, since: datetime) -> int:
        count = await self._active().scalar(
            select(func.count())
            .select_from(LoginAttemptRow)
            .where(
                LoginAttemptRow.normalized_email == normalized_email,
                LoginAttemptRow.succeeded.is_(False),
                LoginAttemptRow.attempted_at >= since,
            )
        )
        return int(count or 0)

    async def commit(self) -> None:
        await self._active().commit()


def _user_row(user: User) -> UserRow:
    return UserRow(
        id=user.id,
        email=user.email,
        normalized_email=user.normalized_email,
        password_hash=user.password_hash,
        totp_secret_encrypted=user.totp_secret_encrypted,
        totp_enabled=user.totp_enabled,
        created_at=user.created_at,
        updated_at=user.updated_at,
        deleted_at=user.deleted_at,
    )


def _to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        normalized_email=row.normalized_email,
        password_hash=row.password_hash,
        totp_secret_encrypted=row.totp_secret_encrypted,
        totp_enabled=row.totp_enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _to_session(row: AuthSessionRow) -> AuthSession:
    return AuthSession(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        stage=SessionStage(row.stage),
        expires_at=row.expires_at,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )
