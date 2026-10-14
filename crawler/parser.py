"""HTML parser extracting all 72 SF-equivalent columns from a page.

Extracts: links, meta tags, headings, content metrics, structured data,
security headers, images, carbon footprint, and indexability signals.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment

logger = logging.getLogger(__name__)

# ── New Batch 2/3 modules ──
try:
    from crawler.extractor import extract_main_content, HAS_TRAFILATURA
except ImportError:
    HAS_TRAFILATURA = False

try:
    from crawler.pixel_width import title_pixel_width, meta_desc_pixel_width, HAS_PILLOW
except ImportError:
    HAS_PILLOW = False

# Flat fallback multiplier — only used when pixel_width module is unavailable
CHAR_PIXEL_WIDTH = 7.4

# Carbon constants — Sustainable Web Design model v4
CO2_PER_BYTE = 0.000000442  # kg CO2 per byte
CO2_TO_MG = 1_000_000  # kg to mg


@dataclass
class ExtractedLink:
    """A single link extracted from a page."""
    url: str
    anchor_text: str = ""
    alt_text: str = ""
    rel_attribute: str = ""
    link_type: str = "hyperlink"  # hyperlink, image, css, js, canonical, alternate, next, prev, iframe, refresh
    link_position: str = "unknown"  # navigation, header, footer, sidebar, main_content, unknown
    follow_status: str = "follow"  # follow, nofollow


@dataclass
class StructuredDataBlock:
    """A single JSON-LD block found on the page."""
    raw_json: str = ""
    parsed: Optional[Dict[str, Any]] = None
    types: List[str] = field(default_factory=list)
    is_valid: bool = False
    error: Optional[str] = None


@dataclass
class ParseResult:
    """Complete parse result for a single page — maps to crawl_urls columns."""

    # Identity
    address: str = ""
    final_url: str = ""
    content_type: str = ""
    status_code: int = 0
    status: str = ""  # OK, Redirect, Client Error, Server Error, etc.

    # Indexability
    indexability: str = "Indexable"  # Indexable, Non-Indexable
    indexability_status: str = ""  # reason if non-indexable

    # Title
    title_1: str = ""
    title_1_length: int = 0
    title_1_pixel_width: int = 0

    # Meta description
    meta_description_1: str = ""
    meta_description_1_length: int = 0
    meta_description_1_pixel_width: int = 0

    # Meta keywords
    meta_keywords_1: str = ""

    # Headings
    h1_1: str = ""
    h1_1_length: int = 0
    h2_1: str = ""
    h2_1_length: int = 0
    h2_2: str = ""
    h2_2_length: int = 0

    # Robots directives
    meta_robots_1: str = ""
    x_robots_tag_1: str = ""

    # Canonical and pagination
    canonical_1: str = ""
    rel_next_1: str = ""
    rel_prev_1: str = ""

    # Size and performance
    size_bytes: int = 0
    response_time: float = 0.0

    # Carbon
    co2_mg: float = 0.0
    carbon_rating: str = ""

    # Content metrics
    word_count: int = 0
    sentence_count: int = 0
    avg_words_per_sentence: float = 0.0
    flesch_reading_ease: float = 0.0
    readability: str = ""
    text_ratio: float = 0.0
    language: str = ""
    content_hash: str = ""

    # Depth
    crawl_depth: int = 0
    folder_depth: int = 0

    # Links (counts — actual link objects stored separately)
    outlinks: int = 0
    unique_outlinks: int = 0
    external_outlinks: int = 0
    unique_external_outlinks: int = 0
    inlinks: int = 0
    unique_inlinks: int = 0

    # Redirect
    redirect_url: str = ""
    redirect_type: str = ""

    # HTTP
    http_version: str = ""
    last_modified: str = ""

    # Structured data types found
    structured_data_types: str = ""

    # All extracted links (not stored in crawl_urls, stored in crawl_links)
    links: List[ExtractedLink] = field(default_factory=list)

    # Structured data blocks (for validation reporting)
    structured_data_blocks: List[StructuredDataBlock] = field(default_factory=list)


def _get_status_label(code: int) -> str:
    """Map status code to a human-readable status label."""
    if code == 0:
        return "Connection Error"
    if 200 <= code < 300:
        return "OK"
    if 300 <= code < 400:
        return "Redirect"
    if 400 <= code < 500:
        return "Client Error"
    if 500 <= code < 600:
        return "Server Error"
    return "Unknown"


def _detect_link_position(element) -> str:
    """Walk up the DOM to classify a link's position."""
    tag_map = {
        "nav": "navigation",
        "header": "header",
        "footer": "footer",
        "aside": "sidebar",
        "main": "main_content",
        "article": "main_content",
    }
    current = element
    for _ in range(20):  # Max depth walk
        current = current.parent
        if current is None or current.name is None:
            break
        if current.name in tag_map:
            return tag_map[current.name]
        # Check role attribute
        role = current.get("role", "").lower()
        if role == "navigation":
            return "navigation"
        if role == "banner":
            return "header"
        if role == "contentinfo":
            return "footer"
        if role == "complementary":
            return "sidebar"
        if role == "main":
            return "main_content"
    return "unknown"


