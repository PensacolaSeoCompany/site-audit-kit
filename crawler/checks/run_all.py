"""Runner that executes all checker modules on a crawl run."""

import logging
import time

from crawler import db

logger = logging.getLogger(__name__)

# Registry of all available checks: (name, import_path, function_name)
CHECKS = [
    ("mixed_content", "crawler.checks.mixed_content", "check_mixed_content"),
    ("heading_hierarchy", "crawler.checks.heading_hierarchy", "check_heading_hierarchy"),
    ("image_audit", "crawler.checks.image_audit", "check_image_audit"),
    ("canonical", "crawler.checks.canonical_validator", "check_canonical_validation"),
    ("security", "crawler.checks.security_headers", "check_security_headers"),
    ("hreflang", "crawler.checks.hreflang_validator", "check_hreflang_validation"),
]


def _load_check(module_path: str, func_name: str):
    """Dynamically import and return a check function."""
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, func_name)


def run_all_checks(run_id: int, only: list = None) -> dict:
    """Run all (or selected) checker modules on a crawl run.

    Args:
        run_id: The crawl_run ID to check.
        only: Optional list of check names to run (e.g. ['mixed_content', 'canonical']).
              If None, all checks are run.

    Returns: {
        total_issues: int,
        checks: {name: {issues_found: N, ...}, ...},
        duration_seconds: float,
    }
    """
    conn = db.connect()

    results = {}
    total = 0
    t0 = time.time()

    try:
        for name, module_path, func_name in CHECKS:
            if only and name not in only:
                continue

            logger.info("Running check: %s (run_id=%d)", name, run_id)
            try:
                fn = _load_check(module_path, func_name)
                result = fn(run_id, conn=conn)
                results[name] = result
                total += result.get("issues_found", 0)
            except Exception as exc:
                logger.error("Check %s failed: %s", name, exc)
                results[name] = {"issues_found": 0, "error": str(exc)}
    finally:
        conn.close()

    elapsed = round(time.time() - t0, 2)
    return {
        "total_issues": total,
        "checks": results,
        "duration_seconds": elapsed,
    }
