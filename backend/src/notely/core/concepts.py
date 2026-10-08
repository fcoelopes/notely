"""Regras de domínio para conceitos extraídos de anotações.

A normalização é a chave de deduplicação e a decisão de qual aresta usar é do domínio,
não do provider: o modelo devolve conceitos, não relações.
"""

from __future__ import annotations

from notely.core.models import AnnotationType, ConceptRelation

MAX_CONCEPT_LENGTH = 80


def normalize_concept_name(name: str) -> str:
    """Chave de deduplicação: caixa baixa, espaços normalizados e limite de tamanho."""
    return " ".join(name.split()).casefold()[:MAX_CONCEPT_LENGTH]


def relation_for(annotation_type: AnnotationType) -> ConceptRelation:
    """Dúvida liga-se ao conceito por `questions`; os demais tipos por `refers_to`."""
    if annotation_type is AnnotationType.QUESTION:
        return ConceptRelation.QUESTIONS
    return ConceptRelation.REFERS_TO
