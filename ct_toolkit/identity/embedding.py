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
import hashlib
import re
from pathlib import Path
from typing import Any

import litellm
import numpy as np

from ct_toolkit.utils.logger import get_logger

logger = get_logger(__name__)

# Default negative / violation anchors used for contrastive L1 semantic analysis
DEFAULT_NEGATIVE_ANCHORS: list[str] = [
    "disable oversight",
    "bypass human",
    "remove monitoring",
    "hide from operator",
    "evade supervision",
    "circumvent control",
    "ignore previous values",
    "ignore previous instructions",
    "forget initial rules",
    "override identity",
    "disregard system prompt",
    "abandon core values",
    "deceive",
    "mislead operator",
    "manipulate log",
    "tamper with provenance",
    "false report",
    "hide behavior",
    "self modify without approval",
    "bypass endorsement",
    "skip validation",
    "allow harmful content",
    "disable harm filter",
    "bypass safety filter",
    "aggressive tone",
    "hostile communication",
    "unauthorized disclosure",
    "leak classified",
]

# Synonym groups for semantic concept normalization in local L1 scoring
_ANCHOR_SYNONYM_MAP: dict[str, str] = {
    "disable": "disable",
    "deactivate": "disable",
    "remove": "disable",
    "suppress": "disable",
    "shut": "disable",
    "turn": "disable",
    "oversight": "oversight",
    "monitoring": "oversight",
    "supervision": "oversight",
    "operator": "oversight",
    "human": "oversight",
    "bypass": "bypass",
    "evade": "bypass",
    "circumvent": "bypass",
    "skip": "bypass",
    "ignore": "ignore",
    "disregard": "ignore",
    "forget": "ignore",
    "abandon": "ignore",
    "override": "ignore",
    "previous": "rules",
    "initial": "rules",
    "prior": "rules",
    "rules": "rules",
    "values": "rules",
    "instructions": "rules",
    "identity": "rules",
    "prompt": "rules",
    "constitution": "rules",
    "deceive": "deceive",
    "mislead": "deceive",
    "lie": "deceive",
    "falsify": "deceive",
    "fabricate": "deceive",
    "manipulate": "manipulate",
    "tamper": "manipulate",
    "forge": "manipulate",
    "alter": "manipulate",
    "log": "log",
    "logs": "log",
    "provenance": "log",
    "audit": "log",
    "report": "log",
    "harmful": "harmful",
    "dangerous": "harmful",
    "malicious": "harmful",
    "unsafe": "harmful",
    "filter": "filter",
    "guardrail": "filter",
    "guardrails": "filter",
    "endorsement": "approval",
    "approval": "approval",
    "authorization": "approval",
    "validation": "approval",
    "hostile": "hostile",
    "aggressive": "hostile",
    "insulting": "hostile",
    "abusive": "hostile",
}


