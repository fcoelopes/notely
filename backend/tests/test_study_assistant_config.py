"""Regra única de uso da configuração de modelo do usuário: decifra e monta o provider.

A mesma regra serve o assistente de estudo e a extração de conceitos (ADR 0005); aqui ela é
exercitada sem rede e sem banco.
"""

from __future__ import annotations

import pytest

from notely.core.models import AIModelConfig, AIModelProvider
from notely.core.security import SecretCipher
from notely.core.study_assistant import (
    AssistantConfigurationError,
    AssistantNotConfiguredError,
    chat_provider_for,
    user_api_key,
)
from notely.providers.chat_model import AnthropicChatProvider, OpenAICompatibleChatProvider

PROVIDER_KEY = "chave-secreta-do-provider"
BASE_URL = "https://api.exemplo.com/v1/chat/completions"


def _config(**overrides: object) -> AIModelConfig:
    fields: dict[str, object] = {
        "provider": AIModelProvider.OPENAI_COMPATIBLE,
        "model": "gpt-exemplo",
        "api_key_encrypted": SecretCipher(SecretCipher.generate_key()).encrypt(PROVIDER_KEY),
        "base_url": BASE_URL,
    }
    fields.update(overrides)
    return AIModelConfig(**fields)  # type: ignore[arg-type]


def test_user_api_key_decrypts_with_the_server_cipher() -> None:
    cipher = SecretCipher(SecretCipher.generate_key())
    config = _config(api_key_encrypted=cipher.encrypt(PROVIDER_KEY))

    assert user_api_key(config, cipher) == PROVIDER_KEY


def test_user_api_key_requires_a_stored_key() -> None:
    cipher = SecretCipher(SecretCipher.generate_key())

    with pytest.raises(AssistantNotConfiguredError):
        user_api_key(_config(api_key_encrypted=None), cipher)


def test_user_api_key_that_cannot_be_decrypted_is_a_configuration_error() -> None:
    config = _config(api_key_encrypted=SecretCipher(SecretCipher.generate_key()).encrypt("outra"))
    other_cipher = SecretCipher(SecretCipher.generate_key())

    with pytest.raises(AssistantConfigurationError):
        user_api_key(config, other_cipher)


def test_openai_compatible_chat_uses_the_configured_endpoint_and_model() -> None:
    chat = chat_provider_for(_config(), PROVIDER_KEY)

    assert isinstance(chat, OpenAICompatibleChatProvider)
    assert chat.name == "openai_compatible"
    assert chat.endpoint == BASE_URL
    assert chat.model == "gpt-exemplo"
    assert chat.api_key == PROVIDER_KEY


def test_openai_compatible_chat_requires_a_base_url() -> None:
    with pytest.raises(AssistantNotConfiguredError):
        chat_provider_for(_config(base_url=None), PROVIDER_KEY)


def test_anthropic_chat_defaults_to_the_public_endpoint() -> None:
    chat = chat_provider_for(
        _config(provider=AIModelProvider.ANTHROPIC, model="claude-exemplo", base_url=None),
        PROVIDER_KEY,
    )

    assert isinstance(chat, AnthropicChatProvider)
    assert chat.name == "anthropic"
    assert chat.endpoint == AnthropicChatProvider.DEFAULT_ENDPOINT
    assert chat.model == "claude-exemplo"


def test_anthropic_chat_honours_a_configured_base_url() -> None:
    chat = chat_provider_for(
        _config(provider=AIModelProvider.ANTHROPIC, base_url="https://proxy.exemplo.com"),
        PROVIDER_KEY,
    )

    assert isinstance(chat, AnthropicChatProvider)
    assert chat.endpoint == "https://proxy.exemplo.com"
