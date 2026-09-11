from pathlib import Path

from coursec.core import llm


def test_identical_call_is_a_cache_hit_on_second_invocation(tmp_path: Path) -> None:
    llm.reset_cache_miss_count()
    calls = []

    def backend(model: str, prompt: str, params: dict) -> str:
        calls.append((model, prompt, params))
        return "response"

    first = llm.call("gpt", "explain gravity", backend=backend, cache_dir=tmp_path, top_p=0.1)
    assert llm.cache_miss_count == 1

    second = llm.call("gpt", "explain gravity", backend=backend, cache_dir=tmp_path, top_p=0.1)
    assert llm.cache_miss_count == 1  # no new miss — served from cache
    assert second == first
    assert len(calls) == 1  # backend called exactly once


def test_different_params_are_different_cache_entries(tmp_path: Path) -> None:
    llm.reset_cache_miss_count()

    def backend(model: str, prompt: str, params: dict) -> str:
        return f"response for {params}"

    llm.call("gpt", "p", backend=backend, cache_dir=tmp_path, temperature=0.0)
    llm.call("gpt", "p", backend=backend, cache_dir=tmp_path, temperature=0.7)
    assert llm.cache_miss_count == 2


def test_param_order_does_not_affect_the_cache_key(tmp_path: Path) -> None:
    llm.reset_cache_miss_count()

    def backend(model: str, prompt: str, params: dict) -> str:
        return "r"

    llm.call("gpt", "p", backend=backend, cache_dir=tmp_path, a=1, b=2)
    llm.call("gpt", "p", backend=backend, cache_dir=tmp_path, b=2, a=1)
    assert llm.cache_miss_count == 1
