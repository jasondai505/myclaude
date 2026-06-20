"""行业研报深度分析 — 日更三阶段管道。

Stage 1: 从 SQLite 筛选今日 TOP30 研报（机构权重+分层配额）
Stage 2: 并行下载 PDF → Haiku 逐篇提取结构化 JSON
Stage 3: Sonnet 跨行业综合研判 → Obsidian 日报

用法:
    python -m daily_review.collectors.industry_deep_read
"""
from __future__ import annotations

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import store
from config import REPORT_DIR

SOURCE_NAME = "industry_deep_read"

# 知名机构权重
TOP_INSTITUTIONS = {
    "中金公司", "中信证券", "华泰证券", "申万宏源", "广发证券",
    "海通证券", "国泰君安", "招商证券", "兴业证券", "中信建投",
    "国信证券", "安信证券", "长江证券", "天风证券", "方正证券",
}
INST_WEIGHT_TOP = 3
INST_WEIGHT_NORMAL = 1

# 分层配额（日更~300篇→筛选30篇）
QUOTA = {"industry": 20, "strategy": 5, "macro": 5}
MAX_PER_INDUSTRY = 3
TOTAL_TARGET = sum(QUOTA.values())

MODEL_SCAN = "claude-haiku-4-5-20251001"
MODEL_DEEP = "claude-sonnet-4-6-20250514"
TIMEOUT = 120
MAX_BODY_CHARS = 3000
PDF_WORKERS = 4

OUT_DIR = REPORT_DIR / "industry"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    """主入口：对日期范围内的行业研报逐 report_date 执行深度分析。"""
    store.init_feeds_tables()

    from .base import fmt_iso

    # 查询日期范围内有数据的 report_date
    since_str = since.isoformat()
    until_str = until.isoformat()
    with store._conn() as conn:
        dates_row = conn.execute(
            "SELECT DISTINCT report_date FROM industry_reports "
            "WHERE report_date >= ? AND report_date <= ? ORDER BY report_date",
            (since_str, until_str),
        ).fetchall()
    report_dates = [r[0] for r in dates_row]

    if not report_dates:
        # 兜底：查最近一天（行业研报日更采集时间与报告日期可能偏移）
        latest = _stage1_select_latest()
        if latest:
            report_dates = [latest]

    if not report_dates:
        return {"last_date": until_str, "status": "ok",
                "message": f"日期范围 ({since_str}~{until_str}) 无行业研报数据",
                "stage1_count": 0, "stage2_count": 0, "saved_count": 0}

    total_s1 = 0
    total_s2 = 0
    total_saved = 0
    msgs = []

    for rd in report_dates:
        reports = _stage1_select_by_date(rd)
        if not reports:
            msgs.append(f"({rd}) 无入选研报")
            continue

        extracted = _stage2_extract(reports)
        scored = [e for e in extracted if e and e.get("core_thesis")]

        if scored:
            md = _stage3_synthesize(scored, rd)
            out_path = OUT_DIR / f"industry_daily_{rd}.md"
            out_path.write_text(md, encoding="utf-8")
            print(f"  [industry_deep_read] 报告: {out_path} ({len(scored)}篇有效提取)")
        else:
            md = ""
            print(f"  [industry_deep_read] ({rd}) 无有效提取，跳过合成")

        total_s1 += len(reports)
        total_s2 += len(scored)
        total_saved += 1 if md else 0
        msgs.append(f"({rd}) S1={len(reports)}→S2={len(scored)}→{'合成' if md else '跳过'}")

    msg = f"{len(report_dates)}个报告日: S1={total_s1}→S2={total_s2}→存档{total_saved} | {'; '.join(msgs[-3:])}"
    return {"last_date": until_str, "status": "ok", "message": msg,
            "stage1_count": total_s1, "stage2_count": total_s2, "saved_count": total_saved}


def _stage1_select_by_date(report_date: str) -> list[dict]:
    """按指定 report_date 筛选研报。"""
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM industry_reports WHERE report_date = ? ORDER BY institution",
            (report_date,),
        ).fetchall()
    all_reports = [dict(r) for r in rows]
    return _apply_quotas(all_reports)


def _stage1_select_latest() -> str | None:
    """查询最近一个有数据的 report_date。"""
    with store._conn() as conn:
        latest = conn.execute(
            "SELECT MAX(report_date) FROM industry_reports"
        ).fetchone()[0]
    return latest


