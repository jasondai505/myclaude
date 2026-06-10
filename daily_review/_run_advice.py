"""Render claude_prompt.txt → SDK 直调 Claude → 输出 advice 报告。
Called by morning_advice.bat Step 5.
"""
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from anthropic import Anthropic

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MODEL = "claude-sonnet-4-6-20250514"
HAIKU_MODEL = "claude-haiku-4-5-20251001"
FEEDS_DIR = BASE / "reports" / "feeds"
MAX_DIRECT_CHARS = 6000

FEED_FILES = [
    ("%%ZSXQ%%", "zsxq", True),
    ("%%NEWS%%", "news", True),
    ("%%ANNOUNCEMENTS%%", "announcements", True),
    ("%%INDUSTRY%%", "industry", False),
    ("%%FINANCIALS%%", "financials", False),
    ("%%EARNINGS%%", "earnings", False),
    ("%%SURVEYS%%", "surveys", False),
    ("%%LOCKUPS%%", "lockups", False),
    ("%%EPS%%", "eps", False),
    ("%%INTERACTIONS%%", "interactions", False),
]


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


def _fetch_market_data() -> tuple[str, str, str, str]:
    try:
        import data
        from config import OVERSEAS_MAP
    except ImportError as e:
        err = json.dumps({"error": f"模块导入失败: {e}"}, ensure_ascii=False)
        return err, err, err, err

    try:
        us = data.fetch_us_movers()
        us_str = json.dumps(us, ensure_ascii=False, indent=2)
    except Exception as e:
        us_str = json.dumps({"error": f"数据暂不可用（{e}）"}, ensure_ascii=False)

    try:
        global_data = data.fetch_global_markets()
        us_idx = global_data.get("indices", {})
        us_idx_out = {}
        for k, v in us_idx.items():
            us_idx_out[k] = {
                "price": v.get("price"),
                "change_pct": v.get("change_pct"),
                "change_pct_5d": v.get("change_pct_5d"),
            }
        us_idx_str = json.dumps(us_idx_out, ensure_ascii=False, indent=2)
    except Exception as e:
        us_idx_str = json.dumps({"error": f"数据暂不可用（{e}）"}, ensure_ascii=False)

    try:
        kr_jp = data.fetch_kr_jp_markets()
        kr_jp_str = json.dumps(kr_jp, ensure_ascii=False, indent=2)
        if not kr_jp or kr_jp_str == "{}":
            kr_jp_str = json.dumps(
                {"info": "日韩尚未开盘，实时数据暂不可用（API未返回），请基于美股映射和星球信号判断"},
                ensure_ascii=False, indent=2)
    except Exception as e:
        kr_jp_str = json.dumps({"error": f"数据暂不可用（{e}）"}, ensure_ascii=False)

    try:
        ov_str = json.dumps(OVERSEAS_MAP, ensure_ascii=False, indent=2)
    except Exception as e:
        ov_str = json.dumps({"error": f"数据暂不可用（{e}）"}, ensure_ascii=False)

    return us_str, us_idx_str, kr_jp_str, ov_str


def _summarize_feed(content: str, source_name: str) -> str:
    """用 Haiku 对大文件做结构化摘要。"""
    api_key = _load_api_key()
    if not api_key:
        return content[:MAX_DIRECT_CHARS] + "\n...(truncated, API key unavailable)"

    summary_prompt = f"""你是A股投研助手。请将以下原始信息提炼为结构化摘要（<=1500字）：

1. 关键事件（<=3条，每条含：事件简述/影响板块/核心标的及代码）
2. 高频提及标的（<=10只，含6位代码+提及次数+核心逻辑）
3. 风险信号（减持/解禁/业绩miss/监管/退市风险等）
4. 其他值得关注的增量信息

原始信息（{source_name}）：
{content[:12000]}"""

    try:
        client = Anthropic(api_key=api_key, base_url="https://api.deepseek.com/anthropic")
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": summary_prompt}],
            thinking={"type": "disabled"},
            timeout=60,
        )
        parts = []
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                parts.append(block.text)
        return "\n".join(parts)
    except Exception:
        return content[:MAX_DIRECT_CHARS] + "\n...(summarization failed, truncated)"


