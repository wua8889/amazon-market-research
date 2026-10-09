# 亚马逊选品市场调研工具包 / Amazon Market Research Toolkit

把「抓榜单 → 清洗 → 价格带 → 竞争格局 → Review 门槛 → 调研报告」这条选品链路脚本化，
输出可复现的 Markdown 报告，替代一次性的人工 Excel。

An automated pipeline for Amazon product (niche) research: scrape Best Sellers lists, normalise
the data, estimate monthly sales from BSR, then produce price-band / competition / review-barrier
analysis as a reproducible Markdown report.

---

## 这个工具解决什么问题

选品调研大量时间花在重复劳动上：翻榜单、抄数据、按价格带分组、算头部集中度、数 Review 门槛、写报告。
本项目把这套流程固定成可重跑的脚本，让结论可以被复核，而不是停留在某个人的表里。

## 功能列表

| 脚本 | 作用 | 是否联网 |
|---|---|---|
| `scripts/scrape_amazon_bsr.py` | 抓取 Best Sellers 榜单前 N 名：ASIN / 标题 / 价格 / 评分 / 评论数 / BSR / 链接 | 是 |
| `scripts/scrape_amazon_details.py` | 抓商品详情页：品牌、卖家、发货方、首次上架时间 | 是 |
| `scripts/scrape_amazon_sellers.py` | 读卖家注册地（用于计算中国卖家占比） | 是 |
| `scripts/scrape_amazon_reviews.py` | 抓 1–2 星差评文本（用于找痛点） | 是 |
| `scripts/prepare_data.py` | 关键词筛选 + BSR → 月销量 / 月销售额估算 | 否 |
| `scripts/market_analysis.py` | 价格带、Review 门槛、头部集中度、卖家结构、标题关键词 | 否 |
| `scripts/analyze_reviews.py` | 差评痛点归类与频次统计 | 否 |
| `scripts/render_report_html.py` | 把 Markdown 报告渲染成可离线打开、可打印的 HTML | 否 |

## 流水线

```
scrape_amazon_bsr.py        →  data/raw/amazon_bsr_<时间戳>.csv
        │
        ▼
prepare_data.py             →  data/processed/bsr_with_sales.csv
        │
        ├─（可选）scrape_amazon_details.py   →  品牌 / 卖家 / 上架时间
        ├─（可选）scrape_amazon_sellers.py   →  卖家注册地
        └─（可选）scrape_amazon_reviews.py   →  差评文本
        │
        ▼
market_analysis.py          →  output/analysis_data.json + output/tables/*.csv
analyze_reviews.py          →  output/review_signals.json
```

## 环境要求

| 项 | 版本 / 说明 |
|---|---|
| Python | 3.10 或更高 |
| Playwright | 1.40+（只有抓取脚本需要） |
| 浏览器 | 建议本机已安装 Chrome（抓取脚本默认 `--channel chrome`） |
| 操作系统 | Windows / macOS / Linux |

> 只跑分析和报告（`prepare_data.py` / `market_analysis.py` / `analyze_reviews.py` / `render_report_html.py`）
> 不需要 Playwright，纯标准库即可。

## 安装

```bash
git clone https://github.com/wua8889/amazon-market-research.git
cd amazon-market-research

# 只做分析
# 无需额外依赖，Python 3.10+ 即可

# 需要抓取时再装
pip install -r requirements.txt
playwright install chromium
```

## 使用方法

### 1. 先用示例数据跑通（不联网，约 1 分钟）

仓库自带一份**虚构**示例数据，可以直接验证整条分析链路：

```bash
python scripts/prepare_data.py --source data/example/example_bsr_top10.csv
python scripts/market_analysis.py
```

结果写入 `output/analysis_data.json` 与 `output/tables/`。

### 2. 抓取真实榜单

```bash
# 先小样本试跑，确认页面结构没有变化
python scripts/scrape_amazon_bsr.py --limit 10 --pages 1

# 默认：2 页 / 100 条
python scripts/scrape_amazon_bsr.py

# 换类目
python scripts/scrape_amazon_bsr.py --url "https://www.amazon.com/Best-Sellers-Home-Kitchen-Furniture/zgbs/home-garden/1063306/" --category-name "Furniture"

# 有头模式，便于人工确认页面
python scripts/scrape_amazon_bsr.py --limit 10 --pages 1 --headful
```

