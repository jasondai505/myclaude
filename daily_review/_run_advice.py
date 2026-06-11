"""Render claude_prompt.txt → SDK 直调 Claude → 输出 advice 报告。
Called by morning_advice.bat Step 5.
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
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
                "%%SUPPLY_CHAIN_INTEL%%", "%%JIUYANG%%", "%%WEIBO%%",
                "%%PRIMARY_SYNTHESIS%%"):
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


def _inject_jiuyang(today: str) -> str:
    """注入韭研公社脱水研报。"""
    path = BASE / "reports" / "feeds" / f"jiuyang_{today}.md"
    if not path.exists():
        return ("（韭研脱水研报暂未生成，请关注 wayzhang007 主页 "
                "https://www.jiuyangongshe.com/u/42ba01f7cc33451ea0ee10c83b4941eb ）")
    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > MAX_DIRECT_CHARS * 3:
            content = content[:MAX_DIRECT_CHARS * 3] + "\n...(truncated)"
        return content
    except Exception as e:
        return f"（韭研脱水研报读取失败: {e}）"


def _inject_weibo(today: str) -> str:
    """注入唐史主任司马迁微博分析。"""
    path = BASE / "reports" / "feeds" / f"weibo_{today}.md"
    if not path.exists():
        return "（唐史微博暂未更新，今日无新帖或采集未运行）"
    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > MAX_DIRECT_CHARS:
            content = content[:MAX_DIRECT_CHARS] + "\n...(truncated)"
        return content
    except Exception as e:
        return f"（唐史微博读取失败: {e}）"


def _inject_primary_synthesis(today: str) -> str:
    """注入四源交叉验证结果。"""
    path = BASE / "reports" / "feeds" / f"primary_synthesis_{today}.md"
    if not path.exists():
        return ("（四源交叉验证暂未生成。请从 %%ZSXQ%% / %%WECHAT_ANALYSIS%% / %%JIUYANG%% / %%WEIBO%% "
                "中自行交叉参考，找多源共识主题和分歧点）")
    try:
        content = path.read_text(encoding="utf-8")
        return content
    except Exception as e:
        return f"（四源交叉验证读取失败: {e}）"


def _inject_marginal(today: str) -> str:
    """读取当日边际变化日报，注入 prompt 供 LLM 识别拐点信号。"""
    path = BASE / "reports" / f"marginal_{today}.md"
    if not path.exists():
        return "（边际变化日报暂未生成。请从其他 feed 中自行判断边际变化信号。）"
    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > 4000:
            lines = content.split("\n")
            out = []
            stop = False
            for ln in lines:
                if ln.startswith("## 符合预期") or ln.startswith("## 首次记录"):
                    stop = True
                if not stop:
                    out.append(ln)
                elif len(out) < len(lines) * 0.5:
                    out.append(ln)
            content = "\n".join(out)
        return content
    except Exception as e:
        return f"（边际变化日报读取失败: {e}）"


def _inject_must_consider() -> str:
    """ChokeMap 高 FEV 标的强制入池，防 LLM 遗漏。"""
    try:
        from daily_review.serenity_kb import init_db, get_stock_scores
        init_db()
        stocks = get_stock_scores()
        if not stocks:
            return "（ChokeMap 暂无评分数据）"
        top = sorted(stocks, key=lambda s: s.get("fev_total", 0), reverse=True)[:15]
        # 补名称（serenity_kb 可能未存 name）
        try:
            import data
            codes = [s["code"] for s in top if not s.get("name")]
            if codes:
                quotes = data.fetch_stock_quotes(codes, batch_size=30)
                for s in top:
                    if not s.get("name") and s["code"] in quotes:
                        s["name"] = quotes[s["code"]].get("name", "")
        except Exception:
            pass
        lines = [
            "以下标的在 ChokeMap 中 FEV 评分靠前，候选池必须覆盖（除非今日有明确反向信号）：",
            "",
            "| 代码 | 名称 | FEV | 产业链 | 反向信号检查 |",
            "|------|------|:---:|--------|-------------|",
        ]
        for s in top:
            if s.get("fev_total", 0) >= 20:
                lines.append(
                    f"| {s['code']} | {s['name']} | {s['fev_total']} | "
                    f"{s.get('chain_name', '')} | □ 无 / □ 有（请填写） |"
                )
        return "\n".join(lines) if len(lines) > 4 else "（无 FEV≥20 标的）"
    except Exception as e:
        return f"（ChokeMap 读取失败: {e}）"


def _inject_yesterday_logic(yesterday: str) -> str:
    """提取昨日 advice 中候选池的 W1-W5 逻辑，供 LLM 判断是否延续。"""
    path = BASE / "reports" / f"advice_{yesterday}.md"
    if not path.exists():
        return f"（{yesterday} advice 不存在）"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"（昨日 advice 读取失败: {e}）"

    import re
    stocks = []
    for m in re.finditer(r"###\s+(?:[^\s]+)?\s*(.+?)\s*\((\d{6})\)", text):
        name, code = m.group(1).strip(), m.group(2)
        name = re.sub(r'^[🥇🥈🥉4-9️⃣🔟]\s*', '', name).strip()
        rest = text[m.end():m.end() + 600]
        w1 = re.search(r"-\s*\*\*W1\*\*\s*(.+)", rest)
        w3 = re.search(r"-\s*\*\*W3\*\*\s*(.+)", rest)
        w4 = re.search(r"-\s*\*\*W4\*\*\s*(.+)", rest)
        w5 = re.search(r"-\s*\*\*W5\*\*\s*(.+)", rest)
        fevd_m = re.search(r"FEVΔ\s*\|\s*\*{0,2}(\d+)/40", rest)
        stocks.append({
            "name": name, "code": code,
            "fevd": fevd_m.group(1) if fevd_m else "?",
            "w1": w1.group(1).strip() if w1 else "",
            "w3": w3.group(1).strip() if w3 else "",
            "w4": w4.group(1).strip() if w4 else "",
            "w5": w5.group(1).strip() if w5 else "",
        })

    if not stocks:
        return f"（{yesterday} advice 中无精选标的）"

    lines = [
        f"昨日（{yesterday}）精选标的及其催化逻辑。请逐只判断：催化是否仍成立？应继续持有/入选还是剔除？",
        "",
        "| 代码 | 名称 | 昨日FEVΔ | W1边际信号 | W3预期差 | W4时间窗 | W5风险 | 今日判断 |",
        "|------|------|:--------:|-----------|---------|---------|--------|---------|",
    ]
    for s in stocks[:10]:
        lines.append(
            f"| {s['code']} | {s['name']} | {s['fevd']} | "
            f"{s['w1'][:40]} | {s['w3'][:30]} | {s['w4'][:25]} | {s['w5'][:25]} | □继续 / □剔除 |"
        )
    return "\n".join(lines)


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

    # 解析持有期标签
    for c in candidates:
        hp_m = re.search(r"[-*]\s*\*{0,2}持有期\*{0,2}\s*(.+)", c["block"])
        c["hold_period"] = hp_m.group(1).strip() if hp_m else "未标注"
        hp = c["hold_period"]
        if "短线" in hp or "超短" in hp:
            c["hold_period"] = "短线催化"
        elif "中线" in hp or "趋势" in hp[:4]:
            c["hold_period"] = "中线趋势"
        elif "长线" in hp or "底仓" in hp or "长期" in hp:
            c["hold_period"] = "长线底仓"

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

    # 连续性加分：昨日精选中催化仍成立的标的，防一日游
    from datetime import date as _dt, timedelta as _td
    _yesterday = (_dt.today() - _td(days=1)).isoformat()
    y_path = BASE / "reports" / f"advice_{_yesterday}.md"
    yesterday_holds: dict[str, str] = {}  # code -> hold_period
    if y_path.exists():
        try:
            y_text = y_path.read_text(encoding="utf-8")
            in_table = False
            for line in y_text.split("\n"):
                if "精选标的" in line and "🎯" in line:
                    in_table = True
                    continue
                if in_table:
                    if line.startswith("> 候选池") or line.startswith("<details>"):
                        break
                    m = re.search(r"\*\*(.+?)\((\d{6})\)\*\*", line)
                    if m:
                        code = m.group(2)
                        hp_m = re.search(r"\|\s*(短线催化|中线趋势|长线底仓)\s*\|", line)
                        hp = hp_m.group(1) if hp_m else ""
                        yesterday_holds[code] = hp
        except Exception:
            pass

    for c in candidates:
        hp = yesterday_holds.get(c["code"], "")
        if hp == "长线底仓":
            c["continuity_bonus"] = 5
        elif hp == "中线趋势":
            c["continuity_bonus"] = 3
        elif hp == "短线催化":
            c["continuity_bonus"] = 1
        elif hp == "":
            c["continuity_bonus"] = 2  # 旧格式无持有期标签，默认+2
        else:
            c["continuity_bonus"] = 0
        c["fevd_adjusted"] = c["fevd"] + c["continuity_bonus"]

    candidates.sort(key=lambda x: -x["fevd_adjusted"])

    # 取前10
    top10 = candidates[:10]
    rank_emoji = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    # 时间姿态总览
    hp_counts = {"短线催化": 0, "中线趋势": 0, "长线底仓": 0}
    for c in top10:
        hp = c.get("hold_period", "")
        if hp in hp_counts:
            hp_counts[hp] += 1
    dominant = max(hp_counts, key=hp_counts.get)
    posture = {
        "短线催化": "偏短线，适合事件驱动盯盘，持仓 1-5 天",
        "中线趋势": "偏中线，适合趋势跟随，持仓 1-3 周",
        "长线底仓": "偏长线，适合底仓配置，持仓 1 月+",
    }
    hp_parts = [f"{v}只 {k}" for k, v in hp_counts.items() if v > 0]

    lines = ["", "## 🎯 精选标的 — 用谁打", "",
             f"> ⏱️ **时间姿态**: {' / '.join(hp_parts)} → {posture.get(dominant, '')}",
             "> 由 Python 后端按 **FEVΔ = FEV + Δ + 连续性加分** 硬排名。延续标的获加分（长线+5/中线+3/短线+1），防一日游。", "",
             "| # | 标的 | FEV | Δ | 加分 | FEVΔ | 持有期 | 来源 |",
             "|:--|------|:---:|:--:|:----:|:-----:|:------:|------|"]
    for i, c in enumerate(top10):
        sign = "+" if c["delta"] >= 0 else ""
        ds_str = f"{sign}{c['delta']}" if c["delta"] != 0 else "0"
        cb = c.get("continuity_bonus", 0)
        cb_str = f"+{cb}" if cb > 0 else "—"
        hp = c.get("hold_period", "未标注")
        lines.append(
            f"| {rank_emoji[i]} | **{c['name']}({c['code']})** | "
            f"{c['fev']} | {ds_str} | {cb_str} | **{c['fevd_adjusted']}** | {hp} | {c['fev_source']} |"
        )
    lines.append("")
    lines.append(f"> 候选池共 {len(candidates)} 只，FEVΔ 范围 {top10[0]['fevd']}~{top10[-1]['fevd']}，"
                 f"未入选 {len(candidates)-10} 只见文末备选。")
    lines.append("")

    # 自辩：对所有精选标的跑 fact-debate
    debate_map = _debate_stocks(top10)

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
        cb = c.get("continuity_bonus", 0)
        if cb > 0:
            lines.append(f"| FEVΔ | **{c['fevd']}/40** + 连续性加分 {cb} = **{c['fevd_adjusted']}** |")
        else:
            lines.append(f"| FEVΔ | **{c['fevd']}/40** |")
        lines.append(f"| ChokeMap | {chain_str} |")
        hp = c.get("hold_period", "未标注")
        # 从 W4 提取时间锚点，与持有期合并展示
        w4_text = ""
        w4_m = re.search(r"-\s*\*\*W4\*\*\s*(.+)", c["block"])
        if w4_m:
            w4_text = w4_m.group(1).strip()
        if w4_text:
            lines.append(f"| ⏱️ 时间锚 | {w4_text} · **{hp}** |")
        else:
            lines.append(f"| ⏱️ 时间锚 | **{hp}** |")
        lines.append("")
        # 附上 LLM 写的 W1/W3/W5 分析（W4 已融入时间锚，跳过）
        for w_tag in ["W1", "W3", "W5"]:
            m = re.search(rf"-\s*\*\*{w_tag}\*\*\s*(.+)", c["block"])
            if m:
                lines.append(f"- **{w_tag}** {m.group(1).strip()}")
        # 自辩质疑
        questions = debate_map.get(c["code"], [])
        if questions:
            lines.append("")
            lines.append("<details><summary>🛡️ 魔鬼辩护人</summary>")
            lines.append("")
            for q in questions[:3]:
                lines.append(f"> ⚡ {q}")
            lines.append("")
            lines.append("</details>")
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

    # 持续追踪池：昨日精选中跌出前10但持有期≠短线催化的标的
    if yesterday_holds:
        today_codes = {c["code"] for c in top10}
        # 判断昨日文件是否有持有期标签（新格式才有）
        has_labels = any(hp in ("短线催化", "中线趋势", "长线底仓") for hp in yesterday_holds.values())
        tracking = []
        for code, hp in yesterday_holds.items():
            should_track = hp in ("中线趋势", "长线底仓")
            if not has_labels:
                should_track = True  # 旧格式无标签，全部纳入追踪
            if code not in today_codes and should_track:
                # 在今日候选池或备选中查找当前FEVΔ
                found = None
                for c in candidates:
                    if c["code"] == code:
                        found = c
                        break
                if found:
                    tracking.append({
                        "code": code,
                        "name": found["name"],
                        "fevd": found["fevd"],
                        "hold_period": hp if hp else "未标注",
                        "delta": found["delta"],
                    })
        if tracking:
            lines.append("## 🔭 持续追踪池（跌出精选但逻辑未破）")
            lines.append("")
            lines.append("> 以下标的昨日在精选池中，持有期为中线/长线，今日因排名下滑跌出前10。催化逻辑可能仍成立，保持关注。")
            lines.append("")
            lines.append("| 标的 | 今日FEVΔ | Δ | 持有期 | 关注原因 |")
            lines.append("|------|:--------:|:--:|:------:|---------|")
            for t in sorted(tracking, key=lambda x: -x["fevd"]):
                sign = "+" if t["delta"] > 0 else ""
                ds = f"{sign}{t['delta']}" if t['delta'] != 0 else "0"
                reason = "Δ衰减但逻辑持续" if t["delta"] == 0 else "被更高分标的挤出"
                lines.append(
                    f"| {t['name']}({t['code']}) | **{t['fevd']}** | {ds} | "
                    f"{t['hold_period']} | {reason} |"
                )
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


def _build_daily_diff(today_output: str, yesterday: str, today: str) -> str:
    """比较今日与昨日精选标的，生成日间变化说明。"""
    path = BASE / "reports" / f"advice_{yesterday}.md"
    if not path.exists():
        return ""

    try:
        y_text = path.read_text(encoding="utf-8")
    except Exception:
        return ""

    # 提取今日精选标的（从硬排名表格中）
    # 表格列: # | 标的 | FEV | Δ | 加分 | FEVΔ | 持有期 | 来源
    today_stocks = {}
    in_table = False
    for line in today_output.split("\n"):
        if "精选标的" in line and "🎯" in line:
            in_table = True
            continue
        if in_table:
            if line.startswith("> 候选池") or line.startswith("<details>"):
                break
            m = re.search(r"\*\*(.+?)\((\d{6})\)\*\*", line)
            if m:
                name, code = m.group(1).strip(), m.group(2)
                cols = line.split("|")
                if len(cols) >= 8:
                    fevd_str = cols[6].strip().strip("*")  # FEVΔ 在第6列
                    delta_str = cols[4].strip().strip("*")  # Δ 在第4列
                    hp_str = cols[7].strip()                 # 持有期 在第7列
                    try:
                        fevd = int(fevd_str)
                    except ValueError:
                        fevd = 0
                    try:
                        delta = int(delta_str)
                    except ValueError:
                        delta = 0
                else:
                    fevd, delta = 0, 0
                    hp_str = ""
                hp = hp_str if hp_str in ("短线催化", "中线趋势", "长线底仓") else ""
                today_stocks[code] = {"name": name, "fevd": fevd, "delta": delta, "hold_period": hp}

    if not today_stocks:
        return ""

    # 提取昨日精选标的（旧格式无加分列，FEVΔ总在第5列）
    yesterday_stocks = {}
    in_table = False
    for line in y_text.split("\n"):
        if "精选标的" in line and "🎯" in line:
            in_table = True
            continue
        if in_table:
            if line.startswith("> 候选池") or line.startswith("<details>"):
                break
            m = re.search(r"\*\*(.+?)\((\d{6})\)\*\*", line)
            if m:
                name, code = m.group(1).strip(), m.group(2)
                cols = line.split("|")
                # 旧/新格式 FEVΔ 都在 cols[5]（加分列是今天才加的）
                fevd_str = cols[5].strip().strip("*") if len(cols) > 5 else "0"
                try:
                    fevd = int(fevd_str)
                except ValueError:
                    fevd = 0
                yesterday_stocks[code] = {"name": name, "fevd": fevd}

    if not yesterday_stocks:
        return ""

    today_set = set(today_stocks.keys())
    yesterday_set = set(yesterday_stocks.keys())
    continued = yesterday_set & today_set
    new_entries = today_set - yesterday_set
    exited = yesterday_set - today_set

    jaccard = len(continued) / len(yesterday_set | today_set) if (yesterday_set | today_set) else 0

    lines = ["", "## 📈 日间变化说明", "",
             f"> 较昨日（{yesterday}）精选标的对比 · 相似度: **{jaccard:.0%}** "
             f"（延续 {len(continued)} / 新进 {len(new_entries)} / 退出 {len(exited)}）", ""]

    if continued:
        lines.append("### ✅ 延续标的")
        lines.append("")
        lines.append("| 标的 | 昨日FEVΔ | 今日FEVΔ | 变化 | 持有期 |")
        lines.append("|------|:--------:|:--------:|:----:|:------:|")
        for code in sorted(continued):
            t = today_stocks[code]
            y = yesterday_stocks[code]
            diff = t["fevd"] - y["fevd"]
            diff_str = f"+{diff}" if diff > 0 else str(diff) if diff < 0 else "—"
            note = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
            lines.append(
                f"| {t['name']}({code}) | {y['fevd']} | **{t['fevd']}** | "
                f"{diff_str} {note} | {t['hold_period']} |"
            )
        lines.append("")

    if new_entries:
        lines.append("### 🆕 新进入")
        lines.append("")
        lines.append("| 标的 | FEVΔ | Δ | 进入原因 | 持有期 |")
        lines.append("|------|:----:|:--:|---------|:------:|")
        for code in sorted(new_entries, key=lambda c: -today_stocks[c]["fevd"]):
            t = today_stocks[code]
            sign = "+" if t["delta"] > 0 else ""
            ds = f"{sign}{t['delta']}" if t["delta"] != 0 else "0"
            if t["delta"] > 0:
                reason = f"新催化 Δ={ds}"
            elif t["fevd"] >= 18:
                reason = "FEVΔ排名进入前10"
            else:
                reason = "候选池排名上升"
            lines.append(
                f"| {t['name']}({code}) | **{t['fevd']}** | {ds} | {reason} | {t['hold_period']} |"
            )
        lines.append("")

    if exited:
        lines.append("### 📤 退出精选")
        lines.append("")
        lines.append("| 标的 | 昨日FEVΔ | 退出原因 |")
        lines.append("|------|:--------:|---------|")
        for code in sorted(exited, key=lambda c: -yesterday_stocks[c]["fevd"]):
            y = yesterday_stocks[code]
            reason = "FEVΔ排名跌出前10（Delta衰减或更高分标的挤压）"
            lines.append(f"| {y['name']}({code}) | {y['fevd']} | {reason} |")
        lines.append("")

    return "\n".join(lines)


def _backtrack_labels(today: str) -> str:
    """回溯历史持有期标签，验证标签准确性。"""
    from datetime import date as _dt, timedelta as _td

    check_dates = {
        "短线催化": [(_dt.fromisoformat(today) - _td(days=i)).isoformat() for i in [3, 5, 7]],
        "中线趋势": [(_dt.fromisoformat(today) - _td(days=i)).isoformat() for i in [10, 15, 20]],
    }

    try:
        import data
        import pandas as pd
    except ImportError:
        return ""

    all_stocks: dict[str, dict] = {}  # code -> {label, rec_date, name}
    for label, dates in check_dates.items():
        for d in dates:
            path = BASE / "reports" / f"advice_{d}.md"
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            in_table = False
            for line in text.split("\n"):
                if "精选标的" in line and "🎯" in line:
                    in_table = True
                    continue
                if in_table and (line.startswith("> 候选池") or line.startswith("<details>")):
                    break
                if in_table:
                    m = re.search(r"\*\*(.+?)\((\d{6})\)\*\*", line)
                    if not m:
                        continue
                    name, code = m.group(1).strip(), m.group(2)
                    if code in all_stocks:
                        continue
                    hp_m = re.search(r"\|\s*(短线催化|中线趋势|长线底仓)\s*\|", line)
                    hp = hp_m.group(1) if hp_m else ""
                    if hp and hp in label:
                        all_stocks[code] = {"name": name, "label": hp, "rec_date": d}

    if len(all_stocks) < 3:
        return "（标签回溯数据不足，需≥3只有标签的历史标的）"

    results = []
    for code, info in all_stocks.items():
        try:
            df = data.fetch_klines(code, days=120)
            if df is None or df.empty:
                continue
            df = df.sort_index()
            after = df[df.index >= pd.Timestamp(info["rec_date"])]
            if after.empty:
                continue
            entry_close = float(after.iloc[0].get("close", 0))
            latest_close = float(after.iloc[-1].get("close", 0))
            max_close = float(after["close"].max())
            if entry_close <= 0:
                continue
            ret = (latest_close / entry_close - 1) * 100
            max_ret = (max_close / entry_close - 1) * 100
            days_held = len(after)
            results.append({
                **info,
                "ret": ret, "max_ret": max_ret, "days_held": days_held,
            })
        except Exception:
            continue

    if len(results) < 3:
        return "（标签回溯数据不足，k线获取失败）"

    lines = ["", "## 🏷️ 标签回溯验证", "",
             "> 回测历史持有期标签实际表现，验证标签判断。", ""]

    for label in ["短线催化", "中线趋势"]:
        group = [r for r in results if r["label"] == label]
        if not group:
            continue
        avg_ret = sum(r["ret"] for r in group) / len(group)
        avg_max = sum(r["max_ret"] for r in group) / len(group)
        avg_days = sum(r["days_held"] for r in group) / len(group)
        pos = sum(1 for r in group if r["ret"] > 0)

        lines.append(f"### {label}（{len(group)}只）")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 平均持有天数 | {avg_days:.0f} 天 |")
        lines.append(f"| 平均收益 | {avg_ret:+.1f}% |")
        lines.append(f"| 平均最大收益 | {avg_max:+.1f}% |")
        lines.append(f"| 胜率 | {pos}/{len(group)} |")
        lines.append("")
        lines.append("| 标的 | 推荐日 | 持有天数 | 收益 | 最大收益 |")
        lines.append("|------|--------|:------:|:-----:|:------:|")
        for r in sorted(group, key=lambda x: -x["ret"]):
            lines.append(
                f"| {r['name']}({r['code']}) | {r['rec_date']} | {r['days_held']}天 | "
                f"{r['ret']:+.1f}% | {r['max_ret']:+.1f}% |"
            )
        lines.append("")

    return "\n".join(lines)


def _debate_stocks(top10: list[dict]) -> dict[str, list[str]]:
    """对精选标的跑 fact-debate，返回每只标的的最致命 2-3 个质疑。"""
    if not top10:
        return {}

    api_key = _load_api_key()
    if not api_key:
        return {}

    # 构建每只标的的简要信息
    stock_briefs = []
    for c in top10:
        w1 = w3 = w4 = ""
        w1_m = re.search(r"-\s*\*\*W1\*\*\s*(.+)", c["block"])
        w3_m = re.search(r"-\s*\*\*W3\*\*\s*(.+)", c["block"])
        w4_m = re.search(r"-\s*\*\*W4\*\*\s*(.+)", c["block"])
        if w1_m:
            w1 = w1_m.group(1).strip()[:80]
        if w3_m:
            w3 = w3_m.group(1).strip()[:80]
        if w4_m:
            w4 = w4_m.group(1).strip()[:60]
        stock_briefs.append(
            f"- {c['name']}({c['code']}) {c.get('hold_period','')} FEVΔ={c['fevd_adjusted']} "
            f"W1:{w1} W3:{w3} W4:{w4}"
        )

    debate_prompt = f"""你是严苛的投委会评审。对以下精选标的，每只找出最致命的2-3个质疑。
