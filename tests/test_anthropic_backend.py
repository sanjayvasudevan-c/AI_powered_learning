from unittest.mock import MagicMock, patch

import pytest

from coursec.core.anthropic_backend import anthropic_backend


def test_raises_clearly_when_api_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        anthropic_backend("model", "prompt", {})


def test_calls_the_sdk_and_extracts_text(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    text_block = MagicMock(type="text", text="the response")
    response = MagicMock(content=[text_block])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("anthropic.Anthropic", return_value=mock_client) as mock_ctor:
        result = anthropic_backend(
            "claude-haiku-4-5-20251001",
            "hello",
            {"max_tokens": 512, "temperature": 0.2, "system": "be terse"},
        )

    assert result == "the response"
    mock_ctor.assert_called_once_with(api_key="test-key")
    mock_client.messages.create.assert_called_once_with(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": "hello"}],
        system="be terse",
        temperature=0.2,
    )


def test_defaults_max_tokens_when_not_given(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    response = MagicMock(content=[MagicMock(type="text", text="ok")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("anthropic.Anthropic", return_value=mock_client):
        anthropic_backend("model", "prompt", {})

    _, kwargs = mock_client.messages.create.call_args
    assert kwargs["max_tokens"] == 2048
    assert "system" not in kwargs
    assert "temperature" not in kwargs


def test_ignores_non_text_content_blocks(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    text_block = MagicMock(type="text", text="kept")
    other_block = MagicMock(type="tool_use", text="dropped")
    response = MagicMock(content=[other_block, text_block])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = anthropic_backend("model", "prompt", {})

    assert result == "kept"
