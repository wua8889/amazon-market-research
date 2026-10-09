# -*- coding: utf-8 -*-
"""
Product detail page enrichment for the Amazon home-storage dataset.

For every ASIN in data/processed/bsr_with_sales.csv it opens https://www.amazon.com/dp/<ASIN>
and extracts:
  - Brand                 (product-details "Brand" / bylineInfo)
  - Sold by               (tabular buybox / merchant info)
  - Ships from            (tabular buybox)
  - Date First Available  (product-details table)
  - detail-page BSR text  (cross-check of the list-page rank)

Outputs:
  data/raw/amazon_details_<stamp>.csv        raw per-ASIN extraction
  data/processed/bsr_with_details.csv        原表 + brand/seller/ships_from/date_first_available
  data/processed/detail_state.json           checkpoint so an interrupted run can resume

Safety: same stealth setup as the list-page scraper; captcha / robot page detection stops the
run immediately and leaves the partial data on disk.
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

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw"
PROC_DIR = BASE_DIR / "data" / "processed"
LOG_DIR = BASE_DIR / "data" / "logs"
SOURCE_CSV = PROC_DIR / "bsr_with_sales.csv"
STATE_FILE = PROC_DIR / "detail_state.json"

SITE_ROOT = "https://www.amazon.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
window.chrome = window.chrome || { runtime: {} };
"""

CAPTCHA_URL_MARKERS = ("validatecaptcha", "/errors/", "captcha")
CAPTCHA_TEXT_MARKERS = (
    "enter the characters you see below",
    "type the characters you see in this image",
    "sorry, we just need to make sure you're not a robot",
    "to discuss automated access to amazon data",
    "api-services-support@amazon.com",
    "robot check",
    "unusual traffic",
)

EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = { rows: {}, byline: '', tabular: [], merchant: '', merchant_info: '', availability: '', seller_trigger: '', title: '', bsr: '' };

  // product detail tables
  document.querySelectorAll('#productDetails_detailBullets_sections1 tr, #productDetails_techSpec_section_1 tr, #productDetails_db_sections tr, #productDetails_detailBullets_sections1 li').forEach((tr) => {
    const cells = tr.tagName === 'LI' ? tr.querySelectorAll('span span') : tr.querySelectorAll('th, td');
    if (cells.length >= 2) {
      const k = norm(cells[0].textContent).replace(/[:\u200f\u200e\u00a0]+$/g, '');
      const v = norm(cells[cells.length - 1].textContent);
      if (k && v && !out.rows[k]) out.rows[k] = v;
    }
  });

  // detail bullets (span span pairs)
  document.querySelectorAll('#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li').forEach((li) => {
    const spans = li.querySelectorAll('span span');
    if (spans.length >= 2) {
      const k = norm(spans[0].textContent).replace(/[:\u200f\u200e\u00a0]+$/g, '');
      const v = norm(spans[1].textContent);
      if (k && v && !out.rows[k]) out.rows[k] = v;
    }
  });

  out.byline = norm((document.querySelector('#bylineInfo') || {}).textContent);
  out.merchant_info = norm((document.querySelector('#merchantInfoFeature_feature_div') || {}).innerText);
  const avail = document.querySelector('#availabilityInsideBuyBox_feature_div');
  out.availability = avail ? (avail.textContent || '') : '';
  out.seller_trigger = norm((document.querySelector('#sellerProfileTriggerId') || {}).textContent);
  out.merchant = norm((document.querySelector('#merchant-info') || {}).textContent);
  out.title = norm((document.querySelector('#productTitle') || {}).textContent);
  document.querySelectorAll('#tabular-buybox .tabular-buybox-text, #tabular-buybox-container .tabular-buybox-text').forEach((n) => {
    out.tabular.push(norm(n.textContent));
  });
  document.querySelectorAll('#offer-display-features > div, #offer-display-features .a-fixed-left-grid').forEach((row) => {
    const label = norm((row.querySelector('.offer-display-feature-label') || {}).textContent);
    const msg = norm((row.querySelector('.offer-display-feature-text-message') || {}).textContent);
    if (label && msg) { out.tabular.push(label); out.tabular.push(msg); }
  });
  return out;
}
"""


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (stream.encoding or "").lower()
            if enc not in ("utf-8", "utf8", "cp65001"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("amazon_details")
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


def human_pause(low=1.0, high=3.0):
    time.sleep(random.uniform(low, high))


def detect_block(page) -> str:
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    for marker in CAPTCHA_URL_MARKERS:
        if marker in url:
            return "URL matched risk-control pattern: %s" % url
    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""
    for marker in CAPTCHA_TEXT_MARKERS:
        if marker in body:
            return "page shows captcha/robot wording: %s" % marker
    if "captcha" in body and len(body) < 4000:
        return "short page containing 'captcha'"
    return ""


GATE_TEXT_MARKERS = (
    "click the button below to continue shopping",
    "continue shopping",
)


def scroll_page(page, logger) -> None:
    """The product-information / offer blocks are lazily rendered; scroll to force them in."""
    for _ in range(7):
        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            break
        time.sleep(random.uniform(0.6, 1.2))
    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    time.sleep(random.uniform(0.5, 1.0))


def detect_gate(page, asin: str) -> str:
    """Detect Amazon's /errors_page/validateCaptcha soft gate and the silent homepage redirect."""
    try:
        html = page.content()
    except Exception:
        html = ""
    if "validatecaptcha" in html.lower():
        return ("Amazon bot gate: form posts to /errors_page/validateCaptcha "
                "(https://www.amazon.com/errors_page/validateCaptcha)")
    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""
    for marker in GATE_TEXT_MARKERS:
        if marker in body and len(body) < 1500:
            return "Amazon bot gate: page only offers 'Continue shopping' (%d chars)" % len(body)
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    if asin and asin.lower() not in url:
        return "Amazon redirected away from the product page (landed on %s)" % url
    if page.locator("#productTitle").count() == 0:
        return "product page did not render (#productTitle missing)"
    return ""


