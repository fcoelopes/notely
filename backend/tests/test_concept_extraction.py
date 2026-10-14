"""Extração de conceitos e camada de chat, sem rede."""

from __future__ import annotations

import json

import pytest

from notely.providers.chat_model import (
    AnthropicChatProvider,
    ChatModelUnavailableError,
    OpenAICompatibleChatProvider,
)
from notely.providers.concept_extraction import (
    MAX_CONCEPTS,
    ConceptExtractionContext,
    ConceptExtractionUnavailableError,
    HeuristicConceptExtractionProvider,
    ModelConceptExtractionProvider,
)


class ScriptedChatProvider:
    """Chat provider roteirizado: devolve a resposta configurada, sem rede."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls: list[str] = []

    async def complete(self, *, system: str, messages, max_tokens: int = 512, temperature: float = 0.0) -> str:
        self.calls.append(messages[0].content)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _context(quote: str = "A materialidade depende da natureza das informações contábeis") -> ConceptExtractionContext:
    return ConceptExtractionContext(
        document_title="IAS 1",
        annotation_type="highlight",
        quote=quote,
        comment=None,
    )


async def test_heuristic_extraction_is_deterministic_and_skips_stopwords() -> None:
    provider = HeuristicConceptExtractionProvider()

    first = await provider.extract(_context())
    second = await provider.extract(_context())

    assert first == second
    names = [concept.name for concept in first]
    assert "materialidade" in names
    assert "natureza" in names
    assert "contábeis" in names
    # Stopwords, verbos comuns e termos curtos ficam de fora.
    assert "para" not in names
    assert "das" not in names
    assert "depende" not in names
    # Heurística não produz confiança calibrada.
    assert all(concept.confidence is None for concept in first)


async def test_heuristic_extraction_weights_the_comment_and_respects_the_limit() -> None:
    provider = HeuristicConceptExtractionProvider()
    context = ConceptExtractionContext(
        document_title="Aula",
        annotation_type="note",
        quote="retorno sobre ativos",
        comment="retorno sobre ativos e alavancagem financeira com margem operacional",
    )

    concepts = await provider.extract(context)

    names = {concept.name.casefold() for concept in concepts}
    assert len(concepts) == MAX_CONCEPTS
    # "retorno" e "ativos" aparecem na citação e no comentário; "alavancagem" só no
    # comentário, que tem peso maior.
    assert {"retorno", "ativos", "alavancagem"}.issubset(names)
    # O termo de menor peso entre os de comentário fica fora do limite de conceitos.
    assert "operacional" not in names


async def test_model_extraction_parses_json_and_normalizes_the_names() -> None:
    chat = ScriptedChatProvider(
        json.dumps(
            {
                "concepts": [
                    {"name": "  Materialidade   contábil ", "confidence": 0.9},
                    {"name": "materialidade contábil"},
                    {"name": ""},
                    {"name": "Relevância", "confidence": 2.5},
                ]
            }
        )
    )
    provider = ModelConceptExtractionProvider(chat=chat)

    concepts = await provider.extract(_context())

    assert [concept.name for concept in concepts] == ["Materialidade contábil", "Relevância"]
    assert concepts[0].confidence == 0.9
    # Confiança fora da faixa é limitada a 1.0.
    assert concepts[1].confidence == 1.0
    assert provider.name == "scripted:concepts"


async def test_model_extraction_accepts_fenced_json_and_a_bare_list() -> None:
    provider = ModelConceptExtractionProvider(
        chat=ScriptedChatProvider('```json\n["Alavancagem", "Liquidez"]\n```')
    )
    concepts = await provider.extract(_context())
    assert [concept.name for concept in concepts] == ["Alavancagem", "Liquidez"]


async def test_model_extraction_reports_invalid_payloads() -> None:
    provider = ModelConceptExtractionProvider(chat=ScriptedChatProvider("não é json"))
    with pytest.raises(ConceptExtractionUnavailableError):
        await provider.extract(_context())


async def test_model_extraction_reports_an_unavailable_chat_provider() -> None:
    provider = ModelConceptExtractionProvider(
        chat=ScriptedChatProvider(ChatModelUnavailableError("down"))
    )
    with pytest.raises(ConceptExtractionUnavailableError):
        await provider.extract(_context())


async def test_openai_compatible_reads_the_first_choice_and_sends_the_key() -> None:
    provider = OpenAICompatibleChatProvider(
        endpoint="http://localhost/v1/chat/completions", model="gpt-x", api_key="secret"
    )
    captured: dict[str, object] = {}

    def fake_post(payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        captured["headers"] = provider._headers()
        return {"choices": [{"message": {"content": "ok"}}]}

    provider._post = fake_post  # type: ignore[method-assign]
    result = await provider.complete(
        system="s", messages=[], max_tokens=16, temperature=0.0
    )

    assert result == "ok"
    assert captured["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer secret"}
    assert captured["payload"]["model"] == "gpt-x"


async def test_openai_compatible_requires_endpoint_and_model() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleChatProvider(endpoint="", model="gpt-x")
    with pytest.raises(ValueError):
        OpenAICompatibleChatProvider(endpoint="http://x", model="")


async def test_anthropic_joins_text_blocks_and_requires_a_key() -> None:
    provider = AnthropicChatProvider(model="claude-x", api_key="secret")
    provider._post = lambda payload: {  # type: ignore[method-assign]
        "content": [{"type": "text", "text": "olá"}, {"type": "other"}, {"type": "text", "text": " mundo"}]
    }
    assert await provider.complete(system="s", messages=[]) == "olá mundo"

    with pytest.raises(ValueError):
        AnthropicChatProvider(model="claude-x", api_key="")


async def test_chat_transport_errors_become_unavailable() -> None:
    provider = OpenAICompatibleChatProvider(endpoint="http://x", model="m")

    def boom(payload: dict[str, object]) -> dict[str, object]:
        raise OSError("network down")

    provider._post = boom  # type: ignore[method-assign]
    with pytest.raises(ChatModelUnavailableError):
        await provider.complete(system="s", messages=[])
