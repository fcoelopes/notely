"""Regras de identidade: normalização de e-mail e política de tentativas de login.

O e-mail normalizado é a chave de unicidade; o valor digitado é preservado à parte para
exibição. A política de bloqueio vive aqui para não ficar implícita no transporte.
"""

from __future__ import annotations

import re
from datetime import timedelta

MAX_EMAIL_LENGTH = 254
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

LOCKOUT_WINDOW = timedelta(minutes=15)
MAX_FAILED_ATTEMPTS = 5


class IdentityError(ValueError):
    """E-mail ou operação de identidade inválida."""


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if (
        not normalized
        or len(normalized) > MAX_EMAIL_LENGTH
        or EMAIL_PATTERN.match(normalized) is None
    ):
        raise IdentityError("email is not valid")
    return normalized


def is_locked_out(failed_attempts: int) -> bool:
    """Bloqueio temporário após tentativas falhas seguidas na janela."""
    return failed_attempts >= MAX_FAILED_ATTEMPTS
