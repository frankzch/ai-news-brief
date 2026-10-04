"""
Embedding-based article filtering using sentence-transformers.
Uses the all-MiniLM-L6-v2 model for efficient local embedding generation.
"""

import logging
import os
from typing import List, Dict, Any
import numpy as np

MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"

# Files sentence-transformers needs for a PyTorch load. snapshot_download treats every
# repo file (incl. the unused TF/Rust/ONNX weights) as required unless filtered, so an
# offline cache that lacks them is reported as incomplete.
MODEL_ALLOW_PATTERNS = [
    "*.json",
    "*.txt",
    "1_Pooling/*",
    "model.safetensors",
]

# Lazy-load the model to avoid startup delay
_model = None
_load_error: "Exception | None" = None


class EmbeddingModelUnavailable(RuntimeError):
    """The embedding model could not be loaded (not cached and/or Hub unreachable)."""


def hf_offline_enabled() -> bool:
    """True if HF_HUB_OFFLINE is set to a truthy value in the environment."""
    return os.environ.get("HF_HUB_OFFLINE", "").strip().upper() in {"1", "ON", "YES", "TRUE"}


def reset_model_failure():
    """Forget a previous load failure so the next call retries (call at the start of a run)."""
    global _load_error
    _load_error = None


def _resolve_model_path() -> str:
    """Find the model snapshot: local cache first, network only if that fails and we're online."""
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(
            MODEL_REPO, local_files_only=True, allow_patterns=MODEL_ALLOW_PATTERNS
        )
    except Exception as local_err:
        if hf_offline_enabled():
            raise EmbeddingModelUnavailable(
                f"Embedding model {MODEL_REPO} is not usable from the local cache and "
                f"HF_HUB_OFFLINE is set, so it cannot be downloaded: {local_err}. "
                "Unset HF_HUB_OFFLINE once to download it."
            ) from local_err
        logging.info(f"Embedding model not in local cache ({local_err}); downloading")
        try:
            return snapshot_download(MODEL_REPO, allow_patterns=MODEL_ALLOW_PATTERNS)
        except Exception as net_err:
            raise EmbeddingModelUnavailable(
                f"Embedding model {MODEL_REPO} is not cached and could not be downloaded: {net_err}"
            ) from net_err


def _get_model():
    """Lazy load the sentence-transformer model.

    Raises EmbeddingModelUnavailable if it cannot be loaded. The failure is remembered
    until reset_model_failure() so callers don't retry a slow load for every article.
    """
    global _model, _load_error
    if _model is not None:
        return _model
    if _load_error is not None:
        raise _load_error
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logging.error(
            "sentence-transformers not installed. "
            "Run: pip install sentence-transformers"
        )
        raise
    try:
        _model = SentenceTransformer(_resolve_model_path())
    except EmbeddingModelUnavailable as e:
        _load_error = e
        raise
    logging.info("Loaded embedding model: all-MiniLM-L6-v2")
    return _model


class EmbeddingFilter:
    """
    Filter articles based on semantic similarity between
    article tags and user categories.
    """
    
    def __init__(self, similarity_threshold: float = 0.4):
        """
        Initialize the embedding filter.
        
        Args:
            similarity_threshold: Minimum cosine similarity for a tag-category
                                  match. Default 0.4 is a good balance.
        """
        self.threshold = similarity_threshold
        self._category_cache = {}  # Cache category embeddings per user
    
    def encode_texts(self, texts: List[str]) -> np.ndarray:
        """
        Encode a list of texts into embeddings.
        
        Args:
            texts: List of text strings to encode
            
        Returns:
            numpy array of shape (len(texts), 384)
        """
        if not texts:
            return np.array([])
        
        model = _get_model()
        return model.encode(texts, convert_to_numpy=True)
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(np.dot(vec1, vec2) / (norm1 * norm2))
    
    def _compute_max_similarity(self, tag_embeddings: np.ndarray, 
                                 category_embeddings: np.ndarray) -> float:
        """
        Compute the maximum similarity between any tag and any category.
        
        Returns:
            Maximum cosine similarity found
        """
        if len(tag_embeddings) == 0 or len(category_embeddings) == 0:
            return 0.0
        
        # Compute all pairwise similarities efficiently
        # tag_embeddings: (n_tags, dim), category_embeddings: (n_cats, dim)
        # Normalize vectors
        tag_norms = np.linalg.norm(tag_embeddings, axis=1, keepdims=True)
        cat_norms = np.linalg.norm(category_embeddings, axis=1, keepdims=True)
        
        # Avoid division by zero
        tag_norms = np.where(tag_norms == 0, 1, tag_norms)
        cat_norms = np.where(cat_norms == 0, 1, cat_norms)
        
        tag_normalized = tag_embeddings / tag_norms
        cat_normalized = category_embeddings / cat_norms
        
        # Compute similarity matrix: (n_tags, n_cats)
        similarity_matrix = np.dot(tag_normalized, cat_normalized.T)
        
        return float(np.max(similarity_matrix))
    
    def filter_articles_by_categories(
        self, 
        articles: List[Dict[str, Any]], 
        user_categories: List[str],
        cache_key: str = None
    ) -> List[Dict[str, Any]]:
        """
        Filter articles based on tag-category semantic similarity.
        
        Articles are kept if at least one tag is similar enough to
        at least one user category.
        
        Args:
            articles: List of article dicts with 'tags' field
            user_categories: List of user interest categories
            cache_key: Optional key (e.g., user_id) for caching category embeddings
            
        Returns:
            Filtered list of articles
        """
        if not articles:
            return []
        
        if not user_categories:
            # No categories = no filtering, return all
            logging.debug("No user categories, skipping embedding filter")
            return articles
        
        # Get or compute category embeddings
        if cache_key and cache_key in self._category_cache:
            category_embeddings = self._category_cache[cache_key]
        else:
            category_embeddings = self.encode_texts(user_categories)
            if cache_key:
                self._category_cache[cache_key] = category_embeddings
        
        filtered_articles = []
        
        for article in articles:
            tags = article.get('tags', [])
            
            if not tags:
                # No tags = can't determine relevance, include by default
                filtered_articles.append(article)
                continue
            
            # Encode article tags
            tag_embeddings = self.encode_texts(tags)
            
            # Find max similarity
            max_sim = self._compute_max_similarity(tag_embeddings, category_embeddings)
            
            if max_sim >= self.threshold:
                filtered_articles.append(article)
                logging.debug(
                    f"Article '{article.get('title', '')[:30]}...' passed filter "
                    f"(max_sim={max_sim:.3f})"
                )
            else:
                logging.debug(
                    f"Article '{article.get('title', '')[:30]}...' filtered out "
                    f"(max_sim={max_sim:.3f} < {self.threshold})"
                )
        
        logging.info(
            f"Embedding filter: {len(filtered_articles)}/{len(articles)} articles passed "
            f"(threshold={self.threshold})"
        )
        
        return filtered_articles
    
    def clear_cache(self, cache_key: str = None):
        """Clear cached category embeddings."""
        if cache_key:
            self._category_cache.pop(cache_key, None)
        else:
            self._category_cache.clear()
