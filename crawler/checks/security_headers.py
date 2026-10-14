"""Check security-related signals from crawl data.

Full security header analysis (HSTS, CSP, X-Frame-Options, etc.) requires
storing response headers during the crawl, which is a future enhancement.
This module checks what is available from existing crawl_urls columns.
"""

import logging
from crawler import db

logger = logging.getLogger(__name__)


def check_security_headers(run_id: int, conn=None) -> dict:
    """Check security-related issues from available crawl data.

    Detects:
    - not_https: page address starts with http:// (not HTTPS)
    - conflicting_robots_directives: meta robots and x-robots-tag contain
      contradictory index/noindex directives

    Note: Full security header checks (HSTS, CSP, X-Content-Type-Options, etc.)
    require response header storage which is not yet implemented. This module
    will be expanded when that data becomes available.

    Returns: {issues_found: int, details: [...]}
    """
    close_conn = False
    if conn is None:
        conn = db.connect()
        close_conn = True

    try:
        details = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT address, status_code, meta_robots_1, x_robots_tag_1
                FROM crawl_urls
                WHERE crawl_run_id = %s
            """, (run_id,))
            rows = cur.fetchall()

            issues = []
            for row in rows:
                addr = row["address"]

                # Check 1: not HTTPS
                if addr.startswith("http://"):
                    detail = "Page served over plain HTTP, not HTTPS"
                    issues.append((run_id, addr, "not_https", "critical", detail))
                    details.append({"url": addr, "issue": "not_https"})

                # Check 2: conflicting robots directives
                meta_robots = (row["meta_robots_1"] or "").lower()
                x_robots = (row["x_robots_tag_1"] or "").lower()

                if meta_robots and x_robots:
                    meta_has_noindex = "noindex" in meta_robots
                    x_has_noindex = "noindex" in x_robots
                    meta_has_index = "index" in meta_robots and not meta_has_noindex
                    x_has_index = "index" in x_robots and not x_has_noindex

                    # Conflict: one says index, the other says noindex
                    if (meta_has_noindex and x_has_index) or (meta_has_index and x_has_noindex):
                        detail = (f"Conflicting robots directives: "
                                  f"meta={row['meta_robots_1']}, x-robots={row['x_robots_tag_1']}")
                        issues.append((run_id, addr, "conflicting_robots_directives", "warning", detail))
                        details.append({
                            "url": addr,
                            "issue": "conflicting_robots_directives",
                            "meta_robots": row["meta_robots_1"],
                            "x_robots_tag": row["x_robots_tag_1"],
                        })

            if issues:
                cur.executemany(
                    "INSERT INTO crawl_issues (crawl_run_id, url, issue_type, severity, details) "
                    "VALUES (%s, %s, %s, %s, %s)", issues)
                conn.commit()

        logger.info("security_headers: %d issues for run_id=%d", len(details), run_id)
        return {"issues_found": len(details), "details": details}

    finally:
        if close_conn:
            conn.close()