def _apply_quotas(all_reports: list[dict]) -> list[dict]:
    """按 subtype 分桶 + 机构加权 + 分层配额筛选。"""
    if not all_reports:
        return []
    buckets: dict[str, list[dict]] = {"industry": [], "strategy": [], "macro": []}
    for r in all_reports:
        st = r.get("report_subtype", "industry")
        if st in buckets:
            inst = r.get("institution", "")
            weight = INST_WEIGHT_TOP if any(t in inst for t in TOP_INSTITUTIONS) else INST_WEIGHT_NORMAL
            r["_weight"] = weight
            buckets[st].append(r)

    selected = []
    for st, quota in QUOTA.items():
        bucket = sorted(buckets[st], key=lambda r: -r["_weight"])
        seen_industries: dict[str, int] = {}
        for r in bucket:
            if len([x for x in selected if x.get("report_subtype") == st]) >= quota:
                break
            ind = r.get("industry_name", "") or "_通用"
            if seen_industries.get(ind, 0) >= MAX_PER_INDUSTRY:
                continue
            seen_industries[ind] = seen_industries.get(ind, 0) + 1
            selected.append(r)

    subtype_order = {"industry": 0, "strategy": 1, "macro": 2}
    selected.sort(key=lambda r: subtype_order.get(r.get("report_subtype", "industry"), 9))
    print(f"  [Stage1] {len(all_reports)}篇→{len(selected)}篇入选")
    return selected


# ============================================================
# Stage 2: PDF 下载 + Haiku 提取
# ============================================================

_EXTRACT_PROMPT = """你是A股行业研究员。从以下研报正文提取结构化信息，返回JSON（只返回JSON）：

## 研报信息
标题: {title}
机构: {institution}
行业: {industry}

## 正文（截取前3000字）
{body}

## 要求
提取以下字段：
- core_thesis: 核心观点（<=100字）
- key_data: 关键数据点列表（如"2026年AI芯片市场增速45%"，最多5条）
- direction: 对行业的态度 — "看多"/"看空"/"中性"
- rating_change: 评级变化 — "上调"/"下调"/"首次"/"维持"/""
- catalysts: 行业催化剂列表（如"政策落地""涨价周期""技术突破"，最多3条）
- mentioned_stocks: 提及的A股标的列表，每项含code(6位数字)/name/role("推荐"/"关注"/"风险")

返回格式：
{{"core_thesis":"...","key_data":["..."],"direction":"看多","rating_change":"维持","catalysts":["..."],"mentioned_stocks":[{{"code":"688981","name":"中芯国际","role":"推荐"}}]}}"""


def _load_llm_client():
    try:
        from daily_review.roles import get_client, get_model
        return get_client, get_model
    except ImportError:
        return None, None


def _haiku_extract(report: dict) -> dict | None:
    """单篇 Haiku 提取，结果附带原始元数据供后续合成使用。"""
    from daily_review.pdf_utils import download_report_pdf

    info_code = report.get("info_code", "")
    pdf_url = report.get("pdf_url", "")

    # 先用缓存 body_text
    body = report.get("body_text", "")
    if not body and pdf_url:
        try:
            body = download_report_pdf(pdf_url, info_code) or ""
            if body and info_code:
                store.save_report_body_text("industry_reports", "info_code", info_code, body)
        except Exception as e:
            print(f"    PDF下载失败 {report.get('title','')[:30]}: {e}")

    if not body:
        return None

    body = body[:MAX_BODY_CHARS]
    prompt = _EXTRACT_PROMPT.format(
        title=report.get("title", ""),
        institution=report.get("institution", ""),
        industry=report.get("industry_name", ""),
        body=body,
    )

    get_client_fn, get_model_fn = _load_llm_client()
    if not get_client_fn:
        return None

    try:
        client = get_client_fn("scan", timeout=60)
        model = get_model_fn("scan")
        resp = client.messages.create(
            model=model, max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"}, timeout=60,
        )
        text = "".join(
            block.text for block in resp.content if hasattr(block, "text") and block.text
        )
        result = _parse_json(text)
        if result:
            result["_title"] = report.get("title", "")
            result["_institution"] = report.get("institution", "")
            result["_industry"] = report.get("industry_name", "")
        return result
    except Exception as e:
        print(f"    Haiku提取失败 {report.get('title','')[:30]}: {e}")
        return None


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _stage2_extract(reports: list[dict]) -> list[dict | None]:
    print(f"  [Stage2] 并行提取 {len(reports)} 篇（{PDF_WORKERS}线程）...")
    results = []
    done = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=PDF_WORKERS) as ex:
        futures = {ex.submit(_haiku_extract, r): i for i, r in enumerate(reports)}
        for f in as_completed(futures):
            try:
                r = f.result()
                results.append(r)
            except Exception:
                results.append(None)
            with lock:
                done += 1
                if done % 10 == 0:
                    print(f"    进度 {done}/{len(reports)}")
    return results


