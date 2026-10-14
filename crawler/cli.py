"""Command-line interface for the site crawler (python -m crawler).
READ-ONLY: this tool only reads (GET/HEAD requests and page loads at most); it never submits a
form, logs in, or changes anything on a site.

Data goes to SQLite at $KIT_WORK/crawler.sqlite by default (no server needed);
set MYSQL_HOST and MYSQL_USER (plus MYSQL_PASSWORD, MYSQL_PORT, MYSQL_DATABASE)
to use MySQL instead.
"""

import argparse
import asyncio
import json
import logging
import sys

from crawler.config import CrawlConfig, MySQLConfig
from crawler.engine import crawl
from crawler.storage import CrawlStorage
from crawler.pagerank import compute_pagerank
from crawler.duplicate_detector import detect_duplicates


def setup_logging(verbose: bool = False) -> None:
    """Configure logging to stderr."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(name)s — %(message)s",
                            datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    logging.root.addHandler(handler)
    logging.root.setLevel(level)


def cmd_crawl(args: argparse.Namespace) -> int:
    """Execute a crawl."""
    config = CrawlConfig(
        seed_url=args.url,
        max_urls=args.max_urls,
        max_depth=args.depth,
        max_concurrent=args.concurrency,
        rate_limit_rps=args.rate_limit,
        timeout_seconds=args.timeout,
        respect_robots=not args.ignore_robots,
        follow_nofollow=args.follow_nofollow,
        crawl_external=args.crawl_external,
        allow_subdomains=args.allow_subdomains,
    )

    if args.user_agent:
        config.user_agent = args.user_agent

    if args.output:
        config.output_dir = args.output

    if args.include:
        config.include_patterns = args.include

    if args.exclude:
        config.exclude_patterns = args.exclude

    print(f"Starting crawl of {args.url}", file=sys.stderr)
    print(f"  max_urls={config.max_urls}, depth={config.max_depth}, "
          f"concurrency={config.max_concurrent}, rate={config.rate_limit_rps} rps",
          file=sys.stderr)

    result = asyncio.run(crawl(config))

    # Print summary to stdout as JSON
    summary = {
        "run_id": result.run_id,
        "domain": result.domain,
        "seed_url": result.seed_url,
        "urls_crawled": result.urls_crawled,
        "urls_queued": result.urls_queued,
        "urls_failed": result.urls_failed,
        "duration_seconds": round(result.duration_seconds, 1),
        "issues_detected": result.issues_detected,
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """Print crawl summary from the crawler database."""
    mysql_config = MySQLConfig()
    storage = CrawlStorage(mysql_config)
    try:
        storage.connect()
        summary = storage.get_crawl_summary(args.run_id)
        print(json.dumps(summary, indent=2, default=str))
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        storage.close()


def cmd_check(args: argparse.Namespace) -> int:
    """Run quick-win checker modules on a completed crawl run."""
    from crawler.checks.run_all import run_all_checks

    run_id = args.run_id
    only = None
    if args.only:
        only = [s.strip() for s in args.only.split(",")]

    label = ", ".join(only) if only else "all"
    print(f"Running checks ({label}) for run_id={run_id}...", file=sys.stderr)

    result = run_all_checks(run_id, only=only)

    # Print human-readable summary to stderr
    print(f"\n  Total new issues: {result['total_issues']} "
          f"({result['duration_seconds']}s)", file=sys.stderr)
    for name, check_result in result["checks"].items():
        count = check_result.get("issues_found", 0)
        err = check_result.get("error")
        status = f"{count} issues" if not err else f"FAILED: {err}"
        print(f"  {name:25s} {status}", file=sys.stderr)

    # JSON to stdout (strip verbose details lists for clean output)
    output = {
        "run_id": run_id,
        "total_issues": result["total_issues"],
        "duration_seconds": result["duration_seconds"],
        "checks": {
            name: {
                "issues_found": cr.get("issues_found", 0),
                **({"error": cr["error"]} if "error" in cr else {}),
                **({"note": cr["note"]} if "note" in cr else {}),
            }
            for name, cr in result["checks"].items()
        },
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Run Phase 2 analysis (PageRank + duplicate detection) for a crawl run."""
    run_id = args.run_id
    run_pagerank = not args.duplicates_only
    run_duplicates = not args.pagerank_only

    results = {}

    if run_pagerank:
        print(f"Computing PageRank for run_id={run_id}...", file=sys.stderr)
        pr_result = compute_pagerank(run_id)
        if pr_result.get("success"):
            print(f"  PageRank: {pr_result['urls_scored']} URLs scored, "
                  f"{pr_result['orphan_count']} orphans", file=sys.stderr)
            # Show top 10 by score
            if pr_result.get("scores"):
                top = sorted(pr_result["scores"].items(), key=lambda x: -x[1])[:10]
                print("  Top pages by link score:", file=sys.stderr)
                for url, score in top:
                    print(f"    {score:6.1f}  {url}", file=sys.stderr)
        else:
            print(f"  PageRank failed: {pr_result.get('message', 'unknown')}", file=sys.stderr)
        # Remove full scores dict from JSON output (can be huge)
        results["pagerank"] = {k: v for k, v in pr_result.items() if k != "scores"}

    if run_duplicates:
        print(f"Detecting duplicates for run_id={run_id}...", file=sys.stderr)
        dup_result = detect_duplicates(run_id)
        if dup_result.get("success"):
            print(f"  Duplicates: {dup_result['exact_duplicate_issues']} exact, "
                  f"{dup_result['near_duplicate_issues']} near-duplicate issues", file=sys.stderr)
        else:
            print(f"  Duplicate detection failed: {dup_result.get('message', 'unknown')}",
                  file=sys.stderr)
        results["duplicates"] = dup_result

    print(json.dumps(results, indent=2, default=str))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Diff two crawl runs (URLs, status, titles, content, issues)."""
    from crawler.comparator import compare_crawls

    result = compare_crawls(args.before, args.after)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("success") else 1


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="crawler",
        description="BFS web crawler with 72-column Screaming Frog-equivalent extraction",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # crawl subcommand
    crawl_parser = subparsers.add_parser("crawl", help="Crawl a website")
    crawl_parser.add_argument("--url", required=True, help="Seed URL to crawl")
    crawl_parser.add_argument("--max-urls", type=int, default=5000, help="Maximum URLs to crawl (default 5000)")
    crawl_parser.add_argument("--depth", type=int, default=10, help="Maximum crawl depth (default 10)")
    crawl_parser.add_argument("--concurrency", type=int, default=5, help="Concurrent fetchers (default 5)")
    crawl_parser.add_argument("--rate-limit", type=float, default=10.0, help="Requests per second (default 10)")
    crawl_parser.add_argument("--timeout", type=int, default=20, help="Request timeout in seconds (default 20)")
    crawl_parser.add_argument("--user-agent", help="Custom User-Agent string")
    crawl_parser.add_argument("--output", help="Output directory for reports")
    crawl_parser.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt")
    crawl_parser.add_argument("--follow-nofollow", action="store_true", help="Follow nofollow links")
    crawl_parser.add_argument("--crawl-external", action="store_true", help="Crawl external domains")
    crawl_parser.add_argument("--allow-subdomains", action="store_true", help="Allow subdomains of seed domain")
    crawl_parser.add_argument("--include", nargs="*", help="Include URL patterns (regex)")
    crawl_parser.add_argument("--exclude", nargs="*", help="Exclude URL patterns (regex)")

    # summary subcommand
    summary_parser = subparsers.add_parser("summary", help="Show crawl run summary")
    summary_parser.add_argument("--run-id", type=int, required=True, help="Crawl run ID")

    # analyze subcommand
    analyze_parser = subparsers.add_parser("analyze", help="Run Phase 2 analysis (PageRank + duplicates)")
    analyze_parser.add_argument("--run-id", type=int, required=True, help="Crawl run ID to analyze")
    analyze_parser.add_argument("--pagerank-only", action="store_true",
                                help="Run only PageRank computation")
    analyze_parser.add_argument("--duplicates-only", action="store_true",
                                help="Run only duplicate detection")

    # check subcommand
    check_parser = subparsers.add_parser("check", help="Run quick-win checker modules on a crawl run")
    check_parser.add_argument("--run-id", type=int, required=True, help="Crawl run ID to check")
    check_parser.add_argument("--only", type=str, default=None,
                              help="Comma-separated list of checks to run "
                                   "(mixed_content,heading_hierarchy,image_audit,canonical,security,hreflang)")

    # compare subcommand
    compare_parser = subparsers.add_parser("compare", help="Diff two crawl runs of the same site")
    compare_parser.add_argument("--before", type=int, required=True, help="Earlier crawl run ID")
    compare_parser.add_argument("--after", type=int, required=True, help="Later crawl run ID")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "crawl":
        sys.exit(cmd_crawl(args))
    elif args.command == "summary":
        sys.exit(cmd_summary(args))
    elif args.command == "analyze":
        sys.exit(cmd_analyze(args))
    elif args.command == "check":
        sys.exit(cmd_check(args))
    elif args.command == "compare":
        sys.exit(cmd_compare(args))


if __name__ == "__main__":
    main()
