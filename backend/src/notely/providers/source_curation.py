"""Avaliação substituível de candidatos de fonte, sem criar referências ao PDF."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from notely.core.curation import SourceCandidate, query_terms


@dataclass(frozen=True, slots=True)
class SourceChoice:
    candidate_id: UUID
    reason: str


class SourceCurationProvider(Protocol):
    name: str
    model: str

    async def evaluate(
        self, *, question: str, quote: str, candidates: list[SourceCandidate]
    ) -> list[SourceChoice]: ...


class LexicalSourceCurationProvider:
    """Busca local explícita; não se apresenta como avaliação feita por IA."""

    name = "lexical_search"
    model = "simple-tsvector-v1"

    async def evaluate(
        self, *, question: str, quote: str, candidates: list[SourceCandidate]
    ) -> list[SourceChoice]:
        terms = query_terms(question, quote)
        selected: list[SourceChoice] = []
        for candidate in candidates:
            content_terms = set(re.findall(r"[^\W_]+", candidate.excerpt.lower(), re.UNICODE))
            matched = [term for term in terms if term in content_terms]
            if len(matched) < 2 and not any(len(term) >= 8 for term in matched):
                continue
            selected.append(SourceChoice(
                candidate_id=candidate.id,
                reason="Termos em comum com a dúvida: " + ", ".join(matched[:4]),
            ))
            if len(selected) == 3:
                break
        return selected
