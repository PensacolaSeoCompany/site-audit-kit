"""Internal PageRank calculator implementing the Reasonable Surfer model.

Computes weighted PageRank scores for all URLs in a crawl run using
position-based link weights and power iteration on a sparse matrix.
Results are stored in the crawl_pagerank table.
"""

import logging
import math
from typing import Dict, List, Tuple

import numpy as np
from scipy import sparse


from crawler import db
from crawler.config import MySQLConfig

logger = logging.getLogger(__name__)

# Reasonable Surfer position weights
POSITION_WEIGHTS = {
    "main_content": 1.0,
    "body": 1.0,
    "header": 0.6,
    "navigation": 0.6,
    "breadcrumb": 0.5,
    "sidebar": 0.4,
    "footer": 0.2,
    "image": 0.3,
    "unknown": 0.6,
}


def _get_connection(config: MySQLConfig = None):
    """Open the crawler database (SQLite by default, MySQL if configured)."""
    return db.connect(config)


def _build_adjacency_matrix(
    links: List[dict],
    url_to_idx: Dict[str, int],
) -> sparse.csr_matrix:
    """Build a sparse weighted adjacency matrix from crawl links.

    Each entry M[j, i] represents the weight of the link from page i to page j,
    normalized by the total outgoing weight from page i.
    """
    n = len(url_to_idx)
    # Accumulate outgoing weight per source and per (source, target) pair
    out_weight: Dict[int, float] = {}
    edge_weight: Dict[Tuple[int, int], float] = {}

    for link in links:
        src = link["source_url"]
        tgt = link["target_url"]
        if src not in url_to_idx or tgt not in url_to_idx:
            continue

        i = url_to_idx[src]
        j = url_to_idx[tgt]

        # Nofollow links get 0 weight but still occupy denominator
        if link["follow_status"] == "nofollow":
            weight = 0.0
        else:
            position = link["link_position"] or "unknown"
            link_type = link["link_type"] or "hyperlink"
            if link_type == "image":
                weight = POSITION_WEIGHTS.get("image", 0.3)
            else:
                weight = POSITION_WEIGHTS.get(position, 0.6)

        # Accumulate: a source may link to the same target multiple times
        edge_weight[(i, j)] = edge_weight.get((i, j), 0.0) + weight
        # Track total outgoing weight (including nofollow slots for evaporation)
        position = link["link_position"] or "unknown"
        link_type = link["link_type"] or "hyperlink"
        if link_type == "image":
            slot_weight = POSITION_WEIGHTS.get("image", 0.3)
        else:
            slot_weight = POSITION_WEIGHTS.get(position, 0.6)
        out_weight[i] = out_weight.get(i, 0.0) + slot_weight

    # Build sparse matrix: M[j, i] = edge_weight(i->j) / out_weight(i)
    rows, cols, data = [], [], []
    for (i, j), w in edge_weight.items():
        if w > 0 and out_weight.get(i, 0) > 0:
            rows.append(j)
            cols.append(i)
            data.append(w / out_weight[i])

    if not data:
        return sparse.csr_matrix((n, n), dtype=np.float64)

    return sparse.csr_matrix(
        (np.array(data, dtype=np.float64), (np.array(rows), np.array(cols))),
        shape=(n, n),
    )


