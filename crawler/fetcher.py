"""Async HTTP fetcher with rate limiting, retries, and redirect chain recording."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from urllib.parse import urlparse

import aiohttp

from crawler.config import CrawlConfig

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result of a single HTTP fetch."""
    url: str
    final_url: str
    status_code: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    body_text: str = ""
    response_time: float = 0.0
    redirect_chain: List[Tuple[str, int]] = field(default_factory=list)
    content_type: str = ""
    http_version: str = ""
    error: Optional[str] = None
    size_bytes: int = 0


class TokenBucket:
    """Token bucket rate limiter for async use."""

    def __init__(self, rate: float):
        """
        Args:
            rate: Maximum requests per second.
        """
        self.rate = rate
        self.tokens = rate
        self.max_tokens = rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
                self._last_refill = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return

            # Wait a fraction of the refill interval before retrying
            await asyncio.sleep(1.0 / self.rate)


class Fetcher:
    """Async HTTP client with connection pooling, rate limiting, and retries."""

    def __init__(self, config: CrawlConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._rate_limiter = TokenBucket(config.rate_limit_rps)

    async def start(self) -> None:
        """Create the aiohttp session."""
        connector = aiohttp.TCPConnector(
            limit=self.config.max_concurrent * 2,
            limit_per_host=self.config.max_concurrent,
            ttl_dns_cache=300,
            ssl=False,
        )
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": self.config.user_agent},
        )

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def fetch(self, url: str) -> FetchResult:
        """Fetch a URL with rate limiting, retries, and redirect tracking.

        Follows redirects manually to record the full chain (max 10 hops).
        Retries on 5xx errors with exponential backoff.
        """
        async with self._semaphore:
            return await self._fetch_with_retry(url)

    async def _fetch_with_retry(self, url: str) -> FetchResult:
        """Retry wrapper with exponential backoff on 5xx."""
        last_result = None
        for attempt in range(self.config.max_retries):
            result = await self._do_fetch(url)
            if result.error is None and result.status_code < 500:
                return result
            last_result = result
            if attempt < self.config.max_retries - 1:
                delay = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                logger.debug("Retry %d/%d for %s (status=%s, error=%s), waiting %.1fs",
                             attempt + 1, self.config.max_retries, url,
                             result.status_code, result.error, delay)
                await asyncio.sleep(delay)
        return last_result

    async def _do_fetch(self, url: str) -> FetchResult:
        """Execute a single fetch with manual redirect following."""
        await self._rate_limiter.acquire()

        current_url = url
        redirect_chain: List[Tuple[str, int]] = []
        max_redirects = 10
        start_time = time.monotonic()

        for hop in range(max_redirects + 1):
            try:
                async with self._session.get(
                    current_url,
                    allow_redirects=False,
                    ssl=False,
                ) as resp:
                    status = resp.status
                    headers = dict(resp.headers)
                    http_version = f"{resp.version.major}.{resp.version.minor}" if resp.version else ""
                    content_type = resp.content_type or ""

                    # Check for redirect
                    if status in (301, 302, 303, 307, 308) and "Location" in headers:
                        redirect_chain.append((current_url, status))
                        location = headers["Location"]
                        # Handle relative redirects
                        if location.startswith("/"):
                            parsed = urlparse(current_url)
                            location = f"{parsed.scheme}://{parsed.netloc}{location}"
                        elif not location.startswith("http"):
                            # Relative path
                            parsed = urlparse(current_url)
                            base_path = parsed.path.rsplit("/", 1)[0]
                            location = f"{parsed.scheme}://{parsed.netloc}{base_path}/{location}"
                        current_url = location
                        continue

                    # Non-redirect response — read body
                    body = await resp.read()
                    elapsed = time.monotonic() - start_time

                    body_text = ""
                    if "text" in content_type or "html" in content_type or "xml" in content_type:
                        # Try to decode the body
                        encoding = resp.get_encoding() or "utf-8"
                        try:
                            body_text = body.decode(encoding, errors="replace")
                        except (UnicodeDecodeError, LookupError):
                            body_text = body.decode("utf-8", errors="replace")

                    return FetchResult(
                        url=url,
                        final_url=current_url,
                        status_code=status,
                        headers=headers,
                        body=body,
                        body_text=body_text,
                        response_time=elapsed,
                        redirect_chain=redirect_chain,
                        content_type=content_type,
                        http_version=http_version,
                        size_bytes=len(body),
                    )

            except asyncio.TimeoutError:
                elapsed = time.monotonic() - start_time
                return FetchResult(
                    url=url, final_url=current_url, status_code=0,
                    response_time=elapsed, error="timeout",
                    redirect_chain=redirect_chain,
                )
            except aiohttp.ClientError as exc:
                elapsed = time.monotonic() - start_time
                return FetchResult(
                    url=url, final_url=current_url, status_code=0,
                    response_time=elapsed, error=str(exc),
                    redirect_chain=redirect_chain,
                )
            except Exception as exc:
                elapsed = time.monotonic() - start_time
                logger.error("Unexpected error fetching %s: %s", current_url, exc)
                return FetchResult(
                    url=url, final_url=current_url, status_code=0,
                    response_time=elapsed, error=str(exc),
                    redirect_chain=redirect_chain,
                )

        # Exceeded max redirects
        elapsed = time.monotonic() - start_time
        return FetchResult(
            url=url, final_url=current_url, status_code=0,
            response_time=elapsed, error=f"too many redirects ({max_redirects})",
            redirect_chain=redirect_chain,
        )
