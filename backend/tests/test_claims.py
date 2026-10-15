"""Regras de domínio das afirmações, sem banco: normalização e validação.

As validações aqui reproduzem os `CHECK` da migration `0015`: elas existem para que um estado
inválido falhe no domínio, e não só no PostgreSQL, quando o caminho não passa pelo banco.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from notely.core.claims import normalize_claim_text
from notely.core.models import (
    Claim,
    ClaimRelation,
    ClaimRelationKind,
    RelationProvenance,
    RelationStatus,
    utc_now,
)


def _claim(**overrides: object) -> Claim:
    values: dict[str, object] = {"text": "A materialidade é julgada caso a caso"}
    values.update(overrides)
    return Claim(**values)  # type: ignore[arg-type]


def _relation(**overrides: object) -> ClaimRelation:
    values: dict[str, object] = {
        "relation": ClaimRelationKind.SUPPORTS,
        "annotation_id": uuid4(),
        "claim_id": uuid4(),
    }
    values.update(overrides)
    return ClaimRelation(**values)  # type: ignore[arg-type]


def test_normalization_collapses_spaces_and_case() -> None:
    assert normalize_claim_text("  A   Materialidade\né julgada ") == "a materialidade é julgada"
    # Espaços internos, tabs e quebras de linha colapsam para um único espaço.
    assert normalize_claim_text("um\t dois") == normalize_claim_text("UM  DOIS")
    # Textos diferentes continuam diferentes.
    assert normalize_claim_text("um") != normalize_claim_text("dois")


def test_claim_text_is_normalized_by_the_caller_not_by_the_dataclass() -> None:
    claim = _claim(normalized_text="a materialidade é julgada caso a caso")
    assert claim.normalized_text == normalize_claim_text(claim.text)
    # Texto em branco não é afirmação.
    with pytest.raises(ValueError):
        _claim(text="   ", normalized_text="a")
    with pytest.raises(ValueError):
        _claim(text="algo", normalized_text="  ")


def test_inferred_claim_requires_provider_and_model() -> None:
    claim = _claim(
        normalized_text="a",
        provenance=RelationProvenance.AI_INFERENCE,
        provider="scripted",
        model="scripted-1",
        status=RelationStatus.SUGGESTED,
    )
    assert claim.provider == "scripted"
    with pytest.raises(ValueError):
        _claim(normalized_text="a", provenance=RelationProvenance.AI_INFERENCE)
    with pytest.raises(ValueError):
        _claim(
            normalized_text="a",
            provenance=RelationProvenance.AI_INFERENCE,
            provider="scripted",
        )
    # Afirmação autoral não carrega provedor: não houve modelo no caminho.
    with pytest.raises(ValueError):
        _claim(normalized_text="a", provider="scripted")


def test_accepted_at_tracks_the_status() -> None:
    accepted = _claim(
        normalized_text="a", status=RelationStatus.ACCEPTED, accepted_at=utc_now()
    )
    assert accepted.accepted_at is not None
    with pytest.raises(ValueError):
        _claim(normalized_text="a", status=RelationStatus.ACCEPTED)
    with pytest.raises(ValueError):
        _claim(normalized_text="a", accepted_at=utc_now())


def test_claim_confidence_stays_within_range() -> None:
    assert _claim(normalized_text="a", confidence=0.5).confidence == 0.5
    with pytest.raises(ValueError):
        _claim(normalized_text="a", confidence=1.5)


def test_relation_requires_exactly_two_endpoints() -> None:
    with pytest.raises(ValueError):
        _relation(claim_id=None)
    with pytest.raises(ValueError):
        _relation(question_annotation_id=uuid4())


def test_each_relation_kind_requires_its_own_endpoints() -> None:
    claim_id = uuid4()
    # supports/contradicts: anotação → afirmação.
    for kind in (ClaimRelationKind.SUPPORTS, ClaimRelationKind.CONTRADICTS):
        edge = _relation(relation=kind, annotation_id=uuid4(), claim_id=claim_id)
        assert edge.relation is kind
        with pytest.raises(ValueError):
            _relation(relation=kind, annotation_id=uuid4(), target_concept_id=uuid4())
    # answered_by: dúvida → anotação.
    question, answer = uuid4(), uuid4()
    edge = _relation(
        relation=ClaimRelationKind.ANSWERED_BY,
        annotation_id=None,
        claim_id=None,
        question_annotation_id=question,
        answer_annotation_id=answer,
    )
    assert edge.question_annotation_id == question
    with pytest.raises(ValueError):
        _relation(
            relation=ClaimRelationKind.ANSWERED_BY,
            annotation_id=None,
            claim_id=None,
            question_annotation_id=question,
            answer_annotation_id=question,
        )
    # related_to: conceito ↔ conceito.
    source, target = uuid4(), uuid4()
    edge = _relation(
        relation=ClaimRelationKind.RELATED_TO,
        annotation_id=None,
        claim_id=None,
        source_concept_id=source,
        target_concept_id=target,
    )
    assert edge.source_concept_id == source
    with pytest.raises(ValueError):
        _relation(
            relation=ClaimRelationKind.RELATED_TO,
            annotation_id=None,
            claim_id=None,
            source_concept_id=source,
            target_concept_id=source,
        )


def test_inferred_relation_requires_provider_and_model() -> None:
    with pytest.raises(ValueError):
        _relation(provenance=RelationProvenance.AI_INFERENCE)
    inferred = _relation(
        provenance=RelationProvenance.AI_INFERENCE,
        provider="scripted",
        model="scripted-1",
    )
    assert inferred.status is RelationStatus.SUGGESTED
    assert inferred.accepted_at is None


def test_user_authoring_is_distinguishable_from_user_confirmation() -> None:
    """`user_confirmed` é inferência aceita; `user_authored` é produzida pelo usuário."""
    assert RelationProvenance.USER_AUTHORED.value == "user_authored"
    assert RelationProvenance.USER_CONFIRMED.value == "user_confirmed"
    assert len(set(RelationProvenance)) == 3
