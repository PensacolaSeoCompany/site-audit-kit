"""Content extraction using trafilatura for boilerplate removal.

Replaces the naive _extract_visible_text() approach with trafilatura's
ML-based content extraction that strips nav/header/footer/sidebar boilerplate.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Try to import trafilatura; fall back gracefully
try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False
    logger.info("trafilatura not installed — falling back to naive text extraction")


def extract_main_content(html: str, url: str = '') -> dict:
    """Extract main content from HTML, removing nav/header/footer/sidebar.

    Uses trafilatura with precision-first extraction, falling back to
    recall-favoring mode, then to an empty string if all else fails.

    Args:
        html: Raw HTML string.
        url: The page URL (helps trafilatura with relative link resolution).

    Returns:
        dict with keys: text, word_count, sentence_count, avg_words_per_sentence
    """
    text = ''

    if HAS_TRAFILATURA and html:
        try:
            text = trafilatura.extract(
                html, url=url,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_precision=True,
            )
            if not text:
                # Fallback: favor recall over precision
                text = trafilatura.extract(html, url=url, favor_recall=True)
            if not text:
                text = ''
        except Exception as e:
            logger.warning("trafilatura failed for %s: %s", url, e)
            text = ''

    words = text.split() if text else []
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()] if text else []

    return {
        'text': text,
        'word_count': len(words),
        'sentence_count': len(sentences),
        'avg_words_per_sentence': round(len(words) / max(len(sentences), 1), 3),
    }