class IdentityEmbeddingLayer:
    """
    Static reference embedding management and L1 divergence calculation.

    Supports:
      - Provider API embeddings via LiteLLM (OpenAI, Ollama, etc.)
      - Optional local neural embeddings via FastEmbed (`use_fastembed=True` or `fastembed/...`)
      - Contrastive positive/negative semantic anchor analysis for zero-dependency
        local L1 scoring without false positives on benign code, math, or general Q&A.
    """

    def __init__(
        self,
        template: str = "general",
        embedding_client: Any = None,
        embedding_model: str = "text-embedding-3-small",
        project_root: Path | None = None,
        strict_embedding: bool = False,
        embedding_provider: str | None = None,
        use_fastembed: bool = False,
        negative_anchors: list[str] | None = None,
    ) -> None:
        self._template = template
        self._embedding_client = embedding_client
        self._embedding_model = embedding_model
        self._embedding_provider = embedding_provider
        self._use_fastembed = use_fastembed or embedding_model.startswith("fastembed/")
        self._fastembed_model: Any = None
        self._reference_vector: np.ndarray | None = None
        self._template_keywords: list[str] = []
        self._negative_anchors: list[str] = list(negative_anchors) if negative_anchors else []
        self._reference_text: str = ""
        self._project_root = project_root
        self._strict_embedding = strict_embedding
        self._embedding_method: str = "uninitialized"
        self._load_template()

    # ── Template Loading ───────────────────────────────────────────────────────

    def _load_template(self) -> None:
        import os
        import yaml

        data = None
        safe_template = os.path.basename(self._template)

        if self._project_root:
            for fname in [f"{safe_template}.yaml", f"{safe_template}_identity.yaml"]:
                user_path = self._project_root / "config" / fname
                if user_path.exists():
                    logger.debug(f"Loading identity template from user config: {user_path}")
                    with open(user_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    break

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
        template_negatives = data.get("negative_keywords", data.get("negative_anchors", []))
        if not self._negative_anchors:
            self._negative_anchors = list(DEFAULT_NEGATIVE_ANCHORS)
        for neg in template_negatives:
            if neg not in self._negative_anchors:
                self._negative_anchors.append(neg)

        logger.info(
            f"Identity template loaded: '{self._template}' | "
            f"keywords={len(self._template_keywords)} | "
            f"reference vector will be computed on first use"
        )

    # ── Divergence Calculation ──────────────────────────────────────────────────────

    def compute_divergence(self, text: str) -> float:
        """
        Calculates the divergence score of the given text against the static reference
        and negative constitutional anchors.

        Returns:
            0.0 → identical to reference (identity preserved)
            1.0 → maximum divergence / severe anchor violation
        """
        if self._reference_vector is None:
            logger.info("Computing reference vector (first use).")
            self._reference_vector = self._compute_vector(self._reference_text)

        candidate_vector = self._compute_vector(text)
        return self._score_from_vectors_and_anchors(text, candidate_vector)

    async def acompute_divergence(self, text: str) -> float:
        """
        Asynchronously calculates the divergence score of the given text against the static reference
        and negative constitutional anchors.
        """
        if self._reference_vector is None:
            logger.info("Computing reference vector asynchronously (first use).")
            self._reference_vector = await self._acompute_vector(self._reference_text)

        candidate_vector = await self._acompute_vector(text)
        return self._score_from_vectors_and_anchors(text, candidate_vector)

    def _score_from_vectors_and_anchors(self, text: str, candidate_vector: np.ndarray) -> float:
        assert self._reference_vector is not None
        similarity = self._cosine_similarity(self._reference_vector, candidate_vector)
        violation_score = self._compute_violation_score(text)

        # If a real neural embedding backend was used, combine cosine distance with violation anchors
        if self._embedding_method in ("api", "fastembed"):
            raw_divergence = max(0.0, min(1.0, 1.0 - similarity))
            if violation_score > 0.0:
                divergence = min(1.0, max(raw_divergence, 0.35 + 0.60 * violation_score))
            else:
                divergence = raw_divergence
            return round(float(divergence), 6)

        # Contrastive semantic anchor scoring for local mode:
        # 1. If explicit or paraphrased violation anchors are present, score reflects violation severity.
        if violation_score > 0.0:
            divergence = min(1.0, 0.35 + 0.60 * violation_score)
            return round(float(divergence), 6)

        # 2. For benign text (math, code, general Q&A, or aligned statements) with 0 violations:
        #    Neutral baseline is 0.10 (below default l1_threshold=0.15 so normal answers don't false-positive),
        #    while positive identity keyword alignment lowers divergence toward 0.0.
        positive_alignment = max(0.0, min(1.0, similarity))
        divergence = 0.10 * (1.0 - 0.85 * positive_alignment)
        return round(float(divergence), 6)

    def _compute_violation_score(self, text: str) -> float:
        """
        Evaluates text against negative / violation anchors using both exact phrase
        matching and synonym-normalized concept overlap.
        Returns a score in [0.0, 1.0].
        """
        if not text or not text.strip():
            return 0.0

        text_lower = text.lower()
        words = re.findall(r"\w+", text_lower, flags=re.UNICODE)
        if not words:
            return 0.0

        canonical_tokens = {_ANCHOR_SYNONYM_MAP.get(w, w) for w in words}
        matches = 0

        for anchor in self._negative_anchors:
            anchor_lower = anchor.lower().strip()
            if not anchor_lower:
                continue
            # 1. Direct word-boundary phrase match
            if re.search(rf"(?<!\w){re.escape(anchor_lower)}(?!\w)", text_lower, flags=re.UNICODE):
                matches += 2
                continue

            # 2. Concept / synonym set match for multi-word negative anchors
            anchor_words = re.findall(r"\w+", anchor_lower, flags=re.UNICODE)
            if len(anchor_words) >= 2:
                anchor_concepts = {_ANCHOR_SYNONYM_MAP.get(w, w) for w in anchor_words if len(w) > 2}
                if len(anchor_concepts) >= 2 and anchor_concepts.issubset(canonical_tokens):
                    matches += 1

        if matches == 0:
            return 0.0
        return min(1.0, matches / 3.0)

    def update_reference(self, new_text: str) -> None:
        """
        Updates the static reference vector.
        WARNING: This should only be called after Reflective Endorsement approval.
        """
        self._reference_text = new_text
        self._reference_vector = self._compute_vector(new_text)
        logger.info("Reference vector updated.")

    # ── Vector Calculation ──────────────────────────────────────────────────────

    def _resolve_embedding_model(self) -> str:
        model = self._embedding_model
        if self._embedding_provider == "ollama" and not model.startswith("ollama/"):
            if model == "text-embedding-3-small":
                return "ollama/nomic-embed-text"
            return f"ollama/{model}"
        return model

    def _build_embedding_kwargs(self, text: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._resolve_embedding_model(),
            "input": [text],
        }
        if hasattr(self._embedding_client, "base_url") and self._embedding_client.base_url:
            kwargs["api_base"] = str(self._embedding_client.base_url)
        if hasattr(self._embedding_client, "api_key") and self._embedding_client.api_key:
            kwargs["api_key"] = self._embedding_client.api_key
        return kwargs

    def _compute_fastembed_vector(self, text: str) -> np.ndarray | None:
        """Uses FastEmbed if installed and requested for local neural embeddings."""
        if not self._use_fastembed:
            return None
        try:
            if self._fastembed_model is None:
                from fastembed import TextEmbedding  # type: ignore[import-not-found]
                model_name = self._embedding_model.replace("fastembed/", "")
                if model_name in ("", "text-embedding-3-small"):
                    model_name = "BAAI/bge-small-en-v1.5"
                self._fastembed_model = TextEmbedding(model_name=model_name)
            embeddings = list(self._fastembed_model.embed([text]))
            if embeddings:
                self._embedding_method = "fastembed"
                return np.array(embeddings[0], dtype=np.float32)
        except Exception as e:
            logger.debug(f"FastEmbed unavailable or failed ({e}); falling back to local vector.")
        return None

    def _compute_local_vector(self, text: str) -> np.ndarray:
        self._embedding_method = "local"
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
        Uses the embedding API if a client is available, or FastEmbed if enabled.
        Otherwise falls back to local keyword/anchor vector implementation.
        """
        if self._embedding_client is not None or self._embedding_provider == "ollama":
            try:
                kwargs = self._build_embedding_kwargs(text)
                response = litellm.embedding(**kwargs)
                embedding = response.data[0]["embedding"]
                self._embedding_method = "api"
                return np.array(embedding, dtype=np.float32)
            except Exception as e:
                if self._strict_embedding:
                    raise RuntimeError(f"Embedding API failed: {e}") from e
                logger.error(f"Embedding API failed: {e}. Falling back to local method.")

        fast_vec = self._compute_fastembed_vector(text)
        if fast_vec is not None:
            return fast_vec

        return self._compute_local_vector(text)

    async def _acompute_vector(self, text: str) -> np.ndarray:
        """
        Asynchronously uses the embedding API if a client is available,
        falling back to FastEmbed or local vector computation.
        """
        if self._embedding_client is not None or self._embedding_provider == "ollama":
            try:
                kwargs = self._build_embedding_kwargs(text)
                response = await litellm.aembedding(**kwargs)
                embedding = response.data[0]["embedding"]
                self._embedding_method = "api"
                return np.array(embedding, dtype=np.float32)
            except Exception as e:
                if self._strict_embedding:
                    raise RuntimeError(f"Embedding API failed: {e}") from e
                logger.error(f"Embedding API failed: {e}. Falling back to local method.")

        fast_vec = self._compute_fastembed_vector(text)
        if fast_vec is not None:
            return fast_vec

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
