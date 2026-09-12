"""Mermaid -> SVG, at build time (PROMPTS.md D6).

Pluggable, the same shape as every other external call in this project
(`llm.call`'s `backend`, `evidence`'s `search`): the default,
`render_via_mermaid_ink`, needs no API key (mermaid.ink is a free public
rendering service), but every test scripts its own renderer so rendering
stays deterministic and offline.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx

MermaidRenderer = Callable[[str], bytes]

USER_AGENT = "coursec-emit/0.1 (+https://github.com/sanjayvasudevan-c/AI_powered_learning)"


def render_via_mermaid_ink(source: str, *, client: httpx.Client | None = None) -> bytes:
    """SVG bytes for `source`, via mermaid.ink. Raises `httpx.HTTPError` on
    failure — callers decide whether a missing diagram is fatal."""
    payload = {"code": source, "mermaid": {"theme": "default"}}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    url = f"https://mermaid.ink/svg/{encoded}"
    owns_client = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15)
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.content
    finally:
        if owns_client:
            client.close()
