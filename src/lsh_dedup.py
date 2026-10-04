"""
LSH-based content deduplication using SimHash algorithm.

SimHash generates a 64-bit fingerprint for text content.
Similar texts will have fingerprints with small Hamming distance.
"""

import logging
from collections import Counter
from typing import Optional

from simhash import Simhash

# simhash multiplies a uint8 array by each token's weight (occurrence count).
# NumPy 2 raises "Python integer N out of bounds for uint8" for weights > 255
# (NumPy 1 silently wrapped), which dropped long articles. Cap the weight so
# the fingerprint of any text can be computed; texts with no token above the
# cap hash exactly as before.
MAX_TOKEN_WEIGHT = 255


def _fingerprint(text: str) -> int:
    features = Counter(Simhash('')._tokenize(text))
    capped = {tok: min(count, MAX_TOKEN_WEIGHT) for tok, count in features.items()}
    return Simhash(capped).value


def compute_simhash(text: str, label: str = '') -> int:
    """
    Compute SimHash value for the given text.
    
    Args:
        text: Input text content
        label: Optional article identifier (title/URL) used in the failure log
        
    Returns:
        64-bit SimHash value as integer; 0 (meaning "no fingerprint", which
        dedup ignores) for empty text or if fingerprinting fails, so a
        fingerprint problem never causes the article to be dropped.
    """
    if not text or not text.strip():
        return 0
    try:
        val = _fingerprint(text)
    except Exception as exc:
        logging.warning(f"SimHash failed for '{label}' ({len(text)} chars), storing without fingerprint: {exc}")
        return 0
    # Convert to signed 64-bit integer for PostgreSQL BIGINT
    if val >= (1 << 63):
        val -= (1 << 64)
    return val


def hamming_distance(hash1: int, hash2: int) -> int:
    """Calculate Hamming distance between two SimHash values."""
    return bin(hash1 ^ hash2).count('1')


def is_similar(hash1: int, hash2: int, threshold: int = 3) -> bool:
    """
    Check if two SimHash values represent similar content.
    
    Args:
        hash1: First SimHash value
        hash2: Second SimHash value
        threshold: Maximum Hamming distance to consider similar (default: 3)
        
    Returns:
        True if content is considered similar/duplicate
    """
    if hash1 == 0 or hash2 == 0:
        return False
    return hamming_distance(hash1, hash2) <= threshold
