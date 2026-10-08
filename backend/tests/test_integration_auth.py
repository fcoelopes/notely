"""Integração do serviço de autenticação: login, MFA, recuperação e bloqueio.

Requer PostgreSQL com as migrations aplicadas, incluindo a `0011`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from notely.core.auth import (
    AuthConfigurationError,
    AuthService,
    InvalidCredentialsError,
    InvalidMfaCodeError,
    InvalidSessionError,
    LockedOutError,
    MfaNotEnrolledError,
    UserExistsError,
)
from notely.core.identity import BOOTSTRAP_USER_ID, MAX_FAILED_ATTEMPTS
from notely.core.models import SessionStage
from notely.core.security import SecretCipher, totp_code
from notely.db.identity import SqlAlchemyIdentityStore

TEST_DATABASE_URL = os.environ.get(
    "NOTELY_TEST_DATABASE_URL",
    "postgresql+asyncpg://notely:notely@localhost:5432/notely_test",
)

PASSWORD = "uma-senha-bem-longa"
START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


class Clock:
    """Relógio controlado: torna os códigos TOTP determinísticos."""

    def __init__(self, start: datetime = START) -> None:
        self.moment = start

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await db.execute(text("select 1 from auth_sessions limit 1"))
            await db.execute(
                text("delete from users where id <> :bootstrap"), {"bootstrap": BOOTSTRAP_USER_ID}
            )
            await db.execute(text("delete from login_attempts"))
            await db.execute(
                text(
                    "update users set password_hash = null, totp_enabled = false, "
                    "totp_secret_encrypted = null, email = 'owner@notely.local', "
                    "normalized_email = 'owner@notely.local' where id = :bootstrap"
                ),
                {"bootstrap": BOOTSTRAP_USER_ID},
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - o motivo é reportado no skip
        await engine.dispose()
        pytest.skip(f"Auth integration database is unavailable: {exc}")
    yield factory
    await engine.dispose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cipher() -> SecretCipher:
    return SecretCipher(SecretCipher.generate_key())


def build(
    sessions: async_sessionmaker[AsyncSession],
    clock: Clock,
    cipher: SecretCipher | None,
) -> AuthService:
    return AuthService(
        lambda: SqlAlchemyIdentityStore(sessions), cipher=cipher, now=clock
    )


async def test_login_without_mfa_creates_an_active_session(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    user = await service.create_user(email="Dono@Exemplo.com", password=PASSWORD)

    outcome = await service.login(email="dono@exemplo.com", password=PASSWORD)

    assert outcome.stage is SessionStage.ACTIVE
    assert outcome.mfa_required is False
    assert outcome.user.id == user.id
    assert outcome.user.email == "Dono@Exemplo.com"
    assert outcome.expires_at > clock.moment

    authenticated = await service.authenticate(token=outcome.token)
    assert authenticated.id == user.id


async def test_unknown_email_and_wrong_password_are_indistinguishable(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    await service.create_user(email="dono@exemplo.com", password=PASSWORD)

    with pytest.raises(InvalidCredentialsError):
        await service.login(email="dono@exemplo.com", password="senha-errada-longa")
    with pytest.raises(InvalidCredentialsError):
        await service.login(email="ninguem@exemplo.com", password=PASSWORD)
    with pytest.raises(InvalidCredentialsError):
        await service.login(email="nao-e-email", password=PASSWORD)


async def test_lockout_blocks_even_a_correct_password_until_the_window_passes(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    await service.create_user(email="dono@exemplo.com", password=PASSWORD)

    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            await service.login(email="dono@exemplo.com", password="senha-errada-longa")

    with pytest.raises(LockedOutError):
        await service.login(email="dono@exemplo.com", password=PASSWORD)

    # Passada a janela, a senha correta volta a funcionar.
    clock.advance(16 * 60)
    outcome = await service.login(email="dono@exemplo.com", password=PASSWORD)
    assert outcome.stage is SessionStage.ACTIVE


async def test_mfa_flow_requires_the_second_factor_before_activating(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    user = await service.create_user(email="dono@exemplo.com", password=PASSWORD)

    enrollment = await service.start_enrollment(user_id=user.id)
    assert enrollment.uri.startswith("otpauth://totp/")
    # Antes da confirmação o MFA não vale.
    stored = next(item for item in await service.list_users() if item.id == user.id)
    assert stored.totp_enabled is False

    codes = await service.confirm_enrollment(
        user_id=user.id, code=totp_code(enrollment.secret, at=clock())
    )
    assert len(codes) == 10

    outcome = await service.login(email="dono@exemplo.com", password=PASSWORD)
    assert outcome.mfa_required is True
    assert outcome.stage is SessionStage.MFA_PENDING

    # A sessão pendente não serve para autenticar.
    with pytest.raises(InvalidSessionError):
        await service.authenticate(token=outcome.token)

    with pytest.raises(InvalidMfaCodeError):
        await service.complete_mfa(token=outcome.token, code="000000")

    active = await service.complete_mfa(
        token=outcome.token, code=totp_code(enrollment.secret, at=clock())
    )
    assert active.stage is SessionStage.ACTIVE
    authenticated = await service.authenticate(token=outcome.token)
    assert authenticated.id == user.id


async def test_recovery_code_activates_once_and_is_then_rejected(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    user = await service.create_user(email="dono@exemplo.com", password=PASSWORD)
    enrollment = await service.start_enrollment(user_id=user.id)
    codes = await service.confirm_enrollment(
        user_id=user.id, code=totp_code(enrollment.secret, at=clock())
    )

    first = await service.login(email="dono@exemplo.com", password=PASSWORD)
    activated = await service.complete_recovery(token=first.token, code=codes[0])
    assert activated.stage is SessionStage.ACTIVE
    assert (await service.authenticate(token=first.token)).id == user.id

    # O mesmo código não serve de novo.
    second = await service.login(email="dono@exemplo.com", password=PASSWORD)
    with pytest.raises(InvalidMfaCodeError):
        await service.complete_recovery(token=second.token, code=codes[0])

    # E o código com separador/caixa diferente é o mesmo código.
    third = await service.login(email="dono@exemplo.com", password=PASSWORD)
    reused_with_noise = await service.complete_recovery(
        token=third.token, code=codes[1].lower().replace("-", " ")
    )
    assert reused_with_noise.stage is SessionStage.ACTIVE


async def test_logout_revokes_the_session(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    await service.create_user(email="dono@exemplo.com", password=PASSWORD)
    outcome = await service.login(email="dono@exemplo.com", password=PASSWORD)

    await service.logout(token=outcome.token)

    with pytest.raises(InvalidSessionError):
        await service.authenticate(token=outcome.token)
    # Repetir o logout não é erro.
    await service.logout(token=outcome.token)


async def test_expired_session_is_rejected(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    await service.create_user(email="dono@exemplo.com", password=PASSWORD)
    outcome = await service.login(email="dono@exemplo.com", password=PASSWORD)

    clock.advance(15 * 24 * 60 * 60)

    with pytest.raises(InvalidSessionError):
        await service.authenticate(token=outcome.token)


async def test_set_credentials_adopts_the_provisional_owner(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)

    owner = await service.set_credentials(email="eu@exemplo.com", password=PASSWORD)

    # O acervo já existente pertence ao dono provisório: ele é reaproveitado, não recriado.
    assert owner.id == BOOTSTRAP_USER_ID
    assert owner.normalized_email == "eu@exemplo.com"
    outcome = await service.login(email="eu@exemplo.com", password=PASSWORD)
    assert outcome.user.id == BOOTSTRAP_USER_ID

    # Repetir o bootstrap atualiza a senha em vez de falhar.
    await service.set_credentials(email="eu@exemplo.com", password="outra-senha-bem-longa")
    await service.login(email="eu@exemplo.com", password="outra-senha-bem-longa")


async def test_duplicate_email_is_rejected(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    await service.create_user(email="dono@exemplo.com", password=PASSWORD)

    with pytest.raises(UserExistsError):
        await service.create_user(email="DONO@exemplo.com", password=PASSWORD)


async def test_mfa_needs_the_server_key(
    sessions: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    service = build(sessions, clock, cipher=None)
    user = await service.create_user(email="dono@exemplo.com", password=PASSWORD)

    with pytest.raises(AuthConfigurationError):
        await service.start_enrollment(user_id=user.id)


async def test_enrollment_must_be_confirmed_with_a_valid_code(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, cipher: SecretCipher
) -> None:
    service = build(sessions, clock, cipher)
    user = await service.create_user(email="dono@exemplo.com", password=PASSWORD)

    with pytest.raises(MfaNotEnrolledError):
        await service.confirm_enrollment(user_id=user.id, code="123456")

    await service.start_enrollment(user_id=user.id)
    with pytest.raises(InvalidMfaCodeError):
        await service.confirm_enrollment(user_id=user.id, code="000000")

    # Desativar exige a senha e remove o segredo e os códigos de recuperação.
    with pytest.raises(InvalidCredentialsError):
        await service.disable_mfa(user_id=user.id, password="senha-errada-longa")
    await service.disable_mfa(user_id=user.id, password=PASSWORD)
    with pytest.raises(MfaNotEnrolledError):
        await service.confirm_enrollment(user_id=user.id, code="123456")
