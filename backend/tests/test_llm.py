from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services import llm


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch):
    # get_settings() is lru_cached at module scope - env var patches below
    # only take effect once that cache is cleared before and after each test.
    get_settings.cache_clear()
    llm._client.cache_clear()
    yield
    get_settings.cache_clear()
    llm._client.cache_clear()


def test_is_configured_false_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert llm.is_configured() is False


def test_is_configured_true_with_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert llm.is_configured() is True


@pytest.mark.asyncio
async def test_generate_text_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(llm.LlmUnavailableError):
        await llm.generate_text("hello")


@pytest.mark.asyncio
async def test_generate_text_empty_prompt_short_circuits(monkeypatch):
    # Should return "" without ever needing a configured client.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert await llm.generate_text("   ") == ""
