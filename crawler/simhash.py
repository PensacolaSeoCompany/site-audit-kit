"""64-bit SimHash implementation (Charikar's algorithm).

Provides locality-sensitive hashing for near-duplicate text detection.
Two documents with small Hamming distance between their SimHash fingerprints
are likely to be similar in content.

This complements the MinHash-based duplicate_detector.py by offering a
lighter-weight, non-probabilistic fingerprint that can be stored as a
single BIGINT column and compared with a simple XOR + popcount.
"""

import hashlib
import logging

logger = logging.getLogger(__name__)

# Default shingle size (number of words per shingle)
DEFAULT_SHINGLE_SIZE = 3


def _get_shingles(text: str, k: int = DEFAULT_SHINGLE_SIZE) -> list:
    """Generate k-word shingles from text.

    Args:
        text: Input text.
        k: Number of words per shingle.

    Returns:
        List of shingle strings.
    """
    words = text.lower().split()
    if len(words) < k:
        return [text.lower()] if text.strip() else []
    return [' '.join(words[i:i + k]) for i in range(len(words) - k + 1)]


def _hash64(data: str) -> int:
    """Produce a 64-bit hash from a string using MD5 truncation."""
    digest = hashlib.md5(data.encode('utf-8')).digest()
    # Take first 8 bytes as a 64-bit unsigned integer (little-endian)
    return int.from_bytes(digest[:8], byteorder='little')


def simhash(text: str, hashbits: int = 64, shingle_size: int = DEFAULT_SHINGLE_SIZE) -> int:
    """Compute a 64-bit SimHash fingerprint for the given text.

    Args:
        text: Input text to fingerprint.
        hashbits: Number of bits in the fingerprint (default 64).
        shingle_size: Number of words per shingle.

    Returns:
        Integer fingerprint (0 to 2^hashbits - 1).
    """
    if not text or not text.strip():
        return 0

    v = [0] * hashbits
    shingles = _get_shingles(text, k=shingle_size)

    if not shingles:
        return 0

    for shingle in shingles:
        h = _hash64(shingle)
        for i in range(hashbits):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1

    fingerprint = 0
    for i in range(hashbits):
        if v[i] > 0:
            fingerprint |= (1 << i)

    return fingerprint


def hamming_distance(hash1: int, hash2: int) -> int:
    """Compute the Hamming distance between two integer hashes.

    The Hamming distance is the number of bit positions where the hashes differ.
    """
    return bin(hash1 ^ hash2).count('1')


def is_near_duplicate(hash1: int, hash2: int, threshold: int = 3) -> bool:
    """Check if two SimHash fingerprints indicate near-duplicate content.

    Args:
        hash1: First SimHash fingerprint.
        hash2: Second SimHash fingerprint.
        threshold: Maximum Hamming distance to consider near-duplicate.
                   Default 3 (out of 64 bits) is a strict threshold.

    Returns:
        True if the documents are likely near-duplicates.
    """
    return hamming_distance(hash1, hash2) <= threshold


def similarity_score(hash1: int, hash2: int, hashbits: int = 64) -> float:
    """Compute a similarity score between 0.0 and 1.0 from two SimHash values.

    Args:
        hash1: First SimHash fingerprint.
        hash2: Second SimHash fingerprint.
        hashbits: Number of bits in the fingerprint.

    Returns:
        Float between 0.0 (completely different) and 1.0 (identical).
    """
    distance = hamming_distance(hash1, hash2)
    return 1.0 - (distance / hashbits)
