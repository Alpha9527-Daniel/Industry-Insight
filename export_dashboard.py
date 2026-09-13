#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Industry-Insight 看板数据导出

把 data/industry_cache/ 下的行业数据整理成 docs/data/ 下的静态 JSON,
供 GitHub Pages 上的 docs/index.html 直接读取(零后端)。

输出:
  docs/data/latest.json          31 个行业 x 13 指标(首页大表,最新一日)
  docs/data/dates.json           可回看的交易日列表(右上角日期选择器)
  docs/data/daily/{YYYYMMDD}.json  各交易日快照(与 latest.json 同结构)
  docs/data/history/{code}.json  近 10 年收盘价 + 成交额(行业页走势图)
  docs/data/etf.json             行业 -> 相关 ETF(关键词匹配)
  docs/data/news.json            行业 -> 相关资讯(东财 + 同花顺 + 新浪,合并去重)

用法:
  python export_dashboard.py                 # 全量(含联网抓取 ETF/资讯)
  python export_dashboard.py --skip-network  # 仅用本地缓存(离线调试)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "industry_cache"
OUT_DIR = ROOT / "docs" / "data"
HISTORY_DIR = OUT_DIR / "history"
DAILY_DIR = OUT_DIR / "daily"

HISTORY_YEARS = 10      # 走势图回溯年数
MAX_NEWS_PER_IND = 12   # 每个行业保留的资讯条数
MAX_ETF_PER_IND = 8     # 每个行业保留的 ETF 条数
RETENTION_DAYS = 730    # 每日快照保留期(缓存日期目录 + 看板 historical JSON)

# 看板 13 列 <- 源 CSV 列名映射(与 Industry_Data.py 的输出列对应)
COLUMN_MAP = {
    'pe':        '最新PE',
    'pePct':     'PE分位数',
    'turnover':  '近10日换手率_pct',
    'turnoverPct': '近10日换手率分位数',
    'roe':       'ROE_pct',
    'roeYoY':    'ROE同比_pct',
    'roePct':    'ROE分位数',
    'roeCap':    'ROE_pct(市值加权)',
    'roeCapYoY': '市值加权ROE同比_pct',
    'roeCapPct': '市值加权ROE分位数',
    'r1w':       '近1周涨跌幅_pct',
    'r1m':       '近1月涨跌幅_pct',
    'r1y':       '近1年涨跌幅_pct',
}

# 行业关键词表:用于资讯归类与 ETF 匹配(名称/概念词命中即算相关)
INDUSTRY_KEYWORDS = {
    '801010': ['农林牧渔', '农业', '养殖', '猪', '生猪', '种业', '育种', '饲料', '禽', '水产', '化肥'],
    '801030': ['基础化工', '化工', '化纤', '农药', '树脂', '橡胶', '塑料', '氟', '磷'],
    '801040': ['钢铁', '螺纹钢', '铁矿', '钢价', '特钢', '钢材'],
    '801050': ['有色金属', '有色', '铜', '铝', '黄金', '稀土', '锂', '镍', '锌', '小金属'],
    '801080': ['电子', '半导体', '芯片', '面板', '消费电子', '存储', '晶圆', '封装', '元器件'],
    '801110': ['家用电器', '家电', '白电', '空调', '冰箱', '小家电'],
    '801120': ['食品饮料', '白酒', '食品', '饮料', '乳业', '调味品', '啤酒'],
    '801130': ['纺织服饰', '纺织', '服装', '服饰', '鞋帽', '棉纺'],
    '801140': ['轻工制造', '轻工', '造纸', '包装', '家具', '文具', '文娱用品'],
    '801150': ['医药生物', '医药', '生物医药', '创新药', '疫苗', '医疗器械', 'CXO', '中药', '医疗'],
    '801160': ['公用事业', '电力', '燃气', '水务', '水电', '火电', '核电'],
    '801170': ['交通运输', '航运', '港口', '物流', '快递', '航空', '铁路', '公路'],
    '801180': ['房地产', '地产', '楼市', '房企', '商品房', '物业'],
    '801200': ['商贸零售', '零售', '商贸', '超市', '电商', '百货', '免税'],
    '801210': ['社会服务', '旅游', '酒店', '餐饮', '免税', '教育', '景区'],
    '801230': ['综合'],
    '801710': ['建筑材料', '建材', '水泥', '玻璃', '防水', '玻纤'],
    '801720': ['建筑装饰', '建筑', '基建', '工程', '装饰', '施工'],
    '801730': ['电力设备', '光伏', '风电', '储能', '锂电池', '锂电', '电池', '电网', '新能源'],
    '801740': ['国防军工', '军工', '国防', '航空航天', '兵器', '导弹', '卫星'],
    '801750': ['计算机', '软件', '云计算', '人工智能', '信创', '大模型', '数据要素', '算力'],
    '801760': ['传媒', '游戏', '影视', '广告', '出版', '院线', '短剧'],
    '801770': ['通信', '5G', '光模块', '运营商', '光通信', '基站', '6G'],
    '801780': ['银行', '信贷', '存款', '息差', '商业银行', 'LPR'],
    '801790': ['非银金融', '券商', '保险', '证券', '信托'],
    '801880': ['汽车', '新能源车', '整车', '汽车零部件', '智驾', '智能驾驶', '车企'],
    '801890': ['机械设备', '机械', '工程机械', '机器人', '机床', '叉车', '自动化'],
    '801950': ['煤炭', '动力煤', '焦煤', '煤价', '炼焦煤', '焦炭'],
    '801960': ['石油石化', '石油', '石化', '原油', '油价', '天然气', '炼化'],
    '801970': ['环保', '污水', '固废', '碳中和', '垃圾焚烧', '环境治理'],
    '801980': ['美容护理', '美容', '化妆品', '护肤', '医美', '个护'],
}

