# GitHub 参考项目笔记

> 写代码前先找参考；能找到可直接复用的逻辑就优先复用，不重复造轮子。

## 0. 检索说明

| 项 | 内容 |
|---|---|
| 检索渠道 | GitHub Search API |
| 关键词 | amazon market research / amazon product analysis / amazon bsr analyzer / amazon product research tool / amazon keyword research / amazon seller analytics |
| 筛选门槛 | Stars > 30 且最近 1 年有更新 |
| 命中（过门槛） | 13 个 |

## 1. 重点项目

| 仓库 | Stars | License | 可借鉴的内容 |
|---|---|---|---|
| `nexscope-ai/eCommerce-Skills` | 1025 | MIT | 电商 Agent 技能库：选品、营销、供应链、经营分析 |
| `nexscope-ai/Amazon-Skills` | 724 | MIT | 67 个亚马逊运营 Skill：关键词、竞品、Listing、利润、Review |
| `zach22-1999/amazon-skills` | 205 | MIT | 选品报告生成 + 搜索词分析 + Listing 体检 |
| `Umair706/amazon-omniscient` | 33 | NOASSERTION | BSR→销量幂律模型、FBA 计算器（只借鉴思路，不复制代码） |
| `cosjef/keepa_MCP` | 36 | MIT | Keepa 价格 / BSR 历史数据源 |
| `mansournorouzi/amazon-sp-mcp` | 47 | - | 亚马逊官方 SP-API 接入 |

## 2. 借鉴到的具体结论

### 2.1 头部集中度阈值（来自 `zach22-1999/amazon-skills`）

该项目的搜索词分析给出了三档阈值，与本项目「前 10 占比 ≥ 70% = 垄断」同源：

| 指标 | 阈值 | 含义 |
|---|---|---|
| 点击集中度 | < 30% | 未整合，优先攻击 |
| 点击集中度 | ≥ 50% | 高度集中，慎入 |
| 集中度 | ≥ 70% | 垄断红线 |

可直接复用的两个派生指标：

- **头部满足度 = 转化集中度 ÷ 点击集中度**：≥ 1 表示头部接得住需求；< 1 表示转化外流到前 3 之外。
- **成交系数 = 该 ASIN 转化份额 ÷ 点击份额**：≥ 1 表示接得住流量。

### 2.2 数据分析诚信红线

该项目明确禁止把缺失值 `fillna(0)` 后当作负面结论。
本项目同样遵循：**缺失就是缺失，标注「数据不足」，不猜测**。

### 2.3 BSR → 销量模型（来自 `Umair706/amazon-omniscient`）

幂律模型 `sales = A × BSR^(-B)`，按类目给系数。
需要注意该仓库只对**美国站**做过真实标定，其他站点是按市场规模比例缩放，
属于数量级参考。本项目 `prepare_data.py` 采用同类思路并在文档中明确声明为估算值。

该仓库 License 为 `NOASSERTION`，因此**只借鉴思路，未复制任何代码**。

## 3. 本项目未复用 / 自行实现的部分

| 模块 | 决策 | 原因 |
|---|---|---|
| 字段识别与数据审计 | 自研 | 参考项目均假设数据已干净，没有审计环节 |
| 价格带分组与 Review 门槛 | 自研 | 未找到同类实现 |
| 交叉维度空白识别 | 自研 | 参考 `zach` 的矩阵 + 稀缺阈值思路 |
| 卖家注册地抓取 | 自研 | 用于计算中国卖家占比 |

## 4. 已排除的项目

| 仓库 | Stars | 原因 |
|---|---|---|
| `mandeep147/Amazon-Product-Recommender-System` | 251 | 最后更新 2017，停更 |
| `alaBay94/Sentiment-analysis-amazon-Products-Reviews` | 47 | 超过 1 年未更新 |
| `sarthakbatragatech/AmazonScrapper` | 31 | 最后更新 2020，停更 |
| `SamithaSakthivel/Alexa_product_reviewer` | 61 | 主题为语音助手，与选品无关 |