质疑必须具体——指出逻辑漏洞、数据缺失、时间风险或反向证据。不要泛泛而谈。
只输出JSON，不要解释。格式: {{"代码": ["质疑1", "质疑2"], ...}}

标的：
{chr(10).join(stock_briefs)}"""

    try:
        client = Anthropic(api_key=api_key, base_url="https://api.deepseek.com/anthropic")
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": debate_prompt}],
            thinking={"type": "disabled"},
            timeout=60,
        )
        text = ""
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                text += block.text
        # 提取 JSON
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            result = json.loads(json_match.group(0))
            print(f"  [DEBATE] 自辩完成，{len(result)} 只标的")
            return result
    except Exception as e:
        print(f"  [DEBATE] 自辩失败: {e}")

    return {}


def _inject_theme_clock(today: str) -> str:
    """从 morning_intel theme_tracker 读取近期主题时钟，注入 advice。"""
    import sqlite3
    from datetime import date as _dt, timedelta as _td

    db_path = BASE.parent / "morning_intel" / "data" / "supply_chain.db"
    if not db_path.exists():
        return "（theme_tracker 数据库暂不可用）"

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        seven_d_ago = (_dt.fromisoformat(today) - _td(days=7)).isoformat()
        rows = conn.execute(
            "SELECT event_name, date, day_n, confidence, status, mainline_match, fading_match, emerging_match "
            "FROM theme_tracker WHERE date >= ? ORDER BY date DESC, day_n DESC",
            (seven_d_ago,)
        ).fetchall()
        conn.close()
    except Exception as e:
        return f"（theme_tracker 读取失败: {e}）"

    if not rows:
        return "（近7天无主题追踪数据）"

    themes: dict[str, dict] = {}
    for r in rows:
        name = r["event_name"]
        if name not in themes:
            themes[name] = {
                "first_date": r["date"], "last_date": r["date"],
                "max_day": r["day_n"], "confidence": r["confidence"],
                "status": r["status"],
                "mainline": r["mainline_match"] or "",
                "fading": r["fading_match"] or "",
                "emerging": r["emerging_match"] or "",
            }
        else:
            themes[name]["first_date"] = r["date"]
            themes[name]["max_day"] = max(themes[name]["max_day"], r["day_n"])

    active = []
    for name, t in themes.items():
        if t["status"] == "dead":
            continue
        try:
            age = (_dt.fromisoformat(today) - _dt.fromisoformat(t["first_date"])).days
        except ValueError:
            age = 0
        active.append({**t, "name": name, "age": age})

    if not active:
        return "（近7天无活跃主题）"

    active.sort(key=lambda x: -x["age"])

    lines = ["## 产业链主题时钟（近7天 morning_intel theme_tracker）", "",
             "| 主题 | 首次出现 | 持续天数 | 状态 | 匹配置信度 |",
             "|------|---------|:------:|:----:|:--------:|"]
    for t in active[:15]:
        status_icon = {"active": "🆕", "confirmed": "✅", "weakening": "⚠️"}.get(t["status"], "→")
        match_info = ""
        if t["mainline"]:
            match_info = f"主线:{t['mainline']}"
        elif t["fading"]:
            match_info = f"退潮:{t['fading']}"
        elif t["emerging"]:
            match_info = f"新兴:{t['emerging']}"
        lines.append(
            f"| {t['name'][:30]} | {t['first_date']} | {t['age']}天 | "
            f"{status_icon} {t['status']} | {t['confidence']} {match_info} |"
        )
    lines.append("")
    lines.append("> 持续 ≥2 天且主线匹配=confirmed → 优先关注。持续 ≥2 天且退潮匹配=weakening → 谨慎。")

    return "\n".join(lines)


def _scan_recurring_themes(today: str) -> str:
    """扫描过去7天 primary_synthesis，Haiku 提取周期性主题和标的。"""
    from datetime import date as dt_date, timedelta

    parts = []
    found = 0
    for i in range(1, 8):
        d = (dt_date.fromisoformat(today) - timedelta(days=i)).isoformat()
        path = BASE / "reports" / "feeds" / f"primary_synthesis_{d}.md"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                relevant = []
                capture = False
                for line in text.split("\n"):
                    if "共识主题" in line or "多源共同提及" in line or "cross_validated" in line:
                        capture = True
                    if capture:
                        relevant.append(line)
                    if capture and line.startswith("##") and "共识" not in line and "共同" not in line:
                        capture = False
                if relevant:
                    parts.append(f"### {d}\n" + "\n".join(relevant[:30]))
                    found += 1
            except Exception:
                pass

    if found < 2:
        return "（周期性主题数据不足，需 ≥2 天 primary_synthesis 产出）"

    combined = "\n\n".join(parts)
    if len(combined) > 5000:
        combined = combined[:5000]

    api_key = _load_api_key()
    if not api_key:
        return "（API key 不可用，周期性主题扫描跳过）"

    scan_prompt = f"""你是A股投研助手。以下是过去{found}天的四源交叉验证摘要。请提取：

