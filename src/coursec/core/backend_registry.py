"""Picks which `LLMBackend` a build runs against — shared by `coursec build`
and the web API's `/api/build`, so the two entry points can't drift into
different sets of supported backends or different error messages.

Every pass takes `backend` as an explicit argument (never reaches for a
global), which is what makes this a one-line seam rather than a rewrite:
selecting a backend here and threading the same callable through every
pass call is the whole integration.
"""

from __future__ import annotations

from collections.abc import Callable

from coursec.core.anthropic_backend import anthropic_backend

# The same shape every pass defines locally (understand.py, compose.py, ...)
# — repeated here rather than imported, since none of those modules exports
# it and this file has no reason to depend on any one pass.
LLMBackend = Callable[[str, str, dict[str, object]], str]

KNOWN_BACKENDS = ("anthropic", "ollama")


def resolve_backend(
    name: str, *, ollama_model_map: dict[str, str] | None = None
) -> LLMBackend:
    """Returns a ready-to-call backend for `name`, or raises `ValueError`
    for anything else — callers turn that into their own exit code /
    error response rather than this module choosing one for them."""
    if name == "anthropic":
        return anthropic_backend
    if name == "ollama":
        from coursec.core.ollama_backend import OllamaBackend

        return OllamaBackend(model_map=ollama_model_map)
    raise ValueError(f"unknown backend {name!r} — expected one of {KNOWN_BACKENDS}")


def parse_model_map(raw: str) -> dict[str, str]:
    """"anthropic_id=local_tag,anthropic_id=local_tag" -> a dict, the same
    grammar `OLLAMA_MODEL_MAP` uses, so a `--ollama-model-map` CLI flag and
    the env var behave identically."""
    pairs: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        anthropic_id, _, local_tag = entry.partition("=")
        if not anthropic_id or not local_tag:
            raise ValueError(f"model-map entry {entry!r} is not 'anthropic_id=local_tag'")
        pairs[anthropic_id.strip()] = local_tag.strip()
    return pairs
