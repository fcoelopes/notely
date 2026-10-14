"""Extração de conceitos de uma anotação, atrás de um provider substituível.

O provider devolve conceitos, não relações: quem decide se a aresta é `REFERS_TO` ou
`QUESTIONS` é o domínio, a partir do tipo da anotação. Conceito extraído é inferência de
modelo, nunca dado autoral (AGENTS.md §1).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from notely.providers.chat_model import (
    ChatMessage,
    ChatModelProvider,
    ChatModelUnavailableError,
)

MAX_CONCEPTS = 5
MAX_CONCEPT_LENGTH = 80
MAX_INPUT_CHARACTERS = 1200
MIN_TERM_LENGTH = 5
MAX_CONFIDENCE = 1.0

STOPWORDS = frozenset(
    {
        "ainda", "ambas", "ambos", "apenas", "aplica", "apos", "após", "aquela", "aquele",
        "aquilo", "assim", "cada", "como", "com", "conforme", "das", "depende", "dessa",
        "desse", "desta", "deste", "deve", "deveria", "dos", "entao", "então", "entre",
        "entretanto", "essa", "esse", "esta", "estas", "este", "estes", "existe", "fazer",
        "for", "from", "havia", "into", "isso", "mais", "mas", "mesmo", "nao", "não",
        "onde", "para", "pela", "pelo", "perante", "pode", "podem", "poderia", "por",
        "porque", "portanto", "porém", "possui", "qual", "quando", "que", "representa",
        "segundo", "seja", "sem", "seu", "seus", "sobre", "sua", "suas", "tambem",
        "também", "that", "the", "this", "torna", "trata", "tudo", "uma", "with",
    }
)

TOKEN_PATTERN = re.compile(r"[^\W\d_]+", re.UNICODE)


class ConceptExtractionUnavailableError(Exception):
    """O provider não conseguiu produzir conceitos para esta anotação."""


@dataclass(frozen=True, slots=True)
class ConceptExtractionContext:
    """Contexto mínimo: o que o usuário escreveu e onde escreveu."""

    document_title: str
    annotation_type: str
    quote: str
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedConcept:
    name: str
    confidence: float | None = None
    rationale: str | None = None


class ConceptExtractionProvider(Protocol):
    name: str
    model: str

    async def extract(self, context: ConceptExtractionContext) -> list[ExtractedConcept]: ...


class HeuristicConceptExtractionProvider:
    """Extração determinística, sem rede: padrão, testes e uso offline.

    Não se apresenta como avaliação feita por IA e não produz confiança calibrada.
    """

    name = "heuristic"
    model = "frequency-v1"

    async def extract(self, context: ConceptExtractionContext) -> list[ExtractedConcept]:
        weights: dict[str, int] = {}
        casing: dict[str, str] = {}
        for text, weight in ((context.comment or "", 2), (context.quote, 1)):
            for token in TOKEN_PATTERN.findall(text):
                key = token.lower()
                if len(key) < MIN_TERM_LENGTH or key in STOPWORDS:
                    continue
                weights[key] = weights.get(key, 0) + weight
                casing.setdefault(key, token)
        ranked = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
        return [
            ExtractedConcept(name=casing[key][:MAX_CONCEPT_LENGTH])
            for key, _ in ranked[:MAX_CONCEPTS]
        ]


class ModelConceptExtractionProvider:
    """Extrai conceitos por um modelo de chat configurado pelo usuário (BYOK)."""

    def __init__(self, *, chat: ChatModelProvider) -> None:
        self.chat = chat
        self.name = f"{chat.name}:concepts"
        self.model = chat.model

    async def extract(self, context: ConceptExtractionContext) -> list[ExtractedConcept]:
        try:
            content = await self.chat.complete(
                system=_system_prompt(),
                messages=[ChatMessage(role="user", content=_user_prompt(context))],
                max_tokens=400,
            )
        except ChatModelUnavailableError as exc:
            raise ConceptExtractionUnavailableError("concept provider is unavailable") from exc
        return _parse_concepts(content)


def _system_prompt() -> str:
    return (
        "Você extrai conceitos de anotações de leitura de um estudante. Responda apenas "
        'com um objeto JSON no formato {"concepts": [{"name": "...", "confidence": 0.0}]}. '
        f"Use no máximo {MAX_CONCEPTS} conceitos, cada nome com no máximo "
        f"{MAX_CONCEPT_LENGTH} caracteres. Extraia somente conceitos presentes no texto "
        "fornecido: não invente conteúdo e não repita o título do documento."
    )


def _user_prompt(context: ConceptExtractionContext) -> str:
    lines = [f"Documento: {context.document_title}", f"Tipo da anotação: {context.annotation_type}"]
    lines.append(f"Trecho marcado: {context.quote}")
    if context.comment:
        lines.append(f"Comentário do usuário: {context.comment}")
    return "\n".join(lines)[:MAX_INPUT_CHARACTERS]


def _parse_concepts(content: str) -> list[ExtractedConcept]:
    stripped = content.strip()
    if not stripped:
        return []
    candidate = stripped
    if candidate.startswith("```"):
        candidate = candidate.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ConceptExtractionUnavailableError("concept provider returned invalid JSON") from exc

    items = parsed.get("concepts") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        raise ConceptExtractionUnavailableError("concept provider returned no concept list")

    concepts: list[ExtractedConcept] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            raw_confidence = item.get("confidence")
            rationale = item.get("rationale")
        else:
            name = str(item).strip()
            raw_confidence = None
            rationale = None
        name = " ".join(name.split())[:MAX_CONCEPT_LENGTH]
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        confidence: float | None = None
        if isinstance(raw_confidence, (int, float)):
            confidence = min(max(float(raw_confidence), 0.0), MAX_CONFIDENCE)
        concepts.append(
            ExtractedConcept(
                name=name,
                confidence=confidence,
                rationale=str(rationale).strip() if rationale else None,
            )
        )
        if len(concepts) == MAX_CONCEPTS:
            break
    return concepts
