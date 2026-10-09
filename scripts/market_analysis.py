# -*- coding: utf-8 -*-
"""Enriched market analysis: uses detail-page fields (brand, seller, country) when available."""
import csv
import json
import re
import statistics as st
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
PROC = BASE_DIR / "data" / "processed"
OUT = BASE_DIR / "output"
TABLES = OUT / "tables"

DETAIL_CSV = PROC / "bsr_with_details.csv"
FALLBACK_CSV = PROC / "bsr_with_sales.csv"
SELLER_CSV = PROC / "seller_profiles.csv"

PRICE_BANDS = [(0, 10, "under $10"), (10, 20, "$10-20"), (20, 30, "$20-30"),
               (30, 50, "$30-50"), (50, 10 ** 9, "$50+")]
RANK_TIERS = [(1, 10, "BSR 1-10"), (11, 25, "BSR 11-25"), (26, 50, "BSR 26-50"),
              (51, 100, "BSR 51-100")]
TITLE_KEYWORDS = ["storage", "organizer", "basket", "bin", "shelf", "rack", "drawer",
                  "container", "box", "closet", "cabinet", "hanger", "shoe", "laundry",
                  "vacuum", "mesh", "foldable", "stackable", "clear", "steel", "plastic",
                  "adjustable", "space saving", "heavy duty", "set", "pack"]
COUNTRY_LABEL = {"china": "中国大陆", "hong_kong": "香港", "china_likely": "疑似中国(仅区域)",
                 "united_states": "美国", "canada": "加拿大", "united_kingdom": "英国",
                 "germany": "德国", "japan": "日本", "vietnam": "越南", "india": "印度",
                 "korea": "韩国", "": "未获取"}


def write_csv(name, fieldnames, rows):
    TABLES.mkdir(parents=True, exist_ok=True)
    p = TABLES / name
    with p.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return p


def load():
    src = DETAIL_CSV if DETAIL_CSV.exists() else FALLBACK_CSV
    with src.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    sellers = {}
    if SELLER_CSV.exists():
        with SELLER_CSV.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                sellers[r["merchant_id"]] = r
    for r in rows:
        r["price"] = float(r["price"] or 0)
        r["rating"] = float(r["rating"] or 0)
        r["review_count"] = int(r["review_count"] or 0)
        r["bsr_rank"] = int(r["bsr_rank"])
        r["estimated_monthly_sales"] = int(r["estimated_monthly_sales"])
        r["estimated_monthly_revenue"] = float(r["estimated_monthly_revenue"])
        mid = (r.get("merchant_id") or "").strip()
        prof = sellers.get(mid, {})
        r["seller_country"] = prof.get("country", "")
        r["seller_business_name"] = prof.get("business_name", "")
        r["seller_address"] = prof.get("business_address", "")
        r["is_amazon"] = (r.get("is_amazon_seller") == "yes")
        r["brand"] = (r.get("brand_detail") or r.get("brand_guess") or "UNKNOWN").strip()
        r["sold_by"] = (r.get("sold_by") or "").strip()
    return rows, src.name


