"""SERP pixel width calculation using actual font metrics.

Google uses Arial/Roboto ~20px for desktop SERP titles, ~14px for descriptions.
This module loads a system font (Liberation Sans ≈ Arial) and measures text
widths via Pillow's ImageFont, giving accurate per-character width instead
of a flat multiplier.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Try to import Pillow; fall back to character-based estimation
try:
    from PIL import ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    logger.info("Pillow not installed — falling back to character-based pixel width")

# Font cache keyed by size
_font_cache = {}

# Flat fallback multiplier when no font is available
_FALLBACK_CHAR_WIDTH = 7.4


def _get_font(size: int = 20):
    """Load and cache a TrueType font at the given size."""
    if size in _font_cache:
        return _font_cache[size]

    if not HAS_PILLOW:
        _font_cache[size] = None
        return None

    font_paths = [
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
    ]

    for path in font_paths:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[size] = font
                return font
            except Exception:
                continue

    # Last resort: Pillow default bitmap font
    try:
        font = ImageFont.load_default()
        _font_cache[size] = font
        return font
    except Exception:
        _font_cache[size] = None
        return None


def calculate_pixel_width(text: str, font_size: int = 20) -> int:
    """Calculate the pixel width of text as rendered in SERP.

    Args:
        text: The text to measure.
        font_size: Font size in pixels (20 for desktop SERP title).

    Returns:
        Width in pixels.
    """
    if not text:
        return 0

    font = _get_font(font_size)
    if font is None:
        # Pure fallback: flat character width
        return int(round(len(text) * _FALLBACK_CHAR_WIDTH))

    try:
        # getlength gives a float width — preferred method
        width = font.getlength(text)
        return int(round(width))
    except AttributeError:
        pass

    try:
        # Older Pillow: use getbbox
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0] if bbox else int(round(len(text) * _FALLBACK_CHAR_WIDTH))
    except Exception:
        return int(round(len(text) * _FALLBACK_CHAR_WIDTH))


def title_pixel_width(text: str) -> int:
    """SERP title pixel width (Google desktop uses ~20px Arial/Roboto)."""
    return calculate_pixel_width(text, 20)


def meta_desc_pixel_width(text: str) -> int:
    """SERP meta description pixel width (Google uses ~14px)."""
    return calculate_pixel_width(text, 14)
