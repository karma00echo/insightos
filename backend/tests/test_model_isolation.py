"""Configuration and adapter tests only: no provider requests."""
import pytest
from app.config import Settings
from app.services import answering


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_deepseek_is_never_an_embedding_endpoint(provider):
    with pytest.raises(ValueError, match="DeepSeek"):
        Settings(_env_file=None, embedding_provider=provider,
                 llm_base_url="https://api.deepseek.com", llm_model="deepseek-flash")


def test_local_embedding_stays_independent():
    settings = Settings(_env_file=None, embedding_provider="sentence-transformers",
                        embedding_model="BAAI/bge-small-zh-v1.5", embedding_dimension=512,
                        llm_base_url="https://api.deepseek.com")
    assert settings.embedding_provider == "sentence-transformers"


def test_deepseek_adapter_disables_thinking_and_automatic_retries(monkeypatch):
    settings = Settings(_env_file=None, llm_base_url="https://api.deepseek.com",
                        llm_model="deepseek-flash", llm_api_key="unit-test-placeholder")
    monkeypatch.setattr(answering, "get_settings", lambda: settings)
    model = answering.configured_llm()
    assert model.extra_body == {"thinking": {"type": "disabled"}}
    assert model.max_retries == 0
