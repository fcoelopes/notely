"""Regras de domínio para afirmações do leitor.

A normalização é a chave de reaproveitamento: afirmar o mesmo texto duas vezes não duplica
conhecimento, apenas acrescenta evidência. Ela mora no domínio, como a de conceitos, e não
na API nem no adapter — é a mesma chave usada pelo índice único parcial de `claims`.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from notely.core.models import Claim, ClaimRelation


def normalize_claim_text(text: str) -> str:
    """Chave de reaproveitamento: caixa baixa e espaços colapsados.

    Espelha `normalize_concept_name`, sem limite de tamanho: uma afirmação é uma proposição
    escrita pelo usuário, e truncá-la mudaria o que ele afirmou. O texto exibido continua
    sendo o original; só a chave de comparação é normalizada.
    """
    return " ".join(text.split()).casefold()


@dataclass(frozen=True, slots=True)
class ClaimEvidence:
    """Afirmação com a evidência que a sustenta.

    É o que responde "o que eu afirmo, e onde está a evidência?": a aresta carrega a relação
    e sua proveniência, e o trecho é montado a partir da anotação que a origem aponta. O
    trecho fica vazio quando a anotação de origem não existe mais no acervo — o dado autoral
    nunca é escondido por causa disso.
    """

    claim: Claim
    relation: ClaimRelation
    quote: str = ""
    comment: str | None = None
    document_id: UUID | None = None
    document_title: str | None = None
    page_number: int | None = None
