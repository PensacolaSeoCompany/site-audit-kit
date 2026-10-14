"""XML Sitemap discovery and parsing.

Handles:
- Regular sitemaps (<urlset>)
- Sitemap index files (<sitemapindex>) with recursive fetching
- Gzipped sitemaps (.xml.gz)
- Namespace-aware and namespace-free XML parsing
"""

import gzip
import logging
from typing import List, Optional
from xml.etree import ElementTree

import aiohttp

logger = logging.getLogger(__name__)

SITEMAP_NS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

# Maximum recursion depth for sitemap index → child sitemaps
MAX_SITEMAP_DEPTH = 3

# Maximum total URLs to collect across all sitemaps (safety limit)
MAX_TOTAL_URLS = 500_000


class SitemapURL:
    """A single URL entry from an XML sitemap."""

    __slots__ = ('loc', 'lastmod', 'changefreq', 'priority')

    def __init__(self, loc: str, lastmod: str = None,
                 changefreq: str = None, priority: float = None):
        self.loc = loc
        self.lastmod = lastmod
        self.changefreq = changefreq
        self.priority = priority

    def __repr__(self):
        return f"SitemapURL(loc={self.loc!r}, lastmod={self.lastmod!r})"


async def fetch_and_parse_sitemap(
    url: str,
    session: aiohttp.ClientSession,
    timeout: int = 30,
    _depth: int = 0,
) -> List[SitemapURL]:
    """Fetch and parse an XML sitemap. Handles sitemap index files and gzip.

    Args:
        url: The sitemap URL to fetch.
        session: An aiohttp ClientSession to use for fetching.
        timeout: Request timeout in seconds.
        _depth: Internal recursion depth counter (do not set manually).

    Returns:
        List of SitemapURL objects found in the sitemap (and children).
    """
    if _depth > MAX_SITEMAP_DEPTH:
        logger.warning("Sitemap recursion depth exceeded for %s", url)
        return []

    urls: List[SitemapURL] = []

    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=False,
        ) as resp:
            if resp.status != 200:
                logger.warning("Sitemap %s returned %d", url, resp.status)
                return urls

            content = await resp.read()

            # Handle gzipped sitemaps (.xml.gz files)
            # Note: only decompress if the URL ends with .gz — transport-level
            # Content-Encoding is already handled by aiohttp automatically.
            if url.endswith('.gz'):
                try:
                    content = gzip.decompress(content)
                except Exception as e:
                    logger.warning("Failed to decompress gzip sitemap %s: %s", url, e)
                    return urls

            text = content.decode('utf-8', errors='replace')

            try:
                root = ElementTree.fromstring(text)
            except ElementTree.ParseError as e:
                logger.warning("Failed to parse XML for sitemap %s: %s", url, e)
                return urls

            # Detect root tag (with or without namespace)
            root_tag = root.tag.lower()

            # Check if it's a sitemap index
            if 'sitemapindex' in root_tag:
                # Try with namespace first, then without
                sitemap_locs = root.findall('.//sm:sitemap/sm:loc', SITEMAP_NS)
                if not sitemap_locs:
                    sitemap_locs = root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap/{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                if not sitemap_locs:
                    # Try without any namespace
                    sitemap_locs = root.findall('.//sitemap/loc')

                logger.info("Sitemap index %s contains %d child sitemaps", url, len(sitemap_locs))

                for sitemap_loc in sitemap_locs:
                    if sitemap_loc.text and len(urls) < MAX_TOTAL_URLS:
                        child_url = sitemap_loc.text.strip()
                        child_urls = await fetch_and_parse_sitemap(
                            child_url, session, timeout, _depth + 1
                        )
                        urls.extend(child_urls)
            else:
                # Regular sitemap — parse <url> entries
                urls = _parse_urlset(root)

    except aiohttp.ClientError as e:
        logger.warning("HTTP error fetching sitemap %s: %s", url, e)
    except Exception as e:
        logger.warning("Error parsing sitemap %s: %s", url, e)

    logger.info("Parsed sitemap %s: %d URLs", url, len(urls))
    return urls


def _parse_urlset(root: ElementTree.Element) -> List[SitemapURL]:
    """Parse a <urlset> element into SitemapURL objects."""
    urls = []

    # Try with namespace
    url_elems = root.findall('.//sm:url', SITEMAP_NS)
    if not url_elems:
        url_elems = root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}url')
    if not url_elems:
        # No namespace
        url_elems = root.findall('.//url')

    for url_elem in url_elems:
        loc = _find_child_text(url_elem, 'loc')
        if not loc:
            continue

        lastmod = _find_child_text(url_elem, 'lastmod')
        changefreq = _find_child_text(url_elem, 'changefreq')
        priority_str = _find_child_text(url_elem, 'priority')

        priority = None
        if priority_str:
            try:
                priority = float(priority_str)
            except ValueError:
                pass

        urls.append(SitemapURL(
            loc=loc,
            lastmod=lastmod,
            changefreq=changefreq,
            priority=priority,
        ))

    return urls


def _find_child_text(parent: ElementTree.Element, tag_name: str) -> Optional[str]:
    """Find a child element by local name, with or without namespace."""
    # Try namespaced
    elem = parent.find(f'sm:{tag_name}', SITEMAP_NS)
    if elem is None:
        elem = parent.find(f'{{http://www.sitemaps.org/schemas/sitemap/0.9}}{tag_name}')
    if elem is None:
        elem = parent.find(tag_name)

    if elem is not None and elem.text:
        return elem.text.strip()
    return None
