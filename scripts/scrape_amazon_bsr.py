# -*- coding: utf-8 -*-
"""
Amazon Best Sellers 榜单抓取（Python + Playwright 同步 API）

目标：抓取亚马逊「家具收纳」品类 Best Sellers 榜单前 N 名商品数据。
字段：ASIN / 商品标题 / 价格 / 评分 / 评论数 / BSR 排名 / 商品链接

用法：
    # 最小测试：第一页前 10 条
    python scripts/scrape_amazon_bsr.py --limit 10 --pages 1

    # 完整抓取：2 页 / 100 条（默认）
    python scripts/scrape_amazon_bsr.py

    # 换类目（例如 Home & Kitchen > Furniture，节点 1063306）
    python scripts/scrape_amazon_bsr.py --url "https://www.amazon.com/Best-Sellers-Home-Kitchen-Furniture/zgbs/home-garden/1063306/"

    # 有头模式（能看到浏览器窗口，便于人工确认）
    python scripts/scrape_amazon_bsr.py --limit 10 --pages 1 --headful

反爬措施：
    1. 真实 User-Agent（与已安装 Chrome 版本一致）
    2. add_init_script 隐藏 navigator.webdriver / 补全 languages、plugins、chrome
    3. 启动参数 --disable-blink-features=AutomationControlled
    4. 页面加载 wait_until="networkidle"
    5. 操作之间随机延迟 1-3 秒 + 模拟人类滚动
    6. 检测到验证码 / 风控页立即停止，记录日志，绝不强行抓取

退出码：0 成功 / 2 被反爬拦截 / 3 页面结构异常（没解析到商品）/ 1 其他异常
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

# --------------------------------------------------------------------------- #
# 基础配置
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw"
LOG_DIR = BASE_DIR / "data" / "logs"

SITE_ROOT = "https://www.amazon.com"

# 默认类目：Home & Kitchen > Storage & Organization（家居收纳，含柜架层架）
DEFAULT_CATEGORY_URL = (
    "https://www.amazon.com/Best-Sellers-Home-Kitchen-Home-Storage-Organization"
    "/zgbs/home-garden/3610841/"
)
DEFAULT_CATEGORY_NAME = "Home & Kitchen > Storage & Organization"

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

# 验证码 / 风控页特征（命中任意一条立即停机）
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
CAPTCHA_TITLE_MARKERS = ("robot check", "amazon.com", "sorry")

# 商品字段抽取（在浏览器里跑，避免多次往返）
EXTRACT_JS = r"""
els => els.map((el, idx) => {
  const txt = (sel) => {
    const n = el.querySelector(sel);
    return n ? (n.innerText || n.textContent || '').trim() : '';
  };
  const anchor =
    el.querySelector('a.a-link-normal[href*="/dp/"]') ||
    el.querySelector('a[href*="/dp/"]') ||
    el.querySelector('a[href*="/gp/product/"]');
  const href = anchor ? (anchor.getAttribute('href') || '') : '';
  const asinMatch = href.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/);

  const title =
    txt('div[class*="p13n-sc-truncate"]') ||
    txt('div[class*="p13n-sc-css-line-clamp"]') ||
    (anchor ? (anchor.innerText || '').trim() : '');

  let price = txt('span.a-price span.a-offscreen') ||
              txt('span[class*="p13n-sc-price"]');
  if (!price) {
    const priceNode = el.querySelector('span.a-price') || el.querySelector('.a-price');
    price = priceNode ? (priceNode.textContent || '').trim() : '';
  }

  const ratingAlt =
    txt('div.a-icon-row i[class*="a-icon-star"] span.a-icon-alt') ||
    txt('div.a-icon-row span.a-icon-alt') ||
    txt('span.a-icon-alt');

  let review = '';
  const nodes = el.querySelectorAll('div.a-icon-row span.a-size-small, div.a-icon-row a.a-size-small');
  nodes.forEach((n) => {
    const t = (n.innerText || n.textContent || '').trim();
    if (!review && /^[0-9][0-9,\.]*[Kk]?$/.test(t)) review = t;
  });

  const rankBadge = txt('span.zg-bdg-text');

  return {
    index: idx,
    asin: asinMatch ? asinMatch[1] : (el.getAttribute('data-asin') || ''),
    title: title,
    price_raw: price,
    rating_raw: ratingAlt,
    review_raw: review,
    rank_badge: rankBadge,
    href: href
  };
})
"""


# --------------------------------------------------------------------------- #
# 日志与工具
# --------------------------------------------------------------------------- #

def setup_console() -> None:
    """管道输出时强制 UTF-8，避免中文在控制台抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (stream.encoding or "").lower()
            if enc not in ("utf-8", "utf8", "cp65001"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("amazon_bsr")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)
    return logger


