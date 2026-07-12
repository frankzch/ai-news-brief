"""
LSH-based content deduplication using SimHash algorithm.

SimHash generates a 64-bit fingerprint for text content.
Similar texts will have fingerprints with small Hamming distance.
"""

from simhash import Simhash
from typing import Optional


def compute_simhash(text: str) -> int:
    """
    Compute SimHash value for the given text.
    
    Args:
        text: Input text content
        
    Returns:
        64-bit SimHash value as integer
    """
    if not text or not text.strip():
        return 0
    val = Simhash(text).value
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
