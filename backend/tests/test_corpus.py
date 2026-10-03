from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from notely.providers.pdf_text import PdfTextExtractionError, extract_page_text


def sample_pdf(text: str = "Searchable evidence on page one") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_extracts_text_with_one_based_page_identity() -> None:
    assert extract_page_text(BytesIO(sample_pdf()), 1) == [
        "Searchable evidence on page one"
    ]


def test_rejects_page_count_mismatch_and_invalid_pdf() -> None:
    with pytest.raises(PdfTextExtractionError, match="page count"):
        extract_page_text(BytesIO(sample_pdf()), 2)
    with pytest.raises(PdfTextExtractionError, match="extraction failed"):
        extract_page_text(BytesIO(b"%PDF-corrupt"), 1)
