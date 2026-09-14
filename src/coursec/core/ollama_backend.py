"""A local-model `llm.call` backend — routes to Ollama instead of the
Anthropic API, so `coursec build` can run with no API key and no per-call
cost, on whatever open-weight model the machine running it has pulled.

Every pass already asks for one of two model tiers by name — a cheap one
for screening/gating (`understand.CHEAP_MODEL`, `structure.CHEAP_MODEL`,
`verify.VERIFY_MODEL`, `item_gates.GATE_MODEL`, all
`claude-haiku-4-5-20251001`) and a strong one for synthesis
(`compose.COMPOSE_MODEL`, `items.ITEM_MODEL`, both `claude-sonnet-5`). This
backend does not change that: it maps each Anthropic model id a pass
requests to a *local* model tag via `model_map`, so "a different, smaller
model for gates than for composition" is still true locally — it is a
substitution at the one seam every pass already calls through
(`llm.call`'s `backend` argument), not a rewrite of the passes themselves.

Same posture as `anthropic_backend`: fails loudly and never fabricates a
response when the local server is unreachable or the requested model isn't
pulled (CLAUDE.md I1/I2 apply to what a local model writes exactly as much
as to what a hosted one writes).
"""

from __future__ import annotations

import os

import httpx

# The two tiers every pass already names, mapped to small/capable local
# defaults. Override via `model_map` or the OLLAMA_MODEL_MAP env var
# (comma-separated "anthropic_id=local_tag" pairs) for whatever the machine
# running the build has actually pulled.
DEFAULT_MODEL_MAP: dict[str, str] = {
    "claude-haiku-4-5-20251001": "llama3.2",
    "claude-sonnet-5": "llama3.1",
}

DEFAULT_BASE_URL = "http://localhost:11434"


def _model_map_from_env() -> dict[str, str]:
    raw = os.environ.get("OLLAMA_MODEL_MAP", "")
    if not raw:
        return {}
    pairs: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        anthropic_id, _, local_tag = entry.partition("=")
        if not anthropic_id or not local_tag:
            raise ValueError(
                f"OLLAMA_MODEL_MAP entry {entry!r} is not 'anthropic_id=local_tag'"
            )
        pairs[anthropic_id.strip()] = local_tag.strip()
    return pairs


class OllamaBackend:
    """A callable matching the `LLMBackend` signature every pass calls
    through — `(model, prompt, params) -> str`. Constructed once (so its
    `httpx.Client` is reused across calls) and passed as `backend=` exactly
    where `anthropic_backend` goes today."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model_map: dict[str, str] | None = None,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self.model_map = {**DEFAULT_MODEL_MAP, **_model_map_from_env(), **(model_map or {})}
        self.client = client or httpx.Client(timeout=timeout)

    def _local_model(self, requested: str) -> str:
        # A model id already naming a local tag (someone passed "llama3.1"
        # straight through, or a pass grows a third tier some day) is used
        # as-is; only the two known Anthropic ids get translated.
        return self.model_map.get(requested, requested)

    def __call__(self, model: str, prompt: str, params: dict[str, object]) -> str:
        local_model = self._local_model(model)
        remaining = dict(params)
        system = remaining.pop("system", None)
        remaining.pop("max_tokens", None)  # num_predict below is Ollama's analogue
        options: dict[str, object] = {}
        if "temperature" in remaining:
            options["temperature"] = remaining.pop("temperature")
        if "max_tokens" in params:
            options["num_predict"] = int(params["max_tokens"])  # type: ignore[arg-type]

        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        payload: dict[str, object] = {
            "model": local_model,
            "prompt": full_prompt,
            "stream": False,
        }
        if options:
            payload["options"] = options

        try:
            response = self.client.post(f"{self.base_url}/api/generate", json=payload)
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"could not reach Ollama at {self.base_url} — is it running? "
                "(`ollama serve`, then `ollama pull <model>`). coursec never "
                "fabricates a response in place of a live model (CLAUDE.md I1/I2)."
            ) from exc

        if response.status_code == 404:
            raise RuntimeError(
                f"Ollama has no model {local_model!r} pulled (requested for "
                f"{model!r}). Run `ollama pull {local_model}`, or point "
                f"OLLAMA_MODEL_MAP at a model you have: "
                f"OLLAMA_MODEL_MAP={model}=<your-local-tag>"
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"Ollama returned {response.status_code} for model {local_model!r}: "
                f"{response.text[:300]}"
            )

        data = response.json()
        text = data.get("response")
        if text is None:
            raise RuntimeError(
                f"Ollama's response for model {local_model!r} had no 'response' field: "
                f"{str(data)[:300]}"
            )
        return text
