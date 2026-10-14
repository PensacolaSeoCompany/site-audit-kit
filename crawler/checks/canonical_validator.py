"""Validate canonical tag correctness: chains, loops, targets, and indexability conflicts."""

import logging
from crawler import db

logger = logging.getLogger(__name__)


def check_canonical_validation(run_id: int, conn=None) -> dict:
    """Validate canonical tags across all crawled pages.

    Detects:
    - canonical_target_not_crawled: canonical points to a URL not in the crawl set
    - canonical_target_not_200: canonical target returned a non-200 status
    - canonical_chain: A->B->C (canonical of canonical)
    - canonical_loop: A->B->A (mutual canonical references)
    - noindex_with_canonical: noindex page has canonical pointing elsewhere (informational)
    - non_self_canonical: indexable page has canonical pointing to a different URL

    Returns: {issues_found: int, details: [...]}
    """
    close_conn = False
    if conn is None:
        conn = db.connect()
        close_conn = True

    try:
        details = []
        with conn.cursor() as cur:
            # Fetch all URLs with their canonical values and indexability
            cur.execute("""
                SELECT address, canonical_1, status_code, indexability, meta_robots_1
                FROM crawl_urls
                WHERE crawl_run_id = %s
            """, (run_id,))
            rows = cur.fetchall()

        if not rows:
            return {"issues_found": 0, "details": []}

        # Build lookup maps
        url_data = {}  # address -> row dict
        canonical_map = {}  # address -> canonical target (only if canonical is set and differs)
        crawled_urls = set()

        for row in rows:
            addr = row["address"]
            crawled_urls.add(addr)
            url_data[addr] = row
            canon = row["canonical_1"]
            if canon and canon.strip():
                canonical_map[addr] = canon.strip()

        issues = []

        for row in rows:
            addr = row["address"]
            canon = canonical_map.get(addr)
            if not canon:
                continue

            sc = row["status_code"] or 0
            meta_robots = (row["meta_robots_1"] or "").lower()
            is_noindex = "noindex" in meta_robots

            # Normalize: treat trailing-slash differences as equivalent for self-canonical check
            addr_norm = addr.rstrip("/")
            canon_norm = canon.rstrip("/")
            is_self = (addr_norm == canon_norm)

            # Check 1: canonical target not in crawl set
            if not is_self and canon not in crawled_urls:
                # Also check without trailing slash
                canon_alt = canon.rstrip("/") if canon.endswith("/") else canon + "/"
                if canon_alt not in crawled_urls:
                    detail = f"Canonical target not crawled: {canon[:200]}"
                    issues.append((run_id, addr, "canonical_target_not_crawled", "info", detail))
                    details.append({"url": addr, "canonical": canon, "issue": "canonical_target_not_crawled"})

            # Check 2: canonical target is crawled but not 200
            if not is_self and canon in url_data:
                target_sc = url_data[canon]["status_code"] or 0
                if target_sc != 200:
                    detail = f"Canonical target returns HTTP {target_sc}: {canon[:200]}"
                    issues.append((run_id, addr, "canonical_target_not_200", "warning", detail))
                    details.append({"url": addr, "canonical": canon, "issue": "canonical_target_not_200", "target_status": target_sc})

            # Check 3 & 4: canonical chains and loops
            if not is_self and canon in canonical_map:
                canon_of_canon = canonical_map[canon]
                canon_of_canon_norm = canon_of_canon.rstrip("/")

                # Loop: A->B->A
                if canon_of_canon_norm == addr_norm:
                    detail = f"Canonical loop: {addr} -> {canon} -> {addr}"
                    issues.append((run_id, addr, "canonical_loop", "critical", detail))
                    details.append({"url": addr, "canonical": canon, "issue": "canonical_loop"})
                # Chain: A->B->C (where C != B, i.e. B's canonical is not self-referencing)
                elif canon_of_canon_norm != canon_norm:
                    detail = f"Canonical chain: {addr} -> {canon} -> {canon_of_canon}"
                    issues.append((run_id, addr, "canonical_chain", "warning", detail))
                    details.append({"url": addr, "canonical": canon, "chain_target": canon_of_canon, "issue": "canonical_chain"})

            # Check 5: noindex page with canonical pointing elsewhere
            if is_noindex and not is_self:
                detail = f"Noindex page has non-self canonical: {canon[:200]}"
                issues.append((run_id, addr, "noindex_with_canonical", "info", detail))
                details.append({"url": addr, "canonical": canon, "issue": "noindex_with_canonical"})

            # Check 6: non-self canonical on an indexable page
            if not is_self and not is_noindex and sc == 200:
                detail = f"Non-self canonical on indexable page: {canon[:200]}"
                issues.append((run_id, addr, "non_self_canonical", "info", detail))
                details.append({"url": addr, "canonical": canon, "issue": "non_self_canonical"})

        # Insert all issues
        if issues:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO crawl_issues (crawl_run_id, url, issue_type, severity, details) "
                    "VALUES (%s, %s, %s, %s, %s)", issues)
                conn.commit()

        logger.info("canonical_validator: %d issues for run_id=%d", len(details), run_id)
        return {"issues_found": len(details), "details": details}

    finally:
        if close_conn:
            conn.close()
