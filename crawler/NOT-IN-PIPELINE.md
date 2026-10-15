# crawler/ is not part of the audit pipeline

`audit.sh` and `run-full-audit.sh` do not import or run this package. The audit crawl is `full-extract.py`, which writes a JSON file; every later stage reads that file.

This package (URL filter, trap detector, simhash, PageRank, comparator, MySQL/SQLite storage) is kept for reference only. Its features are not part of any report. Parts of it may be moved into the pipeline later, each with its own test; until a part is listed in README.md under "What it checks", it does not run.
