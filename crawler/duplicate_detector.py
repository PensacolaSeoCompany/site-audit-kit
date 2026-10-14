"""Duplicate and near-duplicate content detection for crawl runs.

Exact duplicates: identified by matching content_hash values in crawl_urls.
Near-duplicates: identified using MinHash LSH on text shingles from available
page text (title, meta description, headings concatenated as proxy).
Results stored as crawl_issues rows.
"""

import logging
from typing import Dict, List, Tuple


from crawler import db
from crawler.config import MySQLConfig

logger = logging.getLogger(__name__)

# Number of permutations for MinHash — 128 balances accuracy and speed
NUM_PERM = 128

# Shingle size (number of words per shingle)
SHINGLE_SIZE = 3


def _get_connection(config: MySQLConfig = None):
    """Open the crawler database (SQLite by default, MySQL if configured)."""
    return db.connect(config)


def _find_exact_duplicates(run_id: int, conn) -> List[Tuple]:
    """Find exact duplicate pages by content_hash.

    Returns list of (url, issue_type, severity, details) tuples.
    """
    issues = []
    # Portable SQL (SQLite and MySQL): group in Python, not with GROUP_CONCAT.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_hash, address FROM crawl_urls "
            "WHERE crawl_run_id = %s AND content_hash IS NOT NULL "
            "AND content_hash != '' ORDER BY id",
            (run_id,),
        )
        rows = cur.fetchall()

    by_hash: Dict[str, List[str]] = {}
    for row in rows:
        by_hash.setdefault(row["content_hash"], []).append(row["address"])
    groups = [addrs for addrs in by_hash.values() if len(addrs) > 1]

    for urls in groups:
        for url in urls:
            others = [u for u in urls if u != url]
            issues.append((
                run_id, url, "duplicate_content", "warning",
                f"Exact duplicate of: {', '.join(others[:3])}"
                + (f" (+{len(others)-3} more)" if len(others) > 3 else ""),
            ))

    logger.info("Found %d exact duplicate issues across %d groups", len(issues), len(groups))
    return issues


def _make_shingles(text: str, k: int = SHINGLE_SIZE) -> set:
    """Create k-word shingles from text."""
    words = text.lower().split()
    if len(words) < k:
        return {text.lower()} if text.strip() else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _find_near_duplicates(
    run_id: int,
    conn,
    threshold: float = 0.8,
) -> List[Tuple]:
    """Find near-duplicate pages using MinHash LSH on available text fields.

    Uses title + meta_description + h1 + h2s concatenated as proxy text.
    Returns list of (run_id, url, issue_type, severity, details) tuples.
    """
    from datasketch import MinHash, MinHashLSH  # imported here so the module loads without it

    with conn.cursor() as cur:
        cur.execute(
            "SELECT address, title_1, meta_description_1, h1_1, h2_1, h2_2, "
            "word_count "
            "FROM crawl_urls "
            "WHERE crawl_run_id = %s AND status_code = 200",
            (run_id,),
        )
        rows = cur.fetchall()

    if not rows:
        return []

    # Build MinHash for each page from available text
    minhashes: Dict[str, "MinHash"] = {}
    for row in rows:
        # Concatenate available text fields as proxy for full body
        parts = []
        for field in ("title_1", "meta_description_1", "h1_1", "h2_1", "h2_2"):
            val = row.get(field)
            if val:
                parts.append(val)
        text = " ".join(parts)

        # Skip pages with very little text
        if len(text.split()) < SHINGLE_SIZE:
            continue

        shingles = _make_shingles(text)
        if not shingles:
            continue

        mh = MinHash(num_perm=NUM_PERM)
        for s in shingles:
            mh.update(s.encode("utf-8"))
        minhashes[row["address"]] = mh

    if len(minhashes) < 2:
        return []

    # Build LSH index
    lsh = MinHashLSH(threshold=threshold, num_perm=NUM_PERM)
    for url, mh in minhashes.items():
        try:
            lsh.insert(url, mh)
        except ValueError:
            # Duplicate key — skip
            pass

    # Query for near-duplicate pairs
    seen_pairs = set()
    issues = []
    for url, mh in minhashes.items():
        candidates = lsh.query(mh)
        for candidate in candidates:
            if candidate == url:
                continue
            pair = tuple(sorted([url, candidate]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # Compute actual Jaccard similarity for the details
            similarity = minhashes[url].jaccard(minhashes[candidate])
            if similarity >= threshold:
                issues.append((
                    run_id, url, "near_duplicate_content", "warning",
                    f"~{similarity:.0%} similar to: {candidate}",
                ))
                issues.append((
                    run_id, candidate, "near_duplicate_content", "warning",
                    f"~{similarity:.0%} similar to: {url}",
                ))

    logger.info("Found %d near-duplicate issues from %d candidate pairs",
                len(issues), len(seen_pairs))
    return issues


def detect_duplicates(
    run_id: int,
    similarity_threshold: float = 0.8,
    config: MySQLConfig = None,
) -> dict:
    """Detect exact and near-duplicate content for a crawl run.

    Returns: {
        'success': True/False,
        'exact_duplicate_groups': int,
        'exact_duplicate_issues': int,
        'near_duplicate_pairs': int,
        'near_duplicate_issues': int,
        'total_issues_stored': int,
    }
    """
    conn = _get_connection(config)
    try:
        # Find exact duplicates
        exact_issues = _find_exact_duplicates(run_id, conn)

        # Find near-duplicates
        near_issues = _find_near_duplicates(run_id, conn, similarity_threshold)

        # Store all issues
        all_issues = exact_issues + near_issues
        stored = 0

        if all_issues:
            with conn.cursor() as cur:
                # Clear previous duplicate issues for this run
                cur.execute(
                    "DELETE FROM crawl_issues WHERE crawl_run_id = %s "
                    "AND issue_type IN ('duplicate_content', 'near_duplicate_content')",
                    (run_id,),
                )

                sql = (
                    "INSERT INTO crawl_issues "
                    "(crawl_run_id, url, issue_type, severity, details) "
                    "VALUES (%s, %s, %s, %s, %s)"
                )
                cur.executemany(sql, all_issues)
                stored = cur.rowcount
                conn.commit()

        logger.info("Duplicate detection complete: %d exact issues, %d near-dup issues stored",
                    len(exact_issues), len(near_issues))

        return {
            "success": True,
            "exact_duplicate_issues": len(exact_issues),
            "near_duplicate_pairs": len(near_issues) // 2 if near_issues else 0,
            "near_duplicate_issues": len(near_issues),
            "total_issues_stored": stored,
        }

    except Exception as exc:
        logger.error("Duplicate detection failed: %s", exc, exc_info=True)
        return {"success": False, "message": str(exc)}
    finally:
        conn.close()
