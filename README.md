# Industry-Insight · 申万一级行业观测

> 一块每天自动更新的申万一级行业看板：把**估值、拥挤、景气、动量**四类指标收在同一张表里。
>
> 它是**个人研究博客的一个模块** —— 先做成一个能用的工具，后续并入博客的「行业观察」板块。

**看板地址**：<https://alpha9527-daniel.github.io/Industry-Insight/>

---

## ⚠️ 免责声明

- 本项目是**个人学习与研究**项目，**不是投资顾问服务，不构成任何投资建议、要约或推荐**。
- 作者**不具备证券投资咨询业务资格**，不提供任何形式的证券投资咨询服务。
- 页面数据均整理自公开渠道，可能存在延迟、缺失或错误，**不保证准确性与完整性**，请以官方披露为准。
- 任何据此做出的投资决策及其后果，**由使用者自行承担**。
- 本项目**非商业用途**：不投放广告、不收费、不销售任何数据或服务。
- 数据与新闻的版权归原发布方所有。若本站内容侵犯了您的权益，请在本仓库提 Issue，将第一时间删除。

---

## 它做什么

看板把当前 **31 个申万一级行业**的指标收在一处：

| 视图 | 内容 |
|---|---|
| **行业总览** | 全部行业一张大表，点列头可按任一指标排序 |
| **行业详情** | 走势图（鼠标悬停读具体数值）+ 指标卡：PE、ROE、ROE 同比、市值加权 ROE、加权 ROE 同比，以及 PE / ROE / 加权 ROE 的**近 5 年分位数** |
| **日期切换** | 右上角选择已归档的交易日，默认最新一天；可选日期随每日归档累积（快照保留 2 年） |
| **新闻** | 各行业相关新闻，**只展示标题、时间、来源与原文链接** |
| **ETF** | 行业相关 ETF 一览 |

前端**零依赖**：没有框架、没有 CDN、没有构建步骤，图表是手写的 SVG（`docs/app.js`）。

## 目录结构

```
Industry_Data.py                  # 数据采集与指标计算 -> industry_observation.*
export_dashboard.py               # 观测数据 -> 看板用的 JSON
requirements.txt                  # akshare / pandas / openpyxl
run_daily.bat                     # 本地一键运行 (Windows)
.github/workflows/daily_obs.yml   # 云端每日定时任务
data/industry_cache/              # 观测数据，含按日期 (YYYYMMDD) 归档的子目录
docs/                             # 看板本体 (GitHub Pages 从这里发布)
  ├── index.html  app.js  style.css
  └── data/                       # 前端读取的 JSON
        latest.json   dates.json  # 最新一天 / 可选日期索引
        daily/        history/    # 每日归档 / 各行业走势序列
        etf.json      news.json
```

## 数据流水线

1. **`python Industry_Data.py`** —— 经 AKShare 拉取行情、财务与行业成分数据，按申万一级行业计算指标，写出
   `data/industry_cache/industry_observation.{csv,json,xlsx}`，并在 `{YYYYMMDD}/` 子目录留一份当日归档。
2. **`python export_dashboard.py`** —— 生成看板数据：`latest.json`（最新一天）、`dates.json`（可选日期索引）、
   `daily/{YYYYMMDD}.json`（每日归档）、`history/{行业代码}.json`（走势序列）、`etf.json`、`news.json`。
3. **GitHub Actions** 每天**北京时间 06:00**（`cron: '0 22 * * *'` UTC）自动执行以上两步并把结果提交回仓库，Pages 随即更新。

两条稳妥性规则：历史序列**不会被更短的序列覆盖**（短序列只在更长序列缺失时使用）；每日快照保留 **2 年**，更早的自动清理。

## 指标口径

- **估值**：最新 PE（TTM）及其近 5 年分位数
- **拥挤**：近 10 个交易日换手率（成交额合计 / 流通市值）及其近 5 年滚动分位数
- **景气**：ROE 中位数、市值加权 ROE，及各自的同比与分位数
- **动量**：近 1 周 / 1 月 / 1 年涨跌幅
- **打分**：PE 分位、换手率分位、ROE 同比、动量各给一个单项得分，并给出总分

每一项的口径都写在 `Industry_Data.py` 的列说明里，可直接对照代码复核。

**不对外发布的内容**：`模型意见`（高配/平配/低配）只在本地计算，**不写入任何输出文件**；`分析师意见.xlsx` 是作者本地的人工意见表，已被 `.gitignore` 排除。公开仓库只发布客观指标与可追溯的得分。

## 本地运行

```bash
pip install -r requirements.txt
python Industry_Data.py        # 采集 + 计算 (首次较慢，需要联网)
python export_dashboard.py     # 生成看板 JSON

# 本地预览看板
python -m http.server 8000 -d docs   # 打开 http://localhost:8000
```

## 数据来源与版权

- 行业分类与估值口径参考 **申万宏源**（申万一级行业分类）。
- 行情、财务与估值指标经 **[AKShare](https://akshare.akfamily.xyz/)** 取自东方财富、同花顺、新浪财经等**公开数据源**。
- **新闻只发布标题、时间、来源与原文链接**，不转载正文；点开链接即可阅读原文，版权归原媒体所有。
- 本仓库的**代码**可供参考，但**数据本身的版权归原发布方**，请勿用于商业用途。

## 关于搜索引擎收录

看板页面声明了 `<meta name="robots" content="noindex, nofollow">` —— 它是个人研究工具，不打算被搜索引擎收录。

## 路线图

- [ ] 历史日期回看扩展到更长时间（当前只覆盖已归档的每日快照）
- [ ] 并入个人研究博客，作为「行业观察」板块
- [ ] 补一份指标口径的独立说明文档

## 许可

- **代码**：个人研究项目，仓库未附开源许可证；转载或引用请注明出处。
- **数据**：版权归原发布方，不适用本仓库的任何授权。

---

*Maintained by [@Alpha9527-Daniel](https://github.com/Alpha9527-Daniel) · 仅供研究参考，据此操作风险自负。*
