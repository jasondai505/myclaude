"""研报深度跟踪采集器。

在 research_reports collector 之后运行。
检测研报信号变化 → 对有信号的个股触发LLM分析 → 更新Obsidian档案。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Callable

import store
from deep_read.research_tracker import detect_signals
from deep_read.obsidian_research import upsert_stock_dossier

SOURCE_NAME = "research_deep_read"

_RESEARCH_LLM_PROMPT = """你是A股投研分析师。以下是某只股票的最新研报信号和评级历史。

## 股票信息
{name}（{code}）
领域: {domain}

## 最新信号
{signals_summary}

## 研报正文摘要
{report_text}

## 评级历史（最近5家）
{rating_summary}

## 要求
基于研报正文和评级历史，写一段200-300字的投资逻辑分析，覆盖：
1. 机构关注度的变化趋势（是否在升温/降温）
2. 盈利预测的方向（上调/下调）及可能的原因（引用研报中的具体论据）
3. 当前的预期差在哪（市场可能低估/高估了什么）

返回JSON（只返回JSON，不要其他内容）：
{{
  "investment_thesis": "投资逻辑（200-300字）",
  "total_score": 40-100,
  "time_horizon": "week/month/quarter"
}}"""


def _parse_json(text: str) -> dict:
    import re
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    today_str = since.isoformat()

    # 1. 检测信号
    results = detect_signals(today_str)
    if not results:
        return {
            "last_date": today_str, "signal_count": 0, "llm_count": 0,
            "dossier_count": 0, "status": "ok",
            "message": "当日无研报信号",
        }

    # 2. 对有信号的个股触发 LLM
    llm_count = 0
    dossier_count = 0

    for r in results:
        if not r.get("trigger_llm"):
            # 纯负面信号，仍存档但不跑LLM
            try:
                upsert_stock_dossier(r)
                dossier_count += 1
            except Exception as e:
                print(f"  [WARN] 存档失败 {r['code']}: {e}")
            continue

        # LLM 分析
        try:
            from roles import get_client, get_model
            client = get_client("deep", timeout=90)
            model = get_model("deep")

            signals_text = "\n".join(
                f"- [{s['type']}] {s['desc']}" for s in r.get("signals", [])
            )
            rep_text = "\n".join(
                f"- {rep.get('report_date','')} {rep.get('institution','')}: "
                f"{rep.get('rating','')} TP={rep.get('target_price','')} "
                f"EPS={rep.get('eps_y1','')}"
                for rep in r.get("reports", [])[:5]
            )

            # 下载研报正文（PDF → HTML 降级）
            report_text = "（研报正文暂不可用）"
            reports = r.get("reports", [])
            if reports:
                rep = reports[0]
                pdf_url = rep.get("pdf_url", "")
                info_code = rep.get("info_code", "")
                if pdf_url:
                    try:
                        from pdf_utils import download_report_pdf
                        body = download_report_pdf(pdf_url, info_code) or ""
                        if body:
                            report_text = body
                            store.save_report_body_text(
                                "research_reports", "pdf_url", pdf_url, body)
                        else:
                            print(f"  [WARN] 研报正文不可用 {r.get('code','')}")
                    except Exception as e:
                        print(f"  [WARN] 研报下载失败 {r.get('code','')}: {e}")

            prompt = _RESEARCH_LLM_PROMPT.format(
                name=r.get("name", ""),
                code=r.get("code", ""),
                domain=r.get("hunting_domain", ""),
                signals_summary=signals_text,
                report_text=report_text,
                rating_summary=rep_text,
            )

            resp = client.messages.create(
                model=model, max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "disabled"},
            )
            text = "".join(block.text for block in resp.content if block.type == "text")
            llm_result = _parse_json(text)

            r["investment_thesis"] = llm_result.get("investment_thesis", "")
            r["total_score"] = llm_result.get("total_score", 50)
            r["time_horizon"] = llm_result.get("time_horizon", "month")
            llm_count += 1

        except Exception as e:
            print(f"  [WARN] LLM失败 {r['code']}: {e}")
            r["investment_thesis"] = ""
            r["total_score"] = 0

        # 3. 更新 Obsidian 档案
        try:
            upsert_stock_dossier(r)
            dossier_count += 1
        except Exception as e:
            print(f"  [WARN] 存档失败 {r['code']}: {e}")

    return {
        "last_date": today_str,
        "signal_count": len(results),
        "llm_count": llm_count,
        "dossier_count": dossier_count,
        "status": "ok",
        "message": f"{len(results)} 只股票有信号, {llm_count} 只触发LLM, {dossier_count} 份档案更新",
    }