def wait_for_manual(page, url: str, timeout_s: int, logger) -> bool:
    """Human-in-the-loop: let the operator clear the verification in the visible browser."""
    logger.warning("=" * 62)
    logger.warning("检测到 Amazon 风控页，需要你人工验证一次。")
    logger.warning("请在弹出的浏览器窗口里操作：")
    logger.warning("  1) 若看到 Continue shopping 按钮 -> 点它")
    logger.warning("  2) 若要求登录 -> 用你的 Amazon 账号登录")
    logger.warning("  3) 完成后什么都不用做，脚本会自动检测并继续")
    logger.warning("等待上限 %d 秒，每 20 秒自动重试一次商品页。", timeout_s)
    logger.warning("=" * 62)
    deadline = time.time() + timeout_s
    attempt = 0
    time.sleep(20)  # give the operator time to act before probing
    while time.time() < deadline:
        attempt += 1
        try:
            if page.locator("#productTitle").count():
                logger.info("商品页已就绪（第 %d 次检查），开始正式抓取。", attempt)
                return True
        except Exception:
            pass
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        time.sleep(4)
        try:
            if page.locator("#productTitle").count():
                logger.info("商品页已就绪（第 %d 次检查），开始正式抓取。", attempt)
                return True
        except Exception:
            pass
        logger.info("第 %d 次重试仍未通过，继续等待人工验证……", attempt)
        time.sleep(16)
    logger.error("等待人工过验证超时（%d 秒）。", timeout_s)
    return False


def clean_brand(raw_brand: str, byline: str, title: str) -> str:
    brand = (raw_brand or "").strip()
    if brand:
        return brand
    text = (byline or "").strip()
    m = re.search(r"Visit the (.+?) Store", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"Brand:\s*(.+)$", text)
    if m:
        return m.group(1).strip()
    if text:
        return text
    return ""


def parse_tabular(items):
    """['Ships from','Amazon','Sold by','ACME'] -> (ships_from, sold_by)"""
    ships = sold = ""
    for i, val in enumerate(items):
        low = val.lower().rstrip(":")
        if low == "ships from" and i + 1 < len(items):
            ships = items[i + 1]
        elif low == "sold by" and i + 1 < len(items):
            sold = items[i + 1]
    return ships, sold


