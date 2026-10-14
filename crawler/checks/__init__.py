"""Quick-win checker modules for post-crawl analysis.

Each module queries existing crawl data in the crawler database (SQLite by
default at $KIT_WORK/crawler.sqlite, MySQL when MYSQL_HOST and MYSQL_USER are
set) and inserts new issue types into crawl_issues.
"""