def _get_follow_status(rel: str, meta_robots: str) -> str:
    """Determine follow status from rel attribute and meta robots."""
    if "nofollow" in rel.lower():
        return "nofollow"
    if "nofollow" in meta_robots.lower():
        return "nofollow"
    return "follow"


def _compute_carbon(size_bytes: int) -> tuple:
    """Compute CO2 emissions and rating from response size.

    Returns: (co2_mg, carbon_rating)
    """
    co2_mg = size_bytes * CO2_PER_BYTE * CO2_TO_MG
    if co2_mg < 0.5:
        rating = "A+"
    elif co2_mg < 1.0:
        rating = "A"
    elif co2_mg < 2.0:
        rating = "B"
    elif co2_mg < 5.0:
        rating = "C"
    elif co2_mg < 10.0:
        rating = "D"
    elif co2_mg < 20.0:
        rating = "E"
    else:
        rating = "F"
    return round(co2_mg, 3), rating


def _classify_readability(score: float) -> str:
    """Classify Flesch reading ease into a human-readable label."""
    if score >= 90:
        return "Very Easy"
    if score >= 80:
        return "Easy"
    if score >= 70:
        return "Fairly Easy"
    if score >= 60:
        return "Standard"
    if score >= 50:
        return "Fairly Difficult"
    if score >= 30:
        return "Difficult"
    return "Very Difficult"