### 3. 补充详情 / 卖家 / 差评（可选）

```bash
python scripts/scrape_amazon_details.py --limit 20 --only-missing
python scripts/scrape_amazon_sellers.py
python scripts/scrape_amazon_reviews.py --asin B0EXAMPLE1 --pages 2
```

### 4. 分析

```bash
python scripts/prepare_data.py --source data/raw/amazon_bsr_<时间戳>.csv
python scripts/market_analysis.py
python scripts/analyze_reviews.py
```

### 5. 渲染成 HTML

```bash
python scripts/render_report_html.py \
  --input output/example/example_market_report.md \
  --output output/example/example_market_report.html
```

## 示例输出

用 `data/example/example_bsr_top10.csv`（10 条**虚构**数据）跑出来的结果：

| 指标 | 值 |
|---|---|
| 保留产品 | 9 / 10（1 条被关键词规则排除） |
| 估算月销量 | 69,865 件 |
| 估算月销售额 | $2,629,233 |
| 平均评分 | 4.46 |
| Review 中位数 | 2,340 |
| 前 3 名销售额占比 | 46.8% |

价格带分析（示例数据）：

| 价格带 | 销量占比 | 销售额占比 | 平均Review | 新品机会 | 建议 |
|---|---|---|---|---|---|
| $10–20 | 9.5% | 5.0% | 8,750 | 低 | 销量与销额双低，Review 门槛反而最高，回避 |
| $20–30 | 26.9% | 18.7% | 2,709 | 较高 | 评分最高、Review 门槛较低，优先测试 |
| $30–50 | 45.6% | 46.0% | 1,396 | 高 | 体量与门槛最平衡，主力切入区间 |
| $50+ | 18.0% | 30.3% | 2,215 | 中 | 单价高、销量少，需差异化与品牌支撑 |

完整示例报告：[`output/example/example_market_report.md`](output/example/example_market_report.md)

报告预览截图：`docs/example-report.png`

## 目录结构

```
amazon-market-research/
├── config/             配置（站点、类目、阈值）
├── data/
│   ├── raw/            原始抓取数据（已 gitignore）
│   ├── processed/      清洗后数据（已 gitignore）
│   ├── logs/           抓取日志（已 gitignore）
│   └── example/        虚构示例数据（随仓库发布）
├── output/
│   ├── tables/         分析结果 CSV（已 gitignore）
│   └── example/        示例报告（随仓库发布）
├── reference/          GitHub 参考项目笔记
├── scripts/            分析脚本
├── AGENTS.md           项目规则与判断标准
├── README.md
└── QUICKSTART.md
```

## 数据字段

榜单 CSV（`data/raw/amazon_bsr_*.csv`）字段：

| 字段 | 说明 |
|---|---|
| `asin` | 商品 ASIN |
| `title` | 商品标题 |
| `price` | 价格（美元） |
| `rating` | 平均评分 |
| `review_count` | 评论数 |
| `bsr_rank` | 榜单排名 |
| `product_url` | 商品链接 |
| `page` | 榜单页码 |
| `category` | 类目 |

详情页 CSV 额外提供：`brand_detail`、`sold_by`、`merchant_id`、`is_amazon_seller`、
`ships_from`、`date_first_available`、`detail_bsr`。

## 注意事项

1. **销量与销售额是估算值，不是真实数据。**
   BSR → 销量的换算使用亚马逊美国站 Home & Kitchen 的参考曲线插值，
   只适合判断数量级和相对关系，**不能用于财务测算或对外承诺**。
2. **反爬风险。** 脚本采用随机延迟、隐藏自动化特征等常规手段，
   并在检测到验证码或风控页时立即停止（退出码 2），不会强行抓取。
   但高频、大规模抓取仍可能导致 IP 或账号受限。
3. **合规请自行确认。** 抓取 Amazon 是否违反目标站点服务条款由使用者自行判断；
   本仓库用于技术学习与个人研究，请勿用于转售数据。
4. **示例数据全部为虚构内容**，ASIN 使用 `B0EXAMPLE**` 占位符，与任何真实商品无关。
5. 仓库**不包含**任何真实抓取数据、API Key、账号信息或个人配置。

## License

MIT，见 [LICENSE](LICENSE)。