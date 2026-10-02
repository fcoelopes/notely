from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

MAX_THEME_LENGTH = 80
MAX_CONTEXT_CHARACTERS = 4000
MAX_QUOTE_CHARACTERS = 200
MAX_CONTEXT_ITEMS = 12
MAX_TOKENS = 5

STOPWORDS = frozenset(
    {
        "and",
        "aos",
        "apos",
        "após",
        "aquela",
        "aquele",
        "aquilo",
        "como",
        "com",
        "das",
        "dos",
        "entre",
        "esta",
        "este",
        "estes",
        "estas",
        "for",
        "from",
        "into",
        "isso",
        "mais",
        "mas",
        "nao",
        "não",
        "essa",
        "esse",
        "pela",
        "pelo",
        "por",
        "para",
        "que",
        "seu",
        "seus",
        "sua",
        "suas",
        "sobre",
        "that",
        "the",
        "this",
        "uma",
        "um",
        "with",
    }
)

TOKEN_PATTERN = re.compile(r"[^\W\d_]+", re.UNICODE)


class TopicSuggestionUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TopicSuggestionContext:
    """Contexto mínimo necessário: títulos e sinais escritos pelo próprio usuário."""

    document_titles: tuple[str, ...]
    quotes: tuple[str, ...]
    comments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TopicSuggestion:
    theme: str
    rationale: str | None
    provider: str
    model: str


class TopicSuggestionProvider(Protocol):
    async def suggest(self, context: TopicSuggestionContext) -> TopicSuggestion: ...


class HeuristicTopicSuggestionProvider:
    """Sugestão determinística, sem rede: serve de padrão e mantém o fluxo utilizável offline."""

    provider = "heuristic"
    model = "frequency-v1"

    async def suggest(self, context: TopicSuggestionContext) -> TopicSuggestion:
        weights: dict[str, int] = {}
        casing: dict[str, str] = {}
        for text, weight in (
            *((title, 3) for title in context.document_titles),
            *((comment, 2) for comment in context.comments),
            *((quote, 1) for quote in context.quotes),
        ):
            for token in TOKEN_PATTERN.findall(text):
                key = token.lower()
                if len(key) < 4 or key in STOPWORDS:
                    continue
                weights[key] = weights.get(key, 0) + weight
                casing.setdefault(key, token)

        if not weights:
            raise TopicSuggestionUnavailableError(
                "not enough reading signals to suggest a theme"
            )

        ranked = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
        theme = _truncate(" · ".join(casing[key] for key, _ in ranked[:MAX_TOKENS]))
        return TopicSuggestion(
            theme=theme,
            rationale="Sugestão heurística a partir dos títulos e das anotações da sessão.",
            provider=self.provider,
            model=self.model,
        )


class OpenAICompatibleTopicSuggestionProvider:
    """Adapter para endpoints compatíveis com /chat/completions."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not endpoint.strip() or not model.strip():
            raise ValueError("endpoint and model are required for the OpenAI-compatible provider")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def suggest(self, context: TopicSuggestionContext) -> TopicSuggestion:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(context)},
            ],
        }
        try:
            body = await asyncio.to_thread(self._post, payload)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise TopicSuggestionUnavailableError("topic provider is unavailable") from exc

        theme, rationale = _parse_theme(body)
        if not theme:
            raise TopicSuggestionUnavailableError("topic provider returned no theme")
        return TopicSuggestion(
            theme=_truncate(theme),
            rationale=rationale,
            provider="openai_compatible",
            model=self.model,
        )

    def _post(self, payload: dict[str, object]) -> str:
        request = urllib.request.Request(  # noqa: S310 - endpoint comes from configuration
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            document = json.loads(response.read().decode("utf-8"))
        choices = document.get("choices") or []
        if not choices:
            raise TopicSuggestionUnavailableError("topic provider returned no choices")
        return str(choices[0].get("message", {}).get("content", ""))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _system_prompt() -> str:
    return (
        "Você sugere um tema curto para uma sessão de estudo a partir dos documentos e das "
        "anotações de leitura do usuário. Responda apenas com um objeto JSON no formato "
        '{"theme": "...", "rationale": "..."}. O tema deve ter no máximo '
        f"{MAX_THEME_LENGTH} caracteres, não pode inventar conteúdo ausente do contexto e não "
        "pode substituir a decisão do usuário: é apenas uma sugestão."
    )


def _user_prompt(context: TopicSuggestionContext) -> str:
    lines = ["Documentos da sessão:"]
    lines.extend(f"- {title}" for title in context.document_titles)
    signals = [*context.comments, *context.quotes]
    if signals:
        lines.append("Anotações do usuário:")
        lines.extend(
            f"- {signal[:MAX_QUOTE_CHARACTERS]}"
            for signal in signals[:MAX_CONTEXT_ITEMS]
            if signal.strip()
        )
    return "\n".join(lines)[:MAX_CONTEXT_CHARACTERS]


def _parse_theme(content: str) -> tuple[str, str | None]:
    stripped = content.strip()
    if not stripped:
        return "", None
    candidate = stripped
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.removeprefix("json").strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return _sanitize(stripped), None
    if not isinstance(parsed, dict):
        return _sanitize(stripped), None
    theme = str(parsed.get("theme", "")).strip()
    raw_rationale = parsed.get("rationale")
    rationale = str(raw_rationale).strip() if raw_rationale else None
    return theme, rationale


def _sanitize(value: str) -> str:
    single_line = " ".join(value.split())
    return single_line if len(single_line) <= MAX_QUOTE_CHARACTERS else ""


def _truncate(theme: str) -> str:
    if len(theme) <= MAX_THEME_LENGTH:
        return theme
    clipped = theme[:MAX_THEME_LENGTH]
    separator = clipped.rfind(" · ")
    if separator > 0:
        return clipped[:separator]
    return clipped.rstrip()