def table(rows, keyfn, label, total_units, total_rev):
    out = []
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(r)
    for name, sub in groups.items():
        u = sum(r["estimated_monthly_sales"] for r in sub)
        rv = sum(r["estimated_monthly_revenue"] for r in sub)
        out.append({
            "group": label, "name": name, "products": len(sub),
            "product_share": round(len(sub) / len(rows) * 100, 1),
            "est_units": u, "unit_share": round(u / total_units * 100, 1),
            "est_revenue": round(rv, 2), "revenue_share": round(rv / total_rev * 100, 1),
            "avg_price": round(st.mean(r["price"] for r in sub), 2),
            "avg_rating": round(st.mean(r["rating"] for r in sub), 2),
            "median_reviews": int(st.median(r["review_count"] for r in sub)),
        })
    return sorted(out, key=lambda x: -x["est_revenue"])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows_all, src_name = load()
    core = [r for r in rows_all if r["scope"] == "core"]
    summary = {"source_file": src_name}

    # audit
    def audit(rows, label):
        return {
            "label": label, "rows": len(rows),
            "unique_asin": len({r["asin"] for r in rows}),
            "bsr_min": min(r["bsr_rank"] for r in rows),
            "bsr_max": max(r["bsr_rank"] for r in rows),
            "price_min": min(r["price"] for r in rows),
            "price_max": max(r["price"] for r in rows),
            "price_median": round(st.median([r["price"] for r in rows]), 2),
            "price_mean": round(st.mean([r["price"] for r in rows]), 2),
            "rating_mean": round(st.mean([r["rating"] for r in rows]), 2),
            "review_median": int(st.median([r["review_count"] for r in rows])),
            "review_mean": int(st.mean([r["review_count"] for r in rows])),
            "review_max": max(r["review_count"] for r in rows),
            "est_units_total": sum(r["estimated_monthly_sales"] for r in rows),
            "est_rev_total": round(sum(r["estimated_monthly_revenue"] for r in rows), 2),
            "est_units_median": int(st.median([r["estimated_monthly_sales"] for r in rows])),
            "est_rev_median": round(st.median([r["estimated_monthly_revenue"] for r in rows]), 2),
            "has_brand": sum(1 for r in rows if r.get("brand_detail")),
            "has_sold_by": sum(1 for r in rows if r.get("sold_by")),
            "has_merchant_id": sum(1 for r in rows if r.get("merchant_id")),
            "has_ships_from": sum(1 for r in rows if r.get("ships_from")),
            "has_date_first_available": sum(1 for r in rows if r.get("date_first_available")),
            "amazon_self_sold": sum(1 for r in rows if r["is_amazon"]),
        }
    summary["audit_all"] = audit(rows_all, "kept 63")
    summary["audit_core"] = audit(core, "core 40")

    total_u = sum(r["estimated_monthly_sales"] for r in core)
    total_r = sum(r["estimated_monthly_revenue"] for r in core)

    # price bands
    pb = []
    for lo, hi, name in PRICE_BANDS:
        sub = [r for r in core if lo <= r["price"] < hi]
        if not sub:
            continue
        u = sum(r["estimated_monthly_sales"] for r in sub)
        rv = sum(r["estimated_monthly_revenue"] for r in sub)
        pb.append({"price_band": name, "products": len(sub),
                   "product_share": round(len(sub) / len(core) * 100, 1),
                   "est_units": u, "unit_share": round(u / total_u * 100, 1),
                   "est_revenue": round(rv, 2), "revenue_share": round(rv / total_r * 100, 1),
                   "avg_rating": round(st.mean(r["rating"] for r in sub), 2),
                   "median_reviews": int(st.median(r["review_count"] for r in sub)),
                   "avg_price": round(st.mean(r["price"] for r in sub), 2)})
    write_csv("price_bands_core.csv", (list(pb[0].keys()) if pb else ["empty"]), pb)
    summary["price_bands_core"] = pb

    # review thresholds
    rt = []
    for lo, hi, name in RANK_TIERS:
        sub = [r for r in core if lo <= r["bsr_rank"] <= hi]
        if not sub:
            continue
        rt.append({"tier": name, "products": len(sub),
                   "review_median": int(st.median(r["review_count"] for r in sub)),
                   "review_min": min(r["review_count"] for r in sub),
                   "review_max": max(r["review_count"] for r in sub),
                   "units_median": int(st.median(r["estimated_monthly_sales"] for r in sub)),
                   "revenue_total": round(sum(r["estimated_monthly_revenue"] for r in sub), 2),
                   "avg_price": round(st.mean(r["price"] for r in sub), 2)})
    write_csv("review_threshold_core.csv", (list(rt[0].keys()) if rt else ["empty"]), rt)
    summary["review_threshold_core"] = rt

    # brand (real Brand field now)
    bt = table(core, lambda r: r["brand"], "brand", total_u, total_r)
    write_csv("brand_concentration_core.csv", (list(bt[0].keys()) if bt else ["empty"]), bt)
    summary["brand_table_core"] = bt
    summary["brand_top3_rev_share"] = round(sum(b["revenue_share"] for b in bt[:3]), 1)
    summary["brand_top5_rev_share"] = round(sum(b["revenue_share"] for b in bt[:5]), 1)
    summary["brand_top10_rev_share"] = round(sum(b["revenue_share"] for b in bt[:10]), 1)
    summary["brand_count"] = len(bt)
    summary["brand_single_product"] = sum(1 for b in bt if b["products"] == 1)

    # seller type
    st_type = table(core, lambda r: "Amazon 自营" if r["is_amazon"] else "第三方卖家",
                    "seller_type", total_u, total_r)
    write_csv("seller_type_core.csv", (list(st_type[0].keys()) if st_type else ["empty"]), st_type)
    summary["seller_type_core"] = st_type

    # seller country (third-party only)
    tp = [r for r in core if not r["is_amazon"]]
    tp_u = sum(r["estimated_monthly_sales"] for r in tp)
    tp_r = sum(r["estimated_monthly_revenue"] for r in tp)
    sc = table(tp, lambda r: COUNTRY_LABEL.get(r["seller_country"], r["seller_country"] or "未获取"),
               "seller_country", tp_u, tp_r)
    for row in sc:
        row["share_of_third_party_products"] = round(row["products"] / len(tp) * 100, 1)
        row["share_of_all_core_products"] = round(row["products"] / len(core) * 100, 1)
    write_csv("seller_country_core.csv", (list(sc[0].keys()) if sc else ["empty"]), sc)
    summary["seller_country_core"] = sc
    summary["third_party_products"] = len(tp)
    china_like = [r for r in tp if r["seller_country"] in ("china", "hong_kong", "china_likely")]
    summary["china_like_products"] = len(china_like)
    summary["china_like_share_of_third_party"] = round(len(china_like) / len(tp) * 100, 1) if tp else 0
    summary["china_like_share_of_all_core"] = round(len(china_like) / len(core) * 100, 1)
    summary["china_like_revenue_share_of_third_party"] = (
        round(sum(r["estimated_monthly_revenue"] for r in china_like) / tp_r * 100, 1) if tp_r else 0)
    summary["china_like_unit_share_of_third_party"] = (
        round(sum(r["estimated_monthly_sales"] for r in china_like) / tp_u * 100, 1) if tp_u else 0)

    summary["seller_table_core"] = table(core, lambda r: (r.get("sold_by") or "未获取"), "seller", total_u, total_r)

    # title keywords
    kw = []
    for k in TITLE_KEYWORDS:
        hit = [r for r in core if k in r["title"].lower()]
        if not hit:
            continue
        kw.append({"keyword": k, "products": len(hit),
                   "est_units": sum(r["estimated_monthly_sales"] for r in hit),
                   "est_revenue": round(sum(r["estimated_monthly_revenue"] for r in hit), 2),
                   "avg_price": round(st.mean(r["price"] for r in hit), 2),
                   "median_reviews": int(st.median(r["review_count"] for r in hit))})
    kw.sort(key=lambda x: -x["products"])
    write_csv("title_keyword_freq_core.csv", (list(kw[0].keys()) if kw else ["empty"]), kw)
    summary["title_keywords_core"] = kw

    # low review / high sales
    med_units = st.median([r["estimated_monthly_sales"] for r in core])
    q1_reviews = st.quantiles([r["review_count"] for r in core], n=4)[0]
    outl = [{"asin": r["asin"], "bsr_rank": r["bsr_rank"], "brand": r["brand"],
             "sold_by": r["sold_by"], "title": r["title"][:110], "price": r["price"],
             "rating": r["rating"], "review_count": r["review_count"],
             "estimated_monthly_sales": r["estimated_monthly_sales"],
             "estimated_monthly_revenue": r["estimated_monthly_revenue"]}
            for r in core if r["estimated_monthly_sales"] >= med_units and r["review_count"] <= q1_reviews]
    outl.sort(key=lambda x: x["review_count"])
    if outl:
        write_csv("low_review_high_sales_core.csv", (list(outl[0].keys()) if outl else ["empty"]), outl)
    summary["low_review_high_sales"] = outl

    # concentration + sensitivity
    by_rev = sorted(core, key=lambda r: -r["estimated_monthly_revenue"])
    summary["concentration_core"] = {
        "top3_rev_share": round(sum(r["estimated_monthly_revenue"] for r in by_rev[:3]) / total_r * 100, 1),
        "top10_rev_share": round(sum(r["estimated_monthly_revenue"] for r in by_rev[:10]) / total_r * 100, 1),
        "top20_rev_share": round(sum(r["estimated_monthly_revenue"] for r in by_rev[:20]) / total_r * 100, 1),
        "total_revenue": round(total_r, 2), "total_units": total_u}
    summary["sensitivity_core"] = {
        "100pct": {"units": total_u, "revenue": round(total_r, 2)},
        "50pct": {"units": int(total_u * 0.5), "revenue": round(total_r * 0.5, 2)},
        "30pct": {"units": int(total_u * 0.3), "revenue": round(total_r * 0.3, 2)}}

    with (OUT / "analysis_data.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("brand_table_core", "seller_table_core", "title_keywords_core",
                                   "seller_country_core", "seller_type_core", "price_bands_core",
                                   "review_threshold_core", "low_review_high_sales")},
                     ensure_ascii=False, indent=2))
    print("\n--- seller country ---")
    print(json.dumps(sc, ensure_ascii=False, indent=2))
    print("\n--- seller type ---")
    print(json.dumps(st_type, ensure_ascii=False, indent=2))
    print("\n--- brand top 12 ---")
    print(json.dumps(bt[:12], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
