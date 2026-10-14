"""Crawl configuration dataclass with all settings.

Database (optional, no server needed by default):
- SQLite at $KIT_WORK/crawler.sqlite (KIT_WORK defaults to <kit>/work;
  CRAWLER_SQLITE overrides the file path).
- MySQL only when both MYSQL_HOST and MYSQL_USER are set; then MYSQL_PORT
  (3306), MYSQL_PASSWORD and MYSQL_DATABASE (site_audits) are read too.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

KIT_HOME = Path(__file__).resolve().parents[1]
KIT_WORK = Path(os.environ.get("KIT_WORK") or (KIT_HOME / "work"))
SQLITE_PATH = Path(os.environ.get("CRAWLER_SQLITE") or (KIT_WORK / "crawler.sqlite"))


# Default Googlebot mobile user-agent
GOOGLEBOT_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.6778.69 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class MySQLConfig:
    """MySQL connection parameters, read from the environment. No defaults for
    host, user or password: MySQL is used only when MYSQL_HOST and MYSQL_USER
    are both set (see use_mysql); otherwise the crawler uses SQLite."""
    host: str = field(default_factory=lambda: _env("MYSQL_HOST"))
    port: int = field(default_factory=lambda: int(_env("MYSQL_PORT", "3306") or 3306))
    user: str = field(default_factory=lambda: _env("MYSQL_USER"))
    password: str = field(default_factory=lambda: _env("MYSQL_PASSWORD"))
    database: str = field(default_factory=lambda: _env("MYSQL_DATABASE", "site_audits") or "site_audits")
    charset: str = "utf8mb4"


def use_mysql(cfg: Optional[MySQLConfig] = None) -> bool:
    """True when MySQL is configured (host and user both set)."""
    cfg = cfg or MySQLConfig()
    return bool(cfg.host and cfg.user)


@dataclass
class CrawlConfig:
    """Complete configuration for a crawl run."""

    # Required
    seed_url: str = ""

    # Crawl limits
    max_urls: int = 5000
    max_depth: int = 10
    max_concurrent: int = 5

    # Rate limiting
    rate_limit_rps: float = 10.0
    timeout_seconds: int = 20

    # User-agent
    user_agent: str = GOOGLEBOT_MOBILE_UA

    # Robots / link policy
    respect_robots: bool = True
    follow_nofollow: bool = False
    crawl_external: bool = False
    allow_subdomains: bool = False

    # URL filtering
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    max_url_length: int = 2048

    # JavaScript rendering (Phase 2+)
    render_js: bool = False
    render_timeout_ms: int = 5000

    # Retry
    max_retries: int = 3

    # Database (MySQL if configured, else SQLite; see crawler/db.py)
    mysql: MySQLConfig = field(default_factory=MySQLConfig)

    # Output
    output_dir: str = ""

    def get_compiled_includes(self) -> List[re.Pattern]:
        """Compile include patterns to regex objects."""
        return [re.compile(p) for p in self.include_patterns]

    def get_compiled_excludes(self) -> List[re.Pattern]:
        """Compile exclude patterns to regex objects."""
        return [re.compile(p) for p in self.exclude_patterns]

    def to_dict(self) -> dict:
        """Serialize config to JSON-safe dict for storage."""
        return {
            "seed_url": self.seed_url,
            "max_urls": self.max_urls,
            "max_depth": self.max_depth,
            "max_concurrent": self.max_concurrent,
            "rate_limit_rps": self.rate_limit_rps,
            "timeout_seconds": self.timeout_seconds,
            "user_agent": self.user_agent,
            "respect_robots": self.respect_robots,
            "follow_nofollow": self.follow_nofollow,
            "crawl_external": self.crawl_external,
            "allow_subdomains": self.allow_subdomains,
            "include_patterns": self.include_patterns,
            "exclude_patterns": self.exclude_patterns,
            "render_js": self.render_js,
        }
