"""走势归因分析 — 个股涨跌 α/β/γ 分解

将个股日收益分解为三因子:
  r_stock = α + β×r_sector + γ×r_market + ε

α 是个股特质收益 — 不能被板块和大盘解释的部分。
当 α 显著为负且偏离均值 >2σ 时，标记为「逻辑可能证伪，建议止损评估」。
"""
import numpy as np
import pandas as pd
from datetime import date, timedelta
from pathlib import Path
import sqlite3

BASE = Path(__file__).resolve().parent

MARKET_INDEX_CODE = "999999"  # 上证指数 (mootdx 标准市场)
SIGMA_THRESHOLD = 2.0         # α 偏离均值超过 2σ 触发告警
LOOKBACK_MIN = 20             # 最少需要 20 个交易日数据
LOOKBACK_DEFAULT = 60         # 默认回看天数


def _fetch_index_klines(code: str, days: int = 120) -> pd.DataFrame | None:
    """拉取指数 K 线（兼容 mootdx 指数代码）。"""
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std')
        df = client.bars(symbol=code, category=4, offset=days)
        if df is None or df.empty:
            return None
        df = df.reset_index(drop=True)
        for col in ["close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return None


def _get_sector_codes(code: str) -> list[str]:
    """获取标的对应的板块指数代码（同花顺行业+概念）。

    返回 mootdx 兼容的指数代码列表，优先行业指数。
    """
    try:
        import data
        import config
    except ImportError:
        return []

    candidates = []

    # 主要概念 → 尝试找对应的同花顺板块指数
    concept_map = config._lazy("STOCK_PRIMARY_CONCEPT", config._CONCEPT_CACHE)
    primary = concept_map.get(code, "")
    if primary:
        sector_code = _concept_to_index_code(primary)
        if sector_code:
            candidates.append(sector_code)

    # 二级概念
    secondary_map = config._lazy("STOCK_SECONDARY_CONCEPT", config._SECONDARY_CACHE)
    secondary = secondary_map.get(code, "")
    if secondary and secondary != primary:
        sector_code = _concept_to_index_code(secondary)
        if sector_code:
            candidates.append(sector_code)

    # 通过 akshare 查所属行业板块
    try:
        concept_tags = data.fetch_concept_tags(code)
        for tag in concept_tags[:3]:
            sector_code = _concept_to_index_code(tag)
            if sector_code and sector_code not in candidates:
                candidates.append(sector_code)
    except Exception:
        pass

    return candidates[:2]


# 常见概念 → 同花顺板块指数映射（mootdx 代码）
_CONCEPT_INDEX_MAP: dict[str, str] = {}

def _concept_to_index_code(concept_name: str) -> str | None:
    """概念名 → 同花顺板块指数代码（惰性加载缓存）。"""
    if not _CONCEPT_INDEX_MAP:
        _load_concept_index_map()
    return _CONCEPT_INDEX_MAP.get(concept_name)


def _load_concept_index_map():
    """从同花顺行业+概念板块建立名称→指数代码映射。"""
    try:
        import akshare as ak
        for func, prefix in [
            (ak.stock_board_industry_name_ths, "industry"),
            (ak.stock_board_concept_name_ths, "concept"),
        ]:
            try:
                df = func()
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        name = str(row.iloc[0]) if len(row) > 0 else ""
                        code = str(row.iloc[1]) if len(row) > 1 else ""
                        if name and code:
                            _CONCEPT_INDEX_MAP[name] = code
            except Exception:
                pass
    except Exception:
        pass


def _safe_returns(prices: pd.Series) -> pd.Series:
    """价格序列 → 日收益率序列（处理缺失值）。"""
    return prices.pct_change().fillna(0).clip(-0.2, 0.2)


def _run_regression(stock_ret: np.ndarray, factors: np.ndarray) -> dict:
    """OLS 回归: stock_ret = α + factors @ β + ε。

    factors: (n_days, n_factors) 矩阵。
    返回 {alpha, betas, r_squared, residuals}。
    """
    n = len(stock_ret)
    X = np.column_stack([np.ones(n), factors])  # 加截距项
    try:
        coeffs, residuals, rank, singular = np.linalg.lstsq(X, stock_ret, rcond=None)
        alpha = coeffs[0]
        betas = coeffs[1:] if len(coeffs) > 1 else np.array([])
        pred = X @ coeffs
        ss_res = np.sum((stock_ret - pred) ** 2)
        ss_tot = np.sum((stock_ret - np.mean(stock_ret)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        eps = stock_ret - pred
        return {
            "alpha": round(float(alpha), 6),
            "betas": [round(float(b), 4) for b in betas],
            "r_squared": round(float(r_squared), 4),
            "residuals": eps,
        }
    except np.linalg.LinAlgError:
        return {"alpha": 0, "betas": [], "r_squared": 0, "residuals": np.zeros(n)}


def _rolling_attribution(stock_ret: np.ndarray, factors: np.ndarray,
                         window: int = 20) -> list[dict]:
    """滚动窗口归因，返回每窗口的 α 序列。"""
    n = len(stock_ret)
    if n < window:
        return []
    results = []
    for i in range(window, n + 1):
        w_stock = stock_ret[i - window:i]
        w_factors = factors[i - window:i]
        reg = _run_regression(w_stock, w_factors)
        results.append({
            "end_idx": i - 1,
            "alpha": reg["alpha"],
            "r_squared": reg["r_squared"],
        })
    return results


def attribute_return(code: str, target_date: str = None,
                     lookback: int = LOOKBACK_DEFAULT) -> dict:
    """对单只标的做归因分析。

    Args:
        code: 6位股票代码
        target_date: 分析日期 (YYYY-MM-DD)，默认最近交易日
        lookback: 回看天数

    Returns:
        {code, name, alpha, beta_sector, gamma_market, r_squared,
         alpha_mean, alpha_std, alpha_latest, sigma_score,
         flag: "normal"|"⚠️逻辑证伪风险"|"insufficient_data",
         sector_code, sector_name, rolling_alphas, detail}
    """
    try:
        import data
    except ImportError:
        return _fail(code, "data 模块不可用")

    # 取 K 线
    df = data.fetch_klines(code, days=lookback + 10)
    if df is None or df.empty or len(df) < LOOKBACK_MIN:
        return _fail(code, "K线数据不足")

    df = df.sort_index() if isinstance(df.index, pd.DatetimeIndex) else df
    if target_date:
        try:
            cutoff = pd.Timestamp(target_date)
            df = df[df.index <= cutoff]
        except Exception:
            pass

    if len(df) < LOOKBACK_MIN:
        return _fail(code, f"有效数据不足 ({len(df)}天 < {LOOKBACK_MIN})")

    stock_ret = _safe_returns(df["close"]).values

    # 获取市场因子
    market_df = _fetch_index_klines(MARKET_INDEX_CODE, days=lookback + 10)
    market_ret = None
    if market_df is not None and len(market_df) >= len(df):
        market_ret = _safe_returns(market_df["close"]).values[-len(stock_ret):]

    # 获取板块因子
    sector_codes = _get_sector_codes(code)
    sector_ret = None
    sector_name = ""
    sector_code_used = ""
    for sc in sector_codes:
        sdf = _fetch_index_klines(sc, days=lookback + 10)
        if sdf is not None and len(sdf) >= len(df):
            sector_ret = _safe_returns(sdf["close"]).values[-len(stock_ret):]
            sector_code_used = sc
            sector_name = _resolve_index_name(sc)
            break

    # 构建因子矩阵
    factor_list = []
    factor_labels = []
    if sector_ret is not None:
        factor_list.append(sector_ret)
        factor_labels.append("sector")
    if market_ret is not None:
        factor_list.append(market_ret)
        factor_labels.append("market")

    if not factor_list:
        return _fail(code, "无市场/板块数据")

    factors = np.column_stack(factor_list)

    # 全窗口回归
    reg = _run_regression(stock_ret, factors)
    alpha_full = reg["alpha"]
    r_squared = reg["r_squared"]
    residuals = reg["residuals"]
    betas = reg["betas"]

    beta_sector = betas[0] if "sector" in factor_labels else None
    beta_market = betas[-1] if "market" in factor_labels else None

    # 滚动窗口 α
    rolling = _rolling_attribution(stock_ret, factors, window=20)
    alphas = [r["alpha"] for r in rolling] if rolling else [alpha_full]

    alpha_mean = float(np.mean(alphas))
    alpha_std = float(np.std(alphas)) if len(alphas) > 1 else 0.001
    alpha_latest = alphas[-1] if alphas else alpha_full

    # 信号判断
    sigma_score = (alpha_latest - alpha_mean) / alpha_std if alpha_std > 0 else 0
    sigma_score = round(sigma_score, 2)

    if alpha_std < 1e-6:
        flag = "insufficient_data"
    elif sigma_score < -SIGMA_THRESHOLD:
        flag = "⚠️逻辑证伪风险"
    elif alpha_latest < alpha_mean:
        flag = "偏弱"
    else:
        flag = "正常"

    # 最近5日 α 趋势
    recent_5 = alphas[-5:] if len(alphas) >= 5 else alphas
    alpha_trend = "↑" if len(recent_5) >= 2 and recent_5[-1] > recent_5[0] else (
        "↓" if len(recent_5) >= 2 and recent_5[-1] < recent_5[0] else "→")

    # 分解最近一日的收益来源
    latest_ret = float(stock_ret[-1])
    latest_epsilon = float(residuals[-1]) if len(residuals) > 0 else 0
    latest_sector_contrib = float(beta_sector * sector_ret[-1]) if sector_ret is not None and beta_sector else 0
    latest_market_contrib = float(beta_market * market_ret[-1]) if market_ret is not None and beta_market else 0

    # 名称
    name = ""
    try:
        quotes = data.fetch_stock_quotes([code])
        name = quotes.get(code, {}).get("name", "")
    except Exception:
        pass

    return {
        "code": code,
        "name": name,
        "alpha": round(alpha_full, 6),
        "beta_sector": beta_sector,
        "gamma_market": beta_market,
        "r_squared": r_squared,
        "alpha_mean": round(alpha_mean, 6),
        "alpha_std": round(alpha_std, 6),
        "alpha_latest": round(alpha_latest, 6),
        "sigma_score": sigma_score,
        "flag": flag,
        "alpha_trend": alpha_trend,
        "latest_return": round(latest_ret, 4),
        "sector_contrib": round(latest_sector_contrib, 4),
        "market_contrib": round(latest_market_contrib, 4),
        "epsilon": round(latest_epsilon, 4),
        "sector_code": sector_code_used,
        "sector_name": sector_name,
        "factor_labels": factor_labels,
        "rolling_alphas": [round(a, 6) for a in alphas[-10:]],  # 最近10窗口
        "n_days": len(stock_ret),
        "n_windows": len(rolling),
        "error": None,
    }


def attribute_batch(codes: list[str], target_date: str = None,
                    lookback: int = LOOKBACK_DEFAULT) -> list[dict]:
    """批量归因分析，返回 flagged 标的（α<-2σ）+ 全量结果。"""
    results = []
    for code in codes:
        try:
            r = attribute_return(code, target_date, lookback)
            results.append(r)
        except Exception as e:
            results.append(_fail(code, str(e)))
    return results


def format_report(results: list[dict], top_n: int = 15) -> str:
    """将归因结果格式化为 Markdown 报告。"""
    flagged = [r for r in results if r.get("flag") == "⚠️逻辑证伪风险"]
    normal = [r for r in results if r.get("flag") not in ("⚠️逻辑证伪风险", "insufficient_data")]

    lines = ["## 🔬 走势归因分析 — α/β/γ 分解", "",
             "> 个股收益 = α(特质) + β×板块 + γ×大盘 + ε", "",
             f"共分析 {len(results)} 只标的。", ""]

    if flagged:
        lines.append(f"### ⚠️ 逻辑证伪风险 ({len(flagged)}只)")
        lines.append("")
        lines.append("| 标的 | α_latest | σ | 板块贡献 | 大盘贡献 | ε残差 | α趋势 |")
        lines.append("|------|:--------:|:--:|:------:|:------:|:----:|:----:|")
        for r in sorted(flagged, key=lambda x: x["sigma_score"]):
            lines.append(
                f"| **{r['name']}({r['code']})** | {r['alpha_latest']:.4f} | "
                f"**{r['sigma_score']}σ** | {r['sector_contrib']:+.2%} | "
                f"{r['market_contrib']:+.2%} | {r['epsilon']:+.2%} | {r['alpha_trend']} |"
            )
        lines.append("")

    if normal:
        lines.append(f"### 正常标的 ({len(normal)}只)")
        lines.append("")
        lines.append("| 标的 | α | β(板块) | γ(大盘) | R² | σ | α趋势 | 判断 |")
        lines.append("|------|:--:|:------:|:------:|:--:|:--:|:---:|:----:|")
        for r in sorted(normal, key=lambda x: -(x.get("r_squared", 0)))[:top_n]:
            lines.append(
                f"| {r['name']}({r['code']}) | {r['alpha']:.4f} | "
                f"{r['beta_sector'] or '—'} | {r['gamma_market'] or '—'} | "
                f"{r['r_squared']:.2f} | {r['sigma_score']}σ | {r['alpha_trend']} | {r['flag']} |"
            )
        lines.append("")

    return "\n".join(lines)


def _resolve_index_name(code: str) -> str:
    """通过代码反查板块名称。"""
    for name, c in _CONCEPT_INDEX_MAP.items():
        if c == code:
            return name
    return code


def _fail(code: str, reason: str) -> dict:
    return {
        "code": code, "name": "", "alpha": 0, "beta_sector": None,
        "gamma_market": None, "r_squared": 0,
        "alpha_mean": 0, "alpha_std": 0, "alpha_latest": 0,
        "sigma_score": 0, "flag": "insufficient_data",
        "alpha_trend": "—", "latest_return": 0,
        "sector_contrib": 0, "market_contrib": 0, "epsilon": 0,
        "sector_code": "", "sector_name": "", "factor_labels": [],
        "rolling_alphas": [], "n_days": 0, "n_windows": 0,
        "error": reason,
    }
