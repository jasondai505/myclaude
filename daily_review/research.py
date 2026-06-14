"""基本面研报自动采集 — 采集 + 存储 + 报告

数据源:
  reportapi.eastmoney.com  — 全市场个股研报（一次HTTP GET，无需逐只调）
  stock_profit_forecast_ths — 一致预期EPS统计
  stock_comment_em          — 全市场综合得分/机构参与度
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd
import requests
from tqdm import tqdm

from config import UA, RESEARCH_CONFIG, WATCHLIST, REPORT_DIR
import store


# ============================================================
# 全市场研报（东方财富API，一次调用全市场）
# ============================================================

EM_REPORT_URL = "https://reportapi.eastmoney.com/report/list"


def fetch_all_research_reports(begin: str, end: str) -> list[dict]:
    """拉取全市场研报（日期范围），支持翻页。

    返回标准格式列表，可直接喂 store.save_research_reports。
    """
    all_reports = []
    page = 1
    page_size = 200

    while True:
        params = {
            "industryCode": "*", "pageSize": page_size,
            "industry": "*", "rating": "*", "ratingChange": "*",
            "beginTime": begin, "endTime": end,
            "pageNo": page, "fields": "", "qType": 0,
            "orgCode": "", "code": "", "rcode": "",
        }
        try:
            resp = requests.get(EM_REPORT_URL, params=params, timeout=20, headers={"User-Agent": UA})
            resp.raise_for_status()
            data = json.loads(resp.text)
        except Exception as e:
            print(f"  [WARN] 研报API第{page}页失败: {e}")
            break

        items = data.get("data", [])
        if not items:
            break

        for item in items:
            stock_code = str(item.get("stockCode", "")).zfill(6)
            if not stock_code or stock_code == "000000":
                continue

            eps_y1 = _safe_float(item.get("predictThisYearEps"))
            eps_y2 = _safe_float(item.get("predictNextYearEps"))
            eps_y3 = _safe_float(item.get("predictNextTwoYearEps"))
            pe_y1 = _safe_float(item.get("predictThisYearPe"))

            # 目标价：优先用API自带，否则 EPS × PE 估算
            target_price = _safe_float(item.get("indvAimPriceT"))
            if not target_price and eps_y1 and pe_y1:
                target_price = round(eps_y1 * pe_y1, 2)

            # 评级：优先东财评级，其次证券评级
            rating = item.get("emRatingName") or item.get("sRatingName") or ""

            # PDF
            pdf_url = ""
            encode_url = item.get("encodeUrl", "")
            info_code = item.get("infoCode", "")
            if encode_url:
                pdf_url = f"https://pdf.dfcfw.com/pdf/h3_{encode_url}_1.pdf"
            elif info_code:
                pdf_url = f"https://data.eastmoney.com/report/stock/{info_code}.html"

            all_reports.append({
                "code": stock_code,
                "name": str(item.get("stockName", "")),
                "title": str(item.get("title", "")),
                "rating": rating,
                "institution": str(item.get("orgSName", "") or item.get("orgName", "")),
                "report_date": str(item.get("publishDate", ""))[:10],
                "eps_y1": eps_y1,
                "eps_y2": eps_y2,
                "eps_y3": eps_y3,
                "pe_y1": pe_y1,
                "target_price": target_price,
                "industry": str(item.get("indvInduName", "") or item.get("industryName", "")),
                "pdf_url": pdf_url,
                "info_code": str(item.get("infoCode", "")),
            })

        total = data.get("hits", 0)
        if page * page_size >= total:
            break
        page += 1
        time.sleep(0.3)

    return all_reports


def _safe_float(val) -> float | None:
    if val is None or val == "" or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ============================================================
# 一致预期EPS
# ============================================================

def fetch_consensus_eps(code: str) -> dict | None:
    """一致预期EPS统计"""
    try:
        import akshare as ak
        df = ak.stock_profit_forecast_ths(symbol=code)
        if df is None or df.empty:
            return None
        result = {"code": code, "forecasts": []}
        for _, row in df.iterrows():
            result["forecasts"].append({
                "year": str(row.get("年度", "")),
                "eps_avg": row.get("均值"),
                "eps_max": row.get("最大值"),
                "eps_min": row.get("最小值"),
                "inst_count": row.get("预测机构数"),
            })
        if result["forecasts"]:
            result["inst_count"] = result["forecasts"][0].get("inst_count", 0)
            result["eps_avg_y1"] = result["forecasts"][0].get("eps_avg")
            if len(result["forecasts"]) > 1:
                result["eps_avg_y2"] = result["forecasts"][1].get("eps_avg")
        return result
    except Exception:
        return None


def fetch_market_comment() -> dict[str, dict]:
    """全市场综合评分/机构参与度"""
    try:
        import akshare as ak
        df = ak.stock_comment_em()
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            if code:
                result[code] = {
                    "name": row.get("名称", ""),
                    "score": row.get("综合得分"),
                    "inst_count": row.get("机构参与度"),
                }
        return result
    except Exception:
        return {}


# ============================================================
# 行业研报（通过 akshare，量小保留）
# ============================================================

def fetch_industry_reports() -> list[dict]:
    """拉取行业研报列表"""
    try:
        import akshare as ak
        df = ak.stock_research_report_em(symbol="")
        return []
    except Exception:
        return []
