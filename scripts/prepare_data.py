# -*- coding: utf-8 -*-
"""
Step 2 + Step 3: keyword re-classification and BSR-based sales estimation.

Input : data/raw/amazon_bsr_<timestamp>.csv  (newest by default, or --source)
Output: data/processed/bsr_filtered.csv       products kept as home storage/organization
        data/processed/bsr_excluded.csv       removed products with the reason (for audit)
        data/processed/bsr_with_sales.csv     kept products + estimated_monthly_sales / estimated_monthly_revenue

Sales estimation follows the amazon-sales-estimator skill (Mode A - BSR Calculator):
  1. interpolate the skill's BSR-to-sales reference table (Amazon US, Home & Kitchen baseline)
  2. apply the category multiplier (Home & Kitchen = 1.0x)
  3. apply the marketplace factor (US = 1.0x)
  4. monthly revenue = monthly units x price
"""

import argparse
import csv
import math
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw"
PROC_DIR = BASE_DIR / "data" / "processed"
def _default_source() -> Path:
    """Newest data/raw/amazon_bsr_*.csv so the file name never has to be hard-coded."""
    candidates = sorted(RAW_DIR.glob("amazon_bsr_*.csv"))
    return candidates[-1] if candidates else RAW_DIR / "amazon_bsr.csv"


SOURCE_CSV = _default_source()

# --------------------------------------------------------------------------- #
# 1. keyword rules (all patterns use word boundaries)
# --------------------------------------------------------------------------- #

KEEP_PATTERNS = [
    r"\bstorage\b", r"\borganizer?s?\b", r"\borganizers?\b", r"\bbaskets?\b", r"\bbins?\b",
    r"\bshel(?:f|ves|ving)\b", r"\bracks?\b", r"\bdrawers?\b", r"\bcontainers?\b",
    r"\bboxes?\b", r"\bclosets?\b", r"\bcabinets?\b", r"\bhangers?\b", r"\bcadd(?:y|ies)\b",
]

EXCLUDE_PATTERNS = [
    r"\bwater bottles?\b", r"\bbottles?\b", r"\btumblers?\b", r"\blunch boxes?\b",
    r"\blunch bags?\b", r"\bhooks?\b", r"\bmugs?\b", r"\bcups?\b", r"\bdrinkware\b",
    r"\bcoolers?\b", r"\bfood jars?\b", r"\bthermos\b", r"\bcanisters?\b",
]

# measured quantities such as "3.2 Cup", "12-Cup", "4-Cup Capacity", "cup holder" are not products
MEASURE_PATTERNS = [
    r"\b\d+(?:\.\d+)?\s*-?\s*cups?\b",
    r"\bcups?\s+(?:holder|capacity|sleeve|sized)\b",
    r"\bcup\s+holders?\b",
]

# keep these even if an exclude word shows up elsewhere in the title
KEEP_OVERRIDES = [r"\bhangers?\b"]

# borderline: hits a keep word but sits between home storage and kitchen/bath consumables
BORDERLINE_PATTERNS = [
    r"\bfood ?saver\b", r"\bfood storage\b", r"\bvacuum sealer\b", r"\bsealer bags?\b",
    r"\bvacuum storage bags?\b", r"\blaundry bags?\b", r"\blaundry baskets?\b",
    r"\bhamper\b", r"\bshower cadd(?:y|ies)\b", r"\bsponge holder\b", r"\bsink\b",
    r"\bmoving bags?\b", r"\bpacking bags?\b", r"\bjewelry box", r"\bgift wrap\b",
    r"\bmeal prep\b", r"\bchip clips?\b", r"\bbag clips?\b", r"\bcanister\b",
]

# kitchen / bath consumables: matched a keep word but are not storage furniture
KITCHEN_BATH_PATTERNS = [
    r"\bfood storage\b", r"\bfood ?saver\b", r"\bfoodsaver\b", r"\bvacuum sealer\b",
    r"\bsealer bags?\b", r"\bsponge holder\b", r"\bdish drying rack\b",
    r"\bpaper towel holder\b", r"\bsink mats?\b", r"\bsilicone (?:mat|liner|sink)\b",
    r"\bshower cadd(?:y|ies)\b", r"\bmeal prep\b", r"\bchip clips?\b",
    r"\bbag clips?\b", r"\bcanisters?\b", r"\btrash\b", r"\bfood jars?\b",
    r"\bslider storage bags?\b", r"\bgallon\b", r"\bsilverware\b", r"\butensil\b",
    r"\bziploc\b", r"\bsandwich bags?\b",
]


