"""The production `llm.call` backend: routes to the Anthropic API.

No test in this repo uses this module — every test passes its own scripted
`backend` to `llm.call` (that's the whole point of `backend` being an
explicit, required argument). This is what `coursec build` wires in by
default, and it fails loudly, not silently and never by fabricating a
response, when `ANTHROPIC_API_KEY` isn't set.
"""

from __future__ import annotations

import os


def anthropic_backend(model: str, prompt: str, params: dict[str, object]) -> str:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is pinned in pyproject
        raise RuntimeError("the `anthropic` package is not installed") from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. coursec's understand/structure/compose/"
            "assess passes need a live model, and this project never fabricates a "
            "response in its place (CLAUDE.md I1/I2). Set ANTHROPIC_API_KEY and "
            "re-run."
        )

    client = anthropic.Anthropic(api_key=api_key)
    remaining = dict(params)
    max_tokens = int(remaining.pop("max_tokens", 2048))
    system = remaining.pop("system", None)
    kwargs: dict[str, object] = {}
    if system is not None:
        kwargs["system"] = system
    if "temperature" in remaining:
        kwargs["temperature"] = remaining.pop("temperature")

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    return "".join(block.text for block in response.content if block.type == "text")
