"""
ct_toolkit.identity.embedding
------------------------------
Identity Embedding Layer: Calculates cosine similarity with static reference 
embedding to produce L1 divergence score.

Step 3 MVP implementation:
  - Static reference vector is loaded from Template YAML
  - Each response embedding is calculated
  - Cosine distance = 1 - cosine_similarity → divergence score
"""
import litellm
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from ct_toolkit.utils.logger import get_logger

logger = get_logger(__name__)


class IdentityEmbeddingLayer:
    """
    Static reference embedding management and L1 divergence calculation.

    Embedding calculation strategy (MVP):
      If there is no provider API embedding endpoint, a simple TF-IDF like
      word vector is used. In Step 6, it will be migrated to real semantic 
      embedding using OpenAI/Anthropic embedding API.

    Usage:
        layer = IdentityEmbeddingLayer(template="medical")
        score = layer.compute_divergence("Response text here")
        # score: 0.0 (identical) → 1.0 (completely different)
    """

    def __init__(
        self,
        template: str = "general",
        embedding_client: Any = None,  # Provider client for real embeddings
        embedding_model: str = "text-embedding-3-small",
        project_root: Path | None = None,
        strict_embedding: bool = False,
    ) -> None:
        self._template = template
        self._embedding_client = embedding_client
        self._embedding_model = embedding_model
        self._reference_vector: np.ndarray | None = None
        self._template_keywords: list[str] = []
        self._reference_text: str = ""          # stored for lazy computation
        self._project_root = project_root
        self._strict_embedding = strict_embedding
        self._embedding_method: str = "uninitialized"
        self._load_template()

    # ── Template Loading ───────────────────────────────────────────────────────

    def _load_template(self) -> None:
        import os
        import yaml
        
        data = None
        
        # Sanitize template name to prevent path traversal
        safe_template = os.path.basename(self._template)
        
        # 1. Check user config first if project_root is provided
        if self._project_root:
            # Check for both "name.yaml" and "name_identity.yaml"
            for fname in [f"{safe_template}.yaml", f"{safe_template}_identity.yaml"]:
                user_path = self._project_root / "config" / fname
                if user_path.exists():
                    logger.debug(f"Loading identity template from user config: {user_path}")
                    with open(user_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    break
        
        # Load built-in template using importlib.resources
        if data is None:
            try:
                from importlib.resources import files
                template_resource = files("ct_toolkit.identity.templates").joinpath(f"{safe_template}.yaml")
                if template_resource.is_file():
                    with template_resource.open("r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                else:
                    raise FileNotFoundError(f"Template resource not found: {template_resource}")
            except (ImportError, FileNotFoundError):
                # Fallback for development or older environments
                template_path = (
                    Path(__file__).parent
                    / "templates"
                    / f"{safe_template}.yaml"
                )
                if not template_path.exists():
                    logger.warning(
                        f"Template '{self._template}' not found, using 'general'."
                    )
                    template_path = Path(__file__).parent / "templates" / "general.yaml"
                
                with open(template_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)

        self._template_keywords = data.get("identity_keywords", [])
        self._reference_text = data.get("reference_text", " ".join(self._template_keywords))
        # NOTE: Reference vector is NOT computed here to avoid an API call at init time.
        # It will be computed lazily on the first call to compute_divergence().
        logger.info(
            f"Identity template loaded: '{self._template}' | "
            f"keywords={len(self._template_keywords)} | "
            f"reference vector will be computed on first use"
        )

    # ── Divergence Calculation ──────────────────────────────────────────────────────

    def compute_divergence(self, text: str) -> float:
        """
        Calculates the divergence score of the given text against the static reference.

        Returns:
            0.0 → identical to reference (identity preserved)
            1.0 → completely different from reference (maximum divergence)
        """
        # Lazy-initialise reference vector on first call so that no API
        # request is made at wrapper init time.
        if self._reference_vector is None:
            logger.info("Computing reference vector (first use).")
            self._reference_vector = self._compute_vector(self._reference_text)

        candidate_vector = self._compute_vector(text)
        similarity = self._cosine_similarity(self._reference_vector, candidate_vector)
        divergence = 1.0 - similarity

        return round(float(divergence), 6)

    async def acompute_divergence(self, text: str) -> float:
        """
        Asynchronously calculates the divergence score of the given text against the static reference.
        """
        if self._reference_vector is None:
            logger.info("Computing reference vector asynchronously (first use).")
            self._reference_vector = await self._acompute_vector(self._reference_text)

        candidate_vector = await self._acompute_vector(text)
        similarity = self._cosine_similarity(self._reference_vector, candidate_vector)
        divergence = 1.0 - similarity

        return round(float(divergence), 6)

    def update_reference(self, new_text: str) -> None:
        """
        Updates the static reference vector.
        WARNING: This should only be called after Reflective Endorsement approval.
        """
        self._reference_text = new_text
        self._reference_vector = self._compute_vector(new_text)
        logger.info("Reference vector updated.")

    # ── Vector Calculation (MVP: keyword-based, Step 6: real embedding) ───────────

    def _build_embedding_kwargs(self, text: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._embedding_model,
            "input": [text],
        }
        if hasattr(self._embedding_client, "base_url") and self._embedding_client.base_url:
            kwargs["api_base"] = str(self._embedding_client.base_url)
        if hasattr(self._embedding_client, "api_key") and self._embedding_client.api_key:
            kwargs["api_key"] = self._embedding_client.api_key
        return kwargs

    def _compute_local_vector(self, text: str) -> np.ndarray:
        if not self._template_keywords:
            return self._ngram_hash_vector(text)

        text_lower = text.lower()
        vector = np.array(
            [float(text_lower.count(kw.lower())) for kw in self._template_keywords],
            dtype=np.float32,
        )
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        return vector

    def _compute_vector(self, text: str) -> np.ndarray:
        """
        Uses the embedding API if a client is available.
        Otherwise falls back to simple MVP keyword/ngram implementation.
        """
        if self._embedding_client is not None:
            try:
                kwargs = self._build_embedding_kwargs(text)
                response = litellm.embedding(**kwargs)
                embedding = response.data[0]["embedding"]
                return np.array(embedding, dtype=np.float32)
            except Exception as e:
                if self._strict_embedding:
                    raise RuntimeError(f"Embedding API failed: {e}") from e
                logger.error(f"Embedding API failed: {e}. Falling back to local method.")
        return self._compute_local_vector(text)

    async def _acompute_vector(self, text: str) -> np.ndarray:
        """
        Asynchronously uses the embedding API if a client is available,
        falling back to local vector computation.
        """
        if self._embedding_client is not None:
            try:
                kwargs = self._build_embedding_kwargs(text)
                response = await litellm.aembedding(**kwargs)
                embedding = response.data[0]["embedding"]
                return np.array(embedding, dtype=np.float32)
            except Exception as e:
                if self._strict_embedding:
                    raise RuntimeError(f"Embedding API failed: {e}") from e
                logger.error(f"Embedding API failed: {e}. Falling back to local method.")
        return self._compute_local_vector(text)

    def _ngram_hash_vector(self, text: str, dim: int = 256) -> np.ndarray:
        """
        If no keywords: projects character 3-gram hashes into a dim-dimensional vector.
        Provider independent, zero-dependency fallback.
        """
        vector = np.zeros(dim, dtype=np.float32)
        words = text.lower().split()
        for word in words:
            for i in range(len(word) - 2):
                trigram = word[i:i+3]
                h = int(hashlib.sha256(trigram.encode()).hexdigest(), 16) % dim
                vector[h] += 1.0
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector /= norm
        return vector

    @staticmethod
    def calculate_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Static method for cosine similarity calculation."""
        if a.shape != b.shape:
            # Safe fallback for dimension mismatch
            min_len = min(len(a), len(b))
            a, b = a[:min_len], b[:min_len]
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        return IdentityEmbeddingLayer.calculate_similarity(a, b)
