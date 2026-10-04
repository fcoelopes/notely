"""Contratos de recuperação de fontes; candidatos nunca são conteúdo autoral."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, NAMESPACE_URL, uuid5

from notely.core.models import CurationRequest, CuratedSource

RETRIEVAL_VERSION = "lexical-v1"
STOPWORDS = frozenset({
    "para", "com", "uma", "que", "por", "qual", "quais", "como", "sobre",
    "esta", "este", "isso", "essa", "esse", "dos", "das", "nas", "nos",
    "the", "and", "for", "with", "what", "why", "how", "from", "this",
})


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    id: UUID
    document_id: UUID
    document_title: str
    page_number: int
    page_count: int
    chunk_number: int
    start_offset: int
    end_offset: int
    excerpt: str
    content_sha256: str
    score: float


def candidate_id(document_id: UUID, page_number: int, chunk_number: int, digest: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"notely:corpus:{document_id}:{page_number}:{chunk_number}:{digest}")


def query_terms(question: str, quote: str, context: str = "") -> list[str]:
    terms: list[str] = []
    for value in (question, quote, context):
        for term in re.findall(r"[^\W_]+", value[:2000].lower(), flags=re.UNICODE):
            if 3 <= len(term) <= 64 and term not in STOPWORDS and term not in terms:
                terms.append(term)
                if len(terms) == 24:
                    return terms
    return terms


def rank_candidates(
    candidates: list[SourceCandidate], origin_document_id: UUID, origin_page_number: int
) -> list[SourceCandidate]:
    """Prefere outra página quando ela é praticamente tão relevante quanto a origem."""
    return sorted(candidates, key=lambda item: (
        -(item.score + (0.01 if (item.document_id, item.page_number) !=
                          (origin_document_id, origin_page_number) else 0)),
        str(item.document_id), item.page_number,
    ))


@dataclass(frozen=True, slots=True)
class CuratedSourceView:
    source: CuratedSource
    document_title: str
    available: bool


@dataclass(frozen=True, slots=True)
class CurationView:
    request: CurationRequest
    sources: list[CuratedSourceView]
