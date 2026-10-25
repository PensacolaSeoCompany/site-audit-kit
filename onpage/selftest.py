"""Offline self-check for the on-page counter (used by install.sh / doctor.sh). No network.
Checks: the registry matches its schema (50 factors, known measure kinds), and one built-in page gives
the hand-counted answers below. Prints one line; exit 0 OK, 1 FAILED."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schemacheck  # noqa: E402
from measure import load_registry, measure_page, add_tf_idf  # noqa: E402

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Stump Removal | Stump Grinding Pros</title>
<meta name="description" content="Stump removal and stump grinding."><link rel="apple-touch-icon" href="/i.png"></head><body>
<h1>Stump Removal</h1><h2>Why remove a stump?</h2><h2>Stump grinding</h2>
<p>We do stump removal. Call us today.</p><img src="a.jpg" alt="stump"><img src="b.jpg">
<a href="/contact">Contact</a><a href="https://other.example/" rel="nofollow">Other</a></body></html>"""
WANT = {"h1_count": 1, "image_count": 2, "p_count": 1, "keyword_body": 2, "variations_body": 4,
        "variations_title": 2, "starts_with_variation_title": 1, "variations_h1_h2": 3, "exact_variation_headings": 1,
        "heading_questions": 1, "has_heading_question": 1, "sentence_count": 6, "has_apple_touch_icon": 1,
        "og_type_article": 0, "tf_idf": 0}


def main():
    reg = load_registry()
    errs = schemacheck.validate(reg, schemacheck.load("factor.schema.json"))
    if errs:
        print("FAILED registry: " + "; ".join(errs[:3]))
        return 1
    res = measure_page(PAGE, "https://stumps.example/", "stump removal", ["stump removal", "stump", "removal"], registry=reg)
    got = add_tf_idf([res], registry=reg)[0]["values"]
    bad = {k: (got.get(k), v) for k, v in WANT.items() if got.get(k) != v}
    if bad or len(got) != 50:
        print(f"FAILED measure: {bad} ({len(got)} values)")
        return 1
    print(f"50 factors, {len(WANT)} self-test values OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
