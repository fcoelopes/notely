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