def human_pause(low: float = 1.0, high: float = 3.0, why: str = "") -> None:
    """随机延迟 1-3 秒，模拟人类节奏。"""
    seconds = random.uniform(low, high)
    if why:
        logging.getLogger("amazon_bsr").info("等待 %.1f 秒（%s）", seconds, why)
    time.sleep(seconds)


def to_count(raw: str):
    """'1,234' -> 1234；'2.3K' -> 2300；解析不了原样返回。"""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    compact = text.replace(",", "")
    k_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*[Kk]\b", compact)
    if k_match:
        return int(round(float(k_match.group(1)) * 1000))
    num = re.search(r"[0-9]+", compact)
    return int(num.group(0)) if num else text


def to_price(raw: str):
    """'$25.99' -> 25.99；价格区间取最低价；解析不了原样返回。"""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    compact = text.replace(",", "")
    num = re.search(r"[0-9]+(?:\.[0-9]+)?", compact)
    return round(float(num.group(0)), 2) if num else text


def parse_rating(raw: str):
    """'4.5 out of 5 stars' -> 4.5"""
    if not raw:
        return ""
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*out of\s*5", raw)
    if m:
        return float(m.group(1))
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", raw)
    return float(m.group(1)) if m else ""


def detect_block(page) -> str:
    """返回拦截原因；没有拦截返回空字符串。"""
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    for marker in CAPTCHA_URL_MARKERS:
        if marker in url:
            return "URL 命中风控页特征：%s" % url

    try:
        title = (page.title() or "").strip()
    except Exception:
        title = ""

    try:
        body = page.inner_text("body").lower()
    except Exception:
        body = ""

    for marker in CAPTCHA_TEXT_MARKERS:
        if marker in body:
            return "页面出现验证码/风控文案：%s" % marker

    if "captcha" in body and len(body) < 4000:
        return "页面出现 captcha 字样且内容极短（疑似验证码页）"

    if title.lower() == "amazon.com" and len(body) < 1500:
        return "页面标题仅 Amazon.com 且内容极短（疑似风控空壳页）"

    return ""


def count_items(page) -> int:
    try:
        return page.locator("#gridItemRoot").count()
    except Exception:
        return 0


def load_full_page(page, expected_per_page: int, logger: logging.Logger) -> None:
    """亚马逊榜单每页初始只渲染 30 条，滚动到底才会补满 50 条。"""
    stable_rounds = 0
    last_count = -1
    for round_no in range(1, 15):
        count = count_items(page)
        if count >= expected_per_page:
            logger.info("商品条目已加载：%d 条", count)
            return
        if count == last_count:
            stable_rounds += 1
            if stable_rounds >= 2:
                logger.warning("滚动后条目数停在 %d 条（不足 %d），按实际数量继续",
                               count, expected_per_page)
                return
        else:
            stable_rounds = 0
        last_count = count
        logger.info("第 %d 次滚动加载（当前 %d 条）", round_no, count)
        page.mouse.wheel(0, 3000)
        human_pause(1.0, 2.0)


# --------------------------------------------------------------------------- #
# 抓取主体
# --------------------------------------------------------------------------- #

