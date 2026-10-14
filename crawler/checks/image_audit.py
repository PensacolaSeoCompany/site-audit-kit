"""Audit images: missing alt text, broken images, oversized images."""

import logging
from crawler import db

logger = logging.getLogger(__name__)

# Image file extensions to match against target_url
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.avif', '.bmp', '.ico', '.tiff')


def check_image_audit(run_id: int, conn=None) -> dict:
    """Audit images found during crawl.

    Detects:
    - image_missing_alt: image link has no alt text
    - broken_image: image URL returns 4xx status
    - oversized_image: image size > 500KB (if size data available)

    Returns: {issues_found: int, details: [...]}
    """
    close_conn = False
    if conn is None:
        conn = db.connect()
        close_conn = True

    try:
        details = []
        with conn.cursor() as cur:
            # Get all image links for this run
            cur.execute("""
                SELECT source_url, target_url, alt_text
                FROM crawl_links
                WHERE crawl_run_id = %s AND link_type = 'image'
            """, (run_id,))
            image_links = cur.fetchall()

            # Build a lookup of crawled URLs -> status_code and size_bytes
            cur.execute("""
                SELECT address, status_code, size_bytes
                FROM crawl_urls
                WHERE crawl_run_id = %s
            """, (run_id,))
            url_info = {row["address"]: row for row in cur.fetchall()}

            issues = []
            seen_missing_alt = set()  # Deduplicate per (source, target) pair

            for img in image_links:
                source = img["source_url"]
                target = img["target_url"]
                alt = img["alt_text"]
                pair_key = (source, target)

                # Missing alt text
                if (not alt or not alt.strip()) and pair_key not in seen_missing_alt:
                    seen_missing_alt.add(pair_key)
                    detail = f"Image missing alt text: {target[:200]}"
                    issues.append((run_id, source, "image_missing_alt", "warning", detail))
                    details.append({"url": source, "image": target[:200], "issue": "image_missing_alt"})

                # Check if the image URL was crawled and returned an error
                if target in url_info:
                    info = url_info[target]
                    sc = info["status_code"] or 0
                    if 400 <= sc < 500:
                        detail = f"Broken image (HTTP {sc}): {target[:200]}"
                        issues.append((run_id, source, "broken_image", "warning", detail))
                        details.append({"url": source, "image": target[:200], "issue": "broken_image", "status": sc})

                    # Oversized image check (> 500KB)
                    size = info.get("size_bytes") or 0
                    if size > 512000:
                        size_kb = round(size / 1024, 1)
                        detail = f"Oversized image ({size_kb}KB): {target[:200]}"
                        issues.append((run_id, source, "oversized_image", "info", detail))
                        details.append({"url": source, "image": target[:200], "issue": "oversized_image", "size_kb": size_kb})

            if issues:
                cur.executemany(
                    "INSERT INTO crawl_issues (crawl_run_id, url, issue_type, severity, details) "
                    "VALUES (%s, %s, %s, %s, %s)", issues)
                conn.commit()

        logger.info("image_audit: %d issues for run_id=%d", len(details), run_id)
        return {"issues_found": len(details), "details": details}

    finally:
        if close_conn:
            conn.close()
