"""Validate hreflang annotations: reciprocal tags, valid language codes, self-references."""

import logging
import re
from crawler import db

logger = logging.getLogger(__name__)

# ISO 639-1 two-letter language codes (subset of most common)
VALID_LANG_CODES = {
    "aa", "ab", "af", "ak", "am", "an", "ar", "as", "av", "ay", "az",
    "ba", "be", "bg", "bh", "bi", "bm", "bn", "bo", "br", "bs",
    "ca", "ce", "ch", "co", "cr", "cs", "cu", "cv", "cy",
    "da", "de", "dv", "dz",
    "ee", "el", "en", "eo", "es", "et", "eu",
    "fa", "ff", "fi", "fj", "fo", "fr", "fy",
    "ga", "gd", "gl", "gn", "gu", "gv",
    "ha", "he", "hi", "ho", "hr", "ht", "hu", "hy", "hz",
    "ia", "id", "ie", "ig", "ii", "ik", "io", "is", "it", "iu",
    "ja", "jv",
    "ka", "kg", "ki", "kj", "kk", "kl", "km", "kn", "ko", "kr", "ks", "ku", "kv", "kw", "ky",
    "la", "lb", "lg", "li", "ln", "lo", "lt", "lu", "lv",
    "mg", "mh", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my",
    "na", "nb", "nd", "ne", "ng", "nl", "nn", "no", "nr", "nv", "ny",
    "oc", "oj", "om", "or", "os",
    "pa", "pi", "pl", "ps", "pt",
    "qu",
    "rm", "rn", "ro", "ru", "rw",
    "sa", "sc", "sd", "se", "sg", "si", "sk", "sl", "sm", "sn", "so", "sq", "sr", "ss", "st", "su", "sv", "sw",
    "ta", "te", "tg", "th", "ti", "tk", "tl", "tn", "to", "tr", "ts", "tt", "tw", "ty",
    "ug", "uk", "ur", "uz",
    "ve", "vi", "vo",
    "wa", "wo",
    "xh",
    "yi", "yo",
    "za", "zh", "zu",
}
# x-default is a valid hreflang value
VALID_HREFLANG_SPECIAL = {"x-default"}


def _parse_hreflang_value(rel_attr: str) -> str:
    """Extract the hreflang value from a rel_attribute like 'alternate hreflang=en-US'."""
    match = re.search(r"hreflang=([^\s,]+)", rel_attr, re.I)
    return match.group(1).strip() if match else ""


def _is_valid_hreflang(value: str) -> bool:
    """Check if an hreflang value is valid (ISO 639-1, optionally with region)."""
    if not value:
        return False
    if value.lower() in VALID_HREFLANG_SPECIAL:
        return True
    # Accept: "en", "en-US", "en-us", "zh-Hans", "pt-BR"
    parts = value.lower().split("-")
    if not parts:
        return False
    # Language code must be valid ISO 639-1
    return parts[0] in VALID_LANG_CODES


