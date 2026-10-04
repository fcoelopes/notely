from __future__ import annotations

import hashlib

from notely.core.passage import (
    PASSAGE_ID_VERSION,
    PASSAGE_NAMESPACE,
    anchor_from_position,
    canonical_anchor,
    normalize_quote,
    passage_id,
)

DOCUMENT_SHA = "9f2b" * 16


def test_passage_id_is_deterministic_for_the_same_anchor() -> None:
    first = passage_id(document_sha256=DOCUMENT_SHA, page_number=12, quote="Materialidade")
    second = passage_id(document_sha256=DOCUMENT_SHA, page_number=12, quote="Materialidade")

    assert first == second
    assert len(first) == 64


def test_passage_id_ignores_visual_coordinates_and_whitespace_noise() -> None:
    plain = passage_id(
        document_sha256=DOCUMENT_SHA,
        page_number=3,
        quote="A entidade deve apresentar   as demonstrações",
    )
    reflowed = passage_id(
        document_sha256=DOCUMENT_SHA,
        page_number=3,
        quote="A entidade deve apresentar\n as demonstrações",
    )

    # position_json muda com zoom e viewport; a identidade do trecho não.
    assert plain == reflowed


def test_passage_id_changes_with_page_document_or_quote() -> None:
    base = passage_id(document_sha256=DOCUMENT_SHA, page_number=1, quote="Continuidade")

    assert base != passage_id(document_sha256=DOCUMENT_SHA, page_number=2, quote="Continuidade")
    assert base != passage_id(document_sha256="a" * 64, page_number=1, quote="Continuidade")
    assert base != passage_id(document_sha256=DOCUMENT_SHA, page_number=1, quote="Materialidade")


def test_anchor_includes_prefix_and_suffix_as_part_of_identity() -> None:
    without_context = passage_id(
        document_sha256=DOCUMENT_SHA, page_number=5, quote="entidade"
    )
    with_context = passage_id(
        document_sha256=DOCUMENT_SHA,
        page_number=5,
        quote="entidade",
        prefix="A",
        suffix="deve",
    )

    assert without_context != with_context


def test_canonical_anchor_is_versioned_and_stable() -> None:
    anchor = canonical_anchor(
        document_sha256=DOCUMENT_SHA.upper(),
        page_number=2,
        quote="  Materialidade   e  continuidade ",
        prefix=None,
        suffix=None,
    )

    assert anchor == "\n".join(
        [PASSAGE_NAMESPACE, DOCUMENT_SHA, "2", "Materialidade e continuidade", "", ""]
    )
    assert PASSAGE_ID_VERSION == 1
    assert hashlib.sha256(anchor.encode("utf-8")).hexdigest() == passage_id(
        document_sha256=DOCUMENT_SHA,
        page_number=2,
        quote="Materialidade e continuidade",
    )


def test_normalize_quote_collapses_whitespace() -> None:
    assert normalize_quote("  a \n b\t c  ") == "a b c"


def test_anchor_from_position_reads_the_text_quote_selector() -> None:
    position = {
        "rects": [{"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}],
        "textQuoteSelector": {"exact": "trecho", "prefix": "antes ", "suffix": " depois"},
    }

    assert anchor_from_position(position) == ("trecho", "antes ", " depois")
    assert anchor_from_position({"rects": []}) == ("", "", "")
