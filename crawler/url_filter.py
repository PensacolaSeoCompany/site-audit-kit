"""URL filtering: normalization, domain scoping, pattern matching, tracking param stripping."""

import logging
import re
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

from crawler.config import CrawlConfig

logger = logging.getLogger(__name__)

# Tracking parameters to strip
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "utm_creative_format", "utm_marketing_tactic",
    "fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    "msclkid", "twclid", "li_fat_id",
    "mc_cid", "mc_eid",
    "_ga", "_gl", "_hsenc", "_hsmi",
    "yclid", "ymclid",
    "ref", "source", "srsltid",
}


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    - Lowercase scheme and host
    - Remove fragment
    - Sort query parameters
    - Strip trailing slash from path (unless path is just /)
    - Strip known tracking parameters
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Remove default ports
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    # Normalize path — strip trailing slash unless root
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Sort and filter query params
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in sorted(query_params.items())
        if k.lower() not in TRACKING_PARAMS
    }
    query = urlencode(filtered, doseq=True) if filtered else ""

    # Drop fragment entirely
    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def get_domain(url: str) -> str:
    """Extract the registerable domain (host) from a URL."""
    try:
        return urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""


def get_folder_depth(url: str) -> int:
    """Count folder depth (number of / separators in path, minus the leading one)."""
    try:
        path = urlparse(url).path or "/"
        # Remove trailing slash for counting
        path = path.rstrip("/")
        if not path or path == "":
            return 0
        return path.count("/") - (1 if path.startswith("/") else 0)
    except Exception:
        return 0


class URLFilter:
    """URL filter enforcing domain scope, patterns, depth, and length limits."""

    def __init__(self, config: CrawlConfig):
        self.config = config
        self.seed_domain = get_domain(config.seed_url)
        self._include_re = config.get_compiled_includes()
        self._exclude_re = config.get_compiled_excludes()

    def is_allowed(self, url: str, depth: int) -> bool:
        """Check whether a discovered URL should be enqueued for crawling."""
        # Must be http(s)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False

        # URL length
        if len(url) > self.config.max_url_length:
            return False

        # Depth check
        if depth > self.config.max_depth:
            return False

        # Domain scope
        url_domain = get_domain(url)
        if not self.config.crawl_external:
            if self.config.allow_subdomains:
                if not url_domain.endswith(self.seed_domain):
                    return False
            else:
                if url_domain != self.seed_domain:
                    return False

        # Exclude patterns (checked first — exclusions take priority)
        for pat in self._exclude_re:
            if pat.search(url):
                return False

        # Include patterns (if any are set, URL must match at least one)
        if self._include_re:
            if not any(pat.search(url) for pat in self._include_re):
                return False

        # Skip common non-page extensions
        path_lower = parsed.path.lower()
        skip_exts = (
            ".pdf", ".zip", ".gz", ".tar", ".rar", ".7z",
            ".exe", ".dmg", ".iso", ".bin",
            ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm",
            ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".css", ".js", ".json", ".xml", ".rss", ".atom",
            ".woff", ".woff2", ".ttf", ".eot", ".otf",
        )
        if any(path_lower.endswith(ext) for ext in skip_exts):
            return False

        return True
