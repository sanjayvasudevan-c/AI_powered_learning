"""Local sentence embeddings — CLAUDE.md §9: `bge-small`, no GPU required.

Shared by `understand` (concept canonicalization), syllabus anchoring, and
(from D3) evidence retrieval scoring. A model load is expensive, so the
`SentenceTransformer` instance is a process-wide singleton.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"


@lru_cache(maxsize=1)
def _model():  # -> SentenceTransformer, typed loosely to keep the import lazy
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def embed(texts: Sequence[str]) -> np.ndarray:
    """L2-normalized embeddings, one row per input text — so a plain dot
    product between rows (or between two different embedded sets) is their
    cosine similarity; callers just write `vectors @ vectors.T`."""
    return np.asarray(_model().encode(list(texts), normalize_embeddings=True))
