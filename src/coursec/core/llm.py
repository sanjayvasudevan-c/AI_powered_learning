"""The LLM call cache — CLAUDE.md §8.

Every LLM call is keyed by `sha256(model, prompt, sorted params)` into a
persistent on-disk cache. A rebuild with unchanged inputs must issue zero new
calls; this module is what every pass that calls an LLM (understand,
evidence's pedagogical-fit scoring, compose, verify's critic, assess) goes
through to get that for free, rather than re-implementing memoization per
pass.

This module makes no network calls itself — `call` takes the provider as an
explicit `backend` callable, so passes stay swappable and testable without a
live model.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

DEFAULT_CACHE_DIR = Path(".cache/llm")

#: Count of calls that were NOT served from cache, since the process started
#: (or since the last `reset_cache_miss_count()`). Tests assert on this to
#: prove a rebuild issues zero new calls.
cache_miss_count = 0


def reset_cache_miss_count() -> None:
    global cache_miss_count
    cache_miss_count = 0


def _cache_key(model: str, prompt: str, params: dict[str, object]) -> str:
    payload = json.dumps(
        {"model": model, "prompt": prompt, "params": params},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def call(
    model: str,
    prompt: str,
    *,
    backend: Callable[[str, str, dict[str, object]], str],
    cache_dir: Path | None = None,
    **params: object,
) -> str:
    """Return `backend(model, prompt, params)`, cached by content hash.

    A cache hit returns the stored response without calling `backend` and
    without incrementing `cache_miss_count`. `cache_dir` defaults to
    `DEFAULT_CACHE_DIR`, read at call time (not bound at import time) so
    tests can redirect every pass's cache by monkeypatching that module
    global — see conftest.py's `isolated_llm_cache` autouse fixture.
    """
    global cache_miss_count
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    key = _cache_key(model, prompt, params)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{key}.json"

    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))["response"]

    cache_miss_count += 1
    response = backend(model, prompt, params)
    cache_path.write_text(json.dumps({"response": response}), encoding="utf-8")
    return response