# ============================================================
# Stage 3: Sonnet 综合研判
# ============================================================

_SYNTHESIS_PROMPT = """你是A股策略分析师。以下是今日{count}篇行业研报的AI提取摘要，请综合研判后输出Markdown报告。

## 提取摘要
{summaries}

## 要求
输出完整的Markdown报告（不要JSON，从 # 标题开始）：

# 行业研报日度分析 {date}

> {count}篇研报 | {n_inst}家机构 | {n_ind}个行业

## 行业热度分布
按机构覆盖密度排序，列出前8个行业：
| 热度 | 行业 | 报告数 | 机构数 | 共识方向 | 核心主题 |
|:----:|------|:-----:|:-----:|:------:|---------|
| ⭐⭐⭐ | AI算力 | 8 | 5 | 看多 | 光互联/液冷/先进封装需求爆发 |

（热度：≥5篇⭐⭐⭐ / 3-4篇⭐⭐ / 1-2篇⭐）

## 今日共识方向
列出今日机构共识最强的3-5个方向，每个含：
- 方向名称
- 共识强度（高/中）
- 论据摘要（3个以内关键数据点）
- 代表标的：名称(代码)

## 今日评级变化
| 行业 | 上调 | 下调 | 首次 | 维持 | 变化方向 |
|------|:--:|:--:|:--:|:--:|---------|
| ... | 2 | 0 | 1 | 5 | 边际升温 ↑ |

## 高频标的（今日跨报告≥2次）
| 代码 | 名称 | 提及次数 | 推荐/关注/风险 | 所属行业 |
|------|------|:------:|:------------:|---------|

## 核心分歧
机构观点明显分歧的方向（最多3个）：
- 方向名称 | 看多（机构+论据）| 看空（机构+论据）| 判断

## 今日边际信号
- 🆕 新增主题（今日首次密集覆盖）：
- 🔥 升温主题（覆盖密度相比前日上升）：
- 🔻 降温主题：

---
*自动生成于 {gen_time} | 基于 EastMoney 行业/策略/宏观研报*"""


def _stage3_synthesize(extracted: list[dict], today: str) -> str:
    print(f"  [Stage3] Sonnet 综合研判 {len(extracted)} 篇提取...")

    # 构建摘要文本
    summaries = []
    inst_set = set()
    ind_set = set()
    for i, e in enumerate(extracted):
        inst_set.add(e.get("_institution", ""))
        ind_set.add(e.get("_industry", ""))
        stocks_str = ", ".join(
            f"{s.get('name','')}({s.get('code','')})"
            for s in e.get("mentioned_stocks", [])[:3]
        ) or "—"
        summaries.append(
            f"### {i+1}. {e.get('_title','')[:60]}\n"
            f"- 机构: {e.get('_institution','')} | 行业: {e.get('_industry','')}\n"
            f"- 观点: {e.get('direction','')} | 评级: {e.get('rating_change','')}\n"
            f"- 核心: {e.get('core_thesis','')}\n"
            f"- 数据: {'; '.join(e.get('key_data', [])[:3])}\n"
            f"- 催化: {'; '.join(e.get('catalysts', [])[:3])}\n"
            f"- 标的: {stocks_str}\n"
        )

    prompt = _SYNTHESIS_PROMPT.format(
        count=len(extracted), n_inst=len(inst_set), n_ind=len(ind_set),
        date=today,
        summaries="\n".join(summaries),
        gen_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    get_client_fn, get_model_fn = _load_llm_client()
    if not get_client_fn:
        return "_（LLM 客户端不可用，跳过综合研判）_"

    try:
        client = get_client_fn("deep", timeout=180)
        model = get_model_fn("deep")
        resp = client.messages.create(
            model=model, max_tokens=6000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"}, timeout=180,
        )
        return "".join(
            block.text for block in resp.content if hasattr(block, "text") and block.text
        )
    except Exception as e:
        return f"_（Sonnet 综合研判失败: {e}）_"


# ============================================================
# 独立运行
# ============================================================

if __name__ == "__main__":
    today = date.today()
    result = run(today, today, lambda d: set())
    print(json.dumps(result, ensure_ascii=False, indent=2))
