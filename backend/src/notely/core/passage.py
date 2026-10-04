from __future__ import annotations

import hashlib
from typing import Any

PASSAGE_ID_VERSION = 1
PASSAGE_NAMESPACE = "notely-passage:v1"
ANCHOR_SEPARATOR = "\n"


def normalize_quote(text: str) -> str:
    """Colapsa espaços para que a identidade do trecho não dependa da renderização."""
    return " ".join(text.split())


def canonical_anchor(
    *,
    document_sha256: str,
    page_number: int,
    quote: str,
    prefix: str | None = None,
    suffix: str | None = None,
) -> str:
    return ANCHOR_SEPARATOR.join(
        (
            PASSAGE_NAMESPACE,
            document_sha256.lower(),
            str(page_number),
            normalize_quote(quote),
            normalize_quote(prefix or ""),
            normalize_quote(suffix or ""),
        )
    )


def passage_id(
    *,
    document_sha256: str,
    page_number: int,
    quote: str,
    prefix: str | None = None,
    suffix: str | None = None,
) -> str:
    """Identidade estável do trecho: determinística e independente de coordenadas visuais."""
    anchor = canonical_anchor(
        document_sha256=document_sha256,
        page_number=page_number,
        quote=quote,
        prefix=prefix,
        suffix=suffix,
    )
    return hashlib.sha256(anchor.encode("utf-8")).hexdigest()


def anchor_from_position(position: dict[str, Any]) -> tuple[str, str, str]:
    """Extrai (exact, prefix, suffix) do seletor textual persistido em position_json."""
    selector = position.get("textQuoteSelector")
    if not isinstance(selector, dict):
        return "", "", ""
    return (
        str(selector.get("exact", "")),
        str(selector.get("prefix", "")),
        str(selector.get("suffix", "")),
    )