def build_page_url(category_url: str, page_no: int) -> str:
    if page_no <= 1:
        return category_url
    sep = "&" if "?" in category_url else "?"
    return "%s%spg=%d" % (category_url, sep, page_no)


def scrape(args, logger: logging.Logger) -> dict:
    category_url = args.url
    collected = []
    blocked_reason = ""
    pages_done = 0
    started_at = datetime.now().isoformat(timespec="seconds")

    with sync_playwright() as p:
        logger.info("启动浏览器（headless=%s）", not args.headful)
        browser = p.chromium.launch(
            headless=not args.headful,
            channel=args.channel,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-default-browser-check",
                "--start-maximized",
            ],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        context.add_init_script(STEALTH_JS)
        page = context.new_page()

        for page_no in range(1, args.pages + 1):
            url = build_page_url(category_url, page_no)
            logger.info("打开第 %d 页：%s", page_no, url)
            try:
                page.goto(url, wait_until="networkidle", timeout=args.networkidle_timeout * 1000)
            except PlaywrightTimeoutError:
                logger.warning("第 %d 页 networkidle 超时，退化为 domcontentloaded 后继续检查", page_no)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                except Exception as exc:  # noqa: BLE001
                    logger.error("第 %d 页打开失败：%s: %s", page_no, type(exc).__name__, exc)
                    break
            except Exception as exc:  # noqa: BLE001
                logger.error("第 %d 页打开失败：%s: %s", page_no, type(exc).__name__, exc)
                break

            logger.info("页面标题：%s", page.title())

            reason = detect_block(page)
            if reason:
                blocked_reason = reason
                logger.error("检测到反爬拦截，立即停止抓取：%s", reason)
                logger.error("当前页面地址：%s", page.url)
                break

            if count_items(page) == 0:
                try:
                    page.mouse.wheel(0, 1500)
                except Exception:
                    pass
                human_pause(1.0, 2.0)

            if count_items(page) == 0:
                logger.error("第 %d 页没有解析到任何商品条目，疑似页面结构变化或被拦截", page_no)
                if not blocked_reason:
                    blocked_reason = ""
                break

            load_full_page(page, args.per_page, logger)

            raw_items = page.eval_on_selector_all("#gridItemRoot", EXTRACT_JS)
            logger.info("第 %d 页解析到 %d 条商品", page_no, len(raw_items))

            base_rank = (page_no - 1) * args.per_page
            for i, item in enumerate(raw_items):
                asin = (item.get("asin") or "").strip()
                title = (item.get("title") or "").strip()
                if not asin and not title:
                    continue
                badge = (item.get("rank_badge") or "").strip()
                rank = base_rank + i + 1
                badge_num = re.search(r"#\s*([0-9]+)", badge)
                if badge_num:
                    rank = int(badge_num.group(1))
                collected.append({
                    "asin": asin,
                    "title": title,
                    "price": to_price(item.get("price_raw")),
                    "rating": parse_rating(item.get("rating_raw")),
                    "review_count": to_count(item.get("review_raw")),
                    "bsr_rank": rank,
                    "product_url": "%s/dp/%s" % (SITE_ROOT, asin) if asin else "",
                    "page": page_no,
                    "category": args.category_name,
                })

            pages_done = page_no
            logger.info("累计抓取 %d 条", len(collected))

            if args.limit and len(collected) >= args.limit:
                logger.info("已达到 --limit %d，停止翻页", args.limit)
                break

            if page_no < args.pages:
                human_pause(args.min_delay, args.max_delay, "翻页前")

        # 收尾：模拟人类滚动后关闭
        try:
            page.mouse.wheel(0, -2000)
        except Exception:
            pass
        human_pause(1.0, 2.0)
        context.close()
        browser.close()

    if args.limit:
        collected = collected[: args.limit]

    return {
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "category_url": category_url,
        "category_name": args.category_name,
        "pages_requested": args.pages,
        "pages_done": pages_done,
        "limit": args.limit,
        "items": collected,
        "blocked": bool(blocked_reason),
        "blocked_reason": blocked_reason,
    }


