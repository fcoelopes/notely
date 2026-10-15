"""Regras de domínio para conceitos extraídos de anotações.

A normalização é a chave de deduplicação e a decisão de qual aresta usar é do domínio,
não do provider: o modelo devolve conceitos, não relações.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from notely.core.models import (
    AnnotationType,
    ClaimRelation,
    ClaimRelationKind,
    Concept,
    ConceptRelation,
)

MAX_CONCEPT_LENGTH = 80


def normalize_concept_name(name: str) -> str:
    """Chave de deduplicação: caixa baixa, espaços normalizados e limite de tamanho."""
    return " ".join(name.split()).casefold()[:MAX_CONCEPT_LENGTH]


def relation_for(annotation_type: AnnotationType) -> ConceptRelation:
    """Dúvida liga-se ao conceito por `questions`; os demais tipos por `refers_to`."""
    if annotation_type is AnnotationType.QUESTION:
        return ConceptRelation.QUESTIONS
    return ConceptRelation.REFERS_TO


@dataclass(frozen=True, slots=True)
class ConceptReference:
    """Documento cujo acervo cita o conceito, e quantos trechos o citam ali.

    A contagem por documento é o que deixa a lista utilizável durante a leitura: sem ela, o
    que aparece é uma nuvem de termos sem relação com a sessão aberta, e a tela não consegue
    filtrar pelo que a pessoa está lendo.
    """

    document_id: UUID
    document_title: str
    annotation_count: int


@dataclass(frozen=True, slots=True)
class ConceptAssociation:
    """Associação `RELATED_TO` vista da ponta que está sendo listada.

    O par é ordenado no banco: `outgoing` diz se o conceito listado é a origem da aresta, e
    afirmar a associação na ordem inversa cria **outra** aresta. O nome do outro conceito vem
    resolvido para que a tela não precise de uma segunda leitura para dizer com quem o
    conceito está associado.
    """

    relation: ClaimRelation
    other_concept_id: UUID
    other_concept_name: str
    outgoing: bool

    def __post_init__(self) -> None:
        if self.relation.relation is not ClaimRelationKind.RELATED_TO:
            raise ValueError("a concept association is a related_to edge")
        if (
            self.relation.source_concept_id is None
            or self.relation.target_concept_id is None
        ):
            raise ValueError("a concept association needs both concepts")


@dataclass(frozen=True, slots=True)
class ConceptOverview:
    """Conceito do usuário com o que a tela precisa para associá-lo com critério.

    O conceito é inferência de modelo ou heurística, nunca conhecimento confirmado: esta
    leitura não promove nada a autoral. O que é autoral é a associação que a pessoa cria, e é
    ela que aparece em `associations`.
    """

    concept: Concept
    annotation_count: int = 0
    documents: tuple[ConceptReference, ...] = field(default_factory=tuple)
    associations: tuple[ConceptAssociation, ...] = field(default_factory=tuple)
