"""Crawl comparison between two runs of the same domain.

Compares crawl_urls and crawl_issues between two crawl_run IDs,
producing a structured diff report of what changed.
"""

import logging
from typing import Any, Dict, List, Optional


from crawler import db
from crawler.config import MySQLConfig

logger = logging.getLogger(__name__)


def _get_connection(config: MySQLConfig = None):
    """Open the crawler database (SQLite by default, MySQL if configured)."""
    return db.connect(config)


def _fetch_urls_for_run(run_id: int, conn) -> Dict[str, dict]:
    """Fetch all URLs for a run, keyed by address."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT address, status_code, title_1, meta_description_1, "
            "h1_1, word_count, indexability, canonical_1, "
            "content_hash, response_time, crawl_depth "
            "FROM crawl_urls WHERE crawl_run_id = %s",
            (run_id,),
        )
        rows = cur.fetchall()
    return {row['address']: row for row in rows}


def _fetch_issues_for_run(run_id: int, conn) -> Dict[str, List[str]]:
    """Fetch all issues for a run, keyed by URL → list of issue_types."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT url, issue_type, severity, details "
            "FROM crawl_issues WHERE crawl_run_id = %s",
            (run_id,),
        )
        rows = cur.fetchall()

    issues: Dict[str, List[str]] = {}
    for row in rows:
        url = row['url']
        if url not in issues:
            issues[url] = []
        issues[url].append(row['issue_type'])
    return issues


def compare_crawls(
    run_id_before: int,
    run_id_after: int,
    config: MySQLConfig = None,
) -> dict:
    """Compare two crawl runs and return a structured diff report.

    Args:
        run_id_before: The earlier crawl run ID.
        run_id_after: The later crawl run ID.
        config: database config (None = environment; SQLite unless MYSQL_HOST+MYSQL_USER are set).

    Returns:
        dict with keys:
            success: bool
            before_run_id, after_run_id: int
            summary: {before_count, after_count, new_count, removed_count, ...}
            new_urls: list of URLs in after but not before
            removed_urls: list of URLs in before but not after
            status_changes: list of {url, before, after}
            title_changes: list of {url, before, after}
            content_changes: list of {url, before_hash, after_hash, before_words, after_words}
            indexability_changes: list of {url, before, after}
            new_issues: list of {url, issue_type}
            resolved_issues: list of {url, issue_type}
            response_time_changes: list of {url, before, after, delta}
    """
    conn = _get_connection(config)
    try:
        # Verify both runs exist
        with conn.cursor() as cur:
            cur.execute("SELECT id, domain FROM crawl_runs WHERE id IN (%s, %s)",
                        (run_id_before, run_id_after))
            runs = cur.fetchall()
        if len(runs) < 2:
            found_ids = [r['id'] for r in runs]
            return {
                'success': False,
                'message': f"Could not find both runs. Found: {found_ids}",
            }

        before_urls = _fetch_urls_for_run(run_id_before, conn)
        after_urls = _fetch_urls_for_run(run_id_after, conn)

        before_issues = _fetch_issues_for_run(run_id_before, conn)
        after_issues = _fetch_issues_for_run(run_id_after, conn)

        before_set = set(before_urls.keys())
        after_set = set(after_urls.keys())

        # New and removed URLs
        new_urls = sorted(after_set - before_set)
        removed_urls = sorted(before_set - after_set)

        # Changes on URLs present in both runs
        common_urls = before_set & after_set

        status_changes = []
        title_changes = []
        content_changes = []
        indexability_changes = []
        response_time_changes = []

        for url in sorted(common_urls):
            b = before_urls[url]
            a = after_urls[url]

            # Status code changes
            if b['status_code'] != a['status_code']:
                status_changes.append({
                    'url': url,
                    'before': b['status_code'],
                    'after': a['status_code'],
                })

            # Title changes
            b_title = (b.get('title_1') or '').strip()
            a_title = (a.get('title_1') or '').strip()
            if b_title != a_title:
                title_changes.append({
                    'url': url,
                    'before': b_title,
                    'after': a_title,
                })

            # Content hash changes (indicates content changed)
            b_hash = b.get('content_hash') or ''
            a_hash = a.get('content_hash') or ''
            if b_hash and a_hash and b_hash != a_hash:
                content_changes.append({
                    'url': url,
                    'before_hash': b_hash,
                    'after_hash': a_hash,
                    'before_words': b.get('word_count', 0),
                    'after_words': a.get('word_count', 0),
                })

            # Indexability changes
            b_idx = b.get('indexability', '')
            a_idx = a.get('indexability', '')
            if b_idx != a_idx:
                indexability_changes.append({
                    'url': url,
                    'before': b_idx,
                    'after': a_idx,
                })

            # Significant response time changes (>0.5s delta)
            b_rt = b.get('response_time') or 0
            a_rt = a.get('response_time') or 0
            delta = a_rt - b_rt
            if abs(delta) > 0.5:
                response_time_changes.append({
                    'url': url,
                    'before': round(b_rt, 3),
                    'after': round(a_rt, 3),
                    'delta': round(delta, 3),
                })

        # Issue diff
        new_issue_list = []
        resolved_issue_list = []

        all_issue_urls = set(before_issues.keys()) | set(after_issues.keys())
        for url in sorted(all_issue_urls):
            b_issues = set(before_issues.get(url, []))
            a_issues = set(after_issues.get(url, []))

            for issue_type in (a_issues - b_issues):
                new_issue_list.append({'url': url, 'issue_type': issue_type})
            for issue_type in (b_issues - a_issues):
                resolved_issue_list.append({'url': url, 'issue_type': issue_type})

        # Summary
        summary = {
            'before_count': len(before_urls),
            'after_count': len(after_urls),
            'new_count': len(new_urls),
            'removed_count': len(removed_urls),
            'status_change_count': len(status_changes),
            'title_change_count': len(title_changes),
            'content_change_count': len(content_changes),
            'indexability_change_count': len(indexability_changes),
            'new_issue_count': len(new_issue_list),
            'resolved_issue_count': len(resolved_issue_list),
        }

        logger.info(
            "Compared runs %d→%d: %d new, %d removed, %d status changes, "
            "%d title changes, %d content changes, %d new issues, %d resolved",
            run_id_before, run_id_after,
            len(new_urls), len(removed_urls), len(status_changes),
            len(title_changes), len(content_changes),
            len(new_issue_list), len(resolved_issue_list),
        )

        return {
            'success': True,
            'before_run_id': run_id_before,
            'after_run_id': run_id_after,
            'summary': summary,
            'new_urls': new_urls,
            'removed_urls': removed_urls,
            'status_changes': status_changes,
            'title_changes': title_changes,
            'content_changes': content_changes,
            'indexability_changes': indexability_changes,
            'response_time_changes': response_time_changes[:50],  # Cap at 50 for readability
            'new_issues': new_issue_list,
            'resolved_issues': resolved_issue_list,
        }

    except Exception as exc:
        logger.error("Crawl comparison failed: %s", exc, exc_info=True)
        return {'success': False, 'message': str(exc)}
    finally:
        conn.close()