def write_outputs(result: dict, logger: logging.Logger) -> tuple:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = RAW_DIR / ("amazon_bsr_%s.csv" % stamp)
    meta_path = RAW_DIR / ("amazon_bsr_%s.meta.json" % stamp)

    fieldnames = ["asin", "title", "price", "rating", "review_count",
                  "bsr_rank", "product_url", "page", "category"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in result["items"]:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    meta = {k: v for k, v in result.items() if k != "items"}
    meta["item_count"] = len(result["items"])
    meta["csv_path"] = str(csv_path)
    with meta_path.open("w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    logger.info("CSV 已保存：%s（%d 条）", csv_path, len(result["items"]))
    logger.info("运行元数据：%s", meta_path)
    return csv_path, meta_path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="抓取亚马逊 Best Sellers 榜单商品数据（Python + Playwright）")
    parser.add_argument("--url", default=DEFAULT_CATEGORY_URL,
                        help="Best Sellers 类目页地址，默认家居收纳（Storage & Organization）")
    parser.add_argument("--category-name", default=DEFAULT_CATEGORY_NAME,
                        help="写入 CSV 的类目名称")
    parser.add_argument("--pages", type=int, default=2,
                        help="抓取页数，默认 2 页（约 100 条）")
    parser.add_argument("--per-page", type=int, default=50,
                        help="每页榜单条数，默认 50（用于滚动加载与排名计算）")
    parser.add_argument("--limit", type=int, default=0,
                        help="最多抓取多少条，0 = 不限制（测试用 10）")
    parser.add_argument("--timeout", type=int, default=60,
                        help="单页加载超时（秒），默认 60")
    parser.add_argument("--networkidle-timeout", type=int, default=30,
                        help="networkidle 等待上限（秒），默认 30，超时后降级继续")
    parser.add_argument("--min-delay", type=float, default=1.0,
                        help="随机延迟下限（秒）")
    parser.add_argument("--max-delay", type=float, default=3.0,
                        help="随机延迟上限（秒）")
    parser.add_argument("--headful", action="store_true",
                        help="有头模式运行（能看到浏览器窗口）")
    parser.add_argument("--channel", default="chrome",
                        help="浏览器通道，默认 chrome（本机已装 Chrome）；传空字符串用 Playwright 自带 Chromium")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    setup_console()
    args = parse_args(argv)
    if not args.channel:
        args.channel = None

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / ("scrape_amazon_bsr_%s.log" % stamp)
    logger = setup_logging(log_path)

    logger.info("=" * 72)
    logger.info("开始抓取亚马逊 Best Sellers：%s", args.category_name)
    logger.info("页数=%d，每页=%d，上限=%s，有头=%s",
                args.pages, args.per_page, args.limit or "不限", args.headful)
    logger.info("日志文件：%s", log_path)

    exit_code = 0
    try:
        result = scrape(args, logger)
    except Exception as exc:  # noqa: BLE001
        logger.exception("抓取过程异常终止：%s", exc)
        logger.info("抓取条数：0")
        return 1

    csv_path, _ = write_outputs(result, logger)

    count = len(result["items"])
    if result["blocked"]:
        logger.error("结果：被反爬拦截，已停止抓取（抓到 %d 条后中断）", count)
        logger.error("拦截原因：%s", result["blocked_reason"])
        logger.info("请稍后重试、降低频率，或改用 --headful 人工过验证码后再跑；不要强行抓取。")
        exit_code = 2
    elif count == 0:
        logger.error("结果：没有抓到任何商品，请检查类目地址或页面结构")
        exit_code = 3
    else:
        logger.info("结果：成功抓取 %d 条商品，未遇到验证码", count)
        exit_code = 0

    logger.info("CSV 路径：%s", csv_path)
    logger.info("=" * 72)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
