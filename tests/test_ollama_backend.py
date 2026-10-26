"""OllamaBackend tests — a real `httpx.MockTransport`, no live Ollama
server required, the same technique test_evidence.py uses for `Fetcher`.
Every test exercises the actual HTTP contract (request shape, response
parsing, status handling), not a mock of the class's own methods.
"""

import json

import httpx
import pytest

from coursec.core.ollama_backend import DEFAULT_MODEL_MAP, OllamaBackend, _model_map_from_env


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_maps_the_cheap_tier_to_its_local_default() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    backend = OllamaBackend(client=_client(handler))
    backend("claude-haiku-4-5-20251001", "a prompt", {})

    assert captured["body"]["model"] == DEFAULT_MODEL_MAP["claude-haiku-4-5-20251001"]


def test_maps_the_strong_tier_to_its_local_default() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    backend = OllamaBackend(client=_client(handler))
    backend("claude-sonnet-5", "a prompt", {})

    assert captured["body"]["model"] == DEFAULT_MODEL_MAP["claude-sonnet-5"]


def test_an_unmapped_model_id_is_passed_through_unchanged() -> None:
    """A pass could grow a third tier, or a caller could name a local tag
    directly — either way, an unknown id is not silently coerced."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    backend = OllamaBackend(client=_client(handler))
    backend("qwen2.5:14b", "a prompt", {})

    assert captured["body"]["model"] == "qwen2.5:14b"


def test_custom_model_map_overrides_the_default() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    backend = OllamaBackend(
        client=_client(handler), model_map={"claude-sonnet-5": "mistral-large"}
    )
    backend("claude-sonnet-5", "a prompt", {})

    assert captured["body"]["model"] == "mistral-large"


def test_returns_the_response_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "the generated text", "done": True})

    backend = OllamaBackend(client=_client(handler))
    assert backend("claude-sonnet-5", "a prompt", {}) == "the generated text"


def test_prompt_is_sent_verbatim_and_not_streamed() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    backend = OllamaBackend(client=_client(handler))
    backend("claude-sonnet-5", "SOLVE: 2 + 2", {})

    assert captured["body"]["prompt"] == "SOLVE: 2 + 2"
    assert captured["body"]["stream"] is False


def test_system_prompt_is_prepended_when_present() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    backend = OllamaBackend(client=_client(handler))
    backend("claude-sonnet-5", "the user prompt", {"system": "You are terse."})

    assert "You are terse." in captured["body"]["prompt"]
    assert "the user prompt" in captured["body"]["prompt"]


def test_temperature_and_max_tokens_become_ollama_options() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    backend = OllamaBackend(client=_client(handler))
    backend("claude-sonnet-5", "a prompt", {"temperature": 0.2, "max_tokens": 512})

    assert captured["body"]["options"]["temperature"] == 0.2
    assert captured["body"]["options"]["num_predict"] == 512


def test_no_options_key_when_params_are_empty() -> None:
    """A bare request body is easier to debug than one full of Ollama
    defaults masquerading as an explicit choice."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    backend = OllamaBackend(client=_client(handler))
    backend("claude-sonnet-5", "a prompt", {})

    assert "options" not in captured["body"]


def test_unreachable_server_fails_loudly_not_silently() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend = OllamaBackend(client=_client(handler))
    with pytest.raises(RuntimeError, match="could not reach Ollama"):
        backend("claude-sonnet-5", "a prompt", {})


def test_missing_local_model_names_the_pull_command() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    backend = OllamaBackend(client=_client(handler))
    with pytest.raises(RuntimeError, match=r"ollama pull llama3\.1"):
        backend("claude-sonnet-5", "a prompt", {})


def test_other_error_status_surfaces_the_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error detail")

    backend = OllamaBackend(client=_client(handler))
    with pytest.raises(RuntimeError, match="500"):
        backend("claude-sonnet-5", "a prompt", {})


def test_response_missing_the_response_field_fails_loudly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True})  # no "response" key

    backend = OllamaBackend(client=_client(handler))
    with pytest.raises(RuntimeError, match="no 'response' field"):
        backend("claude-sonnet-5", "a prompt", {})


def test_never_fabricates_a_response_on_failure() -> None:
    """The one property that matters most: every failure mode above raises,
    none of them return a string pretending to be model output."""
    for handler in (
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("x", request=r)),
        lambda r: httpx.Response(404),
        lambda r: httpx.Response(500),
        lambda r: httpx.Response(200, json={}),
    ):
        backend = OllamaBackend(client=_client(handler))
        with pytest.raises(RuntimeError):
            backend("claude-sonnet-5", "a prompt", {})


def test_base_url_env_var_is_respected(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434")
    backend = OllamaBackend()
    assert backend.base_url == "http://gpu-box:11434"


def test_base_url_trailing_slash_is_stripped() -> None:
    backend = OllamaBackend(base_url="http://localhost:11434/")
    assert backend.base_url == "http://localhost:11434"


def test_model_map_env_var_parses_pairs(monkeypatch) -> None:
    monkeypatch.setenv(
        "OLLAMA_MODEL_MAP", "claude-sonnet-5=mixtral, claude-haiku-4-5-20251001=phi3"
    )
    parsed = _model_map_from_env()
    assert parsed == {"claude-sonnet-5": "mixtral", "claude-haiku-4-5-20251001": "phi3"}


def test_model_map_env_var_empty_is_a_noop(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL_MAP", raising=False)
    assert _model_map_from_env() == {}


def test_model_map_env_var_rejects_a_malformed_entry(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL_MAP", "not-a-pair")
    with pytest.raises(ValueError, match="not 'anthropic_id=local_tag'"):
        _model_map_from_env()


def test_model_map_precedence_env_over_default_constructor_over_env(monkeypatch) -> None:
    """Constructor argument wins over the env var, which wins over the
    built-in default — the same override order `coursec` uses elsewhere for
    config (CLI flag > env > default)."""
    monkeypatch.setenv("OLLAMA_MODEL_MAP", "claude-sonnet-5=env-tag")
    backend = OllamaBackend(model_map={"claude-sonnet-5": "explicit-tag"})
    assert backend.model_map["claude-sonnet-5"] == "explicit-tag"
