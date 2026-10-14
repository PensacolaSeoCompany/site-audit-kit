"""Storage layer for crawl data.

Tables: crawl_runs, crawl_urls, crawl_links, crawl_issues. The database is
optional infrastructure: SQLite by default at $KIT_WORK/crawler.sqlite, MySQL
when MYSQL_HOST and MYSQL_USER are set (see crawler/db.py).
"""

import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any

from crawler import db
from crawler.config import MySQLConfig
from crawler.parser import ParseResult, ExtractedLink

logger = logging.getLogger(__name__)

# Batch insert size
BATCH_SIZE = 100


class CrawlStorage:
    """Database storage for crawl run data (SQLite or MySQL)."""

    def __init__(self, config: MySQLConfig):
        self.config = config
        self._conn = None

    def connect(self) -> None:
        """Open a database connection."""
        self._conn = db.connect(self.config)
        logger.info("Connected to %s", db.describe(self.config))

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_connection(self) -> None:
        """Reconnect if the connection was lost."""
        if self._conn is None or not self._conn.open:
            self.connect()
        try:
            self._conn.ping(reconnect=True)
        except Exception:
            self.connect()

    def create_crawl_run(self, domain: str, seed_url: str, config_dict: dict) -> int:
        """Insert a new crawl_run row and return its id."""
        self._ensure_connection()
        sql = """
            INSERT INTO crawl_runs (domain, seed_url, source, started_at, config_json)
            VALUES (%s, %s, 'custom', NOW(), %s)
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, (domain, seed_url, json.dumps(config_dict)))
            self._conn.commit()
            run_id = cur.lastrowid
        logger.info("Created crawl_run id=%d for %s", run_id, domain)
        return run_id

    def store_url(self, run_id: int, result: ParseResult) -> Optional[int]:
        """Insert a single ParseResult into crawl_urls. Returns the row id."""
        self._ensure_connection()
        sql = """
            INSERT INTO crawl_urls (
                crawl_run_id, address, content_type, status_code, status,
                indexability, indexability_status,
                title_1, title_1_length, title_1_pixel_width,
                meta_description_1, meta_description_1_length, meta_description_1_pixel_width,
                meta_keywords_1,
                h1_1, h1_1_length, h2_1, h2_1_length, h2_2, h2_2_length,
                meta_robots_1, x_robots_tag_1, canonical_1,
                size_bytes, co2_mg, carbon_rating,
                word_count, sentence_count, avg_words_per_sentence,
                flesch_reading_ease, readability, text_ratio,
                crawl_depth, folder_depth,
                outlinks, unique_outlinks, external_outlinks, unique_external_outlinks,
                inlinks, unique_inlinks,
                content_hash, response_time,
                last_modified, redirect_url, redirect_type,
                language, http_version, crawl_timestamp
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, NOW()
            )
        """
        params = (
            run_id, result.address, result.content_type, result.status_code, result.status,
            result.indexability, result.indexability_status,
            result.title_1[:65535] if result.title_1 else None,
            result.title_1_length, result.title_1_pixel_width,
            result.meta_description_1[:65535] if result.meta_description_1 else None,
            result.meta_description_1_length, result.meta_description_1_pixel_width,
            result.meta_keywords_1[:65535] if result.meta_keywords_1 else None,
            result.h1_1[:65535] if result.h1_1 else None, result.h1_1_length,
            result.h2_1[:65535] if result.h2_1 else None, result.h2_1_length,
            result.h2_2[:65535] if result.h2_2 else None, result.h2_2_length,
            result.meta_robots_1 or None, result.x_robots_tag_1 or None,
            result.canonical_1 or None,
            result.size_bytes, result.co2_mg, result.carbon_rating,
            result.word_count, result.sentence_count, result.avg_words_per_sentence,
            result.flesch_reading_ease, result.readability or None, result.text_ratio,
            result.crawl_depth, result.folder_depth,
            result.outlinks, result.unique_outlinks,
            result.external_outlinks, result.unique_external_outlinks,
            result.inlinks, result.unique_inlinks,
            result.content_hash or None, result.response_time,
            result.last_modified or None, result.redirect_url or None, result.redirect_type or None,
            result.language or None, result.http_version or None,
        )
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                self._conn.commit()
                return cur.lastrowid
        except Exception as exc:
            logger.error("Failed to store URL %s: %s", result.address, exc)
            self._conn.rollback()
            return None

    def store_links(self, run_id: int, source_url: str, links: List[ExtractedLink]) -> int:
        """Bulk insert links into crawl_links. Returns number of rows inserted."""
        if not links:
            return 0

        self._ensure_connection()
        sql = """
            INSERT INTO crawl_links (
                crawl_run_id, source_url, target_url,
                anchor_text, alt_text, rel_attribute,
                link_type, link_position, follow_status, is_internal
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        # Determine seed domain for is_internal
        from urllib.parse import urlparse
        seed_domain = urlparse(source_url).netloc.lower().split(":")[0]

        inserted = 0
        batch = []
        for link in links:
            target_domain = urlparse(link.url).netloc.lower().split(":")[0]
            is_internal = 1 if target_domain == seed_domain else 0

            batch.append((
                run_id, source_url, link.url[:65535],
                link.anchor_text[:65535] if link.anchor_text else None,
                link.alt_text[:65535] if link.alt_text else None,
                link.rel_attribute[:100] if link.rel_attribute else None,
                link.link_type, link.link_position, link.follow_status,
                is_internal,
            ))

            if len(batch) >= BATCH_SIZE:
                inserted += self._execute_batch(sql, batch)
                batch = []

        if batch:
            inserted += self._execute_batch(sql, batch)

        return inserted

    def _execute_batch(self, sql: str, batch: list) -> int:
        """Execute a batch insert, return rows inserted."""
        try:
            with self._conn.cursor() as cur:
                cur.executemany(sql, batch)
                self._conn.commit()
                return cur.rowcount
        except Exception as exc:
            logger.error("Batch insert failed: %s", exc)
            self._conn.rollback()
            return 0

    def detect_issues(self, run_id: int) -> int:
        """Auto-detect SEO issues for all URLs in a crawl run. Returns issue count.

        Filters out /cdn-cgi/ URLs (Cloudflare infrastructure, not real pages).
        Detects:
          - broken_link_4xx / broken_link_5xx
          - missing_title / title_too_long / title_too_short / duplicate_title
          - missing_meta_description / meta_desc_too_long / meta_desc_too_short / duplicate_meta_description
          - missing_h1 / multiple_h1 (if h1_count data available)
          - missing_canonical
          - thin_content / deep_page / slow_response (not yet available from custom crawler)
          - not_in_sitemap (requires sitemap_urls to be passed)
        """
        self._ensure_connection()

        # Fetch all URLs for this run
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id, address, status_code, title_1, title_1_length, "
                "meta_description_1, meta_description_1_length, h1_1, "
                "canonical_1, word_count, indexability, crawl_depth, response_time "
                "FROM crawl_urls WHERE crawl_run_id = %s",
                (run_id,)
            )
            urls = cur.fetchall()

        if not urls:
            return 0

        issues = []

        # Collect titles and descriptions for duplicate detection (excluding cdn-cgi)
        title_map: Dict[str, List[str]] = {}
        desc_map: Dict[str, List[str]] = {}
        for row in urls:
            addr = row["address"] or ""
            if "/cdn-cgi/" in addr:
                continue
            if row["title_1"]:
                title_map.setdefault(row["title_1"].lower().strip(), []).append(addr)
            if row["meta_description_1"]:
                desc_map.setdefault(row["meta_description_1"].lower().strip(), []).append(addr)

        # Find duplicates
        dup_titles = {t for t, addrs in title_map.items() if len(addrs) > 1}
        dup_descs = {d for d, addrs in desc_map.items() if len(addrs) > 1}

        for row in urls:
            addr = row["address"] or ""
            sc = row["status_code"] or 0

            # FIX-002: Skip Cloudflare infrastructure URLs
            if "/cdn-cgi/" in addr:
                continue

            # Status code issues
            if 400 <= sc < 500:
                issues.append((run_id, addr, "broken_link_4xx", "error", f"Status {sc}"))
            elif 500 <= sc < 600:
                issues.append((run_id, addr, "broken_link_5xx", "error", f"Status {sc}"))

            # Title issues
            t1 = (row.get("title_1") or "").strip()
            if not t1:
                issues.append((run_id, addr, "missing_title", "warning", "No title tag found"))
            else:
                tlen = row.get("title_1_length") or 0
                if tlen > 60:
                    issues.append((run_id, addr, "title_too_long", "warning", f"Title length: {tlen} chars"))
                elif tlen < 10:
                    issues.append((run_id, addr, "title_too_short", "warning", f"Title length: {tlen} chars"))
                # FIX-003: duplicate_title
                if t1.lower() in dup_titles:
                    count = len(title_map.get(t1.lower(), []))
                    issues.append((run_id, addr, "duplicate_title", "warning", f"Duplicate title shared with {count} pages"))

            # Meta description issues
            md = (row.get("meta_description_1") or "").strip()
            if not md:
                issues.append((run_id, addr, "missing_meta_description", "warning", "No meta description"))
            else:
                mlen = row.get("meta_description_1_length") or 0
                # FIX-003: meta_desc_too_long (>155)
                if mlen > 155:
                    issues.append((run_id, addr, "meta_desc_too_long", "warning", f"Meta description length: {mlen} chars (>155)"))
                # FIX-003: meta_desc_too_short (<70 but not 0)
                if 0 < mlen < 70:
                    issues.append((run_id, addr, "meta_desc_too_short", "warning", f"Meta description length: {mlen} chars (<70)"))
                if md.lower() in dup_descs:
                    issues.append((run_id, addr, "duplicate_meta_description", "warning", "Duplicate meta description"))

            # H1 issues
            if not (row.get("h1_1") or "").strip():
                issues.append((run_id, addr, "missing_h1", "warning", "No H1 tag found"))

            # Missing canonical
            if not (row.get("canonical_1") or "").strip() and row.get("indexability") == "Indexable":
                issues.append((run_id, addr, "missing_canonical", "warning", "Indexable page has no canonical tag"))

            # Thin content
            wc = row.get("word_count") or 0
            if sc == 200 and 0 < wc < 100:
                issues.append((run_id, addr, "thin_content", "warning", f"Word count: {wc}"))

            # Deep pages
            depth = row.get("crawl_depth") or 0
            if depth > 5:
                issues.append((run_id, addr, "deep_page", "info", f"Crawl depth: {depth}"))

            # Slow response
            rt = row.get("response_time") or 0
            if rt > 1.0:
                issues.append((run_id, addr, "slow_response", "warning", f"Response time: {rt:.2f}s"))

        # Bulk insert issues
        if not issues:
            return 0

        sql = """
            INSERT INTO crawl_issues (crawl_run_id, url, issue_type, severity, details)
            VALUES (%s, %s, %s, %s, %s)
        """
        inserted = 0
        for i in range(0, len(issues), BATCH_SIZE):
            batch = issues[i:i + BATCH_SIZE]
            inserted += self._execute_batch(sql, batch)

        logger.info("Detected %d issues for run_id=%d", inserted, run_id)
        return inserted

    def update_crawl_run(self, run_id: int, urls_crawled: int, urls_queued: int,
                         duration_seconds: int) -> None:
        """Update a crawl_run with completion stats."""
        self._ensure_connection()
        sql = """
            UPDATE crawl_runs
            SET completed_at = NOW(), urls_crawled = %s, urls_queued = %s, duration_seconds = %s
            WHERE id = %s
        """
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, (urls_crawled, urls_queued, duration_seconds, run_id))
                self._conn.commit()
        except Exception as exc:
            logger.error("Failed to update crawl_run %d: %s", run_id, exc)
            self._conn.rollback()

    def get_crawl_summary(self, run_id: int) -> Dict[str, Any]:
        """Return a summary dict for a crawl run."""
        self._ensure_connection()
        summary = {}

        with self._conn.cursor() as cur:
            # Run info
            cur.execute("SELECT * FROM crawl_runs WHERE id = %s", (run_id,))
            run = cur.fetchone()
            if not run:
                return {"error": f"No crawl_run with id={run_id}"}
            summary["run"] = {k: str(v) if v is not None else None for k, v in run.items()}

            # URL stats
            cur.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END) as ok_2xx, "
                "SUM(CASE WHEN status_code BETWEEN 300 AND 399 THEN 1 ELSE 0 END) as redirect_3xx, "
                "SUM(CASE WHEN status_code BETWEEN 400 AND 499 THEN 1 ELSE 0 END) as client_4xx, "
                "SUM(CASE WHEN status_code BETWEEN 500 AND 599 THEN 1 ELSE 0 END) as server_5xx, "
                "SUM(CASE WHEN indexability = 'Indexable' THEN 1 ELSE 0 END) as indexable, "
                "AVG(word_count) as avg_word_count, "
                "AVG(response_time) as avg_response_time "
                "FROM crawl_urls WHERE crawl_run_id = %s",
                (run_id,)
            )
            url_stats = cur.fetchone()
            summary["urls"] = {k: str(v) if v is not None else "0" for k, v in url_stats.items()}

            # Issue stats
            cur.execute(
                "SELECT issue_type, severity, COUNT(*) as cnt "
                "FROM crawl_issues WHERE crawl_run_id = %s "
                "GROUP BY issue_type, severity ORDER BY cnt DESC",
                (run_id,)
            )
            summary["issues"] = [dict(r) for r in cur.fetchall()]

            # Link stats
            cur.execute(
                "SELECT COUNT(*) as total_links, "
                "SUM(CASE WHEN is_internal = 1 THEN 1 ELSE 0 END) as internal_links, "
                "SUM(CASE WHEN is_internal = 0 THEN 1 ELSE 0 END) as external_links "
                "FROM crawl_links WHERE crawl_run_id = %s",
                (run_id,)
            )
            link_stats = cur.fetchone()
            summary["links"] = {k: str(v) if v is not None else "0" for k, v in link_stats.items()}

        return summary
