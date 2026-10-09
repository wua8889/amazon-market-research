# -*- coding: utf-8 -*-
"""Extract drawback signals from the collected review text (positive reviews often keep their caveats)."""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
SRC = BASE / "data" / "processed" / "reviews_critical.csv"
OUT = BASE / "output" / "tables" / "review_signals.csv"

CAVEAT_MARKERS = [
    "but ", "however", "wish", "only issue", "only complaint", "downside", "cons:", "con:",
    "could be better", "could be improved", "my only", "a bit", "slightly", "not as ",
    "harder", "smaller than", "thinner", "flimsy", "cheap", "difficult", "struggle",
    "doesn't", "does not", "didn't", "not very", "would be better", "note that",
]

THEMES = {
    "zipper_quality": ["zipper", "zips", "zip"],
    "handle_quality": ["handle", "strap", "straps"],
    "material_thickness": ["thin", "flimsy", "cheap plastic", "sturdy", "durable", "thick"],
    "assembly": ["assemble", "assembly", "instructions", "put together", "screws"],
    "size_capacity": ["size", "fit", "space", "capacity", "holds", "small"],
    "smell_odor": ["odor", "smell", "smells"],
    "adhesive_mounting": ["adhesive", "stick", "stuck", "mount", "screw", "wall"],
    "stability_strength": ["sturdy", "stable", "wobbl", "weight", "heavy", "sag"],
    "visibility": ["clear", "see through", "see-through", "visible", "label"],
    "battery_power": ["battery", "charge", "charger", "pump", "electric", "cord"],
    "storage_saving": ["space", "closet", "compact", "fold", "stack"],
    "value_price": ["price", "value", "worth", "cost"],
}


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+|(?<=;)\s+", text)
    return [p.strip() for p in parts if p.strip()]


def main():
    with SRC.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    caveats = []
    theme_counter = Counter()
    theme_examples = {}

    for r in rows:
        body = r["review_body"]
        low = body.lower()
        for theme, words in THEMES.items():
            for w in words:
                if w in low:
                    theme_counter[theme] += 1
                    theme_examples.setdefault(theme, (r["asin"], r["rating"], body[:200]))
                    break
        for s in sentences(body):
            ls = s.lower()
            if any(m in ls for m in CAVEAT_MARKERS) and len(s) > 25:
                caveats.append({
                    "asin": r["asin"], "rating": r["rating"], "review_title": r["review_title"],
                    "caveat_sentence": s[:300],
                })

    with OUT.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["asin", "rating", "review_title", "caveat_sentence"])
        w.writeheader()
        w.writerows(caveats)

    print("reviews analysed:", len(rows))
    print("caveat sentences found:", len(caveats))
    print("output:", OUT)
    print()
    print("--- theme frequency (reviews mentioning the theme) ---")
    for t, c in theme_counter.most_common():
        print("  %-22s %d  e.g. [%s %s*] %s" % (t, c, theme_examples[t][0], theme_examples[t][1], theme_examples[t][2][:90]))
    print()
    print("--- caveat sentences ---")
    for c in caveats:
        print("  [%s %s*] %s" % (c["asin"], c["rating"], c["caveat_sentence"][:220]))

    with (BASE / "output" / "review_signals.json").open("w", encoding="utf-8") as fh:
        json.dump({"review_count": len(rows), "caveats": caveats,
                   "theme_counts": dict(theme_counter)}, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
