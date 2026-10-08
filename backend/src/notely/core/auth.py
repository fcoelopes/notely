"""Regras de autenticação: login, segundo fator, sessões e recuperação.

O serviço não conhece HTTP: devolve desfechos e levanta erros de domínio, e quem traduz
para status é a camada de API. Falha de login não revela se o e-mail existe — a
verificação de senha roda mesmo para e-mail desconhecido, para não vazar por tempo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import lru_cache
from uuid import UUID

from notely.core.identity import (
    BOOTSTRAP_USER_ID,
    LOCKOUT_WINDOW,
    IdentityError,
    is_locked_out,
    normalize_email,
)
from notely.core.models import (
    AuthSession,
    SessionStage,
    User,
    utc_now,
)
from notely.core.ports import IdentityStore
from notely.core.security import (
    MFA_CHALLENGE_TTL,
    SESSION_TTL,
    SecretCipher,
    SecurityError,
    generate_recovery_codes,
    generate_session_token,
    generate_totp_secret,
    hash_password,
    hash_recovery_code,
    hash_session_token,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)


class AuthError(Exception):
    """Erro de autenticação; a API traduz para o status adequado."""


class InvalidCredentialsError(AuthError):
    pass


class LockedOutError(AuthError):
    pass


class InvalidSessionError(AuthError):
    pass


class InvalidMfaCodeError(AuthError):
    pass


class MfaNotEnrolledError(AuthError):
    pass


class AuthConfigurationError(AuthError):
    """Falta configuração necessária, como a chave de cifra do segredo TOTP."""


class UserExistsError(AuthError):
    pass


class UserNotFoundError(AuthError):
    pass


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """Resultado de um login. Com MFA ativa, a sessão ainda está pendente do 2º fator."""

    token: str
    user: User
    stage: SessionStage
    expires_at: datetime

    @property
    def mfa_required(self) -> bool:
        return self.stage is SessionStage.MFA_PENDING


@dataclass(frozen=True, slots=True)
class Enrollment:
    secret: str
    uri: str


class AuthService:
    def __init__(
        self,
        store_factory: Callable[[], IdentityStore],
        *,
        cipher: SecretCipher | None = None,
        now: Callable[[], datetime] = utc_now,
        session_ttl: timedelta = SESSION_TTL,
    ) -> None:
        self._store_factory = store_factory
        self._cipher = cipher
        self._now = now
        self._session_ttl = session_ttl

    # -- contas ---------------------------------------------------------------

    async def create_user(self, *, email: str, password: str) -> User:
        clean, normalized = _email_parts(email)
        moment = self._now()
        async with self._store_factory() as store:
            if await store.get_user_by_email(normalized) is not None:
                raise UserExistsError("email is already registered")
            user = User(
                email=clean,
                normalized_email=normalized,
                password_hash=hash_password(password),
                created_at=moment,
                updated_at=moment,
            )
            await store.add_user(user)
            await store.commit()
        return user

    async def set_credentials(self, *, email: str, password: str) -> User:
        """Cria ou atualiza uma conta com a senha informada (bootstrap do dono).

        Reaproveita o dono provisório criado pela migration quando ele ainda não tem senha,
        para que o acervo já existente continue sendo dele.
        """
        clean, normalized = _email_parts(email)
        moment = self._now()
        async with self._store_factory() as store:
            existing = await store.get_user_by_email(normalized)
            if existing is not None:
                updated = replace(
                    existing, password_hash=hash_password(password), updated_at=moment
                )
                await store.update_user(updated)
                await store.commit()
                return updated

            provisional = await store.get_user(BOOTSTRAP_USER_ID)
            if provisional is not None and provisional.password_hash is None:
                adopted = replace(
                    provisional,
                    email=clean,
                    normalized_email=normalized,
                    password_hash=hash_password(password),
                    updated_at=moment,
                )
                await store.update_user(adopted)
                await store.commit()
                return adopted

            created = User(
                email=clean,
                normalized_email=normalized,
                password_hash=hash_password(password),
                created_at=moment,
                updated_at=moment,
            )
            await store.add_user(created)
            await store.commit()
        return created

    async def list_users(self) -> list[User]:
        async with self._store_factory() as store:
            return await store.list_users()

    async def set_password(self, *, email: str, password: str) -> User:
        """Troca a senha de uma conta existente (sem exigir a senha anterior)."""
        normalized = normalize_email(email)
        moment = self._now()
        async with self._store_factory() as store:
            user = await store.get_user_by_email(normalized)
            if user is None:
                raise UserNotFoundError("no account uses this email")
            updated = replace(
                user, password_hash=hash_password(password), updated_at=moment
            )
            await store.update_user(updated)
            await store.commit()
        return updated

    # -- sessão ---------------------------------------------------------------

    async def login(self, *, email: str, password: str) -> LoginOutcome:
        try:
            normalized = normalize_email(email)
        except IdentityError:
            # E-mail inválido não revela nada além de credencial inválida.
            _reject_timing(password)
            raise InvalidCredentialsError("invalid credentials") from None

        moment = self._now()
        async with self._store_factory() as store:
            failures = await store.count_recent_failures(
                normalized_email=normalized, since=moment - LOCKOUT_WINDOW
            )
            if is_locked_out(failures):
                await store.record_login_attempt(
                    normalized_email=normalized, succeeded=False, at=moment
                )
                await store.commit()
                raise LockedOutError("too many failed attempts; try again later")

            user = await store.get_user_by_email(normalized)
            usable = user if user is not None and user.password_hash else None
            matched = (
                verify_password(password, usable.password_hash)
                if usable is not None and usable.password_hash is not None
                else _reject_timing(password)
            )
            if usable is None or not matched:
                await store.record_login_attempt(
                    normalized_email=normalized, succeeded=False, at=moment
                )
                await store.commit()
                raise InvalidCredentialsError("invalid credentials")

            await store.record_login_attempt(
                normalized_email=normalized, succeeded=True, at=moment
            )
            stage = (
                SessionStage.MFA_PENDING if usable.totp_enabled else SessionStage.ACTIVE
            )
            ttl = MFA_CHALLENGE_TTL if stage is SessionStage.MFA_PENDING else self._session_ttl
            token = generate_session_token()
            session = AuthSession(
                user_id=usable.id,
                token_hash=hash_session_token(token),
                stage=stage,
                expires_at=moment + ttl,
                created_at=moment,
            )
            await store.add_session(session)
            await store.commit()
        return LoginOutcome(
            token=token, user=usable, stage=stage, expires_at=session.expires_at
        )

    async def complete_mfa(self, *, token: str, code: str) -> LoginOutcome:
        moment = self._now()
        async with self._store_factory() as store:
            session = await self._pending_session(store, token=token, moment=moment)
            user = await store.get_user(session.user_id)
            if user is None or not user.totp_enabled or not user.totp_secret_encrypted:
                raise MfaNotEnrolledError("no second factor is enrolled for this account")
            secret = self._decrypt(user.totp_secret_encrypted)
            if not verify_totp(secret, code, at=moment):
                raise InvalidMfaCodeError("invalid second factor code")
            active = await self._activate(store, session=session, moment=moment)
            await store.commit()
        return LoginOutcome(
            token=token, user=user, stage=SessionStage.ACTIVE, expires_at=active.expires_at
        )

    async def complete_recovery(self, *, token: str, code: str) -> LoginOutcome:
        """Conclui o login com um código de recuperação, consumido no primeiro uso."""
        moment = self._now()
        async with self._store_factory() as store:
            session = await self._pending_session(store, token=token, moment=moment)
            consumed = await store.consume_recovery_code(
                user_id=session.user_id,
                code_hash=hash_recovery_code(code),
                used_at=moment,
            )
            if not consumed:
                raise InvalidMfaCodeError("invalid recovery code")
            user = await store.get_user(session.user_id)
            if user is None:
                raise InvalidSessionError("session user is unavailable")
            active = await self._activate(store, session=session, moment=moment)
            await store.commit()
        return LoginOutcome(
            token=token, user=user, stage=SessionStage.ACTIVE, expires_at=active.expires_at
        )

    async def authenticate(self, *, token: str) -> User:
        moment = self._now()
        async with self._store_factory() as store:
            session = await store.get_session(hash_session_token(token))
            if session is None or not session.is_valid_at(moment):
                raise InvalidSessionError("session is invalid or expired")
            user = await store.get_user(session.user_id)
            if user is None:
                raise InvalidSessionError("session user is unavailable")
            return user

    async def logout(self, *, token: str) -> None:
        moment = self._now()
        async with self._store_factory() as store:
            session = await store.get_session(hash_session_token(token))
            if session is None or session.revoked_at is not None:
                return
            await store.update_session(replace(session, revoked_at=moment))
            await store.commit()

    # -- segundo fator --------------------------------------------------------

    async def start_enrollment(self, *, user_id: UUID) -> Enrollment:
        """Gera o segredo. O MFA só passa a valer depois da confirmação com um código."""
        moment = self._now()
        secret = generate_totp_secret()
        encrypted = self._cipher_required().encrypt(secret)
        async with self._store_factory() as store:
            user = await store.get_user(user_id)
            if user is None:
                raise InvalidSessionError("user is unavailable")
            await store.update_user(
                replace(
                    user,
                    totp_secret_encrypted=encrypted,
                    totp_enabled=False,
                    updated_at=moment,
                )
            )
            await store.commit()
        return Enrollment(
            secret=secret, uri=totp_provisioning_uri(secret=secret, account=user.email)
        )

    async def confirm_enrollment(self, *, user_id: UUID, code: str) -> list[str]:
        """Confirma o segundo fator e devolve os códigos de recuperação, exibidos uma vez."""
        moment = self._now()
        async with self._store_factory() as store:
            user = await store.get_user(user_id)
            if user is None:
                raise InvalidSessionError("user is unavailable")
            if not user.totp_secret_encrypted:
                raise MfaNotEnrolledError("start the enrollment first")
            secret = self._decrypt(user.totp_secret_encrypted)
            if not verify_totp(secret, code, at=moment):
                raise InvalidMfaCodeError("invalid second factor code")

            codes = generate_recovery_codes()
            await store.replace_recovery_codes(
                user_id=user_id,
                code_hashes=[hash_recovery_code(code_item) for code_item in codes],
                created_at=moment,
            )
            await store.update_user(
                replace(user, totp_enabled=True, updated_at=moment)
            )
            await store.commit()
        return codes

    async def disable_mfa(self, *, user_id: UUID) -> None:
        moment = self._now()
        async with self._store_factory() as store:
            user = await store.get_user(user_id)
            if user is None:
                raise InvalidSessionError("user is unavailable")
            await store.update_user(
                replace(
                    user,
                    totp_enabled=False,
                    totp_secret_encrypted=None,
                    updated_at=moment,
                )
            )
            await store.replace_recovery_codes(
                user_id=user_id, code_hashes=[], created_at=moment
            )
            await store.commit()

    # -- internos -------------------------------------------------------------

    async def _pending_session(
        self, store: IdentityStore, *, token: str, moment: datetime
    ) -> AuthSession:
        session = await store.get_session(hash_session_token(token))
        if (
            session is None
            or session.stage is not SessionStage.MFA_PENDING
            or session.revoked_at is not None
            or session.expires_at <= moment
        ):
            raise InvalidSessionError("session is not awaiting a second factor")
        return session

    async def _activate(
        self, store: IdentityStore, *, session: AuthSession, moment: datetime
    ) -> AuthSession:
        active = replace(
            session, stage=SessionStage.ACTIVE, expires_at=moment + self._session_ttl
        )
        await store.update_session(active)
        return active

    def _cipher_required(self) -> SecretCipher:
        if self._cipher is None:
            raise AuthConfigurationError(
                "NOTELY_AUTH_SECRET_KEY is required to store the second factor secret"
            )
        return self._cipher

    def _decrypt(self, token: str) -> str:
        try:
            return self._cipher_required().decrypt(token)
        except SecurityError as exc:
            raise AuthConfigurationError(str(exc)) from exc


def _email_parts(email: str) -> tuple[str, str]:
    return email.strip(), normalize_email(email)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Hash descartável: mantém o custo da verificação igual para e-mail inexistente."""
    return hash_password("notely-placeholder-password")


def _reject_timing(password: str) -> bool:
    verify_password(password, _dummy_hash())
    return False
