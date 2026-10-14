"""Primitivas de autenticação: senha, TOTP (RFC 6238), recuperação e cifra do segredo."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from notely.core.security import (
    SecretCipher,
    SecurityError,
    generate_recovery_codes,
    generate_session_token,
    generate_totp_secret,
    hash_password,
    hash_recovery_code,
    hash_session_token,
    normalize_recovery_code,
    totp_code,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)

# Vetores do RFC 6238, Apêndice B (SHA-1, segredo "12345678901234567890", 8 dígitos).
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode("ascii")
RFC_VECTORS = (
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
)


def test_password_roundtrip_and_rejection() -> None:
    encoded = hash_password("uma-senha-bem-longa")

    assert encoded.startswith("scrypt$")
    assert verify_password("uma-senha-bem-longa", encoded) is True
    assert verify_password("outra-senha-qualquer", encoded) is False
    # O hash não guarda a senha em claro.
    assert "uma-senha-bem-longa" not in encoded


def test_password_uses_a_fresh_salt_and_rejects_weak_or_broken_input() -> None:
    first = hash_password("uma-senha-bem-longa")
    second = hash_password("uma-senha-bem-longa")

    assert first != second
    assert verify_password("uma-senha-bem-longa", first) is True
    assert verify_password("uma-senha-bem-longa", second) is True

    with pytest.raises(SecurityError):
        hash_password("curta")

    # Entrada malformada não derruba a verificação nem aceita a senha.
    assert verify_password("uma-senha-bem-longa", "lixo") is False
    assert verify_password("uma-senha-bem-longa", "bcrypt$1$2$3$4$5") is False
    assert verify_password("uma-senha-bem-longa", "") is False


@pytest.mark.parametrize(("timestamp", "expected"), RFC_VECTORS)
def test_totp_matches_the_rfc_6238_vectors(timestamp: int, expected: str) -> None:
    moment = datetime.fromtimestamp(timestamp, tz=UTC)
    assert totp_code(RFC_SECRET, at=moment, digits=8) == expected


def test_totp_verification_accepts_the_window_and_rejects_other_codes() -> None:
    moment = datetime.fromtimestamp(1111111111, tz=UTC)
    current = totp_code(RFC_SECRET, at=moment)

    assert verify_totp(RFC_SECRET, current, at=moment) is True
    # Janela adjacente tolera desvio de relógio do autenticador.
    assert verify_totp(RFC_SECRET, current, at=moment + timedelta(seconds=29)) is True
    assert verify_totp(RFC_SECRET, current, at=moment - timedelta(seconds=29)) is True
    # Fora da janela, recusa.
    assert verify_totp(RFC_SECRET, current, at=moment + timedelta(seconds=90)) is False
    assert verify_totp(RFC_SECRET, "000000", at=moment) is False
    assert verify_totp(RFC_SECRET, "abcdef", at=moment) is False
    assert verify_totp(RFC_SECRET, "", at=moment) is False


def test_totp_secret_is_base32_and_uri_carries_the_parameters() -> None:
    secret = generate_totp_secret()

    assert len(secret) == 32
    assert totp_code(secret).isdigit()
    uri = totp_provisioning_uri(secret=secret, account="dono@exemplo.com")
    assert uri.startswith("otpauth://totp/")
    assert f"secret={secret}" in uri
    assert "issuer=Notely" in uri
    assert "digits=6" in uri


def test_recovery_codes_are_unique_and_stored_only_as_hash() -> None:
    codes = generate_recovery_codes()

    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(len(code) == 17 and code[8] == "-" for code in codes)

    hashed = hash_recovery_code(codes[0])
    assert hashed == hash_recovery_code(codes[0])
    # O código pode ser digitado com ou sem separador e em qualquer caixa.
    assert hashed == hash_recovery_code(codes[0].lower().replace("-", " "))
    assert normalize_recovery_code(codes[0].lower()) == codes[0].replace("-", "")
    assert len(hashed) == 64
    assert codes[0] not in hashed


def test_session_tokens_are_random_and_hashed_for_storage() -> None:
    first = generate_session_token()
    second = generate_session_token()

    assert first != second
    assert hash_session_token(first) == hash_session_token(first)
    assert hash_session_token(first) != hash_session_token(second)
    assert first not in hash_session_token(first)


def test_secret_cipher_roundtrip_and_wrong_key() -> None:
    key = SecretCipher.generate_key()
    cipher = SecretCipher(key)
    secret = generate_totp_secret()

    token = cipher.encrypt(secret)
    assert secret not in token
    assert cipher.decrypt(token) == secret

    other = SecretCipher(SecretCipher.generate_key())
    with pytest.raises(SecurityError):
        other.decrypt(token)


def test_secret_cipher_rejects_a_malformed_key() -> None:
    with pytest.raises(SecurityError):
        SecretCipher("não-é-uma-chave")
    with pytest.raises(SecurityError):
        SecretCipher("curta-demais")

    # A chave gerada é utilizável.
    assert SecretCipher(SecretCipher.generate_key()) is not None