1. 反复出现的主题（≥3天出现）：名称 / 出现天数 / 趋势（强化/稳定/减弱）/ 关联标的
2. 多日共同提及标的（≥3天被提及）：6位代码+名称 / 出现天数 / 关联主题
3. 最近2天首次出现的新信号：主题或标的

只输出Markdown表格，不要解释。

数据：
{combined}"""

    try:
        client = Anthropic(api_key=api_key, base_url="https://api.deepseek.com/anthropic")
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": scan_prompt}],
            thinking={"type": "disabled"},
            timeout=60,
        )
        parts_text = []
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                parts_text.append(block.text)
        result = "\n".join(parts_text)
        print(f"  [RECURRING] 周期性主题扫描完成，{found}/7 天有数据")
        return result
    except Exception as e:
        return f"（周期性主题扫描失败: {e}）"


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
    bom_ctx = _inject_bom_context()
    supply_chain = _inject_supply_chain_intel(today)
    serenity_ctx = _inject_serenity_context()
    intel_dims = _inject_intel_dimensions(today)
    jiuyang = _inject_jiuyang(today)
    weibo = _inject_weibo(today)
    primary_synthesis = _inject_primary_synthesis(today)
    marginal = _inject_marginal(today)
    recurring = _scan_recurring_themes(today)
    theme_clock = _inject_theme_clock(today)
    must_consider = _inject_must_consider()
    yesterday_logic = _inject_yesterday_logic(yesterday)
    feeds["%%JIUYANG%%"] = jiuyang
    feeds["%%WEIBO%%"] = weibo
    feeds["%%PRIMARY_SYNTHESIS%%"] = primary_synthesis
    codes = _extract_codes_from_feeds(feeds)
    stock_ctx = _inject_stock_context(codes)

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
        .replace("%%JIUYANG%%", jiuyang)
        .replace("%%WEIBO%%", weibo)
        .replace("%%PRIMARY_SYNTHESIS%%", primary_synthesis)
        .replace("%%MARGINAL_CHANGES%%", marginal)
        .replace("%%RECURRING_THEMES%%", recurring)
        .replace("%%THEME_CLOCK%%", theme_clock)
        .replace("%%MUST_CONSIDER%%", must_consider)
        .replace("%%YESTERDAY_LOGIC%%", yesterday_logic)
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
    diff_section = _build_daily_diff(output, yesterday, today)
    if diff_section:
        # 插入到备选标的前面
        details_marker = "<details><summary>📋 备选标的"
        if details_marker in output:
            idx = output.find(details_marker)
            output = output[:idx] + diff_section + "\n" + output[idx:]
        else:
            output += "\n" + diff_section
    backtrack = _backtrack_labels(today)
    if backtrack and "数据不足" not in backtrack:
        # 插入到日间变化说明之后
        diff_marker = "## 📈 日间变化说明"
        bt_marker = "## 🏷️ 标签回溯验证"
        if bt_marker not in output:
            if diff_marker in output:
                idx = output.find(diff_marker)
                # 找到日间变化说明的结束位置（下一个 ##）
                rest = output[idx + 10:]
                next_sec = re.search(r"\n(?=## |<details>)", rest)
                insert_at = idx + 10 + next_sec.start() if next_sec else len(output)
                output = output[:insert_at] + "\n" + backtrack + "\n" + output[insert_at:]
            else:
                output += "\n" + backtrack

    output = _validate_advice_coverage(output)

    print(output)

    if output.strip() and len(output) > 500:
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        output = f"# 盘前建议 {today} {datetime.now().strftime('%H:%M')}\n\n> 生成时间: {now_ts}\n\n{output}"
        advice_path.write_text(output, encoding="utf-8")
        print("[INFO] advice saved from stdout")
    elif output.strip():
        print("[WARN] advice output too short, not saving (likely error response)")

    print(f"  advice output: {len(output)} chars")


if __name__ == "__main__":
    main()
