# -*- coding: utf-8 -*-
"""
Scrape 1-2 star review text for the priority competitor ASINs.

Amazon now renders only a fixed set of "top reviews" in the review page HTML; star filters and
paging are served by the review page's own AJAX endpoint (/hz/reviews-render/ajax/reviews/get/),
called with the page's own anti-csrftoken-a2z token. This script does exactly what the page's
"1 star" / "2 star" filter buttons do.

Output: data/processed/reviews_critical.csv
        data/processed/reviews_state.json (checkpoint)
"""

import argparse
import csv
import html as html_mod
import json
import logging
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parents[1]
PROC_DIR = BASE_DIR / "data" / "processed"
LOG_DIR = BASE_DIR / "data" / "logs"
OUT_CSV = PROC_DIR / "reviews_critical.csv"
STATE_FILE = PROC_DIR / "reviews_state.json"

DEFAULT_ASINS = [
    # Placeholder ASINs - replace with the competitors you want to analyse.
    ("B0EXAMPLE1", "Example 8-Pack Storage Bins with Lids", "$27.99"),
    ("B0EXAMPLE2", "Example 4-Pack Stackable Storage Drawers", "$35.99"),
    ("B0EXAMPLE3", "Example 5-Tier Storage Shelves", "$43.99"),
    ("B0EXAMPLE4", "Example Under-Sink Pull-Out Organizer", "$37.39"),
    ("B0EXAMPLE5", "Example Rechargeable Vacuum Storage Bag Set", "$69.97"),
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
"""

AJAX_URL = "https://www.amazon.com/hz/reviews-render/ajax/reviews/get/ref=cm_cr_getr_d_paging_btm_next_1"

REVIEW_BLOCK_RE = re.compile(r'<div[^>]*data-hook="review"[^>]*>', re.I)
RATING_RE = re.compile(r'data-hook="review-star-rating"[^>]*>.*?<span class="a-icon-alt">([\d.]+) out of 5', re.S)
RATING_RE2 = re.compile(r'<span class="a-icon-alt">([\d.]+) out of 5 stars</span>')
TITLE_RE = re.compile(r'data-hook="review-title"[^>]*>(.*?)</a>', re.S)
BODY_RE = re.compile(r'data-hook="review-body"[^>]*>(.*?)</div>', re.S)
DATE_RE = re.compile(r'data-hook="review-date"[^>]*>(.*?)</span>', re.S)
VERIFIED_RE = re.compile(r'data-hook="avp-badge"[^>]*>(.*?)</span>', re.S)
AUTHOR_RE = re.compile(r'class="a-profile-name">(.*?)</span>', re.S)


def setup_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower() not in ("utf-8", "utf8", "cp65001"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logging(log_path):
    logger = logging.getLogger("amazon_reviews")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    con = logging.StreamHandler(stream=sys.stdout)
    con.setFormatter(fmt)
    logger.addHandler(con)
    return logger


def strip_tags(text):
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def clean(text):
    text = strip_tags(text)
    return re.sub(r"\s+", " ", text).strip()


def detect_block(page):
    try:
        h = page.content().lower()
    except Exception:
        h = ""
    if "validatecaptcha" in h:
        return "bot gate: validateCaptcha"
    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""
    if "click the button below to continue shopping" in body:
        return "bot gate: continue-shopping interstitial"
    return ""


def get_csrf(page):
    try:
        h = page.content()
    except Exception:
        return ""
    m = re.search(r'anti-csrftoken-a2z[^:]{0,20}:\s*[&quot;"]+([A-Za-z0-9+/=]{20,})', h)
    if m:
        return m.group(1)
    m = re.search(r'anti-csrftoken-a2z&quot;:&quot;([^&]+)&quot;', h)
    return m.group(1) if m else ""


def parse_reviews(blob, asin, star_filter):
    out = []
    parts = re.split(r'data-hook="review"', blob)
    for chunk in parts[1:]:
        chunk = chunk[:12000]
        rating = RATING_RE.search(chunk) or RATING_RE2.search(chunk)
        title = TITLE_RE.search(chunk)
        body = BODY_RE.search(chunk)
        date = DATE_RE.search(chunk)
        verified = VERIFIED_RE.search(chunk)
        author = AUTHOR_RE.search(chunk)
        if not (body or title):
            continue
        out.append({
            "asin": asin,
            "star_filter": star_filter,
            "rating": float(rating.group(1)) if rating else "",
            "review_title": clean(title.group(1)) if title else "",
            "review_body": clean(body.group(1)) if body else "",
            "review_date": clean(date.group(1)) if date else "",
            "author": clean(author.group(1)) if author else "",
            "verified_purchase": "yes" if verified else "no",
        })
    return out


def fetch_filter(page, asin, star, pages, page_size, csrf, logger):
    collected = []
    for pn in range(1, pages + 1):
        payload = {
            "sortBy": "recent", "reviewerType": "all_reviews", "formatType": "current_format",
            "filterByStar": star, "pageNumber": str(pn), "filterByKeyword": "",
            "shouldAppend": "undefined", "deviceType": "desktop", "canShowIntHeader": "undefined",
            "reftag": "cm_cr_getr_d_paging_btm_next_%d" % pn, "pageSize": str(page_size),
            "asin": asin, "scope": "reviewsAjax0",
        }
        body = "&".join("%s=%s" % (k, v) for k, v in payload.items())
        try:
            res = page.evaluate("""async (args) => {
                const r = await fetch(args.url, {
                    method: 'POST',
                    headers: {'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
                              'x-requested-with': 'XMLHttpRequest',
                              'anti-csrftoken-a2z': args.token},
                    body: args.body, credentials: 'include'
                });
                const t = await r.text();
                return {status: r.status, len: t.length, text: t};
            }""", {"url": AJAX_URL, "body": body, "token": csrf})
        except Exception as exc:
            logger.error("    ajax failed: %s", type(exc).__name__)
            break
        if res["status"] != 200 or res["len"] == 0:
            logger.warning("    %s page %d -> HTTP %s (len %s)", star, pn, res["status"], res["len"])
            break
        items = parse_reviews(res["text"], asin, star)
        logger.info("    %s page %d -> %d reviews (bytes %d)", star, pn, len(items), res["len"])
        if not items:
            break
        collected.extend(items)
        time.sleep(random.uniform(3.0, 6.0))
    return collected


def main(argv=None):
    setup_console()
    ap = argparse.ArgumentParser(description="Scrape 1-2 star reviews")
    ap.add_argument("--asin", action="append", default=[], help="ASIN to scrape (repeatable)")
    ap.add_argument("--pages", type=int, default=4, help="pages per star filter")
    ap.add_argument("--page-size", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0, help="only first N ASINs (test)")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--manual-wait", type=int, default=0)
    ap.add_argument("--user-data-dir", default="")
    ap.add_argument("--channel", default="chrome")
    ap.add_argument("--fresh", action="store_true", help="ignore checkpoint")
    args = ap.parse_args(argv)
    if not args.channel:
        args.channel = None

    targets = [(a, "", "") for a in args.asin] if args.asin else list(DEFAULT_ASINS)
    if args.limit:
        targets = targets[: args.limit]

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = setup_logging(LOG_DIR / ("scrape_amazon_reviews_%s.log" % stamp))

    state = {}
    if STATE_FILE.exists() and not args.fresh:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    results = state.get("results", {})

    launch_args = ["--disable-blink-features=AutomationControlled", "--disable-infobars"]
    blocked = ""
    with sync_playwright() as p:
        browser = None
        if args.user_data_dir:
            Path(args.user_data_dir).mkdir(parents=True, exist_ok=True)
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=args.user_data_dir, headless=not args.headful,
                channel=args.channel, args=launch_args, user_agent=USER_AGENT,
                locale="en-US", timezone_id="America/New_York",
                viewport={"width": 1440, "height": 900})
        else:
            browser = p.chromium.launch(headless=not args.headful, channel=args.channel, args=launch_args)
            ctx = browser.new_context(user_agent=USER_AGENT, locale="en-US",
                                      viewport={"width": 1440, "height": 900})
        ctx.add_init_script(STEALTH_JS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for i, (asin, label, price) in enumerate(targets, 1):
            logger.info("[%d/%d] %s  (%s %s)", i, len(targets), asin, label, price)
            url = "https://www.amazon.com/product-reviews/%s/?reviewerType=all_reviews" % asin
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
            except Exception as exc:
                logger.error("  open failed: %s", type(exc).__name__)
                continue

            reason = detect_block(page)
            if reason:
                if args.manual_wait and args.headful:
                    logger.warning("  风控页，等待人工处理 %d 秒……", args.manual_wait)
                    deadline = time.time() + args.manual_wait
                    while time.time() < deadline:
                        time.sleep(15)
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        except Exception:
                            pass
                        if not detect_block(page):
                            break
                    reason = detect_block(page)
                if reason:
                    blocked = reason
                    logger.error("  BLOCKED -> stop: %s", reason)
                    break

            try:
                acct = page.locator("#nav-link-accountList-nav-line-1").first.inner_text(timeout=4000).strip()
            except Exception:
                acct = "?"
            logger.info("  account state: %s", acct)
            csrf = get_csrf(page)
            if not csrf:
                logger.error("  no anti-csrftoken-a2z token found, skipping")
                continue
            logger.info("  token acquired (%d chars)", len(csrf))

            rows = []
            for star in ("one_star", "two_star"):
                rows.extend(fetch_filter(page, asin, star, args.pages, args.page_size, csrf, logger))

            # fallback: whatever reviews the page itself renders (top reviews)
            if not rows:
                try:
                    page_html = page.content()
                except Exception:
                    page_html = ""
                page_rows = parse_reviews(page_html, asin, "page_top_reviews")
                logger.info("  fallback: %d reviews rendered on the page itself", len(page_rows))
                rows.extend(page_rows)

            seen = set()
            deduped = []
            for r in rows:
                key = (r["review_title"], r["review_body"][:120], r["review_date"])
                if key in seen:
                    continue
                seen.add(key)
                r["product_label"] = label
                r["list_price"] = price
                deduped.append(r)

            results[asin] = {
                "asin": asin, "label": label, "reviews": deduped,
                "count": len(deduped),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
            logger.info("  kept %d unique 1-2 star reviews", len(deduped))
            state["results"] = results
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(random.uniform(4.0, 8.0))

        ctx.close()
        if browser:
            browser.close()

    # write flat CSV
    fields = ["asin", "product_label", "list_price", "rating", "review_title", "review_body",
              "review_date", "author", "verified_purchase", "star_filter"]
    total = 0
    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for asin, rec in results.items():
            for r in rec.get("reviews", []):
                w.writerow(r)
                total += 1
    logger.info("=" * 60)
    logger.info("reviews written: %s (%d rows from %d ASINs)", OUT_CSV, total, len(results))
    for asin, rec in results.items():
        logger.info("  %s -> %d reviews", asin, rec.get("count", 0))
    if blocked:
        logger.error("stopped early due to anti-bot page: %s", blocked)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

