"""个股深度分析 — CLI
用法: python stock_deep/run.py 300476
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from anthropic import Anthropic

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
PROJECT = str(BASE.parent)
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
sys.path.insert(0, str(BASE.parent / "daily_review"))

PROMPT_DIR = BASE / "prompts"
REPORT_DIR = BASE / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
MODEL = "claude-sonnet-4-6-20250514"
BASE_URL = "https://api.deepseek.com/anthropic"
MAX_TOKENS = 16000
TIMEOUT = 180


def _parse_args():
    p = argparse.ArgumentParser(description="个股深度分析")
    p.add_argument("code", nargs="?", type=str, help="股票代码")
    p.add_argument("--code", "-c", dest="code_opt", type=str)
    p.add_argument("--peers", "-p", type=str, help="同行代码，逗号分隔")
    return p.parse_args()


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if key:
        return key
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            key = data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        except (json.JSONDecodeError, OSError):
            pass
    return key


def _find_peers(code: str, db) -> list[str]:
    """从BOM知识库中找同赛道同行（直接匹配+概念兜底）。"""
    import sqlite3
    try:
        # 1. 直接同赛道匹配
        rows = db._conn().execute(
            "SELECT DISTINCT l2.stock_code FROM bom_leaders l1 "
            "JOIN bom_leaders l2 ON l1.chain_id = l2.chain_id "
            "WHERE l1.stock_code = ? AND l2.stock_code != ? "
            "ORDER BY l2.moat_total DESC LIMIT 6",
            (code, code)
        ).fetchall()
        if rows:
            return [r["stock_code"] for r in rows]

        # 2. 概念标签兜底：查该股概念，匹配BOM segment
        import data
        tags = data.fetch_concept_tags(code)
        if tags:
            placeholders = ",".join("?" * len(tags))
            rows = db._conn().execute(
                f"SELECT DISTINCT l.stock_code FROM bom_leaders l "
                f"JOIN bom_chains c ON l.chain_id = c.id "
                f"WHERE l.stock_code != ? AND ("
                + " OR ".join("c.segment LIKE ?" for _ in tags) + ") "
                f"ORDER BY l.moat_total DESC LIMIT 6",
                [code] + [f"%{t}%" for t in tags[:5]]
            ).fetchall()
            return [r["stock_code"] for r in rows]
    except Exception:
        pass
    return []


def _fetch_peer_data(peer_codes: list[str]) -> str:
    """拉取同行公司的行情+财务对比数据。"""
    import data
    lines = ["## 同行业可比公司真实数据\n"]
    try:
        quotes = data.fetch_stock_quotes(peer_codes, batch_size=30)
        fin = data.fetch_financial_indicators_lixinger(peer_codes)
        lines.append("| 代码 | 名称 | PE(TTM) | PB | 市值(亿) | ROE% | 毛利率% | 营收YoY% | 利润YoY% |")
        lines.append("|------|------|---------|-----|----------|------|---------|----------|----------|")
        for c in peer_codes:
            q = quotes.get(c, {})
            name = q.get("name", "")
            pe = round(q.get("pe_ttm", 0) or 0, 1)
            pb = round(q.get("pb", 0) or 0, 1)
            mcap = round((q.get("mcap_yi", 0) or 0))
            r = fin.get(c, [{}])[0] if fin.get(c) else {}
            roe = r.get("roe", "N/A")
            gm = r.get("gross_margin", "N/A")
            ry = r.get("revenue_yoy", "N/A")
            py = r.get("profit_yoy", "N/A")
            lines.append(f"| {c} | {name} | {pe} | {pb} | {mcap} | {roe} | {gm} | {ry} | {py} |")
    except Exception as e:
        lines.append(f"（同行数据获取失败: {e}）")
    return "\n".join(lines)


def _fetch_all(code: str) -> dict[str, str]:
    """预取个股全部语料。"""
    result: dict[str, str] = {}
    try:
        import data, store
    except ImportError:
        for k in ["stock_info", "quote", "financials", "eps", "news",
                   "announcements", "ir", "research", "holder", "concepts", "peers"]:
            result[k] = "（不可用）"
        return result

    store.init_feeds_tables()

    # 行情
    try:
        quotes = data.fetch_stock_quotes([code], batch_size=30)
        q = quotes.get(code, {})
        result["stock_info"] = json.dumps(
            {"code": code, "name": q.get("name", ""), "industry": ""}, ensure_ascii=False)
        result["quote"] = json.dumps({
            "price": q.get("price"), "change_pct": q.get("change_pct"),
            "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
            "pb": round(q.get("pb", 0) or 0, 1),
            "mcap_yi": round(q.get("mcap_yi", 0) or 0),
            "amount_wan": q.get("amount_wan"), "vol_ratio": q.get("vol_ratio"),
        }, ensure_ascii=False)
    except Exception as e:
        result["stock_info"] = f'{{"code": "{code}"}}'
        result["quote"] = f"（获取失败: {e}）"

    # 财务指标（6期年报）
    try:
        fin = data.fetch_financial_indicators_lixinger([code])
        records = fin.get(code, [])
        lines = []
        for r in records[:6]:
            lines.append(
                f"{r.get('report_date','')}: ROE={r.get('roe','N/A')}% "
                f"毛利率={r.get('gross_margin','N/A')}% 净利率={r.get('net_margin','N/A')}% "
                f"营收YoY={r.get('revenue_yoy','N/A')}% 利润YoY={r.get('profit_yoy','N/A')}% "
                f"负债率={r.get('debt_ratio','N/A')}% 经营现金流/净利={r.get('opcash_to_profit','N/A')}")
        result["financials"] = "\n".join(lines) if lines else "（无数据）"
    except Exception as e:
        result["financials"] = f"（获取失败: {e}）"

    # EPS 一致预期
    try:
        eps = data.fetch_eps_forecast(code)
        if eps:
            lines = [f"{e.get('year','')}: EPS={e.get('eps','N/A')} "
                     f"高={e.get('max_eps','')} 低={e.get('min_eps','')} "
                     f"机构={e.get('inst_count','')}" for e in eps[:3]]
            result["eps"] = "\n".join(lines)
        else:
            result["eps"] = "（无数据）"
    except Exception as e:
        result["eps"] = f"（获取失败: {e}）"

    # 新闻（3年，从DB）
    try:
        since = (date.today() - timedelta(days=1095)).isoformat()
        news = store.query_stock_news(code, since) if hasattr(store, "query_stock_news") else []
        if news:
            lines = [f"{r.get('publish_time','')[:10]} {r.get('title','')}" for r in news[:20]]
            result["news"] = f"近3年{len(news)}条:\n" + "\n".join(lines)
        else:
            result["news"] = "（无新闻）"
    except Exception:
        result["news"] = "（获取失败）"

    # 公告
    try:
        ann = store.query_announcements(code) if hasattr(store, "query_announcements") else []
        if ann:
            types: dict[str, int] = {}
            for a in ann:
                t = a.get("type", "其他")
                types[t] = types.get(t, 0) + 1
            ts = " | ".join(f"{k}:{v}" for k, v in sorted(types.items(), key=lambda x: -x[1])[:5])
            titles = [f"{a.get('date','')} {a.get('title','')}" for a in ann[:10]]
            result["announcements"] = f"共{len(ann)}条。{ts}\n" + "\n".join(titles)
        else:
            result["announcements"] = "（无公告）"
    except Exception:
        result["announcements"] = "（获取失败）"

    # 互动易
    try:
        irm = data.fetch_irm_szse(code) or data.fetch_irm_sse(code) or []
        if irm:
            lines = [f"Q: {i.get('question','')[:150]}\nA: {i.get('answer','')[:150]}" for i in irm[:5]]
            result["ir"] = "\n\n".join(lines)
        else:
            result["ir"] = "（无互动数据）"
    except Exception as e:
        result["ir"] = f"（获取失败: {e}）"

    # 研报
    try:
        research = data.fetch_stock_research(code, limit=10)
        if research:
            lines = [f"{r.get('date','')} {r.get('org','')}: {r.get('rating','')}"
                     for r in research[:5]]
            result["research"] = "\n".join(lines) if lines else "（无研报）"
        else:
            result["research"] = "（无研报覆盖）"
    except Exception as e:
        result["research"] = f"（获取失败: {e}）"

    # 股东
    try:
        holder = data.fetch_shareholder_count(code)
        if holder:
            lines = [f"{h.get('date','')}: {h.get('count','')}户 环比{h.get('change_pct','N/A')}%" for h in holder[:5]]
            result["holder"] = "\n".join(lines)
        else:
            result["holder"] = "（无数据）"
    except Exception as e:
        result["holder"] = f"（获取失败: {e}）"

    # 概念
    try:
        tags = data.fetch_concept_tags(code)
        result["concepts"] = ", ".join(tags) if tags else "无"
    except Exception:
        result["concepts"] = "（获取失败）"

    # 同行（BOM DB + 实时数据）
    try:
        from bom_analyzer import chain_db
        chain_db.init_db()
        peers = _find_peers(code, chain_db)
        if peers:
            result["peers"] = _fetch_peer_data(peers)
        else:
            result["peers"] = "（未在BOM知识库中找到同行，请LLM根据行业知识自行对标）"
    except Exception:
        result["peers"] = "（获取失败）"

    return result


def _call_llm(prompt: str) -> str:
    api_key = _load_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN 未设置")
    client = Anthropic(api_key=api_key, base_url=BASE_URL)
    resp = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "disabled"}, timeout=TIMEOUT,
    )
    parts = []
    for block in resp.content:
        if hasattr(block, "text") and block.text:
            parts.append(block.text)
    return "\n".join(parts)


def _extract_json(text: str) -> dict | None:
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    m_brace = re.search(r"\{.*\}", text, re.DOTALL)
    if m_brace:
        text = m_brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(text.replace("\n", " ").replace("\r", " "))
        except json.JSONDecodeError:
            return None


def _render_report(data: dict) -> str:
    m = data.get("moat", {})
    dims = [("tech", "技术专利", "tech_reason"),
            ("cost", "成本优势", "cost_reason"),
            ("scale", "规模效应", "scale_reason"),
            ("brand", "品牌溢价", "brand_reason"),
            ("switch_cost", "转换成本", "switch_reason"),
            ("network", "网络效应", "network_reason")]
    total = sum(m.get(k, 0) for k, _, _ in dims)

    lines = [
        f"# {data.get('name','')}({data.get('code','')}) 深度分析",
        f"> 分析日期: {date.today().isoformat()}",
        "",
        "## 1. 公司概况", data.get("overview", ""), "",
        "## 2. 财务全景", data.get("financials", ""), "",
        "## 3. 成长性评估", data.get("growth", ""), "",
        "## 4. 护城河评估",
        f"**综合评级: {m.get('rating','')}** | 总分: {total}/60",
        "",
        "| 维度 | 评分 | 分析 |",
        "|------|:----:|------|",
    ]
    for k, label, reason in dims:
        score = m.get(k, 0)
        r = m.get(reason, "")
        lines.append(f"| {label} | {score} | {r} |")
    lines.extend(["", "## 5. 股权结构与筹码", data.get("holder", ""), "",
                   "## 6. 管理层与公司治理", data.get("management", ""), "",
                   "## 7. 历史叙事演变", data.get("narrative", ""), "",
                   "## 8. 行业与竞争格局", data.get("industry_comp", ""), "",
                   "## 9. 催化剂与风险矩阵", data.get("catalyst_risk", ""), "",
                   "## 10. 估值与投资建议", data.get("valuation", ""), ""])
    return "\n".join(lines)


def _save_report(data: dict, code: str) -> str:
    md = _render_report(data)
    today = date.today().isoformat()
    path = REPORT_DIR / f"deep_{code}_{today}.md"
    path.write_text(md, encoding="utf-8")
    return str(path)


def run(code: str, peers: str = ""):
    print(f"\n{'='*60}")
    print(f"  个股深度分析: {code}")
    print(f"{'='*60}\n")

    print("  [Fetch] 拉取语料数据...")
    ctx = _fetch_all(code)
    if peers:
        peer_codes = [c.strip() for c in peers.split(",") if c.strip()]
        if peer_codes:
            print(f"  同行: {', '.join(peer_codes)}")
            ctx["peers"] = _fetch_peer_data(peer_codes)
    name = ""
    try:
        info = json.loads(ctx["stock_info"])
        name = info.get("name", "")
    except Exception:
        pass
    print(f"  标的: {name}({code})")

    print("  [LLM] Sonnet 深度分析...")
    tpl = (PROMPT_DIR / "deep_analysis.txt").read_text(encoding="utf-8")
    prompt = tpl
    for key, val in ctx.items():
        prompt = prompt.replace(f"%%{key.upper()}%%", val)

    text = _call_llm(prompt)
    data = _extract_json(text)
    if data is None:
        print("  [ERROR] JSON 解析失败")
        return

    # 校验名称
    llm_name = data.get("name", "")
    if llm_name and name and llm_name != name:
        try:
            import data
            q = data.fetch_stock_quotes([code], batch_size=30)
            real = q.get(code, {}).get("name", "")
            if real and real != llm_name:
                print(f"  [FIX] {llm_name} → {real}")
                data["name"] = real
        except Exception:
            pass

    report_path = _save_report(data, code)
    print(f"\n  ✓ 报告: {report_path}")

    moat = data.get("moat", {})
    total = sum(moat.get(k, 0) for k in
                ["tech", "cost", "scale", "brand", "switch_cost", "network"])
    print(f"  护城河: {total}分 | {data.get('conclusion','')[:80]}...")
    print(f"{'='*60}\n")


def main():
    args = _parse_args()
    code = args.code or args.code_opt
    if not code:
        print("用法: python stock_deep/run.py 300476")
        print("      python stock_deep/run.py 300476 --peers 002916,002384,603228")
        return
    run(code, args.peers or "")


if __name__ == "__main__":
    main()