def _extract_visible_text(soup: BeautifulSoup) -> str:
    """Extract visible text from HTML, removing scripts, styles, nav, footer."""
    # Remove non-content elements
    for tag_name in ("script", "style", "noscript", "svg", "path"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Get text from body if available, otherwise whole soup
    body = soup.find("body")
    target = body if body else soup
    text = target.get_text(separator=" ", strip=True)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _count_sentences(text: str) -> int:
    """Count sentences by splitting on sentence-ending punctuation."""
    if not text:
        return 0
    sentences = re.split(r'[.!?]+', text)
    # Filter out empty strings from split
    return len([s for s in sentences if s.strip()])


def parse_page(url: str, html: str, status_code: int, headers: dict,
               response_time: float, size_bytes: int, crawl_depth: int,
               redirect_chain: list, http_version: str,
               content_type: str) -> ParseResult:
    """Parse an HTML page and extract all audit columns.

    Args:
        url: The original requested URL.
        html: The response body as text.
        status_code: HTTP status code.
        headers: Response headers dict.
        response_time: Time to fetch in seconds.
        size_bytes: Size of response body in bytes.
        crawl_depth: Link distance from seed URL.
        redirect_chain: List of (url, status) tuples for redirect hops.
        http_version: HTTP version string.
        content_type: Content-Type header value.

    Returns:
        ParseResult with all extracted fields.
    """
    result = ParseResult()
    result.address = url
    result.status_code = status_code
    result.status = _get_status_label(status_code)
    result.content_type = content_type
    result.response_time = response_time
    result.size_bytes = size_bytes
    result.crawl_depth = crawl_depth
    result.http_version = http_version

    # Folder depth
    try:
        path = urlparse(url).path or "/"
        path = path.rstrip("/")
        result.folder_depth = max(0, path.count("/") - 1) if path else 0
    except Exception:
        result.folder_depth = 0

    # Carbon
    result.co2_mg, result.carbon_rating = _compute_carbon(size_bytes)

    # Redirect info
    if redirect_chain:
        result.redirect_url = redirect_chain[-1][0] if redirect_chain else ""
        result.redirect_type = str(redirect_chain[-1][1]) if redirect_chain else ""

    # X-Robots-Tag from headers
    x_robots = headers.get("X-Robots-Tag", "")
    result.x_robots_tag_1 = x_robots

    # Last-Modified from headers
    result.last_modified = headers.get("Last-Modified", "")

    # If not HTML, return early with minimal data
    if not html or "html" not in content_type.lower():
        result.indexability = "Non-Indexable"
        result.indexability_status = "Non-HTML Content"
        return result

    # Parse HTML
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as exc:
        logger.warning("Failed to parse HTML for %s: %s", url, exc)
        soup = BeautifulSoup(html, "html.parser")

    # ── Title ──
    title_tag = soup.find("title")
    if title_tag:
        result.title_1 = title_tag.get_text(strip=True)
        result.title_1_length = len(result.title_1)
        try:
            result.title_1_pixel_width = title_pixel_width(result.title_1)
        except Exception:
            result.title_1_pixel_width = int(result.title_1_length * CHAR_PIXEL_WIDTH)

    # ── Meta Description ──
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta_desc and meta_desc.get("content"):
        result.meta_description_1 = meta_desc["content"].strip()
        result.meta_description_1_length = len(result.meta_description_1)
        try:
            result.meta_description_1_pixel_width = meta_desc_pixel_width(result.meta_description_1)
        except Exception:
            result.meta_description_1_pixel_width = int(result.meta_description_1_length * CHAR_PIXEL_WIDTH)

    # ── Meta Keywords ──
    meta_kw = soup.find("meta", attrs={"name": re.compile(r"^keywords$", re.I)})
    if meta_kw and meta_kw.get("content"):
        result.meta_keywords_1 = meta_kw["content"].strip()

    # ── Meta Robots ──
    meta_robots = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    if meta_robots and meta_robots.get("content"):
        result.meta_robots_1 = meta_robots["content"].strip()

    # Combine robots directives for indexability check
    all_robots = f"{result.meta_robots_1} {result.x_robots_tag_1}".lower()

    # ── Headings ──
    h1_tags = soup.find_all("h1")
    if h1_tags:
        result.h1_1 = h1_tags[0].get_text(strip=True)
        result.h1_1_length = len(result.h1_1)

    h2_tags = soup.find_all("h2")
    if h2_tags:
        result.h2_1 = h2_tags[0].get_text(strip=True)
        result.h2_1_length = len(result.h2_1)
    if len(h2_tags) > 1:
        result.h2_2 = h2_tags[1].get_text(strip=True)
        result.h2_2_length = len(result.h2_2)

    # ── Canonical ──
    canonical_tag = soup.find("link", attrs={"rel": "canonical"})
    if canonical_tag and canonical_tag.get("href"):
        result.canonical_1 = urljoin(url, canonical_tag["href"].strip())

    # ── Rel Next / Prev ──
    next_tag = soup.find("link", attrs={"rel": "next"})
    if next_tag and next_tag.get("href"):
        result.rel_next_1 = urljoin(url, next_tag["href"].strip())
    prev_tag = soup.find("link", attrs={"rel": "prev"})
    if prev_tag and prev_tag.get("href"):
        result.rel_prev_1 = urljoin(url, prev_tag["href"].strip())

    # ── Content Extraction ──
    # Use trafilatura for boilerplate-free extraction when available,
    # fall back to the naive BeautifulSoup method otherwise.
    visible_text = ""
    if HAS_TRAFILATURA:
        try:
            extracted = extract_main_content(html, url=url)
            visible_text = extracted.get('text', '') or ''
        except Exception as exc:
            logger.debug("trafilatura extraction failed for %s, using fallback: %s", url, exc)
            visible_text = _extract_visible_text(BeautifulSoup(html, "lxml"))
    else:
        visible_text = _extract_visible_text(BeautifulSoup(html, "lxml"))

    if visible_text:
        words = visible_text.split()
        result.word_count = len(words)
        result.sentence_count = _count_sentences(visible_text)
        if result.sentence_count > 0:
            result.avg_words_per_sentence = round(result.word_count / result.sentence_count, 3)

        # Text ratio
        if len(html) > 0:
            result.text_ratio = round(len(visible_text) / len(html) * 100, 3)

        # Content hash
        result.content_hash = hashlib.md5(visible_text.encode("utf-8")).hexdigest()

        # Readability (textstat)
        try:
            import textstat
            if result.word_count >= 10:
                result.flesch_reading_ease = round(textstat.flesch_reading_ease(visible_text), 3)
                result.readability = _classify_readability(result.flesch_reading_ease)
        except Exception as exc:
            logger.debug("textstat failed for %s: %s", url, exc)

        # Language detection
        try:
            from langdetect import detect
            if result.word_count >= 20:
                result.language = detect(visible_text)
        except Exception:
            pass

    # ── Indexability ──
    if "noindex" in all_robots:
        result.indexability = "Non-Indexable"
        result.indexability_status = "noindex"
    elif result.canonical_1 and result.canonical_1 != url:
        # Canonical points elsewhere
        result.indexability = "Non-Indexable"
        result.indexability_status = "Canonicalised"
    elif status_code != 200:
        result.indexability = "Non-Indexable"
        result.indexability_status = f"HTTP {status_code}"
    else:
        result.indexability = "Indexable"
        result.indexability_status = ""

    # ── Links Extraction ──
    links: List[ExtractedLink] = []
    seen_link_urls = set()
    internal_out = set()
    external_out = set()
    seed_domain = urlparse(url).netloc.lower().split(":")[0]

    # <a href>
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "data:")):
            continue
        resolved = urljoin(url, href)
        rel = " ".join(a_tag.get("rel", []))
        anchor = a_tag.get_text(strip=True)[:500]
        position = _detect_link_position(a_tag)
        follow = _get_follow_status(rel, result.meta_robots_1)

        link = ExtractedLink(
            url=resolved, anchor_text=anchor, rel_attribute=rel,
            link_type="hyperlink", link_position=position, follow_status=follow,
        )
        links.append(link)

        # Classify internal/external
        target_domain = urlparse(resolved).netloc.lower().split(":")[0]
        if target_domain == seed_domain:
            internal_out.add(resolved)
        else:
            external_out.add(resolved)

    # <img src>
    for img_tag in soup.find_all("img", src=True):
        src = urljoin(url, img_tag["src"].strip())
        alt = img_tag.get("alt", "")
        links.append(ExtractedLink(
            url=src, alt_text=alt, link_type="image",
            link_position=_detect_link_position(img_tag),
        ))

    # <img srcset>
    for img_tag in soup.find_all("img", srcset=True):
        srcset = img_tag.get("srcset", "")
        for part in srcset.split(","):
            part = part.strip()
            if not part:
                continue
            src_url = part.split()[0]
            if src_url:
                resolved = urljoin(url, src_url)
                links.append(ExtractedLink(
                    url=resolved, alt_text=img_tag.get("alt", ""),
                    link_type="image", link_position=_detect_link_position(img_tag),
                ))

    # <link rel=alternate hreflang>
    for link_tag in soup.find_all("link", attrs={"rel": "alternate", "hreflang": True}):
        href = link_tag.get("href", "").strip()
        if href:
            links.append(ExtractedLink(
                url=urljoin(url, href), link_type="alternate",
                rel_attribute=f"alternate hreflang={link_tag.get('hreflang', '')}",
            ))

    # <link rel=stylesheet>
    for link_tag in soup.find_all("link", attrs={"rel": "stylesheet"}):
        href = link_tag.get("href", "").strip()
        if href:
            links.append(ExtractedLink(url=urljoin(url, href), link_type="css"))

    # <script src>
    for script_tag in soup.find_all("script", src=True):
        src = script_tag.get("src", "").strip()
        if src:
            links.append(ExtractedLink(url=urljoin(url, src), link_type="js"))

    # <iframe src>
    for iframe_tag in soup.find_all("iframe", src=True):
        src = iframe_tag.get("src", "").strip()
        if src:
            links.append(ExtractedLink(
                url=urljoin(url, src), link_type="iframe",
                link_position=_detect_link_position(iframe_tag),
            ))

    # <meta http-equiv=refresh>
    refresh_meta = soup.find("meta", attrs={"http-equiv": re.compile(r"^refresh$", re.I)})
    if refresh_meta and refresh_meta.get("content"):
        content = refresh_meta["content"]
        match = re.search(r"url\s*=\s*(.+)", content, re.I)
        if match:
            refresh_url = match.group(1).strip().strip("'\"")
            links.append(ExtractedLink(
                url=urljoin(url, refresh_url), link_type="refresh",
            ))

    result.links = links
    result.outlinks = len(links)
    unique_urls = set(l.url for l in links if l.link_type == "hyperlink")
    result.unique_outlinks = len(unique_urls)
    result.external_outlinks = len(external_out)
    result.unique_external_outlinks = len(external_out)

    # ── Structured Data (JSON-LD) ──
    sd_types = []
    sd_blocks = []
    for script_tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script_tag.string or ""
        block = StructuredDataBlock(raw_json=raw)
        try:
            parsed = json.loads(raw)
            block.parsed = parsed
            block.is_valid = True

            # Extract @type
            if isinstance(parsed, dict):
                t = parsed.get("@type", "")
                if isinstance(t, list):
                    block.types = t
                    sd_types.extend(t)
                elif t:
                    block.types = [t]
                    sd_types.append(t)
                # Check @graph
                if "@graph" in parsed and isinstance(parsed["@graph"], list):
                    for item in parsed["@graph"]:
                        if isinstance(item, dict) and "@type" in item:
                            graph_type = item["@type"]
                            if isinstance(graph_type, list):
                                block.types.extend(graph_type)
                                sd_types.extend(graph_type)
                            else:
                                block.types.append(graph_type)
                                sd_types.append(graph_type)
            elif isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        t = item.get("@type", "")
                        if isinstance(t, list):
                            block.types.extend(t)
                            sd_types.extend(t)
                        elif t:
                            block.types.append(t)
                            sd_types.append(t)
        except (json.JSONDecodeError, TypeError) as exc:
            block.is_valid = False
            block.error = str(exc)

        sd_blocks.append(block)

    result.structured_data_blocks = sd_blocks
    result.structured_data_types = ", ".join(sorted(set(sd_types))) if sd_types else ""

    return result
