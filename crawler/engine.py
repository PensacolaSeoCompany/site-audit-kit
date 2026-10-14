"""BFS crawl engine — the core orchestrator.

Coordinates URL frontier, fetching, parsing, filtering, and storage
into an async BFS crawl loop with configurable concurrency.
"""

import asyncio
import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

import aiohttp

from crawler.config import CrawlConfig
from crawler.fetcher import Fetcher, FetchResult
from crawler.parser import parse_page, ParseResult
from crawler.robots_parser import RobotsCache
from crawler.sitemap_parser import fetch_and_parse_sitemap
from crawler.storage import CrawlStorage
from crawler.trap_detector import TrapDetector
from crawler.url_filter import URLFilter, normalize_url, get_domain

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    """Summary of a completed crawl."""
    run_id: int = 0
    domain: str = ""
    seed_url: str = ""
    urls_crawled: int = 0
    urls_queued: int = 0
    urls_failed: int = 0
    duration_seconds: float = 0.0
    issues_detected: int = 0


@dataclass
class QueueItem:
    """A URL in the crawl frontier with its depth."""
    url: str
    depth: int


async def crawl(config: CrawlConfig) -> CrawlResult:
    """Execute a full BFS crawl from the seed URL.

    Args:
        config: Complete crawl configuration.

    Returns:
        CrawlResult with crawl statistics.
    """
    result = CrawlResult(
        domain=get_domain(config.seed_url),
        seed_url=config.seed_url,
    )
    start_time = time.monotonic()

    # Initialize components
    url_filter = URLFilter(config)
    robots = RobotsCache(user_agent=config.user_agent)
    fetcher = Fetcher(config)
    storage = CrawlStorage(config.mysql)
    trap_detector = TrapDetector(threshold=20)

    # Connect to the crawler database (SQLite by default, MySQL if configured)
    try:
        storage.connect()
    except Exception as exc:
        logger.error("Failed to connect to the crawler database: %s", exc)
        raise

    # Create crawl run
    run_id = storage.create_crawl_run(result.domain, config.seed_url, config.to_dict())
    result.run_id = run_id
    logger.info("Starting crawl run #%d for %s (max %d URLs, depth %d, concurrency %d)",
                run_id, config.seed_url, config.max_urls, config.max_depth, config.max_concurrent)

    # Start HTTP session
    await fetcher.start()

    # BFS state
    frontier: deque[QueueItem] = deque()
    seen: Set[str] = set()
    crawled_count = 0
    failed_count = 0

    # Seed the frontier
    seed_normalized = normalize_url(config.seed_url)
    frontier.append(QueueItem(url=seed_normalized, depth=0))
    seen.add(seed_normalized)

    # Fetch robots.txt before starting
    if config.respect_robots:
        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": config.user_agent},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as robots_session:
                await robots.fetch(config.seed_url, robots_session)

                # Parse sitemaps and seed the frontier with discovered URLs
                sitemap_urls = robots.get_sitemaps(config.seed_url)
                if sitemap_urls:
                    logger.info("Found %d sitemap(s) in robots.txt — parsing...", len(sitemap_urls))
                    for sitemap_url in sitemap_urls:
                        try:
                            sm_entries = await fetch_and_parse_sitemap(sitemap_url, robots_session)
                            seeded = 0
                            for entry in sm_entries:
                                normalized = normalize_url(entry.loc)
                                if normalized not in seen and url_filter.is_allowed(normalized, 0):
                                    seen.add(normalized)
                                    frontier.append(QueueItem(url=normalized, depth=0))
                                    seeded += 1
                            logger.info("Seeded %d URLs from sitemap %s", seeded, sitemap_url)
                        except Exception as sm_exc:
                            logger.warning("Failed to parse sitemap %s: %s", sitemap_url, sm_exc)
        except Exception as exc:
            logger.warning("Failed to fetch robots.txt: %s", exc)

    # Respect crawl-delay if set
    crawl_delay = robots.get_crawl_delay(config.seed_url)
    if crawl_delay and crawl_delay > 0:
        # Adjust rate limit to respect crawl-delay
        max_rps = 1.0 / crawl_delay
        if max_rps < config.rate_limit_rps:
            logger.info("Reducing rate limit from %.1f to %.1f rps (Crawl-delay: %.1fs)",
                        config.rate_limit_rps, max_rps, crawl_delay)
            # Rebuild rate limiter with lower rate
            fetcher._rate_limiter.rate = max_rps
            fetcher._rate_limiter.max_tokens = max_rps

    try:
        # Main BFS loop — process in batches of max_concurrent
        while frontier and crawled_count < config.max_urls:
            # Dequeue a batch
            batch: List[QueueItem] = []
            while frontier and len(batch) < config.max_concurrent and crawled_count + len(batch) < config.max_urls:
                item = frontier.popleft()
                batch.append(item)

            if not batch:
                break

            # Fetch all in parallel
            tasks = [_process_url(item, fetcher, robots, url_filter, storage,
                                  config, run_id, seen, frontier, trap_detector)
                     for item in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    logger.error("Worker exception: %s", r)
                    failed_count += 1
                elif r is True:
                    crawled_count += 1
                else:
                    failed_count += 1

            # Progress report every 10 URLs
            if crawled_count % 10 == 0 or crawled_count == 1:
                elapsed = time.monotonic() - start_time
                print(f"  [{crawled_count}/{config.max_urls}] "
                      f"crawled={crawled_count} queued={len(frontier)} "
                      f"failed={failed_count} elapsed={elapsed:.1f}s",
                      file=sys.stderr)

    except KeyboardInterrupt:
        logger.warning("Crawl interrupted by user after %d URLs", crawled_count)
        print(f"\nInterrupted after {crawled_count} URLs.", file=sys.stderr)

    except Exception as exc:
        logger.error("Crawl error: %s", exc, exc_info=True)

    finally:
        # Finalize
        duration = time.monotonic() - start_time
        result.urls_crawled = crawled_count
        result.urls_queued = len(frontier)
        result.urls_failed = failed_count
        result.duration_seconds = duration

        # Detect issues
        try:
            issues = storage.detect_issues(run_id)
            result.issues_detected = issues
        except Exception as exc:
            logger.error("Issue detection failed: %s", exc)

        # Update crawl run
        try:
            storage.update_crawl_run(run_id, crawled_count, len(frontier), int(duration))
        except Exception as exc:
            logger.error("Failed to update crawl_run: %s", exc)

        # Log trap detection report
        if trap_detector.trapped_template_count > 0:
            logger.warning("Trap detector flagged %d URL templates as crawl traps",
                           trap_detector.trapped_template_count)
            for trap in trap_detector.get_trap_report():
                logger.warning("  Trap: %s (%d URLs)", trap['template'], trap['count'])

        # Cleanup
        await fetcher.close()
        storage.close()

        logger.info("Crawl complete: %d URLs crawled, %d queued, %d failed, %.1fs",
                     crawled_count, len(frontier), failed_count, duration)

    return result


async def _process_url(
    item: QueueItem,
    fetcher: Fetcher,
    robots: RobotsCache,
    url_filter: URLFilter,
    storage: CrawlStorage,
    config: CrawlConfig,
    run_id: int,
    seen: Set[str],
    frontier: deque,
    trap_detector: TrapDetector = None,
) -> bool:
    """Fetch, parse, store, and enqueue discovered links for a single URL.

    Returns True if the URL was successfully crawled, False otherwise.
    """
    url = item.url
    depth = item.depth

    # Check robots.txt
    if config.respect_robots and not robots.is_allowed(url):
        logger.debug("Blocked by robots.txt: %s", url)
        return False

    # Fetch
    fetch_result: FetchResult = await fetcher.fetch(url)

    if fetch_result.error:
        logger.debug("Fetch error for %s: %s", url, fetch_result.error)
        # Store error URL with minimal data
        error_result = ParseResult(
            address=url,
            status_code=0,
            status="Connection Error",
            indexability="Non-Indexable",
            indexability_status=fetch_result.error,
            response_time=fetch_result.response_time,
            crawl_depth=depth,
        )
        storage.store_url(run_id, error_result)
        return False

    # Parse the page
    try:
        parse_result = parse_page(
            url=url,
            html=fetch_result.body_text,
            status_code=fetch_result.status_code,
            headers=fetch_result.headers,
            response_time=fetch_result.response_time,
            size_bytes=fetch_result.size_bytes,
            crawl_depth=depth,
            redirect_chain=fetch_result.redirect_chain,
            http_version=fetch_result.http_version,
            content_type=fetch_result.content_type,
        )
    except Exception as exc:
        logger.error("Parse error for %s: %s", url, exc)
        # Store the URL with fetch data but no parse data
        error_result = ParseResult(
            address=url,
            status_code=fetch_result.status_code,
            status="Parse Error",
            content_type=fetch_result.content_type,
            response_time=fetch_result.response_time,
            size_bytes=fetch_result.size_bytes,
            crawl_depth=depth,
            http_version=fetch_result.http_version,
        )
        storage.store_url(run_id, error_result)
        return False

    # If the final URL differs from the requested URL (redirect), update address
    if fetch_result.final_url != url:
        parse_result.final_url = fetch_result.final_url
        parse_result.redirect_url = url
        if fetch_result.redirect_chain:
            parse_result.redirect_type = str(fetch_result.redirect_chain[0][1])

    # Store the parsed URL
    storage.store_url(run_id, parse_result)

    # Store links (only hyperlinks for crawl_links table)
    if parse_result.links:
        storage.store_links(run_id, url, parse_result.links)

    # Record this URL in the trap detector
    if trap_detector:
        trap_detector.record(url)

    # Enqueue discovered hyperlinks
    for link in parse_result.links:
        if link.link_type != "hyperlink":
            continue

        # Skip nofollow links unless configured to follow them
        if link.follow_status == "nofollow" and not config.follow_nofollow:
            continue

        normalized = normalize_url(link.url)
        if normalized in seen:
            continue

        if url_filter.is_allowed(normalized, depth + 1):
            # Check trap detector before enqueuing
            if trap_detector and trap_detector.is_trap(normalized):
                logger.debug("Skipping trap URL: %s", normalized)
                continue
            seen.add(normalized)
            frontier.append(QueueItem(url=normalized, depth=depth + 1))

    return True
