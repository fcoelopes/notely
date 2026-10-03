"""Extração local do texto de PDFs; os resultados são projeções do arquivo original."""

from __future__ import annotations

from typing import BinaryIO

from pypdf import PdfReader, __version__ as pypdf_version

EXTRACTOR_VERSION = f"pypdf-{pypdf_version}/text-v1"
MAX_PAGE_TEXT_CHARS = 200_000


class PdfTextExtractionError(Exception):
    def __init__(self, message: str, *, actual_page_count: int | None = None) -> None:
        super().__init__(message)
        self.actual_page_count = actual_page_count


def extract_page_text(content: BinaryIO, expected_page_count: int) -> list[str]:
    try:
        content.seek(0)
        reader = PdfReader(content, strict=False)
        actual = len(reader.pages)
        if actual != expected_page_count:
            raise PdfTextExtractionError(
                f"PDF page count is {actual}, expected {expected_page_count}",
                actual_page_count=actual,
            )
        pages: list[str] = []
        for page in reader.pages:
            extracted = (page.extract_text() or "").replace("\x00", "").strip()
            if len(extracted) > MAX_PAGE_TEXT_CHARS:
                raise PdfTextExtractionError("extracted page exceeds text limit")
            pages.append(extracted)
        return pages
    except PdfTextExtractionError:
        raise
    except Exception as exc:
        raise PdfTextExtractionError("PDF text extraction failed") from exc
