# -*- coding: utf-8 -*-
"""
Seller-profile pass: read the business address behind every third-party merchant id.

Why: the list/detail pages tell you WHO sells (merchant id + seller name) but not WHERE the seller is
registered. Amazon publishes the business address on https://www.amazon.com/sp?seller=<merchantId>,
which is what is needed for a "China seller share" number.

Input : data/processed/bsr_with_details.csv
Output: data/processed/seller_profiles.csv
        data/processed/seller_state.json (checkpoint)
"""

import argparse
import csv
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
IN_CSV = PROC_DIR / "bsr_with_details.csv"
OUT_CSV = PROC_DIR / "seller_profiles.csv"
STATE_FILE = PROC_DIR / "seller_state.json"

AMAZON_MERCHANT_ID = "ATVPDKIKX0DER"

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

COUNTRY_HINTS = {
    "china": ["china", "cn", "p.r.c", "peoples republic"],
    "hong_kong": ["hong kong", "hongkong"],
    "united_states": ["united states", "usa", "u.s.a"],
    "canada": ["canada"],
    "united_kingdom": ["united kingdom", "uk"],
    "germany": ["germany"],
    "japan": ["japan"],
    "vietnam": ["vietnam"],
    "india": ["india"],
    "korea": ["korea"],
}
CHINA_REGION_HINTS = [
    "guangdong", "zhejiang", "fujian", "jiangsu", "shanghai", "shenzhen", "guangzhou",
    "hangzhou", "yiwu", "ningbo", "dongguan", "foshan", "xiamen", "beijing",
]


def setup_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower() not in ("utf-8", "utf8", "cp65001"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logging(log_path):
    logger = logging.getLogger("amazon_sellers")
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


def detect_block(page):
    try:
        html = page.content().lower()
    except Exception:
        html = ""
    if "validatecaptcha" in html:
        return "bot gate: /errors_page/validateCaptcha"
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    if "click the button below to continue shopping" in body.lower():
        return "bot gate: continue-shopping interstitial"
    if len(body) < 400 and "amazon.com" in page.url:
        return "suspiciously empty page (%d chars)" % len(body)
    return ""


def parse_country(address: str, raw: str) -> str:
    blob = (address + " " + raw).lower()
    for country, hints in COUNTRY_HINTS.items():
        for hint in hints:
            if re.search(r"\b%s\b" % re.escape(hint), blob):
                return country
    for hint in CHINA_REGION_HINTS:
        if hint in blob:
            return "china_likely"
    return ""


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def main(argv=None):
    setup_console()
    ap = argparse.ArgumentParser(description="Fetch seller business addresses")
    ap.add_argument("--min-delay", type=float, default=5.0)
    ap.add_argument("--max-delay", type=float, default=10.0)
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--manual-wait", type=int, default=0)
    ap.add_argument("--user-data-dir", default="")
    ap.add_argument("--channel", default="chrome")
    args = ap.parse_args(argv)
    if not args.channel:
        args.channel = None

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = setup_logging(LOG_DIR / ("scrape_amazon_sellers_%s.log" % stamp))

    with IN_CSV.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    wanted = {}
    for r in rows:
        mid = (r.get("merchant_id") or "").strip()
        if not mid or mid == AMAZON_MERCHANT_ID:
            continue
        wanted.setdefault(mid, r.get("sold_by") or "")
    logger.info("unique third-party merchant ids: %d", len(wanted))

    state = load_state()
    results = state.get("results", {})
    todo = [(m, n) for m, n in wanted.items() if m not in results]
    blocked = ""

    launch_args = ["--disable-blink-features=AutomationControlled", "--disable-infobars"]
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

        for i, (mid, name) in enumerate(todo, 1):
            url = "https://www.amazon.com/sp?seller=%s" % mid
            logger.info("[%d/%d] %s (%s)", i, len(todo), name or "-", mid)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
            except Exception as exc:
                logger.error("  open failed: %s", type(exc).__name__)
                continue

            reason = detect_block(page)
            if reason:
                if args.manual_wait and args.headful:
                    logger.warning("风控页，等待人工处理 %d 秒……", args.manual_wait)
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
                text = page.inner_text("body")
            except Exception:
                text = ""

            biz = ""
            m = re.search(r"Business Name:?\s*\n?\s*(.+)", text)
            if m:
                biz = m.group(1).strip()
            addr = ""
            m = re.search(r"Business Address:?\s*\n?([\s\S]{0,300}?)(?:\n\s*\n|Return policy|Detailed Seller|$)", text)
            if m:
                addr = " ".join(m.group(1).split())
            country = parse_country(addr, text)

            results[mid] = {
                "merchant_id": mid, "sold_by": name, "business_name": biz,
                "business_address": addr, "country": country,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
            logger.info("  biz=%s | country=%s | addr=%s", biz or "-", country or "-", addr[:80] or "-")
            state["results"] = results
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(random.uniform(args.min_delay, args.max_delay))

        ctx.close()
        if browser:
            browser.close()

    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        fields = ["merchant_id", "sold_by", "business_name", "business_address", "country", "fetched_at"]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for mid in wanted:
            if mid in results:
                w.writerow(results[mid])

    logger.info("seller profiles written: %s (%d rows)", OUT_CSV, len(results))
    if blocked:
        logger.error("stopped early due to anti-bot page: %s", blocked)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
