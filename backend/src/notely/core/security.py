"""Primitivas de autenticação: senha, TOTP, códigos de recuperação e tokens.

Senha e TOTP não trazem dependência nova: `hashlib.scrypt` é um KDF forte da biblioteca
padrão e o TOTP do RFC 6238 é implementado aqui, coberto pelos vetores do próprio RFC. A
única dependência adicional é `cryptography`, usada para cifrar o segredo TOTP em repouso
com Fernet — escrever criptografia própria seria pior.

Nada aqui registra segredo em log. Comparações sensíveis usam `hmac.compare_digest`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_BYTES = 16
SCRYPT_KEY_BYTES = 32
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024

TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
TOTP_SECRET_BYTES = 20
TOTP_ISSUER = "Notely"

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 10

SESSION_TOKEN_BYTES = 32
SESSION_TTL = timedelta(days=14)
MFA_CHALLENGE_TTL = timedelta(minutes=5)


class SecurityError(Exception):
    """Entrada ou configuração inválida para uma operação de segurança."""


def hash_password(password: str) -> str:
    """Deriva a senha com scrypt e devolve os parâmetros junto do hash."""
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise SecurityError(
            f"password must have between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters"
        )
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    derived = _scrypt(password, salt=salt)
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            _b64(salt),
            _b64(derived),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verifica a senha contra o hash gravado, sem vazar tempo por comparação."""
    try:
        scheme, raw_n, raw_r, raw_p, raw_salt, raw_hash = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = _unb64(raw_salt)
        expected = _unb64(raw_hash)
        derived = _scrypt(
            password,
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (ValueError, SecurityError, binascii.Error):
        return False
    return hmac.compare_digest(derived, expected)


def _scrypt(
    password: str,
    *,
    salt: bytes,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
    dklen: int = SCRYPT_KEY_BYTES,
) -> bytes:
    try:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen
        )
    except (ValueError, MemoryError, OverflowError) as exc:  # parâmetros absurdos
        raise SecurityError("unsupported password hashing parameters") from exc


def generate_totp_secret() -> str:
    """Segredo TOTP em base32, formato aceito por aplicativos autenticadores."""
    return base64.b32encode(secrets.token_bytes(TOTP_SECRET_BYTES)).decode("ascii")


def totp_code(
    secret: str,
    *,
    at: datetime | None = None,
    step: int = TOTP_STEP_SECONDS,
    digits: int = TOTP_DIGITS,
) -> str:
    """Código TOTP do RFC 6238 (HMAC-SHA1, truncamento dinâmico)."""
    moment = at or datetime.now(UTC)
    counter = int(moment.timestamp()) // step
    try:
        key = base64.b32decode(secret.upper())
    except (binascii.Error, ValueError) as exc:
        raise SecurityError("totp secret is not valid base32") from exc
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def verify_totp(
    secret: str,
    code: str,
    *,
    at: datetime | None = None,
    step: int = TOTP_STEP_SECONDS,
    digits: int = TOTP_DIGITS,
    window: int = TOTP_WINDOW,
) -> bool:
    """Aceita a janela adjacente para tolerar desvio de relógio do autenticador."""
    candidate = code.strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != digits:
        return False
    moment = at or datetime.now(UTC)
    for offset in range(-window, window + 1):
        expected = totp_code(
            secret,
            at=moment + timedelta(seconds=offset * step),
            step=step,
            digits=digits,
        )
        if hmac.compare_digest(candidate, expected):
            return True
    return False


def totp_provisioning_uri(*, secret: str, account: str, issuer: str = TOTP_ISSUER) -> str:
    """URI `otpauth://` para o aplicativo autenticador montar a entrada."""
    label = f"{issuer}:{account}"
    return (
        f"otpauth://totp/{_quote(label)}?secret={secret}"
        f"&issuer={_quote(issuer)}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP_SECONDS}"
    )


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Códigos de uso único para quando o autenticador não está disponível."""
    codes: list[str] = []
    for _ in range(count):
        raw = base64.b32encode(secrets.token_bytes(RECOVERY_CODE_BYTES)).decode("ascii")
        codes.append(f"{raw[:8]}-{raw[8:16]}")
    return codes


def hash_recovery_code(code: str) -> str:
    """Guarda apenas o hash: o código em claro só existe na resposta de ativação."""
    normalized = normalize_recovery_code(code)
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def normalize_recovery_code(code: str) -> str:
    return code.strip().replace(" ", "").replace("-", "").upper()


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SecretCipher:
    """Cifra o segredo TOTP em repouso com a chave do servidor."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, binascii.Error) as exc:
            raise SecurityError(
                "auth secret key must be a urlsafe base64 32-byte key"
            ) from exc

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, binascii.Error) as exc:
            raise SecurityError("stored secret cannot be decrypted with this key") from exc


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