def _inject_feeds(today: str, yesterday: str) -> dict[str, str]:
    """读取 feed — 优先 SQLite 缓存，回退文件。大文件经 Haiku 摘要。"""
    try:
        import store
        store.init_feed_cache_table()
    except Exception:
        store = None

    def _read_feed(stem: str, date_str: str) -> str | None:
        if store:
            cached = store.get_feed_cache(stem, date_str)
            if cached:
                return cached
        path = FEEDS_DIR / f"{stem}_{date_str}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    result = {}

    for placeholder, stem in [("%%RECAP%%", "recap"), ("%%REVIEW_SUMMARY%%", "review_summary")]:
        content = _read_feed(stem, yesterday)
        if content is None:
            result[placeholder] = f"（{stem}_{yesterday}.md 暂未生成）"
        elif len(content) > MAX_DIRECT_CHARS:
            result[placeholder] = _summarize_feed(content, stem)
        else:
            result[placeholder] = content

    for placeholder, stem, _required in FEED_FILES:
        content = _read_feed(stem, today)
        if content is None:
            result[placeholder] = f"（{stem}_{today}.md 暂未生成）"
        elif len(content) > MAX_DIRECT_CHARS:
            result[placeholder] = _summarize_feed(content, stem)
        else:
            result[placeholder] = content

    return result


def _inject_wechat_analysis(today: str) -> str:
    path = BASE / "reports" / f"wechat_analysis_{today}.md"
    if not path.exists():
        return "（公众号分析报告暂未生成）"
    text = path.read_text(encoding="utf-8")
    marker = "## 逐篇拆解"
    idx = text.find(marker)
    if idx == -1:
        return text[:MAX_DIRECT_CHARS] + "\n...(truncated)"
    synthesis = text[:idx].strip()
    if len(synthesis) > MAX_DIRECT_CHARS * 2:
        synthesis = synthesis[:MAX_DIRECT_CHARS * 2] + "\n...(truncated)"
    return synthesis


def _extract_codes_from_feeds(feeds: dict[str, str]) -> set[str]:
    """从所有 feed 中提取6位股票代码，覆盖公众号/星球/资讯。"""
    codes = set()
    for key in ("%%RECAP%%", "%%REVIEW_SUMMARY%%", "%%ZSXQ%%",
                "%%WECHAT_ANALYSIS%%", "%%NEWS%%", "%%INDUSTRY%%",
                "%%SUPPLY_CHAIN_INTEL%%"):
        text = feeds.get(key, "")
        codes.update(re.findall(r"\b(\d{6})\b", text))
    return codes


def _inject_stock_context(codes: set[str]) -> str:
    """获取个股关键数据（市值/PE/FEV），构建防幻觉上下文。"""
    if not codes:
        return "{}"

    try:
        import data
    except ImportError:
        return json.dumps({"error": "data module unavailable"}, ensure_ascii=False)

    try:
        quotes = data.fetch_stock_quotes(list(codes), batch_size=30)
    except Exception:
        return json.dumps({"error": "行情获取失败"}, ensure_ascii=False)

    fev_map = {}
    try:
        from daily_review.serenity_kb import init_db, get_stock_scores
        init_db()
        for s in get_stock_scores():
            code = s.get("code", "")
            if code:
                fev_map[code] = {
                    "f": s.get("f_score", 0), "e": s.get("e_score", 0),
                    "v": s.get("v_score", 0), "fev": s.get("fev_total", 0),
                    "chain": s.get("chain_name", ""),
                }
    except Exception:
        pass

    ctx = {}
    for code, q in quotes.items():
        fv = fev_map.get(code, {})
        ctx[code] = {
            "name": q.get("name", ""),
            "mcap_yi": round(q.get("mcap_yi", 0) or 0),
            "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
            "chg_pct": round(q.get("change_pct", 0) or 0, 2),
            "amount_yi": round((q.get("amount_wan", 0) or 0) / 10000, 1),
            "fev": fv.get("fev", 0),
            "f_score": fv.get("f", 0), "e_score": fv.get("e", 0),
            "v_score": fv.get("v", 0),
            "serenity_chain": fv.get("chain", ""),
        }
    return json.dumps(ctx, ensure_ascii=False, indent=2)


