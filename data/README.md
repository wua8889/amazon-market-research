# data/ 目录说明

## 目录用途

| 目录 | 用途 | 是否提交到 Git |
|---|---|---|
| `raw/` | 抓取脚本的原始输出 | 否（已 gitignore） |
| `processed/` | 清洗、估算后的中间数据 | 否（已 gitignore） |
| `logs/` | 抓取日志 | 否（已 gitignore） |
| `example/` | 随仓库发布的**虚构**示例数据 | 是 |

## 文件命名

抓取脚本自动按 `amazon_bsr_<YYYYMMDD-HHMMSS>.csv` 命名。
`prepare_data.py` 默认读取 `raw/` 下**最新**的那份，也可以用 `--source` 指定。

## 字段口径

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

## 重要提醒

- `price` 必须统一币种，不要混入不同站点数据。
- 销量是 BSR 换算的**估算值**，不是平台真实销量。
- 同一 ASIN 只应计入一次，避免重复放大市场容量。
- 不要把真实数据提交到 Git：`raw/`、`processed/`、`logs/` 已在 `.gitignore` 中排除。