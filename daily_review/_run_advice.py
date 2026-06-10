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

    # 从 serenity_kb 取 FEV（产业链关联标的）
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
                    "chain": s.get("chain_name", ""), "source": "serenity",
                }
    except Exception:
        pass

    # 从 feval 取 FEV（独立评分器，覆盖 serenity 未覆盖的标的）
    try:
        from daily_review.feval import get_scores as feval_get_scores
        for code, fs in feval_get_scores().items():
            fev_val = fs.get("fev_total", 0) or 0
            if code not in fev_map or fev_map[code].get("fev", 0) == 0:
                if fev_val > 0 or code not in fev_map:
                    fev_map[code] = {
                        "f": fs.get("f_score", 0), "e": fs.get("e_score", 0),
                        "v": fs.get("v_score", 0), "fev": fev_val,
                        "chain": fev_map.get(code, {}).get("chain", ""), "source": "feval",
                    }
    except Exception:
        pass

    # 从 feval 取 Δ（边际变化评分）
    delta_map = {}
    try:
        from daily_review.feval import get_delta_scores as feval_get_delta
        delta_map = feval_get_delta()
    except Exception:
        pass

    ctx = {}
    for code, q in quotes.items():
        fv = fev_map.get(code, {})
        d = delta_map.get(code, {})
        ctx[code] = {
            "name": q.get("name", ""),
            "mcap_yi": round(q.get("mcap_yi", 0) or 0),
            "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
            "chg_pct": round(q.get("change_pct", 0) or 0, 2),
            "amount_yi": round((q.get("amount_wan", 0) or 0) / 10000, 1),
            "fev": fv.get("fev", 0),
            "f_score": fv.get("f", 0), "e_score": fv.get("e", 0),
            "v_score": fv.get("v", 0),
            "delta": d.get("delta_score", 0) if d else 0,
            "delta_signal": d.get("signal", "") if d else "",
            "serenity_chain": fv.get("chain", ""),
            "fev_source": fv.get("source", ""),
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


def _inject_fev_table() -> str:
    """注入所有已评分标的的 FEV 查询表（serenity + feval 合并）。"""
    fev_rows: dict[str, dict] = {}
    try:
        from daily_review.serenity_kb import init_db, get_stock_scores
        init_db()
        for s in get_stock_scores():
            code = s.get("code", "")
            if code:
                fev_rows[code] = {
                    "f": s.get("f_score", 0), "e": s.get("e_score", 0),
                    "v": s.get("v_score", 0), "fev": s.get("fev_total", 0),
                    "name": s.get("name", ""), "source": "serenity",
                }
    except Exception:
        pass
    try:
        from daily_review.feval import get_scores as feval_get_scores
        for code, fs in feval_get_scores().items():
            if code not in fev_rows:
                fev_rows[code] = {
                    "f": fs.get("f_score", 0), "e": fs.get("e_score", 0),
                    "v": fs.get("v_score", 0), "fev": fs.get("fev_total", 0),
                    "name": fs.get("name", ""), "source": "feval",
                }
    except Exception:
        pass

    delta_map = {}
    try:
        from daily_review.feval import get_delta_scores as feval_get_delta
        delta_map = feval_get_delta()
    except Exception:
        pass

    if not fev_rows and not delta_map:
        return "（FEV 和 Δ 评分数据暂未生成）"

    lines = ["| 代码 | 名称 | F | E | V | FEV | Δ | FEVΔ | 来源 |",
             "|------|------|---|---|---|-----|----|------|------|"]
    for code, r in sorted(fev_rows.items(),
                          key=lambda x: -x[1]["fev"]):
        d = delta_map.get(code, {})
        ds = d.get("delta_score", 0) if d else 0
        sign = "+" if ds >= 0 else ""
        fev = r["fev"]
        lines.append(
            f"| {code} | {r['name']} | {r['f']} | {r['e']} | {r['v']} | "
            f"{fev} | {sign}{ds} | {fev+ds} | {r['source']} |"
        )
    return "\n".join(lines)


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


def _validate_advice_coverage(output: str) -> str:
    """校验 advice 输出的覆盖率：精选标的数量、ChokeMap环节覆盖、FEV有效性。"""
    if not output or len(output) < 500:
        return output

    # 1. 统计精选标的数量（匹配 ### 名称 (6位代码) 格式）
    stock_blocks = re.findall(r"###\s+.+?\s*\((\d{6})\)", output)
    count = len(stock_blocks)
    codes_found = re.findall(r"###\s+\S+\s*\((\d{6})\)", output)

    warnings = []
    if count < 6:
        warnings.append(f"精选标的仅 {count} 只（要求 ≥6）")
    if count > 12:
        warnings.append(f"精选标的 {count} 只（建议 ≤10）")

    # 2. 检查 FEV 是否有效（非待定/非编造）
    fev_issues = re.findall(
        r"###\s+(\S+)\s*\((\d{6})\).*?\n\|\s*FEV\s*\|\s*(待定|未评分|[?？])",
        output, re.DOTALL
    )
    for name, code, _ in fev_issues:
        warnings.append(f"FEV缺失: {name}({code})")

    # 3. 检查 Δ 是否显式标注
    for code in codes_found:
        if f"({code})" in output:
            block_start = output.find(f"({code})")
            block = output[block_start:block_start+500]
            if "Δ" not in block:
                warnings.append(f"Δ缺失: {code}")

    if warnings:
        lines = output.split("\n")
        # 在模块贡献摘要前插入告警
        insert_at = None
        for i, line in enumerate(lines):
            if "模块贡献摘要" in line or "## 📊" in line:
                insert_at = i
                break
        if insert_at:
            alert = ["", "> ⚠️ **覆盖率告警**:"] + [f"> - {w}" for w in warnings] + [""]
            lines = lines[:insert_at] + alert + lines[insert_at:]
            output = "\n".join(lines)
        print(f"  [COVERAGE] {len(warnings)} 个告警: {'; '.join(warnings)}")

    return output


def _build_selection(output: str) -> str:
    """解析 LLM 候选池 → 查 FEVΔ → 硬排名取前10 → 替换候选池为精选标的。"""
    # 找到候选池段落
    pool_start = output.find("## 🎯 候选池")
    if pool_start == -1:
        pool_start = output.find("## 🎯 第三层")
    if pool_start == -1:
        pool_start = output.find("候选池")
    if pool_start == -1:
        return output

    # 找到候选池结束位置（下一个 ## 或 📊 模块贡献摘要）
    rest = output[pool_start:]
    next_section = re.search(r"\n## (?!\#)", rest[5:])  # skip the first ##
    pool_end = pool_start + 5 + next_section.start() if next_section else len(output)
    pool_text = output[pool_start:pool_end]

    # 解析候选标的: 支持三种格式
    # 格式A: ### 名称 (6位代码)
    # 格式B: - **名称** (6位代码)  或  - **名称(6位代码)**
    # 格式C: ### 6位代码 名称 (代码在前，无括号)
    candidates = []
    # 格式A
    for m in re.finditer(r"(?:^|\n)###\s*(.+?)\s*\((\d{6})\)", pool_text):
        name, code = m.group(1), m.group(2)
        rest = pool_text[m.end():]
        next_section = re.search(r"\n(?:###\s|\n##|\n---)", rest)
        block = rest[:next_section.start()] if next_section else rest[:300]
        candidates.append({"code": code, "name": name, "block": block.strip()})
    # 格式B: - **名称** (6位代码) — 提取该行及后续缩进行
    for m in re.finditer(r"-\s*\*{2}([^*]+?)\s*\((\d{6})\)", pool_text):
        name, code = m.group(1), m.group(2)
        line_start = pool_text.rfind("\n", 0, m.start()) + 1
        rest = pool_text[line_start:]
        lines = rest.split("\n")
        block_lines = [lines[0]]
        for ln in lines[1:]:
            if ln.strip().startswith("- ") and not ln.startswith("    "):
                break
            if ln.strip().startswith("### "):
                break
            if ln.strip().startswith("## "):
                break
            block_lines.append(ln)
        block = "\n".join(block_lines)
        candidates.append({"code": code, "name": name, "block": block})
    # 格式C: ### 6位代码 名称 (代码在前，无括号)
    for m in re.finditer(r"(?:^|\n)###\s*(\d{6})\s+(\S+)", pool_text):
        code, name = m.group(1), m.group(2)
        rest = pool_text[m.end():]
        next_section = re.search(r"\n(?:###\s|\n##|\n---)", rest)
        block = rest[:next_section.start()] if next_section else rest[:300]
        candidates.append({"code": code, "name": name, "block": block.strip()})

    if len(candidates) < 5:
        print(f"  [SELECTION] 候选池仅 {len(candidates)} 只，跳过硬排名")
        return output

    # 查 FEV（serenity + feval）
    fev_map = {}
    try:
        from daily_review.serenity_kb import init_db, get_stock_scores
        init_db()
        for s in get_stock_scores():
            fev_map[s["code"]] = {
                "f": s.get("f_score", 0), "e": s.get("e_score", 0),
                "v": s.get("v_score", 0), "fev": s.get("fev_total", 0),
                "chain": s.get("chain_name", ""), "source": "ChokeMap",
            }
    except Exception:
        pass
    try:
        from daily_review.feval import get_scores as feval_get_scores
        for code, fs in feval_get_scores().items():
            fev_val = fs.get("fev_total", 0) or 0
            if code not in fev_map or fev_map[code].get("fev", 0) == 0:
                if fev_val > 0 or code not in fev_map:
                    fev_map[code] = {
                        "f": fs.get("f_score", 0), "e": fs.get("e_score", 0),
                        "v": fs.get("v_score", 0), "fev": fev_val,
                        "chain": fev_map.get(code, {}).get("chain", ""), "source": "feval",
                    }
    except Exception:
        pass

    # 查 Δ
    delta_map = {}
    try:
        from daily_review.feval import get_delta_scores as feval_get_delta
        delta_map = feval_get_delta()
    except Exception:
        pass

    # 候选标的缺 FEV 时实时打分补齐
    missing_fev = [c for c in candidates if c["code"] not in fev_map or fev_map[c["code"]].get("fev", 0) == 0]
    if missing_fev:
        print(f"  [SELECTION] {len(missing_fev)}/{len(candidates)} 只缺FEV，实时打分...")
        try:
            import data
            codes = [c["code"] for c in missing_fev]
            quotes = data.fetch_stock_quotes(codes, batch_size=30)
            stocks = []
            for c in missing_fev:
                q = quotes.get(c["code"], {})
                stocks.append({
                    "code": c["code"],
                    "name": q.get("name", c["name"]),
                    "mcap_yi": round(q.get("mcap_yi", 0) or 0),
                    "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
                    "chg_pct": round(q.get("change_pct", 0) or 0, 2),
                })
            from daily_review.feval import score_batch, save_scores
            new_scores = score_batch(stocks)
            if new_scores:
                save_scores(new_scores)
                for s in new_scores:
                    fev_val = s.get("fev_total", 0)
                    fev_map[s["code"]] = {
                        "f": s.get("f_score", 0), "e": s.get("e_score", 0),
                        "v": s.get("v_score", 0), "fev": fev_val,
                        "chain": "", "source": "feval(onthefly)",
                    }
                print(f"  [SELECTION] 实时打分完成: {len(new_scores)} 只")
            else:
                print(f"  [SELECTION] 实时打分失败，{len(missing_fev)} 只FEV=0")
        except Exception as e:
            print(f"  [SELECTION] 实时打分异常: {e}")

    # 候选标的缺 Δ 时告警
    missing_delta = [c for c in candidates if c["code"] not in delta_map]
    if missing_delta:
        codes_str = ",".join(c["code"] for c in missing_delta[:8])
        print(f"  [SELECTION] WARNING {len(missing_delta)}/{len(candidates)} 只缺Δ ({codes_str}...)，Δ=0，盘前应跑 Δ 评分")

    # 计算 FEVΔ，排序
    for c in candidates:
        fv = fev_map.get(c["code"], {})
        d = delta_map.get(c["code"], {})
        c["fev"] = fv.get("fev", 0)
        c["f_score"] = fv.get("f", 0)
        c["e_score"] = fv.get("e", 0)
        c["v_score"] = fv.get("v", 0)
        c["fev_source"] = fv.get("source", "")
        c["serenity_chain"] = fv.get("chain", "")
        c["delta"] = d.get("delta_score", 0) if d else 0
        c["delta_signal"] = d.get("signal", "") if d else ""
        c["fevd"] = c["fev"] + c["delta"]

    candidates.sort(key=lambda x: -x["fevd"])

    # 取前10
    top10 = candidates[:10]
    rank_emoji = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    lines = ["", "## 🎯 精选标的 — 用谁打", "",
             "> 由 Python 后端按 **FEVΔ = FEV + Δ** 硬排名，消除 LLM 随机性。", "",
             "| # | 标的 | FEV | Δ | FEVΔ | 来源 |",
             "|:--|------|:---:|:--:|:-----:|------|"]
    for i, c in enumerate(top10):
        sign = "+" if c["delta"] >= 0 else ""
        ds_str = f"{sign}{c['delta']}" if c["delta"] != 0 else "0"
        lines.append(
            f"| {rank_emoji[i]} | **{c['name']}({c['code']})** | "
            f"{c['fev']} | {ds_str} | **{c['fevd']}** | {c['fev_source']} |"
        )
    lines.append("")
    lines.append(f"> 候选池共 {len(candidates)} 只，FEVΔ 范围 {top10[0]['fevd']}~{top10[-1]['fevd']}，"
                 f"未入选 {len(candidates)-10} 只见文末备选。")
    lines.append("")

    # 逐只展开
    for i, c in enumerate(top10):
        lines.append(f"### {rank_emoji[i]} {c['name']} ({c['code']})")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        chain_str = f"{c['serenity_chain']}" if c['serenity_chain'] else "未覆盖"
        lines.append(f"| FEV | {c['fev']} (F={c['f_score']} E={c['e_score']} V={c['v_score']}) · {c['fev_source']} |")
        sign = "+" if c['delta'] >= 0 else ""
        lines.append(f"| Δ | {sign}{c['delta']} · {c['delta_signal'][:60]} |")
        lines.append(f"| FEVΔ | **{c['fevd']}/40** |")
        lines.append(f"| ChokeMap | {chain_str} |")
        lines.append("")
        # 附上 LLM 写的 W1-W5 分析（从 block 中提取）
        for w_tag in ["W1", "W3", "W4", "W5"]:
            m = re.search(rf"-\s*\*\*{w_tag}\*\*\s*(.+)", c["block"])
            if m:
                lines.append(f"- **{w_tag}** {m.group(1).strip()}")
        lines.append("")

    # 备选标的
    if len(candidates) > 10:
        rest = candidates[10:]
        lines.append("<details><summary>📋 备选标的（未进前10，点击展开）</summary>")
        lines.append("")
        lines.append("| 标的 | FEVΔ | 未入选原因推测 |")
        lines.append("|------|:----:|---------------|")
        for c in rest:
            reason = "Δ=0无新增信号" if c["delta"] == 0 else f"FEV={c['fev']}偏低"
            lines.append(f"| {c['name']}({c['code']}) | {c['fevd']} | {reason} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # 替换候选池段落
    before = output[:pool_start]
    after = output[pool_end:]
    # 移除候选池标题后的空行
    after = after.lstrip("\n")
    new_output = before.rstrip("\n") + "\n" + "\n".join(lines) + "\n" + after

    print(f"  [SELECTION] 候选 {len(candidates)} → 精选 {len(top10)}，"
          f"FEVΔ {top10[0]['fevd']}~{top10[-1]['fevd']}")
    return new_output


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

    # 盘前先跑 FEV + Δ 评分，确保候选池有数据可查
    print("  [PRE] 盘前 FEV/Δ 评分...")
    try:
        from daily_review.feval import score_from_feeds, score_delta_from_feeds
        score_from_feeds(today)
    except Exception as e:
        print(f"  [PRE] FEV 评分失败: {e}")
    try:
        from daily_review.feval import score_delta_from_feeds
        score_delta_from_feeds(today)
    except Exception as e:
        print(f"  [PRE] Δ 评分失败: {e}")

    fev_table = _inject_fev_table()

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
        .replace("%%FEV_TABLE%%", fev_table)
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
    output = _build_selection(output)
    output = _validate_advice_coverage(output)

    print(output)

    if output.strip() and len(output) > 500:
        advice_path.write_text(output, encoding="utf-8")
        print("[INFO] advice saved from stdout")
    elif output.strip():
        print("[WARN] advice output too short, not saving (likely error response)")

    print(f"  advice output: {len(output)} chars")


if __name__ == "__main__":
    main()
