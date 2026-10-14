"""Crawl trap detection using URL pattern fingerprinting.

Detects crawler traps (infinite calendars, faceted navigation, session IDs)
by converting URLs into structural templates and tracking frequency.
When a template exceeds a threshold, URLs matching it are flagged as traps.
"""

import logging
import re
from collections import defaultdict
from typing import Dict, Optional, Set
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# Default threshold: if a template has more than this many URLs, it's a trap
DEFAULT_TRAP_THRESHOLD = 20


def _url_to_template(url: str) -> str:
    """Convert a URL into a structural template for fingerprinting.

    Replaces:
    - Numeric path segments with {N}
    - UUID-like path segments with {UUID}
    - Query parameter values with {V}
    - Date-like segments (YYYY-MM-DD, YYYY/MM, etc.) with {DATE}

    Examples:
        /products/123/reviews?page=5  → /products/{N}/reviews?page={V}
        /blog/2024/03/my-post         → /blog/{DATE}/{DATE}/my-post
        /item/a1b2c3d4-e5f6-7890-...  → /item/{UUID}
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    # Process path segments
    segments = parsed.path.strip('/').split('/')
    template_segments = []
    for seg in segments:
        if not seg:
            continue
        # UUID pattern
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', seg, re.I):
            template_segments.append('{UUID}')
        # Date-like YYYY-MM-DD
        elif re.match(r'^\d{4}-\d{2}-\d{2}$', seg):
            template_segments.append('{DATE}')
        # Pure numeric
        elif re.match(r'^\d+$', seg):
            template_segments.append('{N}')
        # Hex hash (8+ chars)
        elif re.match(r'^[0-9a-f]{8,}$', seg, re.I) and len(seg) >= 8:
            template_segments.append('{HASH}')
        else:
            template_segments.append(seg)

    template_path = '/' + '/'.join(template_segments) if template_segments else '/'

    # Process query parameters — replace values with {V}, keep keys sorted
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        param_template = '&'.join(
            f'{k}={{V}}' for k in sorted(params.keys())
        )
        return f'{template_path}?{param_template}'

    return template_path


class TrapDetector:
    """Tracks URL templates and detects crawl traps.

    Usage:
        detector = TrapDetector(threshold=20)

        # Before enqueuing each URL:
        if not detector.is_trap(url):
            detector.record(url)
            frontier.append(url)
    """

    def __init__(self, threshold: int = DEFAULT_TRAP_THRESHOLD):
        self.threshold = threshold
        # template → count of URLs matching it
        self._template_counts: Dict[str, int] = defaultdict(int)
        # template → set of first N example URLs (for reporting)
        self._template_examples: Dict[str, list] = defaultdict(list)
        # Templates flagged as traps
        self._trapped_templates: Set[str] = set()

    def record(self, url: str) -> str:
        """Record a URL and return its template. Call AFTER deciding to crawl it."""
        template = _url_to_template(url)
        self._template_counts[template] += 1

        # Keep up to 5 examples per template for diagnostic reporting
        if len(self._template_examples[template]) < 5:
            self._template_examples[template].append(url)

        # Check if this template just crossed the threshold
        if self._template_counts[template] > self.threshold:
            if template not in self._trapped_templates:
                self._trapped_templates.add(template)
                logger.warning(
                    "Trap detected: template '%s' has %d URLs (threshold=%d). "
                    "Examples: %s",
                    template, self._template_counts[template], self.threshold,
                    self._template_examples[template][:3],
                )

        return template

    def is_trap(self, url: str) -> bool:
        """Check if a URL matches a known trap template.

        Returns True if the URL's template has been flagged as a trap.
        """
        template = _url_to_template(url)
        return template in self._trapped_templates

    def get_trap_report(self) -> list:
        """Return a summary of detected trap templates.

        Returns:
            List of dicts: [{template, count, examples}, ...]
        """
        report = []
        for template in sorted(self._trapped_templates):
            report.append({
                'template': template,
                'count': self._template_counts[template],
                'examples': self._template_examples[template][:5],
            })
        return report

    @property
    def trapped_template_count(self) -> int:
        """Number of distinct templates flagged as traps."""
        return len(self._trapped_templates)
