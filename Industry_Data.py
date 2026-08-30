"""
industry_observer.py
使用 AKshare 免费数据构建行业观测数据集

调整说明（2026-08-30）：
- 移除 换手率/波动率、换手率/波动率分位数、ROE同比的同比 三个指标
- 成交额市值占比改为近10日换手率（近10个交易日成交额合计 / 当日流通市值），分位数基于该指标近5年滚动序列
- 新增 模型意见（多因子打分 -> 高配/平配/低配），并在其后附各因子单项得分与模型总得分（可追溯高低配原因）
- 新增 市值加权 PE/ROE（成分股按流通市值加权；历史序列以当期市值固定回测，与申万官网行业估值分位口径一致）
- 新增 分析师意见 列（默认平配；每次运行导入 脚本目录/分析师意见.xlsx 覆盖该列）

修复说明（2026-08-29 二次修复）：
- 修复缓存读取 KeyError（缓存列已标准化为 date，读取时勿再用 发布日期）
- 缓存增量刷新：指标日报表/历史价格缓存超过 3 天自动补拉新增区间
- 指标日报表拉取扩展到近5年（首次约8-9分钟，之后增量秒级）
- 换手率/波动率分位数改为真实历史序列计算（替代原复制换手率分位数的代理值）
- ROE同比改为按季度号精确匹配去年同期（修复季度缺失时 iloc 错位）
- ROE分位数剔除当期值（避免当期同时计入历史序列导致分位数有偏）
- ROE匹配性能优化（预建 季度->行业->ROE序列 索引，替代全表扫描）
- 新增 Excel 输出（industry_observation.xlsx）
- SW_CODES 清理为 2021 版 31 个一级行业（删除 2014 版旧代码）
- 修复 fetch_roe_history 用 90 天步长可能跳过季度的边界问题（改为30天步长）

实测结论（2026-08-29, akshare 1.18.64 / pandas 3.0.1）：
- ak.index_analysis_daily_sw(): 逐日爬取，可回溯 5 年以上，约 0.4秒/交易日
  -> 120天约21秒，5年约8-9分钟（仅首次全量，之后增量补拉）
- ak.index_hist_sw(): 单请求全量（1999至今），约1.5秒/行业；无涨跌幅列，由收盘价计算
- ak.sw_index_daily_indicator(): 该 akshare 版本中不存在，已弃用此备选

数据源：
- 申万一级行业信息: ak.sw_index_first_info()
- 申万一级行业指标日报表: ak.index_analysis_daily_sw() — 含PE、换手率、流通市值、成交额占比
- 申万指数历史行情: ak.index_hist_sw() — 含价格、成交量、成交额
- A股业绩报表(ROE): ak.stock_yjbb_em()

输出指标（14个）:
1. 行业名称
2. 行业代码
3. 行业A股占比（行业流通市值 / 全部行业流通市值之和）
4. 最新PE值
5. 近5年PE分位数
6. 近10日换手率（近10个交易日成交额合计 / 当日流通市值）
7. 近10日换手率近5年分位数
8. ROE（净资产收益率，中位数）
9. ROE近5年分位数
10. ROE同比（当前 vs 去年同期）
11. 近1周涨跌幅
12. 近1月涨跌幅
13. 近1年涨跌幅
14. 模型意见（多因子打分 -> 高配/平配/低配）
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import time
import warnings

warnings.filterwarnings('ignore')

YEAR_DAYS = 5 * 365 + 60  # 近5年指标窗口（含30天冗余覆盖节假日）


class IndustryObserver:
    """
    行业观测数据构建器
    基于 AKshare 免费接口，申万一级行业
    """

    # 申万一级行业代码（2021版标准31个一级行业）
    SW_CODES = [
        '801010', '801030', '801040', '801050', '801080',
        '801110', '801120', '801130', '801140', '801150',
        '801160', '801170', '801180', '801200', '801210',
        '801230', '801710', '801720', '801730', '801740',
        '801750', '801760', '801770', '801780', '801790',
        '801880', '801890', '801950', '801960', '801970',
        '801980'
    ]

    # 东财行业 -> 申万一级行业 近似映射（用于ROE数据匹配）
    INDUSTRY_MAP = {
        '农林牧渔': ['农林牧渔', '种植业', '渔业', '饲料', '农产品加工', '养殖业'],
        '基础化工': ['化工', '化学制品', '化学原料', '化学纤维', '塑料', '橡胶'],
        '钢铁': ['钢铁', '普钢', '特钢'],
        '有色金属': ['有色金属', '贵金属', '工业金属', '能源金属', '小金属', '金属新材料'],
        '建筑材料': ['建筑材料', '水泥', '玻璃玻纤', '装修建材'],
        '建筑装饰': ['建筑装饰', '房屋建设', '基础建设', '专业工程', '装修装饰'],
        '机械设备': ['机械设备', '通用设备', '专用设备', '轨交设备', '工程机械', '自动化设备'],
        '电力设备': ['电力设备', '电池', '光伏设备', '风电设备', '电网设备', '电机'],
        '国防军工': ['国防军工', '航天装备', '航空装备', '地面兵装', '航海装备', '军工电子'],
        '汽车': ['汽车', '乘用车', '商用车', '汽车零部件', '汽车服务'],
        '家用电器': ['家用电器', '白色家电', '黑色家电', '小家电', '厨卫电器', '照明设备'],
        '食品饮料': ['食品饮料', '白酒', '非白酒', '饮料乳品', '休闲食品', '食品加工'],
        '纺织服饰': ['纺织服饰', '纺织制造', '服装家纺', '饰品'],
        '轻工制造': ['轻工制造', '造纸', '包装印刷', '家居用品', '文娱用品'],
        '医药生物': ['医药生物', '生物制品', '化学制药', '中药', '医疗器械', '医疗服务', '医药商业'],
        '公用事业': ['公用事业', '电力', '燃气', '水务'],
        '交通运输': ['交通运输', '物流', '航运港口', '航空机场', '铁路公路'],
        '房地产': ['房地产', '房地产开发', '房地产服务'],
        '商贸零售': ['商贸零售', '贸易', '一般零售', '专业连锁', '互联网电商'],
        '社会服务': ['社会服务', '旅游及景区', '酒店餐饮', '教育', '专业服务'],
        '综合': ['综合'],
        '计算机': ['计算机', '软件开发', 'IT服务', '计算机设备'],
        '传媒': ['传媒', '游戏', '广告营销', '影视院线', '出版', '电视广播', '数字媒体'],
        '通信': ['通信', '通信设备', '通信服务'],
        '银行': ['银行', '国有大型银行', '股份制银行', '城商行', '农商行'],
        '非银金融': ['非银金融', '证券', '保险', '多元金融'],
        '电子': ['电子', '半导体', '元件', '光学光电子', '消费电子', '电子化学品', '其他电子'],
        '煤炭': ['煤炭', '煤炭开采'],
        '石油石化': ['石油石化', '油气开采', '油服工程', '炼化及贸易'],
        '环保': ['环保', '环境治理', '环保设备'],
        '美容护理': ['美容护理', '个护用品', '化妆品']
    }

    def __init__(self, cache_dir: str = "data/industry_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.roe_dir = self.cache_dir / "roe"
        self.roe_dir.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # 数据获取（带重试）
    # ------------------------------------------------------------------

    def _safe_call(self, func, max_retries=3, sleep_sec=2, **kwargs):
        """带重试的安全调用器"""
        for i in range(max_retries):
            try:
                return func(**kwargs)
            except Exception as e:
                err_str = str(e).lower()
                if 'proxy' in err_str or 'connection' in err_str or 'timeout' in err_str:
                    print(f"      网络错误，{sleep_sec}秒后重试 ({i+1}/{max_retries})...")
                    time.sleep(sleep_sec * (i + 1))
                elif i < max_retries - 1:
                    print(f"      错误: {e}, 重试 ({i+1}/{max_retries})...")
                    time.sleep(sleep_sec)
                else:
                    raise
        return pd.DataFrame()

    def _load_or_fetch(self, name: str, fetch_func, use_cache: bool = True, **kwargs):
        """通用缓存加载器"""
        cache_path = self.cache_dir / f"{name}.csv"
        if use_cache and cache_path.exists():
            try:
                return pd.read_csv(cache_path)
            except Exception:
                pass
        df = fetch_func(**kwargs)
        if not df.empty:
            df.to_csv(cache_path, index=False, encoding='utf-8-sig')
        return df

    def fetch_industry_list(self, use_cache: bool = True) -> pd.DataFrame:
        """获取申万一级行业列表（含当前PE/PB）"""
        try:
            df = self._load_or_fetch(
                "industry_list",
                lambda: self._safe_call(ak.sw_index_first_info),
                use_cache
            )
        except Exception as e:
            print(f"    [警告] 行业列表获取失败: {e}")
            return pd.DataFrame()
        if df.empty:
            return df
        # 标准化列名
        df = df.rename(columns={
            '行业代码': 'code',
            '行业名称': 'name',
            '成份个数': 'cons_count',
            '静态市盈率': 'pe_static',
            'TTM(滚动)市盈率': 'pe_ttm',
            '市净率': 'pb',
            '静态股息率': 'dividend_yield'
        })
        df['code'] = df['code'].astype(str).str.replace('.SI', '', regex=False)
        df = df[df['code'].isin(self.SW_CODES)].copy()
        return df

    @staticmethod
    def _standardize_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """指标日报表列名标准化（网络/缓存统一入口）"""
        col_map = {
            '指数代码': 'code',
            '指数名称': 'name',
            '发布日期': 'date',
            '收盘指数': 'close',
            '成交量': 'volume',
            '涨跌幅': 'chg_pct',
            '换手率': 'turnover',
            '市盈率': 'pe',
            '市净率': 'pb',
            '均价': 'vwap',
            '成交额占比': 'amount_pct',
            '流通市值': 'float_mv',
            '平均流通市值': 'avg_float_mv',
            '股息率': 'dividend_yield'
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        if 'date' not in df.columns:
            return pd.DataFrame()
        if 'code' in df.columns:
            # 统一为字符串并去掉 .SI 后缀,避免与行业列表(code为str)类型不匹配
            df['code'] = df['code'].astype(str).str.replace('.SI', '', regex=False)
        df['date'] = pd.to_datetime(df['date'])
        return df

    def _fetch_indicators_by_chunks(self, start_date, end_date, chunk_days: int = 370) -> list:
        """按年分块拉取指标日报表，避免单次请求过大"""
        frames = []
        cur = start_date
        while cur < end_date:
            nxt = min(cur + timedelta(days=chunk_days), end_date)
            try:
                part = self._safe_call(
                    ak.index_analysis_daily_sw,
                    symbol="一级行业",
                    start_date=cur.strftime("%Y%m%d"),
                    end_date=nxt.strftime("%Y%m%d")
                )
                if not part.empty:
                    frames.append(self._standardize_indicators(part))
                print(f"      {cur:%Y-%m-%d} ~ {nxt:%Y-%m-%d}: "
                      f"{'OK' if not part.empty else '空'} ({len(part)}行)")
            except Exception as e:
                print(f"      [警告] {cur:%Y-%m-%d} ~ {nxt:%Y-%m-%d} 获取失败: {e}")
            cur = nxt + timedelta(days=1)
            time.sleep(0.3)  # 避免请求过快
        return frames

    def fetch_industry_indicators(self, days: int = YEAR_DAYS, use_cache: bool = True) -> pd.DataFrame:
        """
        获取申万一级行业指标日报表（含PE、换手率、流通市值、成交额占比）
        首次全量拉取近5年（约8-9分钟），之后缓存超过3天自动增量补拉
        """
        cache = self.cache_dir / f"industry_indicators_{days}d.csv"
        if use_cache and cache.exists():
            try:
                df = pd.read_csv(cache)
                df['date'] = pd.to_datetime(df['date'])
                # read_csv 会把纯数字代码推断为 int,统一转回字符串并与行业列表对齐
                df['code'] = df['code'].astype(str).str.replace('.SI', '', regex=False)
                last = df['date'].max()
                # 增量：每日运行自动补拉(阈值1天, 周一/节假日后自动补齐)
                if last >= datetime.now() - timedelta(days=1):
                    return df
                print(f"      缓存到 {last:%Y-%m-%d}，补拉新增区间...")
                frames = self._fetch_indicators_by_chunks(
                    last - timedelta(days=10), datetime.now())
                if frames:
                    combined = pd.concat([df] + frames) \
                        .drop_duplicates(subset=['code', 'date'], keep='last') \
                        .sort_values(['code', 'date']).reset_index(drop=True)
                    combined.to_csv(cache, index=False, encoding='utf-8-sig')
                    return combined
                return df
            except Exception as e:
                print(f"      缓存读取失败，重新全量获取: {e}")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 30)  # 多取30天确保覆盖交易日
        print(f"      首次全量拉取近{int(days / 365)}年指标数据"
              f"（约{int(days / 365 * 250)}个交易日，需数分钟）...")
        frames = self._fetch_indicators_by_chunks(start_date, end_date)
        if not frames:
            print("    [警告] 行业指标获取失败")
            return pd.DataFrame()
        df = pd.concat(frames) \
            .drop_duplicates(subset=['code', 'date'], keep='last') \
            .sort_values(['code', 'date']).reset_index(drop=True)
        df.to_csv(cache, index=False, encoding='utf-8-sig')
        return df

    def _fetch_hist_raw(self, symbol: str) -> pd.DataFrame:
        """拉取单行业原始历史价格（保留近5年窗口；该接口无涨跌幅列，由收盘价计算）"""
        try:
            df = self._safe_call(ak.index_hist_sw, symbol=symbol, period="day")
            if df.empty:
                return df
            df['日期'] = pd.to_datetime(df['日期'])
            cutoff = datetime.now() - timedelta(days=YEAR_DAYS)
            df = df[df['日期'] >= cutoff].copy()
            return df.sort_values('日期').reset_index(drop=True)
        except Exception as e:
            print(f"    [警告] {symbol} 历史数据获取失败: {e}")
            return pd.DataFrame()

    def fetch_industry_hist(self, symbol: str, use_cache: bool = True) -> pd.DataFrame:
        """
        获取单个申万一级行业历史价格数据（近5年+）
        该接口单请求全量（约1.5秒），缓存超过3天直接重拉合并
        """
        cache = self.cache_dir / f"hist_{symbol}.csv"
        if use_cache and cache.exists():
            try:
                df = pd.read_csv(cache)
                df['日期'] = pd.to_datetime(df['日期'])
                # 增量：每日运行自动补拉(阈值1天, 周一/节假日后自动补齐)
                if df['日期'].max() >= datetime.now() - timedelta(days=1):
                    return df
                # 缓存太旧，重拉合并
                df_new = self._fetch_hist_raw(symbol)
                if not df_new.empty:
                    combined = pd.concat([df, df_new]) \
                        .drop_duplicates(subset='日期', keep='last') \
                        .sort_values('日期').reset_index(drop=True)
                    combined.to_csv(cache, index=False, encoding='utf-8-sig')
                    return combined
                return df
            except Exception:
                pass

        df = self._fetch_hist_raw(symbol)
        if not df.empty:
            df.to_csv(cache, index=False, encoding='utf-8-sig')
        return df

    def fetch_all_industry_hist(self, sw_codes: list, use_cache: bool = True) -> dict:
        """批量获取所有行业历史价格数据"""
        all_data = {}
        for code in sw_codes:
            print(f"  获取 {code} 历史价格...", end=" ")
            df = self.fetch_industry_hist(code, use_cache)
            if not df.empty and len(df) >= 60:
                all_data[code] = df
                print(f"OK ({len(df)}条)")
            else:
                print(f"FAIL")
            time.sleep(0.3)  # 避免请求过快
        return all_data

    def fetch_roe_by_quarter(self, quarter: str, use_cache: bool = True) -> pd.DataFrame:
        """获取指定季度的业绩报表（含ROE、净利润和行业）"""
        cache = self.roe_dir / f"roe_{quarter}.csv"
        if use_cache and cache.exists():
            df = pd.read_csv(cache)
            # 旧缓存缺少净利润列(市值加权PE需要TTM净利润) -> 失效重拉
            if '净利润-净利润' in df.columns:
                return df
            print(f"    [提示] {quarter} 缓存缺少净利润列, 重新拉取")

        try:
            df = self._safe_call(ak.stock_yjbb_em, date=quarter)
            if df.empty:
                return df
            keep = ['股票代码', '股票简称', '所处行业', '净资产收益率', '净利润-净利润', '最新公告日期']
            keep = [c for c in keep if c in df.columns]
            df = df[keep].copy()
            for col in ('净资产收益率', '净利润-净利润'):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df.to_csv(cache, index=False, encoding='utf-8-sig')
            return df
        except Exception as e:
            print(f"    [警告] {quarter} ROE数据获取失败: {e}")
            return pd.DataFrame()

    def fetch_roe_history(self, years: int = 5, use_cache: bool = True) -> pd.DataFrame:
        """获取近N年季度ROE数据（30天步长迭代，确保覆盖每个季度）"""
        quarters = []
        now = datetime.now()
        for i in range(years * 12 + 3):
            d = now - timedelta(days=i * 30)
            y, m = d.year, d.month
            q = (f"{y}0331" if m <= 3 else
                 f"{y}0630" if m <= 6 else
                 f"{y}0930" if m <= 9 else f"{y}1231")
            if q not in quarters:
                quarters.append(q)

        frames = []
        for q in sorted(quarters):
            df = self.fetch_roe_by_quarter(q, use_cache)
            if not df.empty and '所处行业' in df.columns:
                df['季度'] = q
                frames.append(df)
            time.sleep(0.3)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def fetch_component_lists(self, use_cache: bool = True) -> dict:
        """获取申万一级行业成分股名单(最新) -> {sw_code: [(股票代码, 最新权重),...]}

        权重取申万官方指数成分'最新权重'(市值加权口径), 供市值加权PE/ROE使用;
        旧缓存缺 '证券代码'/'最新权重' 列时失效重拉。
        """
        cache = self.cache_dir / "components_latest.csv"
        if use_cache and cache.exists():
            cdf = pd.read_csv(cache)
            if {'sw_code', '股票代码', '最新权重'}.issubset(cdf.columns):
                out = {}
                # CSV 中 sw_code 为整数, 统一转 str 与 SW_CODES 匹配
                for code, grp in cdf.groupby(cdf['sw_code'].astype(str)):
                    out[code] = list(zip(grp['股票代码'].astype(str).str.zfill(6),
                                         pd.to_numeric(grp['最新权重'], errors='coerce')))
                return out
            print("      [提示] 成分股缓存缺 最新权重 列, 重新拉取")
        frames = []
        for code in self.SW_CODES:
            try:
                df = self._safe_call(ak.index_component_sw, symbol=code)
                if df.empty:
                    continue
                code_col = next((c for c in ('证券代码', '成分券代码', '成分代码', '股票代码') if c in df.columns), None)
                name_col = next((c for c in ('证券名称', '成分券名称', '成分名称', '股票名称') if c in df.columns), None)
                w_col = next((c for c in ('最新权重',) if c in df.columns), None)
                if not code_col:
                    continue
                cols = [code_col] + ([name_col] if name_col else []) + ([w_col] if w_col else [])
                sub = df[cols].copy()
                sub.columns = ['股票代码', '股票名称', '最新权重'][:len(sub.columns)]
                sub['sw_code'] = code
                frames.append(sub)
                print(f"      {code}: {len(sub)} 只成分股")
                time.sleep(0.3)
            except Exception as e:
                print(f"      [警告] {code} 成分股获取失败: {e}")
        if frames:
            cdf = pd.concat(frames, ignore_index=True)
            cdf.to_csv(cache, index=False, encoding='utf-8-sig')
            out = {}
            for code, grp in cdf.groupby('sw_code'):
                out[code] = list(zip(grp['股票代码'].astype(str).str.zfill(6),
                                     pd.to_numeric(grp['最新权重'], errors='coerce')))
            return out
        return {}

    def fetch_market_snapshot(self, use_cache: bool = True) -> pd.DataFrame:
        """全市场A股快照(代码->流通市值), 缓存1天"""
        cache = self.cache_dir / "market_snapshot.csv"
        if use_cache and cache.exists():
            age = datetime.now() - datetime.fromtimestamp(cache.stat().st_mtime)
            if age < timedelta(days=1):
                return pd.read_csv(cache)
        df = self._safe_call(ak.stock_zh_a_spot_em)
        if df.empty:
            return pd.DataFrame()
        keep = [c for c in ('代码', '流通市值') if c in df.columns]
        df = df[keep].copy()
        df.to_csv(cache, index=False, encoding='utf-8-sig')
        return df

    # ------------------------------------------------------------------
    # 计算工具
    # ------------------------------------------------------------------

    @staticmethod
    def percentile(series: pd.Series, latest) -> float:
        """计算最新值在历史序列中的百分位（0-100）"""
        clean = pd.to_numeric(series, errors='coerce').dropna()
        if clean.empty or pd.isna(latest):
            return np.nan
        return (clean < latest).mean() * 100

    @staticmethod
    def n_day_return(df: pd.DataFrame, n: int, close_col='收盘') -> float:
        """计算N日涨跌幅（%）"""
        if len(df) < n + 1:
            return np.nan
        latest = pd.to_numeric(df.iloc[-1][close_col], errors='coerce')
        past = pd.to_numeric(df.iloc[-(n + 1)][close_col], errors='coerce')
        if pd.isna(latest) or pd.isna(past) or past == 0:
            return np.nan
        return (latest / past - 1) * 100

    @staticmethod
    def prev_year_quarter(q: str) -> str:
        """返回去年同季度报告期，如 20260630 -> 20250630"""
        return f"{int(q[:4]) - 1}{q[4:]}"

    def build_roe_index(self, roe_history: pd.DataFrame) -> dict:
        """预构建 季度 -> {东财行业名: ROE序列} 索引，替代逐行全表扫描"""
        idx = {}
        for q, g in roe_history.groupby('季度'):
            m = {}
            for ind, sub in g.groupby('所处行业'):
                m[str(ind)] = sub['净资产收益率'].dropna()
            idx[q] = m
        return idx

    def match_industry_roe(self, roe_index: dict, quarter: str, sw_name: str) -> pd.Series:
        """基于预构建索引匹配ROE（关键词子串匹配）"""
        m = roe_index.get(quarter)
        if not m:
            return pd.Series(dtype=float)
        keywords = self.INDUSTRY_MAP.get(sw_name, [sw_name])
        parts = [s for ind, s in m.items() if any(kw in str(ind) for kw in keywords)]
        if not parts:
            return pd.Series(dtype=float)
        return pd.concat(parts)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def build(self, use_cache: bool = True) -> pd.DataFrame:
        """构建完整行业观测数据集"""
        print("=" * 72)
        print("行业观测数据集构建开始")
        print("=" * 72)

        # 1. 行业列表
        print("\n[1/6] 获取申万一级行业列表...")
        industry_list = self.fetch_industry_list(use_cache)
        if industry_list.empty:
            print("[错误] 无法获取行业列表，请检查网络连接")
            return pd.DataFrame()
        print(f"      -> 共 {len(industry_list)} 个一级行业")

        # 2. 行业指标日报表（近5年，含PE、换手率、流通市值等）
        print("\n[2/6] 获取行业指标日报表（近5年，含PE/换手率/流通市值）...")
        indicators_df = self.fetch_industry_indicators(days=YEAR_DAYS, use_cache=use_cache)
        if indicators_df.empty:
            print("      [警告] 行业指标获取失败，将使用行业列表中的快照数据")
        else:
            print(f"      -> {indicators_df['date'].nunique()} 个交易日, "
                  f"{indicators_df['code'].nunique()} 个行业")
        ind_codes = set(indicators_df['code'].values) if not indicators_df.empty else set()

        # 3. 历史价格数据（近5年，计算涨跌幅和波动率）
        print("\n[3/6] 获取行业历史价格（近5年，计算涨跌幅/波动率）...")
        sw_codes = industry_list['code'].tolist()
        all_hist = self.fetch_all_industry_hist(sw_codes, use_cache)
        print(f"      -> 成功 {len(all_hist)} 个行业")

        # 4. ROE历史
        print("\n[4/6] 获取ROE季度数据（近5年）...")
        roe_history = self.fetch_roe_history(years=5, use_cache=use_cache)
        if not roe_history.empty:
            print(f"      -> {roe_history['季度'].nunique()} 个季度, {len(roe_history)} 条记录")
            roe_index = self.build_roe_index(roe_history)
        else:
            print("      -> 获取失败，ROE指标将留空")
            roe_index = {}

        # 4.5 市值加权指标准备(成分股名单/市值快照/季度净利润)
        print("\n[4.5] 市值加权指标数据准备(成分股/市值/净利润)...")
        net_map = {}
        if not roe_history.empty:
            # TTM净利润需要 去年同期累计 与 去年年报, 补齐缺失季度
            need_qs = set()
            for q in roe_history['季度'].unique():
                need_qs.add(self.prev_year_quarter(q))
                need_qs.add(f"{int(q[:4]) - 1}1231")
            miss_qs = need_qs - set(roe_history['季度'].unique())
            extra = []
            for q in sorted(miss_qs):
                df_q = self.fetch_roe_by_quarter(q, use_cache)
                if not df_q.empty:
                    df_q['季度'] = q
                    extra.append(df_q)
                time.sleep(0.3)
            if extra:
                roe_history = pd.concat([roe_history] + extra, ignore_index=True)
            # 季度 -> 净利润表
            for q, g in roe_history.groupby('季度'):
                sub = g[['股票代码', '净利润-净利润']].copy()
                sub['股票代码'] = sub['股票代码'].astype(str).str.zfill(6)
                sub['净利润-净利润'] = pd.to_numeric(sub['净利润-净利润'], errors='coerce')
                net_map[q] = sub.dropna(subset=['净利润-净利润'])
        print(f"      -> 可用季度 {len(net_map)} 个 (含TTM补拉季度)")

        def ttm_profit(q):
            """TTM净利润: 当期累计 - 去年同期累计 + 去年年报"""
            n_q = net_map.get(q)
            n_qy = net_map.get(self.prev_year_quarter(q))
            n_y = net_map.get(f"{int(q[:4]) - 1}1231")
            if n_q is None or n_qy is None or n_y is None:
                return {}
            d = n_q.set_index('股票代码')['净利润-净利润']
            dy = n_qy.set_index('股票代码')['净利润-净利润']
            ya = n_y.set_index('股票代码')['净利润-净利润']
            return (d - dy.reindex(d.index).fillna(0) + ya.reindex(d.index).fillna(0)).to_dict()

        # 成分股名单(最新) + 全市场市值快照
        comp_lists = {}
        mkt_map = {}
        if net_map:
            comp_lists = self.fetch_component_lists(use_cache)
            snap = self.fetch_market_snapshot(use_cache)
            if not snap.empty:
                snap['代码'] = snap['代码'].astype(str).str.zfill(6)
                mkt_map = dict(zip(snap['代码'], pd.to_numeric(snap['流通市值'], errors='coerce')))
        print(f"      -> 成分股覆盖 {len(comp_lists)} 个行业, 市值覆盖 {len(mkt_map)} 只股票")

        # 5. 逐行业计算
        print("\n[5/6] 计算各项指标...")
        records = []

        for _, row in industry_list.iterrows():
            code = row['code']
            name = row['name']

            if code not in all_hist:
                continue

            hist = all_hist[code]
            if len(hist) < 60:
                continue

            rec = {'行业名称': name, '行业代码': code}

            # ---- 从指标日报表获取最新数据 ----
            ind_latest = None
            ind_hist = None
            if not indicators_df.empty and code in ind_codes:
                ind_hist = indicators_df[indicators_df['code'] == code].sort_values('date')
                ind_latest = ind_hist.iloc[-1]

            # ---- 3. 行业A股占比 ----
            if ind_latest is not None and 'float_mv' in ind_latest and pd.notna(ind_latest['float_mv']):
                # 用所有行业最新流通市值之和作为分母
                latest_date = indicators_df['date'].max()
                latest_all = indicators_df[indicators_df['date'] == latest_date]
                total_mv = pd.to_numeric(latest_all['float_mv'], errors='coerce').sum()
                if total_mv > 0:
                    rec['行业A股占比_pct'] = round(float(ind_latest['float_mv']) / total_mv * 100, 4)
                else:
                    rec['行业A股占比_pct'] = np.nan
            else:
                rec['行业A股占比_pct'] = np.nan

            # ---- 4. 最新PE ----
            if ind_latest is not None and 'pe' in ind_latest and pd.notna(ind_latest['pe']):
                pe = float(ind_latest['pe'])
            elif 'pe_ttm' in row and pd.notna(row['pe_ttm']):
                pe = float(row['pe_ttm'])
            else:
                pe = np.nan
            rec['最新PE'] = round(pe, 2) if not pd.isna(pe) else np.nan

            # ---- 5. 近5年PE分位数 ----
            if ind_hist is not None and 'pe' in ind_hist.columns and not pd.isna(pe):
                pe_series = ind_hist['pe'].replace([0, np.inf, -np.inf], np.nan).dropna()
                if len(pe_series) >= 10:
                    rec['PE分位数'] = round(self.percentile(pe_series, pe), 2)
                else:
                    rec['PE分位数'] = np.nan
            else:
                rec['PE分位数'] = np.nan

            # ---- 6/7. 近10日换手率（近10个交易日成交额合计 / 当日流通市值）及近5年分位数 ----
            if ind_hist is not None and 'turnover' in ind_hist.columns and 'float_mv' in ind_hist.columns:
                # 每日成交额 = 当日换手率(%) × 当日流通市值 / 100（指标表无成交额绝对值，由换手率反推）
                daily_amount = ind_hist['turnover'] * ind_hist['float_mv'] / 100
                roll_amount = daily_amount.rolling(10, min_periods=10).sum()
                roll_turnover = roll_amount / ind_hist['float_mv'] * 100  # 近10日换手率(%)
                latest_tr = roll_turnover.iloc[-1]
                rec['近10日换手率_pct'] = round(latest_tr, 2) if pd.notna(latest_tr) else np.nan
                series = roll_turnover.replace([0, np.inf, -np.inf], np.nan).dropna()
                if len(series) >= 10 and pd.notna(latest_tr):
                    rec['近10日换手率分位数'] = round(self.percentile(series, latest_tr), 2)
                else:
                    rec['近10日换手率分位数'] = np.nan
            else:
                rec['近10日换手率_pct'] = np.nan
                rec['近10日换手率分位数'] = np.nan

            # ---- 10-13. ROE ----
            if roe_index:
                latest_quarter = max(roe_index.keys())
                roe_series = self.match_industry_roe(roe_index, latest_quarter, name)
                roe_current = roe_series.median() if not roe_series.empty else np.nan
                rec['ROE_pct'] = round(roe_current, 2) if not pd.isna(roe_current) else np.nan

                roe_by_q = []
                for q in sorted(roe_index.keys()):
                    q_roe = self.match_industry_roe(roe_index, q, name)
                    if not q_roe.empty:
                        roe_by_q.append({'季度': q, 'ROE': q_roe.median()})

                if len(roe_by_q) >= 4:
                    roe_df_q = pd.DataFrame(roe_by_q).sort_values('季度')
                    # 分位数：剔除当期，避免当期同时计入历史序列
                    hist_only = roe_df_q[roe_df_q['季度'] < latest_quarter]
                    if len(hist_only) >= 4 and not pd.isna(roe_current):
                        rec['ROE分位数'] = round(self.percentile(hist_only['ROE'], roe_current), 2)
                    else:
                        rec['ROE分位数'] = np.nan

                    # 同比：按季度号精确匹配去年同期
                    roe_map = dict(zip(roe_df_q['季度'], roe_df_q['ROE']))
                    if latest_quarter in roe_map:
                        prev_q = self.prev_year_quarter(latest_quarter)
                        if prev_q in roe_map:
                            roe_yoy = roe_map[latest_quarter] - roe_map[prev_q]
                            rec['ROE同比_pct'] = round(roe_yoy, 2)

            # ---- 16.5 市值加权 PE/ROE (成分股按流通市值加权, 当期市值固定回测历史) ----
            w_list = []
            for c, w in comp_lists.get(code, []):
                if w is not None and not pd.isna(w) and w > 0:
                    w_list.append((c, float(w)))          # 申万官方成分权重(市值口径)
                elif mkt_map.get(c, 0) > 0:
                    w_list.append((c, float(mkt_map[c])))  # 兜底: 全市场流通市值
            w_total = sum(w for _, w in w_list)
            if w_total > 0 and net_map:
                wmap = {c: w / w_total for c, w in w_list}
                roe_w_by_q = {}
                for q in sorted(net_map):
                    # 市值加权ROE: 各股ROE * 市值权重
                    gq = roe_history[roe_history['季度'] == q]
                    if not gq.empty:
                        gq2 = gq.copy()
                        gq2['_code'] = gq2['股票代码'].astype(str).str.zfill(6)
                        w = gq2['_code'].map(wmap)
                        roe_v = pd.to_numeric(gq2['净资产收益率'], errors='coerce')
                        m = w.notna() & roe_v.notna()
                        if m.any() and w[m].sum() > 0:
                            roe_w_by_q[q] = float((roe_v[m] * w[m]).sum() / w[m].sum())
                # 填列
                if roe_w_by_q:
                    lq = max(roe_w_by_q)
                    roe_w_latest = roe_w_by_q[lq]
                    rec['ROE_pct(市值加权)'] = round(roe_w_latest, 2)
                    hist_q = [roe_w_by_q[q] for q in sorted(roe_w_by_q) if q < lq]
                    if len(hist_q) >= 4:
                        rec['市值加权ROE分位数'] = round(self.percentile(pd.Series(hist_q), roe_w_latest), 2)
                    prev_q = self.prev_year_quarter(lq)
                    if prev_q in roe_w_by_q:
                        rec['市值加权ROE同比_pct'] = round(roe_w_latest - roe_w_by_q[prev_q], 2)

            # ROE 中位数块缺数据时置空相关键, 避免下游 KeyError
            for k in ('ROE_pct', 'ROE分位数', 'ROE同比_pct'):
                rec.setdefault(k, np.nan)

            # ---- 14-16. 涨跌幅 ----
            rec['近1周涨跌幅_pct'] = round(self.n_day_return(hist, 5), 2)
            rec['近1月涨跌幅_pct'] = round(self.n_day_return(hist, 20), 2)
            rec['近1年涨跌幅_pct'] = round(self.n_day_return(hist, 252), 2)

            # ---- 17. 模型意见（多因子打分：PE分位/换手率分位/ROE同比/动量） ----
            # 各因子单项得分单独成列，便于追溯 高配/低配 的原因
            pe_score = tr_score = roe_score = mom_score = 0
            if not pd.isna(rec['PE分位数']):
                if rec['PE分位数'] > 90:
                    pe_score = -1          # PE 过高，风险
                elif rec['PE分位数'] < 10:
                    pe_score = +1          # PE 过低，机会
            if not pd.isna(rec['近10日换手率分位数']):
                if rec['近10日换手率分位数'] > 90:
                    tr_score = -1          # 换手过热
                elif rec['近10日换手率分位数'] < 10:
                    tr_score = +1          # 换手低迷
            if not pd.isna(rec['ROE同比_pct']) and rec['ROE同比_pct'] > 0:
                roe_score = +1             # 盈利改善
            w, m = rec['近1周涨跌幅_pct'], rec['近1月涨跌幅_pct']
            if not pd.isna(w) and not pd.isna(m) and w > 0 and m > 0:
                # 双正: 近1周>60%*近1月记+1, 近1周<60%*近1月记0, 相等记-1
                mom_score = 1 if w > 0.6 * m else (0 if w < 0.6 * m else -1)
            else:
                mom_score = -1
            score = pe_score + tr_score + roe_score + mom_score
            rec['PE分位数得分'] = pe_score
            rec['换手率分位数得分'] = tr_score
            rec['ROE同比得分'] = roe_score
            rec['动量得分'] = mom_score
            rec['模型总得分'] = score
            rec['模型意见'] = '高配' if score > 0 else ('低配' if score < 0 else '平配')

            records.append(rec)

        # 6. 输出
        print("\n[6/6] 生成数据集...")
        df = pd.DataFrame(records)

        col_order = [
            '行业名称', '行业代码', '行业A股占比_pct',
            '最新PE', 'PE分位数',
            '近10日换手率_pct', '近10日换手率分位数',
            'ROE_pct', 'ROE分位数', 'ROE同比_pct',
            'ROE_pct(市值加权)', '市值加权ROE分位数', '市值加权ROE同比_pct',
            '近1周涨跌幅_pct', '近1月涨跌幅_pct', '近1年涨跌幅_pct',
            '模型意见',
            'PE分位数得分', '换手率分位数得分', 'ROE同比得分', '动量得分', '模型总得分',
            '分析师意见'
        ]
        col_order = [c for c in col_order if c in df.columns]
        df = df[col_order]

        # ---- 分析师意见: 默认平配, 导入 脚本目录/分析师意见.xlsx 覆盖 ----
        ana_path = Path(__file__).resolve().parent / "分析师意见.xlsx"
        df['分析师意见'] = '平配'
        if ana_path.exists():
            try:
                ana = pd.read_excel(ana_path)
                name_col = next((c for c in ('行业名称', '行业') if c in ana.columns), ana.columns[0])
                op_col = next((c for c in ('分析师意见', '意见') if c in ana.columns), ana.columns[-1])
                a_map = dict(zip(ana[name_col].astype(str), ana[op_col].astype(str)))
                df['分析师意见'] = df['行业名称'].map(a_map).fillna('平配')
                print(f"      -> 已导入 {len(a_map)} 条分析师意见")
            except Exception as e:
                print(f"      [警告] 读取 分析师意见.xlsx 失败: {e} (全部默认平配)")
        else:
            print("      [提示] 未找到 分析师意见.xlsx, 全部默认平配")
            try:
                ana = df[['行业名称']].copy()
                ana['分析师意见'] = '平配'
                ana.to_excel(ana_path, index=False)
                print(f"      已生成模板: {ana_path} (编辑后运行即可导入)")
            except Exception as e:
                print(f"      [警告] 生成模板失败: {e}")

        output_csv = self.cache_dir / "industry_observation.csv"
        output_json = self.cache_dir / "industry_observation.json"
        output_xlsx = self.cache_dir / "industry_observation.xlsx"
        # 带日期的归档版本(每日定时任务按日期留档)
        dated = datetime.now().strftime('%Y%m%d')
        dated_csv = self.cache_dir / f"industry_observation_{dated}.csv"
        dated_json = self.cache_dir / f"industry_observation_{dated}.json"
        dated_xlsx = self.cache_dir / f"industry_observation_{dated}.xlsx"
        df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        df.to_json(output_json, orient='records', force_ascii=False, indent=2)
        df.to_csv(dated_csv, index=False, encoding='utf-8-sig')
        df.to_json(dated_json, orient='records', force_ascii=False, indent=2)
        try:
            df.to_excel(output_xlsx, index=False)
            df.to_excel(dated_xlsx, index=False)
            print(f"  Excel: {output_xlsx}  /  {dated_xlsx}")
        except Exception as e:
            print(f"  [警告] Excel 输出失败（需 pip install openpyxl）: {e}")

        print(f"\n{'='*72}")
        print(f"数据集构建完成！")
        print(f"  CSV : {output_csv}")
        print(f"  JSON: {output_json}")
        print(f"  Excel: {output_xlsx}")
        print(f"  行业数: {len(df)} | 指标数: {len(df.columns)}")
        print(f"{'='*72}")

        return df


# ==============================================================================
# 入口
# ==============================================================================

def main():
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description='行业观测数据集构建')
    parser.add_argument('--no-cache', action='store_true', help='强制重新获取数据')
    parser.add_argument('--cache-dir', default='data/industry_cache', help='缓存目录')
    args = parser.parse_args()

    observer = IndustryObserver(cache_dir=args.cache_dir)
    df = observer.build(use_cache=not args.no_cache)

    if df.empty:
        print("\n[错误] 数据集为空，请检查网络连接和AKshare版本")
        return df

    print("\n" + "=" * 72)
    print("数据预览（前5行）:")
    print("=" * 72)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)
    print(df.head().to_string(index=False))

    print("\n" + "=" * 72)
    print("数据统计:")
    print("=" * 72)
    print(df.describe().round(2).to_string())

    print("\n" + "=" * 72)
    print("列说明:")
    print("=" * 72)
    desc = {
        '行业名称': '申万一级行业名称',
        '行业代码': '申万行业代码',
        '行业A股占比_pct': '行业流通市值 / 全部行业流通市值之和 (%)',
        '最新PE': '最新市盈率（TTM）',
        'PE分位数': '当前PE在近5年历史数据中的分位数 (0-100)',
        '近10日换手率_pct': '近10个交易日成交额合计 / 当日流通市值 (%)',
        '近10日换手率分位数': '近10日换手率在近5年滚动序列中的分位数 (0-100)',
        'ROE_pct': '净资产收益率中位数 (%)',
        'ROE分位数': 'ROE在近5年季度数据中的分位数 (0-100，已剔除当期)',
        'ROE同比_pct': '当前ROE vs 去年同期 (pct)',
        '模型意见': '多因子打分: PE分位>90或近10日换手率分位>90记-1,<10记+1; ROE同比>0记+1; 近1周>0、近1月>0且近1周>60%*近1月记+1; 合计>0高配,<0低配,=0平配',
        'PE分位数得分': 'PE分位>90记-1, <10记+1, 其余0',
        '换手率分位数得分': '近10日换手率分位>90记-1, <10记+1, 其余0',
        'ROE同比得分': 'ROE同比>0记+1, 其余0',
        '动量得分': '近1周>0且近1月>0: 近1周>60%*近1月记+1, 近1周<60%*近1月记0; 其余记-1',
        '模型总得分': '四项得分合计',
        'ROE_pct(市值加权)': '成分股ROE按流通市值加权平均 (%)',
        '市值加权ROE分位数': '市值加权ROE在近5年季度序列中的分位数 (0-100, 已剔除当期)',
        '市值加权ROE同比_pct': '市值加权ROE vs 去年同期 (pct)',
        '分析师意见': '人工意见列, 编辑脚本同目录 分析师意见.xlsx 后运行导入',
        '近1周涨跌幅_pct': '近5个交易日涨跌幅 (%)',
        '近1月涨跌幅_pct': '近20个交易日涨跌幅 (%)',
        '近1年涨跌幅_pct': '近252个交易日涨跌幅 (%)',
    }
    for col in df.columns:
        print(f"  {col:<30} {desc.get(col, '')}")

    return df


if __name__ == "__main__":
    df = main()
