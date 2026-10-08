"""Contrato HTTP da autenticação: gate nas rotas, cookie e tradução de erros.

A sessão real é coberta por `test_integration_auth.py`; aqui o serviço é um duplo, para
verificar o transporte.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from auth_helpers import TEST_USER_ID, test_user
from notely.api.app import create_app
from notely.core.auth import (
    AuthConfigurationError,
    Enrollment,
    InvalidCredentialsError,
    InvalidMfaCodeError,
    InvalidSessionError,
    LockedOutError,
    LoginOutcome,
)
from notely.core.models import SessionStage

FUTURE = datetime.now(UTC) + timedelta(days=1)


class StubAuthService:
    """Duplo do serviço: devolve desfechos fixos ou levanta o erro configurado."""

    def __init__(self, *, error: Exception | None = None, stage: SessionStage = SessionStage.ACTIVE) -> None:
        self.error = error
        self.stage = stage
        self.tokens: list[str] = []

    def _fail(self) -> None:
        if self.error is not None:
            raise self.error

    async def login(self, *, email: str, password: str) -> LoginOutcome:
        self._fail()
        return LoginOutcome(
            token="token-de-teste", user=test_user(), stage=self.stage, expires_at=FUTURE
        )

    async def complete_mfa(self, *, token: str, code: str) -> LoginOutcome:
        self._fail()
        self.tokens.append(token)
        return LoginOutcome(
            token=token, user=test_user(), stage=SessionStage.ACTIVE, expires_at=FUTURE
        )

    async def complete_recovery(self, *, token: str, code: str) -> LoginOutcome:
        return await self.complete_mfa(token=token, code=code)

    async def authenticate(self, *, token: str) -> object:
        if token != "token-de-teste":
            raise InvalidSessionError("session is invalid or expired")
        return test_user()

    async def logout(self, *, token: str) -> None:
        self.tokens.append(token)

    async def start_enrollment(self, *, user_id: UUID) -> Enrollment:
        self._fail()
        return Enrollment(secret="ABCDEFGHIJKLMNOP", uri="otpauth://totp/x?secret=ABCDEFGHIJKLMNOP")

    async def confirm_enrollment(self, *, user_id: UUID, code: str) -> list[str]:
        self._fail()
        return ["AAAA-1111", "BBBB-2222"]

    async def disable_mfa(self, *, user_id: UUID, password: str) -> None:
        self._fail()


class StubService:
    """Serviço de domínio mínimo, para checar apenas o gate."""

    def __init__(self) -> None:
        self.users: list[UUID] = []

    async def list_documents(self) -> list[object]:
        return []


def build_app(auth: StubAuthService) -> object:
    return create_app(service_factory=lambda user_id: StubService(), auth_service=auth)  # type: ignore[arg-type]


def client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]


async def test_protected_route_requires_a_session() -> None:
    async with client(build_app(StubAuthService())) as http:
        response = await http.get("/api/documents")

    assert response.status_code == 401
    assert response.json()["detail"] == "authentication required"


async def test_health_stays_public() -> None:
    async with client(build_app(StubAuthService())) as http:
        response = await http.get("/health")

    assert response.status_code == 200


async def test_login_sets_an_httponly_cookie_and_reports_the_stage() -> None:
    async with client(build_app(StubAuthService())) as http:
        response = await http.post(
            "/api/auth/login", json={"email": "dono@exemplo.com", "password": "uma-senha-longa"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["stage"] == "active"
        assert body["mfa_required"] is False
        assert body["user"]["id"] == str(TEST_USER_ID)
        assert http.cookies.get("notely_session") == "token-de-teste"

    cookie_header = response.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header


async def test_login_with_mfa_enabled_reports_the_pending_stage() -> None:
    async with client(build_app(StubAuthService(stage=SessionStage.MFA_PENDING))) as http:
        response = await http.post(
            "/api/auth/login", json={"email": "dono@exemplo.com", "password": "uma-senha-longa"}
        )

    assert response.status_code == 200
    assert response.json()["mfa_required"] is True
    assert response.json()["stage"] == "mfa_pending"


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (InvalidCredentialsError("invalid credentials"), 401),
        (LockedOutError("too many failed attempts"), 429),
        (InvalidMfaCodeError("invalid second factor code"), 401),
        (AuthConfigurationError("server key missing"), 503),
    ),
)
async def test_login_errors_map_to_status_codes(error: Exception, expected: int) -> None:
    async with client(build_app(StubAuthService(error=error))) as http:
        response = await http.post(
            "/api/auth/login", json={"email": "dono@exemplo.com", "password": "uma-senha-longa"}
        )

    assert response.status_code == expected
    assert response.json()["detail"] == str(error)


async def test_invalid_email_is_rejected_by_the_contract() -> None:
    async with client(build_app(StubAuthService())) as http:
        response = await http.post(
            "/api/auth/login", json={"email": "", "password": "uma-senha-longa"}
        )

    assert response.status_code == 422


async def test_me_requires_a_valid_session_cookie() -> None:
    async with client(build_app(StubAuthService())) as http:
        anonymous = await http.get("/api/auth/me")
        assert anonymous.status_code == 401

        http.cookies.set("notely_session", "token-de-teste")
        identified = await http.get("/api/auth/me")

    assert identified.status_code == 200
    assert identified.json()["email"] == "teste@exemplo.com"

    # Um cookie inválido não autentica.
    async with client(build_app(StubAuthService())) as http:
        http.cookies.set("notely_session", "outro-token")
        rejected = await http.get("/api/auth/me")
    assert rejected.status_code == 401


async def test_second_factor_endpoints_use_the_pending_session() -> None:
    auth = StubAuthService()
    async with client(build_app(auth)) as http:
        http.cookies.set("notely_session", "token-de-teste")
        activated = await http.post("/api/auth/mfa", json={"code": "123456"})
        recovered = await http.post("/api/auth/recovery", json={"code": "AAAA-1111"})

    assert activated.status_code == 200
    assert activated.json()["stage"] == "active"
    assert recovered.status_code == 200
    assert auth.tokens == ["token-de-teste", "token-de-teste"]


async def test_second_factor_without_a_session_is_rejected() -> None:
    async with client(build_app(StubAuthService())) as http:
        response = await http.post("/api/auth/mfa", json={"code": "123456"})

    assert response.status_code == 401


async def test_enrollment_returns_the_secret_and_once_only_recovery_codes() -> None:
    async with client(build_app(StubAuthService())) as http:
        http.cookies.set("notely_session", "token-de-teste")
        enrollment = await http.post("/api/auth/mfa/enroll")
        confirmed = await http.post("/api/auth/mfa/confirm", json={"code": "123456"})

    assert enrollment.status_code == 200
    assert enrollment.json()["uri"].startswith("otpauth://totp/")
    assert confirmed.status_code == 200
    assert confirmed.json()["recovery_codes"] == ["AAAA-1111", "BBBB-2222"]


async def test_logout_revokes_the_session_and_clears_the_cookie() -> None:
    auth = StubAuthService()
    async with client(build_app(auth)) as http:
        http.cookies.set("notely_session", "token-de-teste")
        response = await http.post("/api/auth/logout")

    assert response.status_code == 204
    assert auth.tokens == ["token-de-teste"]
    assert "notely_session=" in response.headers.get("set-cookie", "")


async def test_disabling_mfa_requires_the_password() -> None:
    async with client(build_app(StubAuthService())) as http:
        http.cookies.set("notely_session", "token-de-teste")
        missing = await http.post("/api/auth/mfa/disable", json={})
        provided = await http.post("/api/auth/mfa/disable", json={"password": "uma-senha-longa"})

    assert missing.status_code == 422
    assert provided.status_code == 204
