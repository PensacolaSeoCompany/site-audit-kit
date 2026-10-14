"""robots.txt fetching, parsing, caching, and rule evaluation."""

import logging
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp

logger = logging.getLogger(__name__)


class RobotsCache:
    """Fetches and caches robots.txt per domain, checks URL allowance."""

    def __init__(self, user_agent: str, timeout: int = 10):
        self.user_agent = user_agent
        self.timeout = timeout
        self._cache: dict[str, RobotFileParser] = {}
        self._sitemaps: dict[str, list[str]] = {}
        self._crawl_delays: dict[str, float | None] = {}
        self._fetch_times: dict[str, float] = {}

    def _get_origin(self, url: str) -> str:
        """Extract scheme://host from a URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def fetch(self, url: str, session: aiohttp.ClientSession) -> None:
        """Fetch and parse robots.txt for the domain of the given URL."""
        origin = self._get_origin(url)
        if origin in self._cache:
            return

        robots_url = f"{origin}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        sitemaps = []

        try:
            async with session.get(
                robots_url,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={"User-Agent": self.user_agent},
                ssl=False,
            ) as resp:
                if resp.status == 200:
                    text = await resp.text(errors="replace")
                    rp.parse(text.splitlines())

                    # Extract Sitemap directives manually (robotparser doesn't expose them reliably)
                    for line in text.splitlines():
                        stripped = line.strip()
                        if stripped.lower().startswith("sitemap:"):
                            sitemap_url = stripped.split(":", 1)[1].strip()
                            if sitemap_url:
                                sitemaps.append(sitemap_url)

                    # Extract crawl-delay
                    crawl_delay = rp.crawl_delay(self.user_agent)
                    self._crawl_delays[origin] = crawl_delay

                    logger.info("Fetched robots.txt for %s (%d sitemaps found)", origin, len(sitemaps))
                elif resp.status in (403, 401):
                    # If robots.txt is forbidden, assume everything is disallowed
                    rp.parse(["User-agent: *", "Disallow: /"])
                    logger.warning("robots.txt returned %d for %s — assuming disallow all", resp.status, origin)
                else:
                    # 404 or other — assume everything is allowed
                    rp.parse([])
                    logger.info("robots.txt returned %d for %s — assuming allow all", resp.status, origin)

        except Exception as exc:
            # On error, assume everything is allowed (standard practice)
            rp.parse([])
            logger.warning("Failed to fetch robots.txt for %s: %s — assuming allow all", origin, exc)

        self._cache[origin] = rp
        self._sitemaps[origin] = sitemaps
        self._fetch_times[origin] = time.time()

    def is_allowed(self, url: str) -> bool:
        """Check if a URL is allowed by robots.txt rules."""
        origin = self._get_origin(url)
        rp = self._cache.get(origin)
        if rp is None:
            # Not fetched yet — allow (caller should fetch first)
            return True
        return rp.can_fetch(self.user_agent, url)

    def get_sitemaps(self, url: str) -> list[str]:
        """Return sitemap URLs discovered from robots.txt for this domain."""
        origin = self._get_origin(url)
        return self._sitemaps.get(origin, [])

    def get_crawl_delay(self, url: str) -> float | None:
        """Return the Crawl-delay directive for this domain, or None."""
        origin = self._get_origin(url)
        return self._crawl_delays.get(origin)
