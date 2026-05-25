"""
Embedding and clustering.

Embeds text fragments with sentence-transformers (text2vec-large-chinese)
when available, falling back to TF-IDF + jieba.  Clusters via KMeans to
discover recurring narrative patterns.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

import numpy as np

logger = logging.getLogger("inkoctobot.analysis.feature_extraction.embedding_cluster")

# ── Embedding backends ──

_SBERT_MODEL: Any = None


def _load_sbert() -> Any:
    global _SBERT_MODEL
    if _SBERT_MODEL is not None:
        return _SBERT_MODEL
    from sentence_transformers import SentenceTransformer
    _SBERT_MODEL = SentenceTransformer("shibing624/text2vec-base-chinese")
    return _SBERT_MODEL


def _tfidf_embed(texts: list[str]) -> np.ndarray:
    """Fallback TF-IDF embedding using jieba tokenisation."""
    try:
        import jieba
        tokenised = [" ".join(jieba.cut(t)) for t in texts]
    except ImportError:
        # character bigram fallback
        tokenised = [
            " ".join(t[i:i+2] for i in range(max(len(t) - 1, 1)))
            for t in texts
        ]

    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(max_features=1024)
    matrix = vec.fit_transform(tokenised)
    return matrix.toarray().astype(np.float32)


def embed_texts(texts: list[str], *, backend: str = "auto") -> np.ndarray:
    """Return an (N, D) embedding matrix for *texts*.

    Parameters
    ----------
    backend : str
        ``"sbert"`` – force sentence-transformers,
        ``"tfidf"`` – force TF-IDF,
        ``"auto"`` (default) – try sbert first, fall back to tfidf.
    """
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    if backend == "tfidf":
        return _tfidf_embed(texts)

    if backend in ("sbert", "auto"):
        try:
            model = _load_sbert()
            vecs = model.encode(texts, show_progress_bar=False,
                                normalize_embeddings=True)
            return np.asarray(vecs, dtype=np.float32)
        except Exception:
            if backend == "sbert":
                raise
            logger.info("sentence-transformers unavailable, using TF-IDF fallback")
            return _tfidf_embed(texts)

    raise ValueError(f"unknown backend: {backend}")


def cluster_fragments(
    texts: list[str],
    n_clusters: int = 8,
    *,
    backend: str = "auto",
) -> dict[str, Any]:
    """Embed *texts* and cluster them with KMeans.

    Returns::

        {"n_clusters": int,
         "clusters": [{"cluster_id": int,
                        "center_text": str,
                        "size": int,
                        "members": [str, ...]}, ...]}
    """
    if not texts:
        return {"n_clusters": 0, "clusters": []}

    from sklearn.cluster import KMeans

    n_clusters = min(n_clusters, len(texts))
    embeddings = embed_texts(texts, backend=backend)

    km = KMeans(n_clusters=n_clusters, n_init="auto", random_state=42)
    labels = km.fit_predict(embeddings)

    # Build cluster groups
    groups: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(idx)

    clusters: list[dict[str, Any]] = []
    for cid in sorted(groups):
        member_idxs = groups[cid]
        members = [texts[i] for i in member_idxs]

        # Representative: closest to centroid
        center_vec = km.cluster_centers_[cid]
        dists = [
            float(np.linalg.norm(embeddings[i] - center_vec))
            for i in member_idxs
        ]
        best = member_idxs[int(np.argmin(dists))]

        clusters.append({
            "cluster_id": cid,
            "center_text": texts[best],
            "size": len(members),
            "members": members,
        })

    return {"n_clusters": n_clusters, "clusters": clusters}
