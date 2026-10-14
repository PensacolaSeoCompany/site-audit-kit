"""
site-crawler: Async BFS web crawler replicating Screaming Frog's core 72-column audit.
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

Stores results in a database for cross-run analysis: SQLite by default at
$KIT_WORK/crawler.sqlite (no server needed), or MySQL when MYSQL_HOST and
MYSQL_USER are set. The database is optional infrastructure for this CLI;
the main site-audit path does not import this package.
"""

__version__ = "0.1.0"
__author__ = "site-audit-kit contributors"

from crawler.config import CrawlConfig
from crawler.engine import crawl

__all__ = ["CrawlConfig", "crawl", "__version__"]