# ETF 名称里出现这些词但与行业无关的,过滤掉(避免误匹配)
ETF_BLACKLIST = ['债', '货币', '国债', '信用债', '可转债', '短融', '利率']


def log(msg: str) -> None:
    print(msg, flush=True)


def read_csv_safe(path: Path) -> pd.DataFrame | None:
    """读取缓存 CSV(win 下 utf-8-sig 写出,读时同样指定编码)"""
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, encoding='utf-8-sig')
    except Exception as e:
        log(f"  [警告] 读取失败 {path.name}: {e}")
        return None


def to_num(v):
    """转成 JSON 友好的数值;NaN/缺失 -> None"""
    try:
        if v is None or pd.isna(v):
            return None
        return round(float(v), 4)
    except Exception:
        return None


def write_json(path: Path, payload, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        if indent is None:
            json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
        else:
            json.dump(payload, f, ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------- latest.json
def build_industries(df: pd.DataFrame) -> list[dict]:
    """观测表 -> 看板记录(13 指标 + 模型/分析师意见)"""
    industries = []
    for _, row in df.iterrows():
        rec = {
            'name': str(row.get('行业名称', '')).strip(),
            'code': str(row.get('行业代码', '')).strip(),
        }
        for out_key, src_col in COLUMN_MAP.items():
            rec[out_key] = to_num(row.get(src_col)) if src_col in df.columns else None
        # 附加:意见与总分(首页可选展示)
        # 不导出「分析师意见」:它来自人工维护的 分析师意见.xlsx,不随本项目公开
        for extra in ('模型意见', '模型总得分', 'PE分位数得分',
                      '换手率分位数得分', 'ROE同比得分', '动量得分', '行业A股占比_pct'):
            rec[extra] = row.get(extra) if extra in df.columns else None
        industries.append(rec)

    industries.sort(key=lambda r: r['code'])
    return industries


def build_payload(df: pd.DataFrame, date_str: str) -> dict:
    """看板数据体(与 latest.json 同结构,前端一套渲染逻辑通吃)"""
    industries = build_industries(df)
    return {
        'date': date_str,
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'count': len(industries),
        'industries': industries,
    }


def export_latest() -> dict | None:
    """最新一日的行业快照"""
    df = read_csv_safe(CACHE_DIR / "industry_observation.csv")
    if df is None or df.empty:
        log("  [跳过] industry_observation.csv 不存在")
        return None

    payload = build_payload(df, datetime.now().strftime('%Y-%m-%d'))
    write_json(OUT_DIR / 'latest.json', payload)
    log(f"  latest.json: {payload['count']} 个行业")
    return payload


# ---------------------------------------------------------------- daily/*.json
def export_daily_archives(today_payload: dict | None) -> list[str]:
    """历史每日快照 -> docs/data/daily/{YYYYMMDD}.json + docs/data/dates.json

    前端右上角日期选择器据此工作:默认最新一日,可回看任意已归档交易日。
    数据源是 Industry_Data.py 按日期留档的 data/industry_cache/{YYYYMMDD}/。
    """
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    prune_old_snapshots()      # 先清理过期快照,再据此生成可选日期列表
    dates: list[str] = []

    for d in sorted(p for p in CACHE_DIR.glob('[0-9]' * 8) if p.is_dir()):
        df = read_csv_safe(d / 'industry_observation.csv')
        if df is None or df.empty:
            continue
        ds = d.name
        write_json(DAILY_DIR / f"{ds}.json",
                   build_payload(df, f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"),
                   indent=None)
        dates.append(ds)

    # 扁平的最新快照也留一份档,保证选择器里"最新一日"始终可选。
    # 归属到最近一个已归档交易日(扁平文件就是那次运行的产物),
    # 避免在当日任务还没跑时凭空造出一个没有数据的"今天"。
    if today_payload:
        ds = dates[-1] if dates else datetime.now().strftime('%Y%m%d')
        today_payload['date'] = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"   # 与归档标签保持一致
        write_json(DAILY_DIR / f"{ds}.json", today_payload, indent=None)
        if ds not in dates:
            dates.append(ds)

    dates = sorted(set(dates))
    write_json(OUT_DIR / 'dates.json', {
        'dates': dates,
        'labels': {d: f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates},
        'latest': dates[-1] if dates else None,
    })
    if dates:
        log(f"  dates.json: {len(dates)} 个交易日快照 ({dates[0]} ~ {dates[-1]})")
    else:
        log("  dates.json: 无历史快照(仅最新一日可选)")
    return dates


def prune_old_snapshots() -> int:
    """只保留最近 RETENTION_DAYS 天的快照

    删两处:data/industry_cache/{YYYYMMDD}/(每日 3 个文件,长期会撑大仓库)
    和 docs/data/daily/{YYYYMMDD}.json(日期选择器的历史选项)。
    注意:工作缓存(hist_*.csv / roe/ / 指标日报表)不在此列 —— 分位数依赖
    它们的 5 年滚动序列,删了会算不出结果。
    """
    import shutil
    from datetime import timedelta

    cutoff_int = int((datetime.now() - timedelta(days=RETENTION_DAYS)).strftime('%Y%m%d'))
    removed = 0

    for d in CACHE_DIR.glob('[0-9]' * 8):
        if d.is_dir() and d.name.isdigit() and int(d.name) < cutoff_int:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1

    for f in DAILY_DIR.glob('*.json'):
        if f.stem.isdigit() and len(f.stem) == 8 and int(f.stem) < cutoff_int:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass

    if removed:
        log(f"  清理: 删除 {removed} 个超过 {RETENTION_DAYS} 天的快照")
    return removed


# --------------------------------------------------------------- history/*.json
def export_history(skip_network: bool) -> int:
    """各行业近 10 年收盘价 + 成交额(紧凑数组格式,d/c/a 三列)"""
    df_latest = read_csv_safe(CACHE_DIR / "industry_observation.csv")
    name_by_code = {}
    if df_latest is not None and not df_latest.empty:
        name_by_code = {
            str(r['行业代码']).strip(): str(r['行业名称']).strip()
            for _, r in df_latest.iterrows()
        }

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    codes = sorted(p.stem.replace('hist_', '') for p in CACHE_DIR.glob('hist_*.csv'))
    ok = 0
    for code in codes:
        df = None
        # 优先联网拉全量(接口本身返回全历史,缓存被 Industry_Data.py 裁到了 5 年)
        if not skip_network:
            df = fetch_hist_full(code)
        if df is None or df.empty:
            df = read_csv_safe(CACHE_DIR / f"hist_{code}.csv")
        if df is None or df.empty:
            continue

        df = df.copy()
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
        df = df.dropna(subset=['日期']).sort_values('日期')
        if HISTORY_YEARS:
            cutoff = datetime.now().replace(year=datetime.now().year - HISTORY_YEARS)
            df = df[df['日期'] >= cutoff]
        if df.empty:
            continue

        amount = df['成交额'] if '成交额' in df.columns else pd.Series([None] * len(df), index=df.index)
        payload = {
            'code': code,
            'name': name_by_code.get(code, code),
            'd': [int(d.strftime('%Y%m%d')) for d in df['日期']],
            'c': [round(float(v), 2) for v in df['收盘']],
            'a': [round(float(v), 2) if pd.notna(v) else None for v in amount],
        }

        # 不用更短的序列覆盖已有文件:离线模式读到的是被裁到 5 年的缓存,
        # 放任覆盖会把 10 年走势图缩水成 5 年
        prev = HISTORY_DIR / f"{code}.json"
        if prev.exists():
            try:
                with open(prev, encoding='utf-8') as f:
                    if len(json.load(f).get('d', [])) > len(payload['d']):
                        continue
            except Exception:
                pass

        write_json(prev, payload, indent=None)
        ok += 1
    log(f"  history/: {ok} 个行业")
    return ok


def fetch_hist_full(code: str) -> pd.DataFrame | None:
    """联网拉取单行业全量历史(不裁剪),失败返回 None"""
    try:
        import akshare as ak
        df = ak.index_hist_sw(symbol=code, period="day")
        if df is None or df.empty:
            return None
        keep = [c for c in ('日期', '收盘', '成交额') if c in df.columns]
        return df[keep]
    except Exception as e:
        log(f"    [警告] {code} 历史拉取失败,改用缓存: {type(e).__name__}")
        return None


# ------------------------------------------------------------------- etf.json
def export_etf(skip_network: bool) -> None:
    """按行业关键词匹配全市场 ETF"""
    if skip_network:
        log("  etf.json: 已跳过(离线模式)")
        return
    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
    except Exception as e:
        log(f"  [警告] ETF 行情获取失败({type(e).__name__}),保留旧文件")
        return
    if df is None or df.empty:
        log("  [警告] ETF 行情为空,保留旧文件")
        return

    name_col = '名称' if '名称' in df.columns else df.columns[1]
    code_col = '代码' if '代码' in df.columns else df.columns[0]

    def num(row, *cands):
        for c in cands:
            if c in df.columns:
                v = to_num(row.get(c))
                if v is not None:
                    return v
        return None

    result = {}
    for code, keywords in INDUSTRY_KEYWORDS.items():
        hits = []
        for _, row in df.iterrows():
            etf_name = str(row.get(name_col, '')).strip()
            if any(b in etf_name for b in ETF_BLACKLIST):
                continue
            score = sum(2 for kw in keywords if kw in etf_name)
            if score == 0:
                continue
            hits.append({
                'code': str(row.get(code_col, '')).strip(),
                'name': etf_name,
                'price': num(row, '最新价'),
                'chg': num(row, '涨跌幅'),
                'amount': num(row, '成交额'),
                'scale': num(row, '流通市值', '总市值'),
                '_score': score,
            })
        # 相关度优先,其次成交额(流动性)
        hits.sort(key=lambda h: (-h['_score'], -(h['amount'] or 0)))
        for h in hits:
            h.pop('_score', None)
        result[code] = hits[:MAX_ETF_PER_IND]

    write_json(OUT_DIR / 'etf.json', {
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'industries': result,
    })
    total = sum(len(v) for v in result.values())
    log(f"  etf.json: {total} 条匹配,覆盖 {sum(1 for v in result.values() if v)} 个行业")


# ------------------------------------------------------------------ news.json
def export_news(skip_network: bool) -> None:
    """财联社电报 + 东财全球财讯,合并去重后按行业归类"""
    if skip_network:
        log("  news.json: 已跳过(离线模式)")
        return

    try:
        import akshare as ak
    except Exception as e:
        log(f"  [警告] akshare 不可用: {e}")
        return

    items = []

    # 1) 东方财富全球财讯:列 = 标题/摘要/发布时间/链接(条数最多,主力源)
    try:
        df = ak.stock_info_global_em()
        for _, r in df.iterrows():
            title = str(r.get('标题', '')).strip()
            if not title:
                continue
            items.append({
                'title': title,
                'summary': str(r.get('摘要', '')).strip()[:200],
                'time': str(r.get('发布时间', '')).strip(),
                'source': '东方财富',
                'url': str(r.get('链接', '')).strip(),
            })
        log(f"    东方财富: {len(items)} 条")
    except Exception as e:
        log(f"    [警告] 东财财讯失败: {type(e).__name__}")

    # 2) 同花顺全球快讯:列 = 标题/内容/发布时间/链接(内容更详实)
    try:
        df_ths = ak.stock_info_global_ths()
        n0 = len(items)
        for _, r in df_ths.iterrows():
            title = str(r.get('标题', '')).strip()
            if not title:
                continue
            items.append({
                'title': title,
                'summary': str(r.get('内容', '')).strip()[:200],
                'time': str(r.get('发布时间', '')).strip(),
                'source': '同花顺',
                'url': str(r.get('链接', '')).strip(),
            })
        log(f"    同花顺: {len(items) - n0} 条")
    except Exception as e:
        log(f"    [警告] 同花顺快讯失败: {type(e).__name__}")

    # 3) 新浪财经全球快讯:列 = 时间/内容(无独立标题,取正文首句)
    try:
        df_sina = ak.stock_info_global_sina()
        n0 = len(items)
        for _, r in df_sina.iterrows():
            content = str(r.get('内容', '')).strip()
            if not content:
                continue
            head = re.split(r'[。;;::!?!?]', content)[0].strip()
            items.append({
                'title': head[:60] if head else content[:60],
                'summary': content[:200],
                'time': str(r.get('时间', '')).strip(),
                'source': '新浪财经',
                'url': '',
            })
        log(f"    新浪财经: {len(items) - n0} 条")
    except Exception as e:
        log(f"    [警告] 新浪快讯失败: {type(e).__name__}")

    if not items:
        log("  [警告] 资讯全部抓取失败,保留旧文件")
        return

    # 去重:标题归一化后相同视为同一条(保留信息更全的那条)
    def norm(t: str) -> str:
        return re.sub(r'[\s\W]+', '', t)[:40]

    dedup: dict[str, dict] = {}
    for it in items:
        k = norm(it['title'])
        if not k:
            continue
        old = dedup.get(k)
        if old is None or len(it.get('summary', '')) > len(old.get('summary', '')):
            dedup[k] = it
    items = list(dedup.values())
    log(f"    去重后: {len(items)} 条")

    # 按行业关键词归类(一条资讯可同时属于多个行业)
    by_ind: dict[str, list] = {code: [] for code in INDUSTRY_KEYWORDS}
    for it in items:
        text = it['title'] + ' ' + it.get('summary', '')   # 摘要仅用于归类匹配,不对外发布
        for code, keywords in INDUSTRY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                by_ind[code].append(it)

    # 对外只发布「标题 + 时间 + 来源 + 原文链接」:
    # 摘要是他人作品的正文片段,公开转载有版权风险;标题通常不构成独立作品,
    # 且已附原文链接,读者点开即可看到全文。
    for code in by_ind:
        by_ind[code] = [{'title': i['title'], 'time': i['time'],
                         'source': i['source'], 'url': i['url']}
                        for i in by_ind[code][:MAX_NEWS_PER_IND]]

    write_json(OUT_DIR / 'news.json', {
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'total': len(items),
        'industries': by_ind,
    })
    covered = sum(1 for v in by_ind.values() if v)
    log(f"  news.json: 全市场 {len(items)} 条,覆盖 {covered}/{len(by_ind)} 个行业")


def main() -> int:
    ap = argparse.ArgumentParser(description='导出 Industry-Insight 看板数据')
    ap.add_argument('--skip-network', action='store_true',
                    help='只用本地缓存(不联网抓 ETF/资讯/历史)')
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log('=' * 64)
    log(f"Industry-Insight 数据导出 @ {datetime.now():%Y-%m-%d %H:%M:%S}")
    log('=' * 64)

    latest = export_latest()
    if latest is None:
        log("[中止] 缺少 industry_observation.csv,请先运行 Industry_Data.py")
        return 1

    export_daily_archives(latest)
    export_history(args.skip_network)
    export_etf(args.skip_network)
    export_news(args.skip_network)

    log('=' * 64)
    log(f"导出完成 -> {OUT_DIR}")
    log('=' * 64)
    return 0


if __name__ == '__main__':
    sys.exit(main())