def match_any(patterns, text):
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return pat
    return ""


def strip_measures(text):
    cleaned = text
    for pat in MEASURE_PATTERNS:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)
    return cleaned


# --------------------------------------------------------------------------- #
# 2. BSR -> monthly units (skill reference table)
# --------------------------------------------------------------------------- #

BSR_TABLE = [
    (1, 100, 3000, 10000),
    (100, 500, 1000, 3000),
    (500, 2000, 300, 1000),
    (2000, 10000, 100, 300),
    (10000, 50000, 30, 100),
    (50000, 100000, 10, 30),
    (100000, 1000000, 2, 10),
]

ANCHORS = [
    (math.sqrt(lo * hi), math.sqrt(s_lo * s_hi))
    for lo, hi, s_lo, s_hi in BSR_TABLE
]

CATEGORY_MULTIPLIER = 1.0   # Home & Kitchen
MARKETPLACE_FACTOR = 1.0    # Amazon US
ESTIMATE_BASIS = ("estimate_based_on_BSR | Home&Kitchen baseline x1.0 | US x1.0 | "
                  "source: amazon-sales-estimator skill")


def bsr_to_monthly_units(bsr: int) -> float:
    if bsr <= 0:
        return 0.0
    xs = [math.log(a[0]) for a in ANCHORS]
    ys = [math.log(a[1]) for a in ANCHORS]
    x = math.log(bsr)

    if x <= xs[0]:
        slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
        y = ys[0] + slope * (x - xs[0])
    elif x >= xs[-1]:
        slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
        y = ys[-1] + slope * (x - xs[-1])
    else:
        y = ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                ratio = (x - xs[i]) / (xs[i + 1] - xs[i])
                y = ys[i] + ratio * (ys[i + 1] - ys[i])
                break

    units = math.exp(y) * CATEGORY_MULTIPLIER * MARKETPLACE_FACTOR
    return max(1.0, round(units))


# --------------------------------------------------------------------------- #
# 3. main
# --------------------------------------------------------------------------- #

GENERIC_WORDS = {
    "pack", "packs", "pcs", "pc", "set", "sets", "extra", "large", "jumbo", "heavy",
    "duty", "premium", "new", "the", "vacuum", "storage", "bag", "bags", "box", "boxes",
    "bin", "bins", "shelf", "shelves", "shelving", "rack", "racks", "organizer",
    "organizers", "container", "containers", "basket", "baskets", "drawer", "drawers",
    "closet", "cabinet", "hanger", "hangers", "laundry", "moving", "mesh", "clear",
    "foldable", "stackable", "adjustable", "steel", "plastic", "space", "saving",
    "seal", "travel", "portable", "collapsible", "under", "over", "with", "for",
    "strong", "hand", "handles", "zipper", "zippers", "carrying", "saver", "combo",
    "size", "sizes", "quart", "gallon", "inch", "inches", "tier", "tiers", "pairs",
    "clips", "totes", "bulk", "made", "pump", "dark", "blue", "black", "white",
    "grey", "gray", "beige", "natural", "silver", "rose", "kids", "fabric", "metal",
    "wire", "slim", "velvet", "drying", "dish", "hat", "door", "garage", "bed", "bath",
    "kitchen", "xl", "mini", "small", "medium", "soft", "stainless", "aluminum",
    "silicone", "wooden", "bamboo", "non", "slip", "nonslip", "sturdy", "durable",
    "versatile", "essential", "essentials", "bedding", "clothes", "clothing", "adult",
    "men", "women", "transparent", "quart", "can", "open", "front", "wide", "deep",
    "comfortable", "zipper", "pairs", "tier", "inch", "compact", "design", "made",
}
BRAND_SUFFIXES = {"basics", "home", "houseware", "housewares", "usa", "living", "life", "day"}


def brand_guess(title: str) -> str:
    """Heuristic brand extraction from the title prefix; not verified against Amazon brand field."""
    tokens = [t.strip(",|") for t in re.split(r"[\s,|]+", title.strip()) if t.strip(",|")]
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if re.match(r"^[0-9]", low):
            continue
        if low in GENERIC_WORDS:
            continue
        brand = tok
        if i + 1 < len(tokens) and tokens[i + 1].lower() in BRAND_SUFFIXES:
            brand = brand + " " + tokens[i + 1]
        return brand
    return "UNBRANDED"


