"""Segmentação determinística do texto de uma página, mantendo offsets verificáveis."""

from __future__ import annotations

from dataclasses import dataclass

CHUNK_SIZE = 700
CHUNK_OVERLAP = 100


@dataclass(frozen=True, slots=True)
class CorpusChunk:
    number: int
    start: int
    end: int
    text: str


def chunk_page(text: str) -> list[CorpusChunk]:
    chunks: list[CorpusChunk] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + CHUNK_SIZE // 2, end)
            if boundary > start:
                end = boundary
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end <= start:
            start += 1
            continue
        chunks.append(CorpusChunk(len(chunks), start, end, text[start:end]))
        if end == len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks
