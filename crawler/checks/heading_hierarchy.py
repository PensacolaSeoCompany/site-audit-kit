"""Validate heading structure (H1/H2 presence and hierarchy)."""

import logging
from crawler import db

logger = logging.getLogger(__name__)


def check_heading_hierarchy(run_id: int, conn=None) -> dict:
    """Check heading structure for all pages in a crawl run.

    Detects:
    - heading_skip_h1: page has H2 but no H1
    - no_headings: page with word_count > 100 has neither H1 nor H2

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
                SELECT address, h1_1, h2_1, word_count, status_code
                FROM crawl_urls
                WHERE crawl_run_id = %s AND status_code = 200
            """, (run_id,))
            rows = cur.fetchall()

            issues = []
            for row in rows:
                addr = row["address"]
                has_h1 = bool(row["h1_1"] and row["h1_1"].strip())
                has_h2 = bool(row["h2_1"] and row["h2_1"].strip())
                wc = row["word_count"] or 0

                if has_h2 and not has_h1:
                    detail = f"Page has H2 (\"{row['h2_1'][:60]}\") but no H1"
                    issues.append((run_id, addr, "heading_skip_h1", "warning", detail))
                    details.append({"url": addr, "issue": "heading_skip_h1", "h2": row["h2_1"][:80]})

                if wc > 100 and not has_h1 and not has_h2:
                    detail = f"Page has {wc} words but no H1 or H2 headings"
                    issues.append((run_id, addr, "no_headings", "info", detail))
                    details.append({"url": addr, "issue": "no_headings", "word_count": wc})

            if issues:
                cur.executemany(
                    "INSERT INTO crawl_issues (crawl_run_id, url, issue_type, severity, details) "
                    "VALUES (%s, %s, %s, %s, %s)", issues)
                conn.commit()

        logger.info("heading_hierarchy: %d issues for run_id=%d", len(details), run_id)
        return {"issues_found": len(details), "details": details}

    finally:
        if close_conn:
            conn.close()