def _inject_bom_context() -> str:
    """注入 BOM 产业链知识库摘要。"""
    try:
        import sys
        parent = str(BASE.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from bom_analyzer import chain_db
        chain_db.init_db()
        industries = chain_db.list_industries()
        if not industries:
            return "（BOM 知识库暂无数据）"
        lines = ["## BOM产业链知识库（最近分析）", ""]
        for ind in industries[:10]:
            data = chain_db.query_industry(ind)
            segs = data.get("segments", [])
            h3_segs = [s for s in segs if s.get("is_3h")]
            if not h3_segs:
                continue
            lines.append(f"### {ind}")
            for s in h3_segs:
                leaders = s.get("leaders", [])
                top = leaders[:3] if leaders else []
                stock_str = " / ".join(
                    f"{l['stock_name']}({l['stock_code']}) {l.get('moat_total',0)}分"
                    for l in top)
                lines.append(f"- {s['segment']}（{s['tier']}）: {stock_str}")
            lines.append("")
        return "\n".join(lines) if len(lines) > 3 else "（无三高赛道数据）"
    except Exception as e:
        return f"（BOM 数据获取失败: {e}）"


def _inject_serenity_context() -> str:
    """注入 Serenity 产业链卡脖子分析 — 卡脖子排行 + FEV 高分标的。"""
    try:
        import sys
        parent = str(BASE.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from daily_review.serenity_kb import (
            init_db, get_all_chain_summary, get_stock_scores,
        )
        init_db()
        chains = get_all_chain_summary()
        stocks = get_stock_scores()
        if not chains:
            return "（Serenity 暂无分析数据）"

        lines = ["## 产业链卡脖子排行（全球供应链反推 → A股映射）", ""]
        lines.append("| 产业链 | 卡脖子分 | 环节数 |")
        lines.append("|--------|:------:|:-----:|")
        for c in chains[:14]:
            lines.append(f"| {c['chain_name']} | {c['max_score']} | {c['segment_count']} |")
        lines.append("")

        if stocks:
            top = sorted(stocks, key=lambda s: s.get("fev_total", 0), reverse=True)[:15]
            lines.append("### FEV 高分标的（卡脖子产业链内）")
            lines.append("")
            lines.append("| 代码 | 名称 | 产业链 | F | E | V | FEV |")
            lines.append("|------|------|--------|---|---|---|-----|")
            for s in top:
                lines.append(
                    f"| {s['code']} | {s['name']} | {s['chain_name']} | "
                    f"{s['f_score']} | {s['e_score']} | {s['v_score']} | {s['fev_total']} |"
                )
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"（Serenity 数据获取失败: {e}）"


def _inject_supply_chain_intel(today: str) -> str:
    """注入 morning_intel interpret.py 的供应链映射分析结果。"""
    path = BASE.parent / "morning_intel" / "reports" / f"morning_{today}.md"
    if not path.exists():
        return "（晨间情报报告暂未生成）"
    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > MAX_DIRECT_CHARS * 2:
            content = content[:MAX_DIRECT_CHARS * 2] + "\n...(truncated)"
        return content
    except Exception as e:
        return f"（晨间情报读取失败: {e}）"


def _inject_intel_dimensions(today: str) -> str:
    """注入五维信号预处理结果。暂为占位，后续由 _preprocess_intel.py 产出。"""
    path = BASE / "reports" / "feeds" / f"intel_dimensions_{today}.md"
    if not path.exists():
        return ("（五维信号预处理模块待上线。请从 %%ZSXQ%% / %%NEWS%% / %%INDUSTRY%% / "
                "%%ANNOUNCEMENTS%% 中自行按五维框架提取：①边际变化与催化 ②供需缺口与价格弹性 "
                "③核心预期差 ④业绩兑现时间窗 ⑤风险排雷）")
    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > MAX_DIRECT_CHARS * 3:
            content = content[:MAX_DIRECT_CHARS * 3] + "\n...(truncated)"
        return content
    except Exception as e:
        return f"（五维信号读取失败: {e}）"


def _validate_index_claims(output: str, us_indices_json: str) -> str:
    """校验 LLM 输出中的指数涨跌幅声明，与注入的真实数据比对，不匹配时自动修正。"""
    try:
        real = json.loads(us_indices_json)
    except (json.JSONDecodeError, TypeError):
        return output

    if not real or "error" in real:
        return output

    # 构建 {指数名: 真实涨跌幅} 映射
    truth: dict[str, float] = {}
    for name, info in real.items():
        chg = info.get("change_pct")
        if chg is not None:
            truth[name] = round(float(chg), 2)

    if not truth:
        return output

    fixed = 0
    for idx_name, real_chg in truth.items():
        short = idx_name.replace("指数", "").replace("综合", "")
        # 匹配 "纳斯达克 +1.2%" / "标普500 -0.5%" / "道琼斯 +0.3%" 等模式
        for pattern in [
            rf"({re.escape(idx_name)}|{re.escape(short)})\s*[：:]*\s*[+\-]?\d+\.?\d*\s*%",
        ]:
            for m in re.finditer(pattern, output):
                claimed_text = m.group(0)
                claimed_val = re.search(r"([+\-]?\d+\.?\d*)\s*%", claimed_text)
                if not claimed_val:
                    continue
                claimed_num = round(float(claimed_val.group(1)), 2)
                if abs(claimed_num - real_chg) > 0.01:
                    sign = "+" if real_chg >= 0 else ""
                    correct_text = re.sub(
                        r"[+\-]?\d+\.?\d*\s*%",
                        f"{sign}{real_chg}%",
                        claimed_text,
                    )
                    output = output.replace(claimed_text, correct_text, 1)
                    print(f"  [FIX] {claimed_text.strip()} → {correct_text.strip()}")
                    fixed += 1

    if fixed:
        print(f"  共修正 {fixed} 处指数涨跌幅编造")
    return output


def _validate_code_names(output: str) -> str:
    paren_pairs = re.findall(r"([一-龥]+)\((\d{6})\)", output)
    table_pairs = []
    for m in re.finditer(
        r"\|\s*\*{0,2}([一-龥]{2,8})\*{0,2}\s*\|\s*(\d{6})\s*\|", output
    ):
        table_pairs.append((m.group(1), m.group(2)))

    all_pairs = paren_pairs + table_pairs
    if not all_pairs:
        return output

    codes = sorted(set(c for _, c in all_pairs))
    try:
        import data
        quotes = data.fetch_stock_quotes(codes, batch_size=30)
    except Exception as e:
        print(f"  [WARN] 代码校验查询失败: {e}")
        return output

    name_map = {c: q.get("name", "") for c, q in quotes.items()}
    fixed = 0

    for llm_name, code in all_pairs:
        real_name = name_map.get(code, "")
        if not real_name:
            print(f"  [WARN] 代码 {code} 查无数据，可能无效，LLM 标注为「{llm_name}」")
            continue
        if llm_name != real_name:
            for template in [
                f"{llm_name}({code})",
                f"| {llm_name} | {code}",
                f"| **{llm_name}** | {code}",
                f"|{llm_name} | {code}",
                f"| **{llm_name}**| {code}",
            ]:
                if template in output:
                    new_template = template.replace(llm_name, real_name)
                    output = output.replace(template, new_template)
                    print(f"  [FIX] {template.strip()} → {new_template.strip()}")
                    fixed += 1

    code_names: dict[str, set[str]] = {}
    for nm, cd in all_pairs:
        code_names.setdefault(cd, set()).add(nm)
    for cd, names in code_names.items():
        real_name = name_map.get(cd, "")
        if len(names) > 1:
            print(f"  [WARN] 代码 {cd} 出现多个名称: {names}，API 名称为「{real_name}」")
            for nm in names:
                if nm != real_name:
                    for tmpl in [
                        f"{nm}({cd})",
                        f"| {nm} | {cd}",
                        f"| **{nm}** | {cd}",
                    ]:
                        if tmpl in output:
                            new_tmpl = tmpl.replace(nm, real_name)
                            output = output.replace(tmpl, new_tmpl)
                            print(f"  [FIX] ambiguous: {tmpl.strip()} → {new_tmpl.strip()}")
                            fixed += 1

    if fixed:
        print(f"  共修正 {fixed} 处代码-名称不匹配")
    return output


DOW_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DOW_FULL = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
DOW_ALL = DOW_CN + DOW_FULL


def _validate_dow_claims(output: str, yesterday: str) -> str:
    """校验「黑色星期一」模板错误 — LLM 常将任何暴跌 pattern-match 为黑色星期一。

    仅针对「黑色星期一」/「黑色周一」，不碰其他星期（它们不太可能是模板错误）。
    """
    real_dow = None
    try:
        real_dow = DOW_CN[date.fromisoformat(yesterday).weekday()]
    except ValueError:
        pass

    year = date.today().year
    date_dow: dict[str, str] = {}
    for m in re.finditer(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", output):
        try:
            mm, dd = int(m.group(1)), int(m.group(2))
            d = date(year, mm, dd)
            date_dow[m.group(0)] = DOW_CN[d.weekday()]
        except ValueError:
            continue

    # 只匹配黑色星期一/黑色周一（最常见的模板惯性错误）
    pattern = r"([""「『']?)黑色(星期一|周一)([""」』']?)"
    fixed = 0
    for m in re.finditer(pattern, output):
        full = m.group(0)
        prefix = m.group(1)
        suffix = m.group(3)

        pos = m.start()
        ctx_before = output[max(0, pos - 80):pos]
        ctx_after = output[pos:pos + 80]
        correct = real_dow
        for d_str, d_dow in date_dow.items():
            if d_str in ctx_before or d_str in ctx_after:
                correct = d_dow
                break

        if not correct or correct in ("周一", "星期一"):
            continue

        # 保持原文格式：原文用「星期一」则用全称，原文用「周一」则简称
        if "星期一" in full:
            correct = DOW_FULL[DOW_CN.index(correct)] if correct in DOW_CN else correct
        elif "周一" in full:
            correct = DOW_CN[DOW_FULL.index(correct)] if correct in DOW_FULL else correct

        corrected = f"{prefix}黑色{correct}{suffix}"
        output = output.replace(full, corrected)
        print(f"  [FIX] {full} → {corrected}")
        fixed += 1

    if fixed:
        print(f"  共修正 {fixed} 处日期星期表述")
    return output


def main():
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    yesterday = sys.argv[2] if len(sys.argv) > 2 else (date.today() - timedelta(days=1)).isoformat()
    today_dow = DOW_CN[date.fromisoformat(today).weekday()]
    yesterday_dow = DOW_CN[date.fromisoformat(yesterday).weekday()]

    tpl = (BASE / "claude_prompt.txt").read_text(encoding="utf-8")
    us_movers, us_indices, kr_jp, ov_map = _fetch_market_data()
    feeds = _inject_feeds(today, yesterday)
    wechat_analysis = _inject_wechat_analysis(today)
    feeds["%%WECHAT_ANALYSIS%%"] = wechat_analysis
    codes = _extract_codes_from_feeds(feeds)
    stock_ctx = _inject_stock_context(codes)
    bom_ctx = _inject_bom_context()
    supply_chain = _inject_supply_chain_intel(today)
    serenity_ctx = _inject_serenity_context()
    intel_dims = _inject_intel_dimensions(today)

    prompt = (tpl
        .replace("%%TODAY%%", today)
        .replace("%%TODAY_DOW%%", today_dow)
        .replace("%%YESTERDAY%%", yesterday)
        .replace("%%YESTERDAY_DOW%%", yesterday_dow)
        .replace("%%US_MOVERS%%", us_movers)
        .replace("%%US_INDICES%%", us_indices)
        .replace("%%KR_JP_MARKETS%%", kr_jp)
        .replace("%%OVERSEAS_MAP%%", ov_map)
        .replace("%%STOCK_CONTEXT%%", stock_ctx)
        .replace("%%WECHAT_ANALYSIS%%", wechat_analysis)
        .replace("%%BOM_CONTEXT%%", bom_ctx)
        .replace("%%SUPPLY_CHAIN_INTEL%%", supply_chain)
        .replace("%%SERENITY_TOP%%", serenity_ctx)
        .replace("%%INTEL_DIMENSIONS%%", intel_dims)
    )
    for key, val in feeds.items():
        prompt = prompt.replace(key, val)

    advice_path = BASE / "reports" / f"advice_{today}.md"

    try:
        client = Anthropic(
            api_key=_load_api_key(),
            base_url="https://api.deepseek.com/anthropic",
        )
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
            timeout=600,
        )
        parts = []
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                parts.append(block.text)
        output = "\n".join(parts)
    except Exception as e:
        output = f"[ERROR] LLM 调用失败: {e}"

    output = _validate_index_claims(output, us_indices)
    output = _validate_code_names(output)
    output = _validate_dow_claims(output, yesterday)

    print(output)

    if output.strip() and len(output) > 500:
        advice_path.write_text(output, encoding="utf-8")
        print("[INFO] advice saved from stdout")
    elif output.strip():
        print("[WARN] advice output too short, not saving (likely error response)")

    print(f"  advice output: {len(output)} chars")


if __name__ == "__main__":
    main()
