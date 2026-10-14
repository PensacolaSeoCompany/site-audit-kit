# example-case: synthetic audit case folder

Invented site "Example Plumbing" at https://example.com (6 pages, served locally, nothing real).
The logs were produced by running the kit's own stages on that local site, then the local
address was replaced with example.com:

- checks.log   - audit-checks.py
- trace.log    - trace-errors.py
- hack.log     - hack-scan.py (no SERPAPI_KEY, so the Google index probe is skipped)
- findings.log - checks-to-findings.py
- findings-slug-auto-original.json - the sidecar checks-to-findings.py wrote
- findings-verified.json - the same sidecar after a verifier pass (no changes needed here)

Planted defects: a link to a missing page (/missing-coupon/), a page without an H1
(/services/drains/), an image without alt text (/about/), placeholder text and a dead
"Book now" link (/contact/), and a link to /old-page/ that does not exist.