def check_hreflang_validation(run_id: int, conn=None) -> dict:
    """Validate hreflang annotations across all crawled pages.

    Uses crawl_links where link_type='alternate' and rel_attribute contains 'hreflang'.

    Detects:
    - hreflang_invalid_code: hreflang value is not a valid ISO 639-1 code
    - hreflang_missing_self: page declares hreflang for other languages but not itself
    - hreflang_missing_reciprocal: page A has hreflang to B, but B does not link back to A
    - hreflang_target_not_200: hreflang target URL returns non-200 status

    Returns: {issues_found: int, details: [...]}
    """
    close_conn = False
    if conn is None:
        conn = db.connect()
        close_conn = True

    try:
        details = []
        with conn.cursor() as cur:
            # Get hreflang links
            cur.execute("""
                SELECT source_url, target_url, rel_attribute
                FROM crawl_links
                WHERE crawl_run_id = %s
                  AND link_type = 'alternate'
                  AND rel_attribute LIKE '%%hreflang%%'
            """, (run_id,))
            hreflang_links = cur.fetchall()

        if not hreflang_links:
            logger.info("hreflang_validator: no hreflang data found for run_id=%d", run_id)
            return {"issues_found": 0, "details": [], "note": "No hreflang annotations found"}

        # Build URL -> status lookup
        with conn.cursor() as cur:
            cur.execute("""
                SELECT address, status_code
                FROM crawl_urls
                WHERE crawl_run_id = %s
            """, (run_id,))
            url_status = {row["address"]: row["status_code"] for row in cur.fetchall()}

        # Build per-page hreflang map: {source_url: [(hreflang_value, target_url), ...]}
        page_hreflangs = {}
        for link in hreflang_links:
            src = link["source_url"]
            tgt = link["target_url"]
            hreflang_val = _parse_hreflang_value(link["rel_attribute"] or "")
            page_hreflangs.setdefault(src, []).append((hreflang_val, tgt))

        # Build reverse map for reciprocal checking: {target_url: {source_url, ...}}
        reverse_map = {}
        for src, entries in page_hreflangs.items():
            for _, tgt in entries:
                reverse_map.setdefault(tgt, set()).add(src)

        issues = []
        seen = set()  # Prevent duplicate issues

        for source, entries in page_hreflangs.items():
            has_self_ref = False
            source_norm = source.rstrip("/")

            for hreflang_val, target in entries:
                target_norm = target.rstrip("/")

                # Check: valid hreflang code
                if not _is_valid_hreflang(hreflang_val):
                    key = ("hreflang_invalid_code", source, hreflang_val)
                    if key not in seen:
                        seen.add(key)
                        detail = f"Invalid hreflang code \"{hreflang_val}\" pointing to {target[:200]}"
                        issues.append((run_id, source, "hreflang_invalid_code", "warning", detail))
                        details.append({"url": source, "hreflang": hreflang_val, "target": target, "issue": "hreflang_invalid_code"})

                # Check: self-referencing
                if target_norm == source_norm:
                    has_self_ref = True

                # Check: target status
                if target in url_status:
                    tgt_sc = url_status[target] or 0
                    if tgt_sc != 200:
                        key = ("hreflang_target_not_200", source, target)
                        if key not in seen:
                            seen.add(key)
                            detail = f"Hreflang target returns HTTP {tgt_sc}: {target[:200]}"
                            issues.append((run_id, source, "hreflang_target_not_200", "warning", detail))
                            details.append({"url": source, "target": target, "status": tgt_sc, "issue": "hreflang_target_not_200"})

                # Check: reciprocal - does the target page hreflang back to source?
                if target_norm != source_norm and target in page_hreflangs:
                    target_targets = {t.rstrip("/") for _, t in page_hreflangs[target]}
                    if source_norm not in target_targets:
                        key = ("hreflang_missing_reciprocal", source, target)
                        if key not in seen:
                            seen.add(key)
                            detail = f"Missing reciprocal hreflang: {source} -> {target} but {target} does not link back"
                            issues.append((run_id, source, "hreflang_missing_reciprocal", "warning", detail))
                            details.append({"url": source, "target": target, "issue": "hreflang_missing_reciprocal"})

            # Check: missing self-reference
            if entries and not has_self_ref:
                key = ("hreflang_missing_self", source)
                if key not in seen:
                    seen.add(key)
                    detail = f"Page has hreflang annotations for {len(entries)} locale(s) but no self-referencing tag"
                    issues.append((run_id, source, "hreflang_missing_self", "warning", detail))
                    details.append({"url": source, "issue": "hreflang_missing_self", "locale_count": len(entries)})

        # Insert all issues
        if issues:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO crawl_issues (crawl_run_id, url, issue_type, severity, details) "
                    "VALUES (%s, %s, %s, %s, %s)", issues)
                conn.commit()

        logger.info("hreflang_validator: %d issues for run_id=%d", len(details), run_id)
        return {"issues_found": len(details), "details": details}

    finally:
        if close_conn:
            conn.close()
