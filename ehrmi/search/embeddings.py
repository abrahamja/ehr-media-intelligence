"""Text embedders.

SentenceTransformerEmbedder (all-MiniLM-L6-v2) is the default. HashingEmbedder is a
dependency-free fallback used by the tests and when the model can't be downloaded
(EHRMI_EMBEDDER=hash). It only captures word overlap, not meaning."""

from __future__ import annotations

import hashlib
import math
import os
import re
from functools import lru_cache
from typing import Protocol


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # heavy import, only when used

        self.model = SentenceTransformer(model_name)
        self.name = model_name
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "in", "on", "with", "for", "to", "is", "was", "no", "at", "by"}


class HashingEmbedder:
    """Bag of words + character trigrams hashed into a fixed vector, L2-normalised."""

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _features(self, text: str):
        for tok in _TOKEN.findall(text.lower()):
            if tok in _STOP:
                continue
            yield tok, 1.0
            padded = f"#{tok}#"
            for i in range(len(padded) - 2):
                yield padded[i:i + 3], 0.3

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat, weight in self._features(text):
                h = int(hashlib.md5(feat.encode()).hexdigest(), 16)
                vec[h % self.dim] += weight if (h >> 64) & 1 else -weight
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


@lru_cache(maxsize=1)
def default_embedder() -> Embedder:
    if os.getenv("EHRMI_EMBEDDER", "").lower() == "hash":
        return HashingEmbedder()
    try:
        return SentenceTransformerEmbedder(os.getenv("EHRMI_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    except Exception as exc:  # model not installed / no network
        print(f"[ehrmi] sentence-transformers unavailable ({exc.__class__.__name__}); using hashing embedder")
        return HashingEmbedder()
