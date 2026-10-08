"""Assistente de estudo ancorado nas notas e no corpus do próprio usuário.

A resposta é inferência de modelo. Ela nunca é apresentada como texto do usuário nem como
o que o documento afirma, e não é persistida como conhecimento autoral (AGENTS.md §1).

A chamada ao modelo acontece fora da unidade de trabalho: o contexto é lido em uma
transação de leitura, a transação termina, e só então o provider é chamado. O contexto
enviado é o mínimo necessário — trechos relevantes e as anotações do usuário, nunca o
documento inteiro (PRD §18).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from notely.core.models import (
    AIModelConfig,
    AIModelProvider,
    Annotation,
    CorpusPassage,
    utc_now,
)
from notely.core.ports import UnitOfWork
from notely.core.security import SecretCipher, SecurityError
from notely.core.services import StudySessionNotFoundError
from notely.providers.chat_model import (
    AnthropicChatProvider,
    ChatMessage,
    ChatModelProvider,
    ChatModelUnavailableError,
    OpenAICompatibleChatProvider,
)

logger = logging.getLogger("notely.assistant")

MAX_SOURCES = 5
MAX_EXCERPT_CHARACTERS = 600
MAX_ANNOTATIONS = 8
MAX_ANNOTATION_CHARACTERS = 300
MAX_QUESTION_CHARACTERS = 2000
MAX_ANSWER_TOKENS = 900
ANSWER_TEMPERATURE = 0.2


class AssistantError(Exception):
    """Erro do assistente; a API traduz para o status adequado."""


class AssistantNotConfiguredError(AssistantError):
    """O usuário ainda não configurou um modelo."""


class AssistantConfigurationError(AssistantError):
    """Falta a chave de cifra, então a chave do provider não pode ser guardada."""


class AssistantUnavailableError(AssistantError):
    """O provider não respondeu."""


@dataclass(frozen=True, slots=True)
class AssistantSource:
    document_id: UUID
    document_title: str
    page_number: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class AssistantAnswer:
    answer: str
    provider: str
    model: str
    grounded: bool
    sources: tuple[AssistantSource, ...]


class StudyAssistant:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        cipher: SecretCipher | None = None,
        max_sources: int = MAX_SOURCES,
    ) -> None:
        self._uow_factory = uow_factory
        self._cipher = cipher
        self._max_sources = max_sources

    # -- configuração ---------------------------------------------------------

    async def get_config(self) -> AIModelConfig | None:
        async with self._uow_factory() as uow:
            return await uow.get_ai_model_config()

    async def save_config(
        self,
        *,
        provider: AIModelProvider,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        enabled: bool = True,
    ) -> AIModelConfig:
        """Grava a configuração. Sem `api_key`, a chave já guardada é mantida."""
        moment = utc_now()
        async with self._uow_factory() as uow:
            existing = await uow.get_ai_model_config()
            encrypted = existing.api_key_encrypted if existing else None
            if api_key is not None and api_key.strip():
                encrypted = self._cipher_required().encrypt(api_key.strip())
            config = AIModelConfig(
                id=existing.id if existing else uuid4(),
                provider=provider,
                model=model.strip(),
                api_key_encrypted=encrypted,
                base_url=base_url.strip() if base_url and base_url.strip() else None,
                enabled=enabled,
                created_at=existing.created_at if existing else moment,
                updated_at=moment,
            )
            await uow.save_ai_model_config(config)
            await uow.commit()
        return config

    async def clear_config(self) -> None:
        """Remove a configuração: depois disso a leitura volta a ser 404."""
        async with self._uow_factory() as uow:
            await uow.delete_ai_model_config()
            await uow.commit()

    # -- resposta -------------------------------------------------------------

    async def ask(
        self,
        *,
        message: str,
        study_session_id: UUID | None = None,
        document_id: UUID | None = None,
    ) -> AssistantAnswer:
        question = message.strip()[:MAX_QUESTION_CHARACTERS]
        if not question:
            raise AssistantError("message is required")

        chat, config = await self._provider()
        passages, annotations = await self._context(
            study_session_id=study_session_id,
            document_id=document_id,
            question=question,
        )

        try:
            answer = await chat.complete(
                system=_system_prompt(),
                messages=[
                    ChatMessage(
                        role="user",
                        content=_user_prompt(question, passages, annotations),
                    )
                ],
                max_tokens=MAX_ANSWER_TOKENS,
                temperature=ANSWER_TEMPERATURE,
            )
        except ChatModelUnavailableError as exc:
            raise AssistantUnavailableError("the model provider is unavailable") from exc

        return AssistantAnswer(
            answer=answer.strip(),
            provider=config.provider.value,
            model=config.model,
            grounded=bool(passages or annotations),
            sources=tuple(
                AssistantSource(
                    document_id=passage.document_id,
                    document_title=passage.document_title,
                    page_number=passage.page_number,
                    excerpt=passage.excerpt[:MAX_EXCERPT_CHARACTERS],
                )
                for passage in passages
            ),
        )

    async def _context(
        self,
        *,
        study_session_id: UUID | None,
        document_id: UUID | None,
        question: str,
    ) -> tuple[list[CorpusPassage], list[Annotation]]:
        """Lê o contexto em uma transação de leitura que termina antes da chamada ao modelo."""
        if study_session_id is None:
            # Sem sessão não há ancoragem: é o chat livre.
            return [], []
        async with self._uow_factory() as uow:
            if await uow.get_study_session(study_session_id) is None:
                raise StudySessionNotFoundError(str(study_session_id))
            passages = await uow.search_session_passages(
                session_id=study_session_id,
                question=question,
                document_id=document_id,
                limit=self._max_sources,
            )
            annotations = await uow.list_session_annotations(
                session_id=study_session_id, limit=MAX_ANNOTATIONS
            )
        return passages, annotations

    async def _provider(self) -> tuple[ChatModelProvider, AIModelConfig]:
        async with self._uow_factory() as uow:
            config = await uow.get_ai_model_config()
        if config is None or not config.enabled:
            raise AssistantNotConfiguredError(
                "configure a model provider before asking"
            )
        if not config.api_key_encrypted:
            raise AssistantNotConfiguredError("the model provider has no stored key")
        try:
            api_key = self._cipher_required().decrypt(config.api_key_encrypted)
        except SecurityError as exc:
            raise AssistantConfigurationError(str(exc)) from exc

        if config.provider is AIModelProvider.ANTHROPIC:
            return (
                AnthropicChatProvider(
                    model=config.model,
                    api_key=api_key,
                    endpoint=config.base_url or AnthropicChatProvider.DEFAULT_ENDPOINT,
                ),
                config,
            )
        if not config.base_url:
            raise AssistantNotConfiguredError(
                "an OpenAI-compatible provider needs a base URL"
            )
        return (
            OpenAICompatibleChatProvider(
                endpoint=config.base_url, model=config.model, api_key=api_key
            ),
            config,
        )

    def _cipher_required(self) -> SecretCipher:
        if self._cipher is None:
            raise AssistantConfigurationError(
                "NOTELY_AI_SECRET_KEY is required to store a provider key"
            )
        return self._cipher


def _system_prompt() -> str:
    return (
        "Você ajuda um estudante a entender o material que ele está lendo. Responda em "
        "português, apenas com base no contexto fornecido, e cite o documento e a página "
        "de cada evidência que usar. Se o contexto não bastar para responder, diga que não "
        "há evidência suficiente no material em vez de supor. As anotações do estudante "
        "são a voz dele: não as apresente como afirmações do documento. A resposta é uma "
        "sugestão de estudo, não um texto do estudante nem do autor."
    )


def _user_prompt(
    question: str,
    passages: list[CorpusPassage],
    annotations: list[Annotation],
) -> str:
    lines: list[str] = []
    if annotations:
        lines.append("Anotações do estudante nesta sessão:")
        for annotation in annotations:
            comment = (annotation.comment or "").strip()[:MAX_ANNOTATION_CHARACTERS]
            quote = annotation.quote.strip()[:MAX_ANNOTATION_CHARACTERS]
            detail = f"{quote}" + (f" — {comment}" if comment else "")
            lines.append(f"- [{annotation.type}] {detail}")
    if passages:
        lines.append("Trechos do material:")
        for passage in passages:
            excerpt = " ".join(passage.excerpt.split())[:MAX_EXCERPT_CHARACTERS]
            lines.append(
                f"- {passage.document_title}, página {passage.page_number}: {excerpt}"
            )
    if not lines:
        lines.append("Não há contexto do material disponível para esta pergunta.")
    lines.append(f"Pergunta do estudante: {question}")
    return "\n".join(lines)
