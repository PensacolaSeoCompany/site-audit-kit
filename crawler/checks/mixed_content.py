"""Check for HTTP resources loaded on HTTPS pages (mixed content)."""

import logging
from crawler import db

logger = logging.getLogger(__name__)


def check_mixed_content(run_id: int, conn=None) -> dict:
    """Find HTTP resources referenced from HTTPS pages.

    Checks hyperlinks, images, CSS, and JS links where the source page
    is HTTPS but the target resource uses plain HTTP.

    Returns: {issues_found: int, details: [...]}
    """
    close_conn = False
    if conn is None:
        conn = db.connect()
        close_conn = True

    try:
        details = []
        with conn.cursor() as cur:
            # Find all links where HTTPS page loads HTTP resource
            cur.execute("""
                SELECT source_url, target_url, link_type
                FROM crawl_links
                WHERE crawl_run_id = %s
                  AND source_url LIKE 'https://%%'
                  AND target_url LIKE 'http://%%'
                  AND target_url NOT LIKE 'https://%%'
            """, (run_id,))
            rows = cur.fetchall()

            issues = []
            for row in rows:
                resource_type = row["link_type"] or "unknown"
                detail = f"Mixed content ({resource_type}): {row['target_url'][:200]}"
                issues.append((run_id, row["source_url"], "mixed_content", "warning", detail))
                details.append({
                    "source": row["source_url"],
                    "target": row["target_url"],
                    "type": resource_type,
                })

            if issues:
                cur.executemany(
                    "INSERT INTO crawl_issues (crawl_run_id, url, issue_type, severity, details) "
                    "VALUES (%s, %s, %s, %s, %s)", issues)
                conn.commit()

        logger.info("mixed_content: %d issues for run_id=%d", len(details), run_id)
        return {"issues_found": len(details), "details": details}

    finally:
        if close_conn:
            conn.close()