def main(argv=None) -> int:
    global SOURCE_CSV
    ap = argparse.ArgumentParser(description="Filter a BSR export and add BSR-estimated sales.")
    ap.add_argument("--source", default="", help="path to data/raw/amazon_bsr_*.csv (default: newest in data/raw)")
    args = ap.parse_args(argv)
    if args.source:
        SOURCE_CSV = Path(args.source)

    if not SOURCE_CSV.exists():
        print("SOURCE_NOT_FOUND:", SOURCE_CSV)
        return 1

    with SOURCE_CSV.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    kept, excluded, borderline = [], [], []
    for row in rows:
        title = (row.get("title") or "").strip()
        cleaned = strip_measures(title)

        drop_hit = match_any(EXCLUDE_PATTERNS, cleaned)
        override = match_any(KEEP_OVERRIDES, cleaned)
        if drop_hit and override:
            drop_hit = ""
        if drop_hit:
            row["exclude_reason"] = "exclude word: %s" % drop_hit
            excluded.append(row)
            continue

        keep_hit = match_any(KEEP_PATTERNS, cleaned)
        if not keep_hit:
            row["exclude_reason"] = "no keep keyword matched"
            excluded.append(row)
            continue

        edge_hit = match_any(BORDERLINE_PATTERNS, cleaned)
        kb_hit = match_any(KITCHEN_BATH_PATTERNS, cleaned)
        row["scope"] = "kitchen_bath" if kb_hit else "core"
        row["scope_reason"] = ("kitchen/bath consumable: %s" % kb_hit) if kb_hit else ""
        row["match_keyword"] = keep_hit
        row["borderline"] = ("borderline: %s" % edge_hit) if edge_hit else ""
        kept.append(row)
        if edge_hit:
            borderline.append(row)

    for row in kept:
        bsr = int(row["bsr_rank"])
        price = float(row["price"]) if row.get("price") else 0.0
        units = bsr_to_monthly_units(bsr)
        row["estimated_monthly_sales"] = int(units)
        row["estimated_monthly_revenue"] = round(units * price, 2)
        row["estimate_basis"] = ESTIMATE_BASIS
        row["brand_guess"] = brand_guess(row["title"])

    PROC_DIR.mkdir(parents=True, exist_ok=True)

    filtered_csv = PROC_DIR / "bsr_filtered.csv"
    with filtered_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        fields = ["asin", "title", "price", "rating", "review_count", "bsr_rank",
                  "product_url", "page", "category", "scope", "scope_reason",
                  "match_keyword", "borderline", "brand_guess"]
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)

    excluded_csv = PROC_DIR / "bsr_excluded.csv"
    with excluded_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        fields = ["asin", "title", "price", "rating", "review_count", "bsr_rank",
                  "product_url", "exclude_reason"]
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(excluded)

    with_sales_csv = PROC_DIR / "bsr_with_sales.csv"
    with with_sales_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        fields = ["asin", "title", "price", "rating", "review_count", "bsr_rank",
                  "product_url", "page", "category", "scope", "scope_reason", "brand_guess",
                  "estimated_monthly_sales", "estimated_monthly_revenue", "estimate_basis"]
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)

    total_units = sum(r["estimated_monthly_sales"] for r in kept)
    total_rev = sum(r["estimated_monthly_revenue"] for r in kept)

    print("SOURCE ROWS     :", len(rows))
    print("KEPT            :", len(kept))
    print("EXCLUDED        :", len(excluded))
    print("BORDERLINE KEPT :", len(borderline))
    core = [r for r in kept if r["scope"] == "core"]
    kb = [r for r in kept if r["scope"] == "kitchen_bath"]
    print("CORE (storage furniture) :", len(core))
    print("KITCHEN/BATH CONSUMABLES :", len(kb))
    print("  core units/month      :", sum(r["estimated_monthly_sales"] for r in core))
    print("  core revenue/month    : %.0f" % sum(r["estimated_monthly_revenue"] for r in core))
    print("EST UNITS/MONTH :", total_units)
    print("EST REVENUE/MONTH: %.0f" % total_rev)
    print("OUTPUT          :", filtered_csv)
    print("OUTPUT          :", excluded_csv)
    print("OUTPUT          :", with_sales_csv)
    print()
    print("--- EXCLUDED (reason | bsr | title) ---")
    for row in excluded:
        print(" -", row["exclude_reason"], "| #", row["bsr_rank"], "|", row["title"][:88])
    print()
    print("--- BORDERLINE KEPT (flag | bsr | title) ---")
    for row in borderline:
        print(" -", row["borderline"], "| #", row["bsr_rank"], "|", row["title"][:88])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())






