# QUICKSTART —— 5 分钟跑通

不需要联网、不需要安装任何东西（只要 Python 3.10+），先把整条分析链路跑一遍。

## 第 1 步：拿到代码

```bash
git clone https://github.com/wua8889/amazon-market-research.git
cd amazon-market-research
```

## 第 2 步：跑示例数据

仓库自带一份虚构数据 `data/example/example_bsr_top10.csv`：

```bash
python scripts/prepare_data.py --source data/example/example_bsr_top10.csv
python scripts/market_analysis.py
```

看到类似输出就成功了：

```
SOURCE ROWS     : 10
KEPT            : 9
EXCLUDED        : 1
EST UNITS/MONTH : 69865
EST REVENUE/MONTH: 2629233
```

## 第 3 步：看结果

```bash
# 汇总指标
cat output/analysis_data.json

# 逐个分析表
ls output/tables/
#  price_bands_core.csv         价格带分析
#  review_threshold_core.csv    Review 门槛
#  brand_concentration_core.csv 品牌集中度
#  seller_type_core.csv         卖家结构
```

## 第 4 步（可选）：渲染成 HTML 报告

```bash
python scripts/render_report_html.py --input output/example/example_market_report.md --output output/example/report.html
```

用浏览器打开 `output/example/report.html` 即可，可直接打印成 PDF。

## 第 5 步（可选）：抓真实数据

需要先安装依赖：

```bash
pip install -r requirements.txt
playwright install chromium
```

然后小样本试跑：

```bash
python scripts/scrape_amazon_bsr.py --limit 10 --pages 1 --headful
```

确认没问题后跑完整榜单：

```bash
python scripts/scrape_amazon_bsr.py
```

## 常见问题

**Q：`SOURCE_NOT_FOUND`**
`data/raw/` 下没有 `amazon_bsr_*.csv`。先跑抓取脚本，或用 `--source` 指定文件路径。

**Q：抓取返回退出码 2**
遇到验证码或风控页。等一段时间、降低频率再试，不要反复重试。

**Q：想换类目**
抓取时用 `--url` 指定榜单地址；分析时的关键词规则在 `scripts/prepare_data.py` 顶部的
`KEEP_PATTERNS` / `EXCLUDE_PATTERNS` 里改。

**Q：为什么销量是估算值**
Amazon 不公开真实销量，行业通用做法是用 BSR 换算。详见 README「注意事项」。