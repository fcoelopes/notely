"""Projeção derivada no Apache AGE.

O AGE é acessado pela função SQL ``cypher()`` no schema ``ag_catalog``. Os valores
são passados como parâmetros Cypher (``$name``) e o grafo/query são literais
controlados por este módulo — nunca interpolamos dado de domínio em SQL ou Cypher.
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

GRAPH_NAME = "notely"

# O AGE exige ag_catalog no search_path para resolver `cypher()` e `agtype`.
AGE_SEARCH_PATH = "ag_catalog, public"

DOCUMENT_LABEL = "Document"
ANNOTATION_LABEL = "Annotation"
QUESTION_LABEL = "Question"
HAS_ANNOTATION = "HAS_ANNOTATION"
CONCEPT_LABEL = "Concept"
REFERS_TO = "REFERS_TO"
QUESTIONS = "QUESTIONS"
CLAIM_LABEL = "Claim"
SUPPORTS = "SUPPORTS"
CONTRADICTS = "CONTRADICTS"
ANSWERED_BY = "ANSWERED_BY"
RELATED_TO = "RELATED_TO"

# Rótulo do AGE para cada tipo de relação. O domínio fala em caixa baixa; o grafo usa o
# rótulo em caixa alta, como os rótulos já existentes.
CLAIM_EDGE_LABELS = {
    "supports": SUPPORTS,
    "contradicts": CONTRADICTS,
    "answered_by": ANSWERED_BY,
    "related_to": RELATED_TO,
}


class AgeGraph:
    """Adapter para projeções idempotentes no grafo ``notely``."""

    def __init__(self, graph_name: str = GRAPH_NAME) -> None:
        self.graph_name = graph_name

    async def project_document(
        self, *, session: AsyncSession, document_id: UUID, title: str
    ) -> None:
        query = (
            f"MERGE (d:{DOCUMENT_LABEL} {{source_id: $source_id}}) "
            "SET d.source_type = 'document', d.title = $title"
        )
        await self._run(
            session,
            query,
            {"source_id": str(document_id), "title": title},
        )

    async def project_annotation(
        self,
        *,
        session: AsyncSession,
        annotation_id: UUID,
        document_id: UUID,
        annotation_type: str,
        page_number: int,
        document_title: str,
    ) -> None:
        label = QUESTION_LABEL if annotation_type == "question" else ANNOTATION_LABEL
        query = (
            f"MERGE (d:{DOCUMENT_LABEL} {{source_id: $document_id}}) "
            "SET d.source_type = 'document', d.title = $document_title "
            f"MERGE (a:{label} {{source_id: $annotation_id}}) "
            "SET a.source_type = 'annotation', a.document_id = $document_id, "
            "a.annotation_type = $annotation_type, a.page_number = $page_number "
            f"MERGE (d)-[r:{HAS_ANNOTATION}]->(a)"
        )
        await self._run(
            session,
            query,
            {
                "document_id": str(document_id),
                "document_title": document_title,
                "annotation_id": str(annotation_id),
                "annotation_type": annotation_type,
                "page_number": int(page_number),
            },
        )

    async def project_concept(
        self, *, session: AsyncSession, concept_id: UUID, name: str
    ) -> None:
        query = (
            f"MERGE (c:{CONCEPT_LABEL} {{source_id: $source_id}}) "
            "SET c.source_type = 'concept', c.name = $name"
        )
        await self._run(session, query, {"source_id": str(concept_id), "name": name})

    async def project_annotation_concept(
        self,
        *,
        session: AsyncSession,
        annotation_id: UUID,
        annotation_type: str,
        concept_id: UUID,
        concept_name: str,
        relation: str,
        provenance: str,
        status: str,
    ) -> None:
        """Projeta a aresta anotação → conceito, com proveniência e status explícitos.

        O grafo guarda a inferência como inferência: `provenance` e `status` ficam na
        aresta e distinguem o que o modelo sugeriu do que o usuário confirmou.
        """
        annotation_label = (
            QUESTION_LABEL if annotation_type == "question" else ANNOTATION_LABEL
        )
        edge_label = QUESTIONS if relation == "questions" else REFERS_TO
        query = (
            f"MERGE (c:{CONCEPT_LABEL} {{source_id: $concept_id}}) "
            "SET c.source_type = 'concept', c.name = $concept_name "
            f"MERGE (a:{annotation_label} {{source_id: $annotation_id}}) "
            f"MERGE (a)-[r:{edge_label}]->(c) "
            "SET r.provenance = $provenance, r.status = $status"
        )
        await self._run(
            session,
            query,
            {
                "annotation_id": str(annotation_id),
                "concept_id": str(concept_id),
                "concept_name": concept_name,
                "provenance": provenance,
                "status": status,
            },
        )

    async def project_claim(
        self,
        *,
        session: AsyncSession,
        claim_id: UUID,
        text: str,
        provenance: str,
        status: str,
    ) -> None:
        """Projeta o nó `Claim`. `source_id` é a referência ao registro no PostgreSQL.

        O nó carrega `provenance` e `status` como os demais: uma afirmação do usuário nunca
        se confunde com uma sugestão de modelo no grafo.
        """
        query = (
            f"MERGE (c:{CLAIM_LABEL} {{source_id: $source_id}}) "
            "SET c.source_type = 'claim', c.text = $text, "
            "c.provenance = $provenance, c.status = $status"
        )
        await self._run(
            session,
            query,
            {
                "source_id": str(claim_id),
                "text": text,
                "provenance": provenance,
                "status": status,
            },
        )

    async def project_claim_relation(
        self,
        *,
        session: AsyncSession,
        relation: str,
        provenance: str,
        status: str,
        annotation_id: UUID | None = None,
        claim_id: UUID | None = None,
        question_annotation_id: UUID | None = None,
        answer_annotation_id: UUID | None = None,
        source_concept_id: UUID | None = None,
        target_concept_id: UUID | None = None,
    ) -> None:
        """Projeta as quatro arestas semânticas, uma aresta por relação.

        A origem e o destino são resolvidos por `source_id` — o mesmo identificador do
        registro no PostgreSQL —, e a aresta carrega `provenance` e `status`. `MATCH` (e não
        `MERGE`) nas pontas: elas pertencem a outros eventos, e criar um nó incompleto aqui
        seria inventar projeção. Sem elas, nada é escrito e a projeção seguinte reconcilia.

        O `MATCH` da ponta de anotação não fixa o rótulo: a mesma anotação é `Annotation` ou
        `Question` conforme o tipo, e `source_id` é único entre os dois.
        """
        edge_label = CLAIM_EDGE_LABELS[relation]
        if relation in ("supports", "contradicts"):
            source_pattern, source_id = "(s {source_id: $source_id})", annotation_id
            target_pattern, target_id = f"(t:{CLAIM_LABEL} {{source_id: $target_id}})", claim_id
        elif relation == "answered_by":
            source_pattern = f"(s:{QUESTION_LABEL} {{source_id: $source_id}})"
            source_id = question_annotation_id
            target_pattern = "(t {source_id: $target_id})"
            target_id = answer_annotation_id
        else:
            source_pattern = f"(s:{CONCEPT_LABEL} {{source_id: $source_id}})"
            source_id = source_concept_id
            target_pattern = f"(t:{CONCEPT_LABEL} {{source_id: $target_id}})"
            target_id = target_concept_id

        if source_id is None or target_id is None:
            return
        query = (
            f"MATCH {source_pattern} MATCH {target_pattern} "
            f"MERGE (s)-[r:{edge_label}]->(t) "
            "SET r.provenance = $provenance, r.status = $status"
        )
        await self._run(
            session,
            query,
            {
                "source_id": str(source_id),
                "target_id": str(target_id),
                "provenance": provenance,
                "status": status,
            },
        )

    async def remove_claim(self, *, session: AsyncSession, claim_id: UUID) -> None:
        """Remove o nó `Claim` e, por `DETACH DELETE`, as arestas que o citavam."""
        query = f"MATCH (c:{CLAIM_LABEL} {{source_id: $source_id}}) DETACH DELETE c"
        await self._run(session, query, {"source_id": str(claim_id)})

    async def remove_annotation(self, *, session: AsyncSession, annotation_id: UUID) -> None:
        """Remove o nó de anotação (e de dúvida) com as arestas que o citavam.

        O rótulo não é fixado no padrão: a anotação pode ser `Annotation` ou `Question`, e o
        mesmo `source_id` identifica as duas.
        """
        query = f"MATCH (n {{source_id: $source_id}}) DETACH DELETE n"
        await self._run(session, query, {"source_id": str(annotation_id)})

    async def remove_claim_relation(
        self,
        *,
        session: AsyncSession,
        relation: str,
        annotation_id: UUID | None = None,
        claim_id: UUID | None = None,
        question_annotation_id: UUID | None = None,
        answer_annotation_id: UUID | None = None,
        source_concept_id: UUID | None = None,
        target_concept_id: UUID | None = None,
    ) -> None:
        """Remove uma aresta semântica identificada por suas pontas.

        A aresta do AGE não guarda o id do registro de origem — só as pontas —, então é o par
        de `source_id` que a identifica. Os padrões são os mesmos da projeção, para que a
        aresta removida seja exatamente a que foi criada. `DELETE r` sem `DETACH`: os nós
        permanecem.
        """
        edge_label = CLAIM_EDGE_LABELS[relation]
        if relation in ("supports", "contradicts"):
            source_pattern, source_id = "(s {source_id: $source_id})", annotation_id
            target_pattern, target_id = f"(t:{CLAIM_LABEL} {{source_id: $target_id}})", claim_id
        elif relation == "answered_by":
            source_pattern = f"(s:{QUESTION_LABEL} {{source_id: $source_id}})"
            source_id = question_annotation_id
            target_pattern, target_id = "(t {source_id: $target_id})", answer_annotation_id
        else:
            source_pattern = f"(s:{CONCEPT_LABEL} {{source_id: $source_id}})"
            source_id = source_concept_id
            target_pattern = f"(t:{CONCEPT_LABEL} {{source_id: $target_id}})"
            target_id = target_concept_id

        if source_id is None or target_id is None:
            return
        query = (
            f"MATCH {source_pattern} MATCH {target_pattern} "
            f"MATCH (s)-[r:{edge_label}]->(t) DELETE r"
        )
        await self._run(
            session, query, {"source_id": str(source_id), "target_id": str(target_id)}
        )

    async def count_claims(self, *, session: AsyncSession) -> int:
        return await self.count(session=session, label=CLAIM_LABEL)

    async def truncate(self, *, session: AsyncSession) -> None:
        """Remove todos os vértices e arestas do grafo (usado pelo rebuild)."""
        await self._run(session, "MATCH (n) DETACH DELETE n")

    async def count(self, *, session: AsyncSession, label: str | None = None) -> int:
        """Conta vértices (ou vértices de um label) para validação e testes."""
        target = f":{label}" if label else ""
        query = f"MATCH (n{target}) RETURN count(n)"
        value = await self._scalar(session, query)
        return int(value)

    async def count_edges(self, *, session: AsyncSession) -> int:
        query = "MATCH ()-[r]->() RETURN count(r)"
        value = await self._scalar(session, query)
        return int(value)

    async def count_labelled_edges(self, *, session: AsyncSession, label: str) -> int:
        query = f"MATCH ()-[r:{label}]->() RETURN count(r)"
        value = await self._scalar(session, query)
        return int(value)

    async def node_properties(
        self, *, session: AsyncSession, label: str | None = None
    ) -> list[dict[str, object]]:
        """Propriedades dos nós, para validação e testes de projeção.

        O que interessa é o que a projeção escreveu a partir do PostgreSQL, então as chaves
        internas do AGE (`id`, `label`) ficam de fora. O alias do resultado é o da coluna
        SQL: o `cypher()` recebe a query como `cstring` e não conhece `AS` do Cypher.
        """
        target = f":{label}" if label else ""
        rows = await self._run(
            session,
            f"MATCH (n{target}) RETURN properties(n) ORDER BY n.source_id",
            {},
            returning=True,
        )
        return [props for props in (_mapping(row["result"]) for row in rows) if props is not None]

    async def edge_properties(
        self, *, session: AsyncSession, label: str
    ) -> list[dict[str, object]]:
        rows = await self._run(
            session, f"MATCH ()-[r:{label}]->() RETURN properties(r)", {}, returning=True
        )
        return [props for props in (_mapping(row["result"]) for row in rows) if props is not None]

    async def _scalar(self, session: AsyncSession, query: str) -> object:
        rows = await self._run(session, query, {}, returning=True)
        if not rows:
            return 0
        return rows[0]["result"]

    async def _run(
        self,
        session: AsyncSession,
        query: str,
        params: dict[str, object] | None = None,
        *,
        returning: bool = False,
    ) -> list[dict[str, object]]:
        # O AGE exige a query como literal dollar-quoted (o argumento é `cstring`) e os
        # valores como bind parameter. A query é montada apenas com constantes deste
        # módulo; dado de domínio nunca é interpolado, só passa por `:params`.
        await session.execute(text(f"SET search_path = {AGE_SEARCH_PATH}"))
        sql = (
            f"SELECT result FROM cypher('{self.graph_name}', $notely${query}$notely$, :params) "
            "AS (result agtype)"
        )
        result = await session.execute(text(sql), {"params": json.dumps(params or {})})
        if not returning:
            return []
        rows = result.mappings().all()
        decoded: list[dict[str, object]] = []
        for row in rows:
            raw = row["result"]
            decoded.append(
                {"result": _decode_agtype(raw) if isinstance(raw, str) else raw}
            )
        return decoded


def _decode_agtype(raw: str) -> object:
    """agtype é um superset JSON; para escalares/mapas simples basta json.loads."""
    stripped = raw.strip()
    if stripped == "" or stripped == "null":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _mapping(value: object) -> dict[str, object] | None:
    """`properties(...)` devolve um mapa agtype; qualquer outra forma é ignorada."""
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return None