def parse_merchant(text):
    ships = sold = ""
    if not text:
        return ships, sold
    m = re.search(r"Sold by\s+(.+?)(?:\s+and\s+|\s*$)", text)
    if m:
        sold = m.group(1).strip()
    m = re.search(r"Fulfilled by\s+(.+?)(?:\.|$)", text)
    if m:
        ships = m.group(1).strip()
    return ships, sold


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Enrich ASINs with detail-page fields")
    parser.add_argument("--limit", type=int, default=0, help="only process the first N ASINs (test)")
    parser.add_argument("--only-missing", action="store_true", help="skip ASINs already in the checkpoint")
    parser.add_argument("--min-delay", type=float, default=4.0)
    parser.add_argument("--max-delay", type=float, default=9.0)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--manual-wait", type=int, default=0,
                        help="有头模式下遇到风控页时，等待人工完成验证的秒数（0=直接停止）")
    parser.add_argument("--user-data-dir", default="",
                        help="独立浏览器 profile 目录（持久化登录与验证状态）")
    parser.add_argument("--channel", default="chrome")
    args = parser.parse_args(argv)
    if not args.channel:
        args.channel = None

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = setup_logging(LOG_DIR / ("scrape_amazon_details_%s.log" % stamp))

    with SOURCE_CSV.open(encoding="utf-8-sig", newline="") as fh:
        source_rows = list(csv.DictReader(fh))
    if args.limit:
        source_rows = source_rows[: args.limit]

    state = load_state()
    results = state.get("results", {})
    todo = [r for r in source_rows if not (args.only_missing and r["asin"] in results)]

    logger.info("=" * 72)
    logger.info("detail-page enrichment: %d ASINs to fetch (%d already cached)",
                len(todo), len(results))
    blocked = ""

    launch_args = ["--disable-blink-features=AutomationControlled", "--disable-infobars"]
    with sync_playwright() as p:
        browser = None
        if args.user_data_dir:
            profile = Path(args.user_data_dir)
            profile.mkdir(parents=True, exist_ok=True)
            logger.info("使用独立浏览器 profile：%s", profile)
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=not args.headful,
                channel=args.channel,
                args=launch_args,
                user_agent=USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1440, "height": 900},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
        else:
            browser = p.chromium.launch(
                headless=not args.headful,
                channel=args.channel,
                args=launch_args,
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1440, "height": 900},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
        context.add_init_script(STEALTH_JS)
        page = context.pages[0] if context.pages else context.new_page()

        for i, row in enumerate(todo, 1):
            asin = row["asin"]
            url = "%s/dp/%s" % (SITE_ROOT, asin)
            logger.info("[%d/%d] %s", i, len(todo), asin)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                try:
                    page.wait_for_selector("#productTitle", timeout=15000)
                except PlaywrightTimeoutError:
                    pass
            except Exception as exc:  # noqa: BLE001
                logger.error("  open failed: %s: %s", type(exc).__name__, exc)
                continue

            reason = detect_block(page) or detect_gate(page, asin)
            if reason:
                if args.manual_wait:
                    if args.headful:
                        if wait_for_manual(page, url, args.manual_wait, logger):
                            reason = detect_block(page) or detect_gate(page, asin)
                    else:
                        logger.warning("--manual-wait 需要配合 --headful 使用，已跳过人工等待。")
                if reason:
                    blocked = reason
                    logger.error("  BLOCKED -> stop: %s", reason)
                    logger.error("  按反爬规则停止抓取，不强行闯关。")
                    break

            scroll_page(page, logger)
            try:
                raw = page.evaluate(EXTRACT_JS)
            except Exception as exc:  # noqa: BLE001
                logger.error("  extract failed: %s", type(exc).__name__)
                continue

            rows = raw.get("rows", {})
            ships, sold = parse_tabular(raw.get("tabular", []))
            if not sold:
                ms, msl = parse_merchant(raw.get("merchant", ""))
                ships = ships or ms
                sold = sold or msl
            if not sold:
                sold = raw.get("seller_trigger", "")

            merchant_info = raw.get("merchant_info", "")
            if not sold:
                m = re.search(r"Sold by\s+(.+?)(?:\s*$|\s{2,})", merchant_info, re.M)
                if m:
                    sold = m.group(1).strip()
            if not sold:
                m = re.search(r"Shipper\s*/\s*Seller\s+(.+)", merchant_info)
                if m:
                    sold = m.group(1).strip()

            merchant_id = ""
            m = re.search(r'"merchantId"\s*:\s*"([A-Z0-9]+)"', raw.get("availability", ""))
            if m:
                merchant_id = m.group(1)
            is_amazon = merchant_id == "ATVPDKIKX0DER"
            if is_amazon:
                sold = sold or "Amazon.com"
                ships = ships or "Amazon"

            brand = clean_brand(
                rows.get("Brand") or rows.get("brand") or "",
                raw.get("byline", ""),
                raw.get("title", ""),
            )
            dfa = rows.get("Date First Available", "")
            bsr_text = rows.get("Best Sellers Rank", "")

            body_text = ""
            try:
                body_text = page.inner_text("body")
            except Exception:
                pass
            if not dfa:
                m = re.search(r"Date First Available\s*[:\u200f\u200e]*\s*([A-Z][a-z]+ \d{1,2}, \d{4})", body_text)
                if m:
                    dfa = m.group(1)
            if not sold:
                m = re.search(r"Sold by\s*\n?\s*([^\n]{2,60})", body_text)
                if m:
                    sold = m.group(1).strip()
            if not ships:
                m = re.search(r"Ships from\s*\n?\s*([^\n]{2,60})", body_text)
                if m:
                    ships = m.group(1).strip()
            if not brand:
                m = re.search(r"Brand\s+([A-Za-z0-9][^\n]{1,40})", body_text)
                if m:
                    brand = m.group(1).strip()

            results[asin] = {
                "asin": asin,
                "detail_title": raw.get("title", ""),
                "brand_detail": brand,
                "sold_by": sold,
                "merchant_id": merchant_id,
                "is_amazon_seller": "yes" if is_amazon else "no",
                "ships_from": ships,
                "date_first_available": dfa,
                "dfa_available": "yes" if dfa else "no",
                "detail_bsr": bsr_text,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
            logger.info("  brand=%s | sold_by=%s | merchant_id=%s | ships_from=%s | first_available=%s",
                        brand or "-", sold or "-", merchant_id or "-", ships or "-", dfa or "-")
            if bsr_text:
                logger.info("  detail BSR: %s", bsr_text[:120])

            state["results"] = results
            save_state(state)
            human_pause(args.min_delay, args.max_delay)

        context.close()
        if browser:
            browser.close()

    # ---------- write merged outputs ----------
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    detail_csv = RAW_DIR / ("amazon_details_%s.csv" % stamp)
    detail_fields = ["asin", "detail_title", "brand_detail", "sold_by", "merchant_id",
                     "is_amazon_seller", "ships_from", "date_first_available",
                     "dfa_available", "detail_bsr", "fetched_at"]
    with detail_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=detail_fields, extrasaction="ignore")
        w.writeheader()
        for r in source_rows:
            rec = results.get(r["asin"])
            if rec:
                w.writerow(rec)

    merged_csv = PROC_DIR / "bsr_with_details.csv"
    merged_fields = list(source_rows[0].keys()) + [
        "brand_detail", "sold_by", "merchant_id", "is_amazon_seller", "ships_from",
        "date_first_available", "detail_bsr"]
    filled = 0
    with merged_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=merged_fields, extrasaction="ignore")
        w.writeheader()
        for r in source_rows:
            rec = results.get(r["asin"], {})
            for k in ("brand_detail", "sold_by", "merchant_id", "is_amazon_seller",
                      "ships_from", "date_first_available", "detail_bsr"):
                r[k] = rec.get(k, "")
            if rec:
                filled += 1
            w.writerow(r)

    logger.info("-" * 72)
    logger.info("detail rows captured : %d", filled)
    logger.info("raw details csv      : %s", detail_csv)
    logger.info("merged csv           : %s", merged_csv)
    if blocked:
        logger.error("run stopped early due to anti-bot page: %s", blocked)
        return 2
    logger.info("done, no captcha encountered")
    return 0


if __name__ == "__main__":
    sys.exit(main())




