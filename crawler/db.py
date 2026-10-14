"""Database connection helper: SQLite by default, MySQL when configured.

The crawler stores its data in five tables: crawl_runs, crawl_urls,
crawl_links, crawl_issues and crawl_pagerank.

- Default: SQLite at $KIT_WORK/crawler.sqlite (override with CRAWLER_SQLITE).
  No database server is needed. The schema is created on first connect.
- MySQL: used only when both MYSQL_HOST and MYSQL_USER are set. pymysql is
  imported only on that path. The MySQL tables must already exist.

connect() returns an object with the pymysql surface the package uses:
cursor() (usable as a context manager), execute/executemany with %s
placeholders, fetchone/fetchall returning dicts, lastrowid, rowcount,
commit, rollback, close, ping and open.
"""

import re
import sqlite3
from pathlib import Path
from typing import Optional

from crawler.config import MySQLConfig, SQLITE_PATH, use_mysql

SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT,
    seed_url TEXT,
    source TEXT,
    started_at TEXT,
    completed_at TEXT,
    urls_crawled INTEGER,
    urls_queued INTEGER,
    duration_seconds INTEGER,
    config_json TEXT
);

CREATE TABLE IF NOT EXISTS crawl_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_run_id INTEGER NOT NULL,
    address TEXT,
    content_type TEXT,
    status_code INTEGER,
    status TEXT,
    indexability TEXT,
    indexability_status TEXT,
    title_1 TEXT,
    title_1_length INTEGER,
    title_1_pixel_width INTEGER,
    meta_description_1 TEXT,
    meta_description_1_length INTEGER,
    meta_description_1_pixel_width INTEGER,
    meta_keywords_1 TEXT,
    h1_1 TEXT,
    h1_1_length INTEGER,
    h2_1 TEXT,
    h2_1_length INTEGER,
    h2_2 TEXT,
    h2_2_length INTEGER,
    meta_robots_1 TEXT,
    x_robots_tag_1 TEXT,
    canonical_1 TEXT,
    size_bytes INTEGER,
    co2_mg REAL,
    carbon_rating TEXT,
    word_count INTEGER,
    sentence_count INTEGER,
    avg_words_per_sentence REAL,
    flesch_reading_ease REAL,
    readability TEXT,
    text_ratio REAL,
    crawl_depth INTEGER,
    folder_depth INTEGER,
    outlinks INTEGER,
    unique_outlinks INTEGER,
    external_outlinks INTEGER,
    unique_external_outlinks INTEGER,
    inlinks INTEGER,
    unique_inlinks INTEGER,
    content_hash TEXT,
    response_time REAL,
    last_modified TEXT,
    redirect_url TEXT,
    redirect_type TEXT,
    language TEXT,
    http_version TEXT,
    crawl_timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_crawl_urls_run ON crawl_urls (crawl_run_id);

CREATE TABLE IF NOT EXISTS crawl_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_run_id INTEGER NOT NULL,
    source_url TEXT,
    target_url TEXT,
    anchor_text TEXT,
    alt_text TEXT,
    rel_attribute TEXT,
    link_type TEXT,
    link_position TEXT,
    follow_status TEXT,
    is_internal INTEGER
);
CREATE INDEX IF NOT EXISTS idx_crawl_links_run ON crawl_links (crawl_run_id);

CREATE TABLE IF NOT EXISTS crawl_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_run_id INTEGER NOT NULL,
    url TEXT,
    issue_type TEXT,
    severity TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_crawl_issues_run ON crawl_issues (crawl_run_id);

CREATE TABLE IF NOT EXISTS crawl_pagerank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_run_id INTEGER NOT NULL,
    url TEXT,
    raw_pagerank REAL,
    link_score_0_100 REAL,
    inlink_count INTEGER,
    outlink_count INTEGER,
    is_orphan INTEGER
);
CREATE INDEX IF NOT EXISTS idx_crawl_pagerank_run ON crawl_pagerank (crawl_run_id);
"""

# %s -> ?, %% -> %  (pymysql "format" paramstyle to sqlite "qmark")
_PLACEHOLDER = re.compile(r"%(%|s)")


def translate_sql(sql: str) -> str:
    """Rewrite the MySQL dialect bits this package uses into SQLite SQL."""
    sql = _PLACEHOLDER.sub(lambda m: "%" if m.group(1) == "%" else "?", sql)
    sql = re.sub(r"\bNOW\(\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
    sql = re.sub(r"\bINSERT\s+IGNORE\b", "INSERT OR IGNORE", sql, flags=re.I)
    sql = sql.replace("`", '"')
    if re.search(r"ON\s+DUPLICATE\s+KEY|GROUP_CONCAT\([^)]*SEPARATOR", sql, re.I):
        raise NotImplementedError("MySQL-only SQL; write a portable statement: " + sql[:120])
    return sql


def _dict_row(cursor, row):
    return {d[0]: row[i] for i, d in enumerate(cursor.description)}


class SQLiteCursor:
    """pymysql-style cursor over sqlite3."""

    def __init__(self, conn: sqlite3.Connection):
        self._cur = conn.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def execute(self, sql, params=None):
        self._cur.execute(translate_sql(sql), tuple(params) if params is not None else ())
        return self._cur.rowcount

    def executemany(self, sql, seq):
        self._cur.executemany(translate_sql(sql), [tuple(p) for p in seq])
        return self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()


class SQLiteConnection:
    """pymysql-style connection over sqlite3."""

    dialect = "sqlite"

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(str(path), timeout=30)
        self._conn.row_factory = _dict_row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @property
    def open(self) -> bool:
        return self._conn is not None

    def ping(self, reconnect: bool = True) -> None:
        if self._conn is None:
            raise sqlite3.ProgrammingError("connection closed")

    def cursor(self) -> SQLiteCursor:
        return SQLiteCursor(self._conn)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def connect(cfg: Optional[MySQLConfig] = None):
    """Open the crawler database: MySQL if configured, else SQLite."""
    cfg = cfg or MySQLConfig()
    if use_mysql(cfg):
        import pymysql
        from pymysql.cursors import DictCursor
        return pymysql.connect(
            host=cfg.host, port=cfg.port, user=cfg.user,
            password=cfg.password, database=cfg.database,
            charset=cfg.charset, cursorclass=DictCursor, autocommit=False,
        )
    return SQLiteConnection(SQLITE_PATH)


def describe(cfg: Optional[MySQLConfig] = None) -> str:
    """Human-readable name of the database connect() would open (no secrets)."""
    cfg = cfg or MySQLConfig()
    if use_mysql(cfg):
        return f"mysql {cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}"
    return f"sqlite {SQLITE_PATH}"