def _power_iteration(
    M: sparse.csr_matrix,
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> np.ndarray:
    """Run power iteration to compute PageRank vector.

    Handles dangling nodes (pages with no outlinks) by distributing
    their rank evenly across all pages.

    Returns: rank vector of length N summing to 1.0.
    """
    n = M.shape[0]
    if n == 0:
        return np.array([])

    # Initial uniform distribution
    r = np.ones(n, dtype=np.float64) / n

    # Identify dangling nodes: columns that sum to 0
    col_sums = np.array(M.sum(axis=0)).flatten()
    dangling = (col_sums == 0)

    teleport = (1.0 - alpha) / n

    for iteration in range(max_iter):
        # Dangling node contribution: their rank is distributed evenly
        dangling_sum = r[dangling].sum() if dangling.any() else 0.0

        r_new = alpha * (M.dot(r) + (dangling_sum / n) * np.ones(n)) + teleport * np.ones(n)

        # Check convergence
        delta = np.abs(r_new - r).sum()
        r = r_new

        if delta < tol:
            logger.info("PageRank converged after %d iterations (delta=%.2e)", iteration + 1, delta)
            break
    else:
        logger.warning("PageRank did not converge after %d iterations (delta=%.2e)", max_iter, delta)

    # Normalize to sum to 1 (should already be close)
    r = r / r.sum()
    return r


def _scale_to_100(raw_scores: Dict[str, float], n: int) -> Dict[str, float]:
    """Scale raw PageRank values to a 0-100 score using logarithmic scaling.

    Formula: link_score = max(1, min(100, round(log10(raw_pr * N * 1000) * 15)))
    """
    scaled = {}
    for url, pr in raw_scores.items():
        val = pr * n * 1000
        if val <= 0:
            scaled[url] = 1.0
        else:
            score = math.log10(val) * 15
            scaled[url] = max(1.0, min(100.0, round(score, 1)))
    return scaled


def compute_pagerank(
    run_id: int,
    damping: float = 0.85,
    config: MySQLConfig = None,
) -> dict:
    """Compute and store internal PageRank for a crawl run.

    Returns: {
        'success': True/False,
        'urls_scored': int,
        'orphan_count': int,
        'iterations': int,
        'scores': {url: scaled_score, ...}  (top results)
    }
    """
    conn = _get_connection(config)
    try:
        with conn.cursor() as cur:
            # 1. Get all internal URLs for this run
            cur.execute(
                "SELECT DISTINCT address FROM crawl_urls WHERE crawl_run_id = %s",
                (run_id,),
            )
            urls = [row["address"] for row in cur.fetchall()]

        if not urls:
            return {"success": False, "message": f"No URLs found for run_id={run_id}"}

        url_to_idx = {url: i for i, url in enumerate(urls)}
        idx_to_url = {i: url for url, i in url_to_idx.items()}
        n = len(urls)

        # 2. Get all internal follow links
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_url, target_url, link_type, link_position, follow_status "
                "FROM crawl_links "
                "WHERE crawl_run_id = %s AND is_internal = 1",
                (run_id,),
            )
            links = cur.fetchall()

        logger.info("Building adjacency matrix: %d URLs, %d internal links", n, len(links))

        # 3. Build adjacency matrix with position weights
        M = _build_adjacency_matrix(links, url_to_idx)

        # 4. Power iteration
        rank_vector = _power_iteration(M, alpha=damping)

        # 5. Compute inlink and outlink counts
        inlink_count: Dict[str, int] = {url: 0 for url in urls}
        outlink_count: Dict[str, int] = {url: 0 for url in urls}
        for link in links:
            src, tgt = link["source_url"], link["target_url"]
            if src in outlink_count:
                outlink_count[src] = outlink_count.get(src, 0) + 1
            if tgt in inlink_count:
                inlink_count[tgt] = inlink_count.get(tgt, 0) + 1

        # Deduplicate link counts (unique links only)
        inlink_unique: Dict[str, set] = {url: set() for url in urls}
        outlink_unique: Dict[str, set] = {url: set() for url in urls}
        for link in links:
            src, tgt = link["source_url"], link["target_url"]
            if src in outlink_unique:
                outlink_unique[src].add(tgt)
            if tgt in inlink_unique:
                inlink_unique[tgt].add(src)
        inlink_count = {url: len(srcs) for url, srcs in inlink_unique.items()}
        outlink_count = {url: len(tgts) for url, tgts in outlink_unique.items()}

        # 6. Raw scores and scaling
        raw_scores = {idx_to_url[i]: float(rank_vector[i]) for i in range(n)}
        scaled_scores = _scale_to_100(raw_scores, n)

        # 7. Identify orphans (0 unique inlinks)
        orphans = {url for url, cnt in inlink_count.items() if cnt == 0}

        # 8. Clear previous results and store
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crawl_pagerank WHERE crawl_run_id = %s", (run_id,))

            batch = []
            for url in urls:
                batch.append((
                    run_id,
                    url,
                    raw_scores[url],
                    scaled_scores[url],
                    inlink_count.get(url, 0),
                    outlink_count.get(url, 0),
                    1 if url in orphans else 0,
                ))

            cur.executemany(
                "INSERT INTO crawl_pagerank "
                "(crawl_run_id, url, raw_pagerank, link_score_0_100, "
                "inlink_count, outlink_count, is_orphan) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                batch,
            )
            conn.commit()

        logger.info(
            "PageRank stored: %d URLs scored, %d orphans", n, len(orphans),
        )

        return {
            "success": True,
            "urls_scored": n,
            "orphan_count": len(orphans),
            "scores": scaled_scores,
        }

    except Exception as exc:
        logger.error("PageRank computation failed: %s", exc, exc_info=True)
        return {"success": False, "message": str(exc)}
    finally:
        conn.close()
