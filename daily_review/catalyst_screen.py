"""催化快速筛查 — 从4源信息中提取高行动性催化剂，映射A股标的

用法:
    python daily_review/catalyst_screen.py                    # 今天
    python daily_review/catalyst_screen.py --date 2026-06-11  # 指定日期
    python daily_review/catalyst_screen.py --phase 1           # 仅Haiku提取(调试)
"""
import json, re, sys, argparse, hashlib, os
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from store import (
    query_zsxq_by_date, query_wechat_articles, query_jiuyang_reports,
    query_weibo_posts, load_weibo_report,
)
from roles import get_client, get_model
from config import REPORT_DIR, CONCEPT_HIERARCHY

REPORT_DIR.mkdir(parents=True, exist_ok=True)
CATALYST_DIR = REPORT_DIR / "catalyst"
CATALYST_DIR.mkdir(parents=True, exist_ok=True)

MULTI_MAP_PATH = Path(__file__).parent / "data" / "multi_concept_map.json"

BATCH_SIZE = 10
TOP_CATALYSTS_FOR_MAPPING = 5

# ============================================================
# 数据加载
# ============================================================
def _load_multi_map():
    if not MULTI_MAP_PATH.exists():
        return None
    return json.loads(MULTI_MAP_PATH.read_text(encoding="utf-8"))

def _load_sources(today_str: str) -> dict[str, list[dict]]:
    """加载当日+前一日信息源数据（盘前需要看昨晚的帖子）"""
    yesterday = (date.fromisoformat(today_str) - timedelta(days=1)).isoformat()
    return _load_sources_multi([yesterday, today_str])

def _load_sources_multi(dates: list[str]) -> dict[str, list[dict]]:
    sources = {}

    seen_ids = set()
    for d in dates:
        zsxq_rows = query_zsxq_by_date(d)
        if zsxq_rows:
            if "zsxq" not in sources:
                sources["zsxq"] = []
            for r in zsxq_rows:
                tid = r.get("topic_id", "")
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    sources["zsxq"].append({
                        "id": tid, "title": r.get("title", ""),
                        "text": r.get("text", ""), "author": r.get("author", ""),
                        "likes": r.get("likes_count", 0), "date": d,
                    })

        wechat_rows = query_wechat_articles(d, unanalyzed_only=False)
        if wechat_rows:
            if "wechat" not in sources:
                sources["wechat"] = []
            for i, r in enumerate(wechat_rows):
                wid = f"{d}_{i}"
                if wid not in seen_ids:
                    seen_ids.add(wid)
                    sources["wechat"].append({
                        "id": wid, "title": r.get("title", ""),
                        "text": r.get("content", ""), "author": r.get("feed_source", ""),
                        "date": d,
                    })

        jiuyang = query_jiuyang_reports(d)
        if jiuyang:
            if "jiuyang" not in sources:
                sources["jiuyang"] = []
            for i, r in enumerate(jiuyang):
                sources["jiuyang"].append({
                    "id": f"{d}_jy_{i}", "title": r.get("title", ""),
                    "text": r.get("content", ""), "author": "韭研公社",
                    "date": d,
                })

        weibo = query_weibo_posts(d)
        if weibo:
            if "weibo" not in sources:
                sources["weibo"] = []
            for i, r in enumerate(weibo):
                pid = r.get("post_id", f"{d}_{i}")
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    sources["weibo"].append({
                        "id": pid, "title": r.get("text", "")[:50],
                        "text": r.get("text", ""), "author": "唐史主任司马迁",
                        "date": d,
                    })

    return sources

# ============================================================
# Phase 1: Haiku 催化提取
# ============================================================
_EXTRACT_PROMPT = """你是A股事件驱动分析师。从以下帖子中提取**具体的、可交易的催化事件**。

忽略（不是催化）：
- 模糊方向判断（"看好AI方向" "XX板块有行情"）
- 纯技术分析（"均线金叉" "放量突破"）
- 纯复盘总结（"今天XX涨了"无新信息）
- 已充分定价的旧闻（市场已反应>3天）

提取以下类型的催化：
- supply_shock: 供给冲击（停产/出口管制/矿山事故/产能退出）
- price_spike: 价格异动（涨价函/报价跳涨/原材料暴涨）
- demand_surge: 需求爆发（大额订单/客户导入/政策驱动需求）
- policy_change: 政策变化（新规/补贴/禁令/标准制定）
- tech_breakthrough: 技术突破（量产突破/良率提升/认证/专利）
- order_contract: 订单签约（大合同/中标/框架协议/战略合作）

评分标准（1-10）：
- magnitude: 10=产业级巨变(价格翻倍/供给崩溃90%), 7-9=大变化(涨价30%+/大厂转向), 4-6=明显变化(10-30%), 1-3=边际
- specificity: 10=具体到SKU/分子式/条文, 7-9=产品/公司/政策名, 4-6=细分行业, 1-3=大类板块
- novelty: 10=突发事件无前例, 7-9=新变化少数人知, 4-6=延续加速, 1-3=持续讨论
- urgency: 10=即刻影响今日盘面, 7-9=本周兑现, 4-6=月度窗, 1-3=季度级

只输出JSON数组，每个催化一个对象：
[{
  "source_id": "帖子序号",
  "catalyst_name": "规范化的催化名称",
  "catalyst_type": "6种之一",
  "magnitude_score": 1-10,
  "specificity_score": 1-10,
  "novelty_score": 1-10,
  "urgency_score": 1-10,
  "thesis": "核心逻辑1-2句（引用原文数字）",
  "price_data": {"from": "变化前", "to": "变化后", "unit": "单位", "change_pct": null或数字},
  "key_entities": [{"name": "实体名", "type": "product/company/policy/region"}],
  "mentioned_codes": ["6位代码"或空数组],
  "time_horizon": "immediate/this_week/this_month/quarterly"
}]

如果没有可交易的催化，输出空数组 []。

--- 帖子内容 ---"""

def _haiku_extract(client, model, items: list[dict], source_type: str) -> list[dict]:
    """批量提取：每批 BATCH_SIZE 条，返回所有提取的催化"""
    results = []
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        batch_text = "\n\n---\n\n".join(
            f"[{j}] {it.get('title','')} | {it.get('author','')}\n{it.get('text','')[:800]}"
            for j, it in enumerate(batch, start=i)
        )

        prompt = _EXTRACT_PROMPT + "\n来源: " + source_type + "\n\n" + batch_text

        try:
            resp = client.messages.create(
                model=model, max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "disabled"},
            )
            text = "".join(block.text for block in resp.content if block.type == "text")
            extracted = _parse_json(text)
            if isinstance(extracted, list):
                for ex in extracted:
                    ex["source_type"] = source_type
                    # 映射 source_id 到实际帖子
                    idx = int(ex.get("source_id", "0").strip("[]"))
                    if 0 <= idx - i < len(batch):
                        batched_item = batch[idx - i]
                        ex["original_title"] = batched_item.get("title", "")
                        ex["original_author"] = batched_item.get("author", "")
                    results.append(ex)
        except Exception as e:
            print(f"  [WARN] Haiku extract batch {i} failed: {e}")

    return results

def _parse_json(text: str):
    """从LLM输出中提取JSON（处理markdown代码块和各种格式问题）"""
    text = text.strip()
    # 移除 markdown 代码块标记
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    # 查找 JSON 对象或数组的边界
    start = text.find("[")
    if start < 0:
        start = text.find("{")
    if start < 0:
        return {}
    end = text.rfind("]") if text[start] == "[" else text.rfind("}")

    if end < 0 or end <= start:
        return {}

    json_str = text[start:end + 1]

    # 尝试直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 移除尾部逗号 (最常见的 LLM JSON 错误)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", json_str)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 移除单行注释
    cleaned2 = re.sub(r"//[^\n]*", "", cleaned)
    try:
        return json.loads(cleaned2)
    except json.JSONDecodeError:
        pass

    return {}

# ============================================================
# 机械评分
# ============================================================
def _compute_actionability(ex: dict, source_count: int = 1) -> int:
    """M×3 + S×2 + N×2 + U×1，再加机械加成分"""
    def _get(key1, key2, default=1):
        val = ex.get(key1)
        if val is None or val == 0:
            val = ex.get(key2, default)
        return int(val) if val else default

    m = _get("magnitude", "magnitude_score")
    s = _get("specificity", "specificity_score")
    n = _get("novelty", "novelty_score")
    u = _get("urgency", "urgency_score")
    score = m * 3 + s * 2 + n * 2 + u

    if source_count >= 2:
        score += 5
    ct = ex.get("catalyst_type", "")
    if ct in ("supply_shock", "price_spike"):
        score += 3
    return min(score, 80)

def _actionability_label(score: int) -> str:
    if score >= 60: return "CRITICAL"
    if score >= 40: return "HIGH"
    if score >= 20: return "MEDIUM"
    return "NOISE"

# ============================================================
# Phase 2: Sonnet 交叉验证+排序
# ============================================================
_VALIDATE_PROMPT = """你是A股事件驱动策略分析师。以下是4个信息源提取的催化事件，请交叉验证、去重合并、综合排名。

多源可靠性排序: 知识星球(一线基金经理/产业专家) > 韭研公社(机构脱水研报) > 微信公众号(卖方/媒体) > 微博(碎片信号)

任务:
1. 去重合并: 同一催化的不同来源报道合并为一条
2. 交叉验证: 多源独立确认的催化置信度更高
3. 综合排名: 按真实可交易性排序（即时性>幅度>新奇度>来源数）
4. 补充 suggested_concepts: 每条催化推测可能关联的A股概念板块
5. 仅保留 actionability >= 20 的催化

{haiku_results}

输出JSON:
{{
  "ranked_catalysts": [
    {{
      "rank": 1,
      "catalyst_name": "催化名称",
      "merged_from": ["来源1", "来源2"],
      "source_count": 2,
      "catalyst_type": "6种之一",
      "final_actionability": 72,
      "magnitude": 10, "specificity": 10, "novelty": 10, "urgency": 10,
      "thesis": "合并后的核心逻辑",
      "price_data": {{"from": "...", "to": "...", "unit": "...", "change_pct": 数字或null}},
      "key_entities": [{{"name": "实体名", "type": "product/company/policy/region"}}],
      "suggested_concepts": ["概念1", "概念2"],
      "mentioned_codes": ["代码"或空数组],
      "time_horizon": "immediate/this_week/this_month/quarterly",
      "validation_note": "多源确认说明"
    }}
  ],
  "summary": "今日最重要的3-5个催化一览，每条一句话",
  "market_implication": "这些催化对今日盘面的综合影响判断（100字内）"
}}

只输出JSON。"""

def _sonnet_validate(client, model, all_extractions: list[dict]) -> dict:
    """Sonnet交叉验证+排序，失败时回退到原始Haiku结果"""
    grouped = defaultdict(list)
    for ex in all_extractions:
        grouped[ex.get("source_type", "?")].append(ex)

    formatted_parts = []
    for src, items in grouped.items():
        formatted_parts.append(f"\n## {src} ({len(items)}条)")
        for j, it in enumerate(items):
            formatted_parts.append(
                f"[{j}] {it.get('catalyst_name','?')} | type={it.get('catalyst_type','?')} "
                f"| M={it.get('magnitude_score',0)} S={it.get('specificity_score',0)} "
                f"N={it.get('novelty_score',0)} U={it.get('urgency_score',0)} "
                f"| {it.get('thesis','')[:200]}"
            )

    prompt = _VALIDATE_PROMPT.replace("{haiku_results}", "\n".join(formatted_parts))

    try:
        resp = client.messages.create(
            model=model, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        result = _parse_json(text)
        if isinstance(result, list):
            result = {"ranked_catalysts": result, "summary": "", "market_implication": ""}
        if (isinstance(result, dict) and result.get("ranked_catalysts")
                and len(result["ranked_catalysts"]) > 0):
            return result
        print(f"  [WARN] Sonnet returned empty or invalid JSON, falling back to Haiku")
    except Exception as e:
        print(f"  [WARN] Sonnet validation failed: {e}, falling back to Haiku")

    # 回退：直接用 Haiku 结果
    fallback = []
    for ex in all_extractions:
        fb = {
            "rank": 0,
            "catalyst_name": ex.get("catalyst_name", ""),
            "merged_from": [ex.get("source_type", "?")],
            "source_count": 1,
            "catalyst_type": ex.get("catalyst_type", ""),
            "magnitude": ex.get("magnitude_score", 0),
            "specificity": ex.get("specificity_score", 0),
            "novelty": ex.get("novelty_score", 0),
            "urgency": ex.get("urgency_score", 0),
            "thesis": ex.get("thesis", ""),
            "price_data": ex.get("price_data", {}),
            "key_entities": ex.get("key_entities", []),
            "suggested_concepts": [],
            "mentioned_codes": ex.get("mentioned_codes", []),
            "time_horizon": ex.get("time_horizon", "?"),
            "validation_note": "Haiku提取(Sonnet解析失败,已回退)",
        }
        fallback.append(fb)
    return {
        "ranked_catalysts": fallback,
        "summary": "",
        "market_implication": "",
    }

# ============================================================
# Phase 3: 标的映射
# ============================================================
def _keyword_match_stocks(catalyst_name: str, key_entities: list[dict]) -> list[dict]:
    """Layer 1: 关键词匹配 multi_concept_map 中的概念→反查股票"""
    mm = _load_multi_map()
    if not mm:
        return []

    search_terms = [catalyst_name.lower()] + [
        e["name"].lower() for e in (key_entities or [])
    ]

    concept_stocks = defaultdict(list)
    for code, concepts in mm["stocks"].items():
        for c in concepts:
            concept_stocks[c].append(code)

    matched = []
    seen = set()
    for term in search_terms:
        for concept, codes in concept_stocks.items():
            if term in concept.lower():
                for code in codes:
                    if code not in seen:
                        seen.add(code)
                        matched.append({
                            "code": code, "concept": concept,
                            "method": "keyword_match", "confidence": "medium",
                        })

    return matched[:30]  # 上限30只，避免噪音

def _haiku_map_stocks(client, model, catalyst: dict, keyword_results: list[dict]) -> list[dict]:
    """Layer 3: LLM 直接映射标的（仅 Top 5 催化）"""
    mm = _load_multi_map()
    if not mm:
        return keyword_results

    # 准备概念摘要（只发相关概念，不发全量）
    suggested = catalyst.get("suggested_concepts", [])
    related_concepts = set(suggested)
    for kr in keyword_results[:20]:
        related_concepts.add(kr["concept"])

    concept_summary = []
    for c in list(related_concepts)[:15]:
        stocks = [code for code, concepts in mm["stocks"].items() if c in concepts]
        concept_summary.append(f"- {c}: {len(stocks)}只标的 (如{', '.join(stocks[:5])})")

    prompt = f"""你是A股概念板块专家。给定一个催化事件，找出所有可能受益的A股标的。

催化: {catalyst.get('catalyst_name')}
逻辑: {catalyst.get('thesis', '')[:300]}
类型: {catalyst.get('catalyst_type')}

相关概念板块及标的:
{chr(10).join(concept_summary)}

已有关键词匹配结果:
{[f"{r['code']}({r['concept']})" for r in keyword_results[:15]]}

请确认、修正、补充标的映射。输出JSON:
[{{"code": "6位代码", "name": "股票简称", "concept": "匹配概念",
   "relevance": "具体关联逻辑(为什么利好)", "confidence": "high/medium/low"}}]

只输出JSON数组。"""

    try:
        resp = client.messages.create(
            model=model, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        mapped = _parse_json(text)
        if isinstance(mapped, list):
            for m in mapped:
                m["method"] = "llm_direct"
            return mapped
    except Exception as e:
        print(f"  [WARN] Haiku stock map failed: {e}")

    return keyword_results

# ============================================================
# 报告生成
# ============================================================
def _generate_report(catalysts: list[dict], stock_maps: dict, today: str) -> Path:
    """生成 Markdown 报告"""
    L = []
    def w(s=""): L.append(s)

    w(f"# 催化快速筛查 {today}")
    w()
    w(f"> 生成时间: {today} | 扫描 4 源 | 提取 {len(catalysts)} 条催化")
    w()

    for cat in catalysts:
        score = cat.get("final_actionability", 0)
        label = _actionability_label(score)
        if label in ("NOISE",):
            continue

        emoji = {"CRITICAL": "RED", "HIGH": "HIGH", "MEDIUM": "YELLOW"}.get(label, "LOW")
        sources = cat.get("merged_from", [])
        source_tag = f"{len(sources)}源确认({'+'.join(sources)})" if len(sources) > 1 else sources[0] if sources else "?"

        w(f"## [{emoji}] #{cat.get('rank','?')} {cat.get('catalyst_name','?')} [{cat.get('catalyst_type','?')}]")
        w(f"- **行动性**: {score}/80 ({label})")
        w(f"- **核心逻辑**: {cat.get('thesis','')}")
        pd = cat.get("price_data", {}) or {}
        if pd.get("from") or pd.get("to"):
            w(f"- **价格变化**: {pd.get('from','?')} → {pd.get('to','?')} {pd.get('unit','')}" +
              (f" ({pd['change_pct']:+.0f}%)" if pd.get("change_pct") else ""))
        w(f"- **M={cat.get('magnitude',0)} S={cat.get('specificity',0)} N={cat.get('novelty',0)} U={cat.get('urgency',0)}")
        w(f"- **时效**: {cat.get('time_horizon','?')} | **来源**: {source_tag}")
        w(f"- **建议概念**: {', '.join(cat.get('suggested_concepts',[]))}")
        w(f"- **验证**: {cat.get('validation_note','')}")

        mapped = stock_maps.get(cat.get("catalyst_name", ""), [])
        if mapped:
            w()
            w(f"| 代码 | 名称 | 概念 | 关联逻辑 | 置信度 |")
            w(f"|------|------|------|----------|--------|")
            for m in mapped[:10]:
                w(f"| {m.get('code','?')} | {m.get('name','?')} | {m.get('concept','?')} | {m.get('relevance','?')} | {m.get('confidence','?')} |")
        w()

    # Summary
    w("---")
    json_cat = catalysts[0] if catalysts else {}
    w(f"\n**今日综判**: {json_cat.get('summary','') or '（无高行动性催化）'}")
    w(f"\n**盘面影响**: {json_cat.get('market_implication','') or '—'}")

    out = CATALYST_DIR / f"catalyst_screen_{today}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out

def _save_json(catalysts: list[dict], stock_maps: dict, today: str):
    out = CATALYST_DIR / f"catalyst_screen_{today}.json"
    data = {"date": today, "catalysts": catalysts, "stock_maps": stock_maps}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ============================================================
# Main
# ============================================================
def main(today_str: str, phase: int = 12, max_per_source: int = 50):
    print(f"[catalyst_screen] {today_str}")

    sources = _load_sources(today_str)
    # 截断每源条数
    for src in list(sources.keys()):
        if len(sources[src]) > max_per_source:
            sources[src] = sources[src][:max_per_source]
    total_items = sum(len(v) for v in sources.values())
    if total_items == 0:
        print("  [SKIP] 无信息源数据")
        return

    print(f"  信息源: {', '.join(f'{k}({len(v)}条)' for k, v in sources.items())}")

    # --- Phase 1: Haiku 提取（4源并行）---
    print("  [Phase 1] Haiku 催化提取...")
    scan_client, scan_model = get_client("scan", timeout=60), get_model("scan")
    all_extractions = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for src, items in sources.items():
            if items:
                futures[src] = pool.submit(_haiku_extract, scan_client, scan_model, items, src)
        for src, fut in futures.items():
            try:
                extracted = fut.result()
                all_extractions.extend(extracted)
                print(f"    {src}: {len(extracted)}条催化")
            except Exception as e:
                print(f"    {src}: FAILED ({e})")

    if not all_extractions:
        print("  [SKIP] 未提取到催化事件")
        return

    # --- 计算单源行动性（用于 Phase 2 参考）---
    for ex in all_extractions:
        ex["single_score"] = _compute_actionability(ex, 1)

    # --- Phase 2: Sonnet 验证（仅在 phase=12 或 phase=2 时）---
    if phase >= 2:
        print("  [Phase 2] Sonnet 交叉验证...")
        deep_client, deep_model = get_client("synthesis", timeout=120), get_model("synthesis")
        validated = _sonnet_validate(deep_client, deep_model, all_extractions)
        catalysts = validated.get("ranked_catalysts", [])
        print(f"    验证后保留: {len(catalysts)}条")

        # 重新计算行动性（含多源加成分）
        for cat in catalysts:
            cat["final_actionability"] = _compute_actionability(
                cat, cat.get("source_count", 1))
    else:
        # Phase 1 only: 直接用单源结果
        catalysts = all_extractions
        for cat in catalysts:
            cat["rank"] = 0
            cat["final_actionability"] = cat.get("single_score", 0)
            cat["merged_from"] = [cat.get("source_type", "?")]
            cat["source_count"] = 1
            cat["suggested_concepts"] = []
            cat["validation_note"] = "未验证"
            cat["time_horizon"] = cat.get("time_horizon", "?")
            cat["magnitude"] = cat.get("magnitude_score", 0)
            cat["specificity"] = cat.get("specificity_score", 0)
            cat["novelty"] = cat.get("novelty_score", 0)
            cat["urgency"] = cat.get("urgency_score", 0)

    # 筛选: actionability >= 20
    catalysts = [c for c in catalysts if c.get("final_actionability", 0) >= 20]
    catalysts.sort(key=lambda x: -x.get("final_actionability", 0))
    for i, cat in enumerate(catalysts):
        cat["rank"] = i + 1

    print(f"    最终筛选后(>=20分): {len(catalysts)}条")
    for c in catalysts[:5]:
        print(f"      #{c.get('rank')} {c.get('catalyst_name','?')} 行动性={c.get('final_actionability',0)}")

    # --- Phase 3: 标的映射（仅 Top 5）---
    stock_maps = {}
    if phase >= 2:
        print("  [Phase 3] 标的映射...")
        for cat in catalysts[:TOP_CATALYSTS_FOR_MAPPING]:
            name = cat.get("catalyst_name", "")
            kw = _keyword_match_stocks(name, cat.get("key_entities", []))
            full = _haiku_map_stocks(scan_client, scan_model, cat, kw)
            stock_maps[name] = full
            print(f"    {name}: {len(full)}只标的")

    # --- 入库 ---
    try:
        from store import save_catalyst_signals, save_catalyst_stock_map, init_db
        init_db()
        db_signals = []
        for c in catalysts:
            merged_from = c.get("merged_from", [])
            db_signals.append({
                "date": today_str,
                "signal_id": hashlib.md5(
                    f"{today_str}:{c.get('catalyst_name','')}".encode()).hexdigest()[:16],
                "source_type": merged_from[0] if merged_from else "?",
                "catalyst_name": c.get("catalyst_name", ""),
                "catalyst_type": c.get("catalyst_type", ""),
                "magnitude_score": c.get("magnitude", c.get("magnitude_score", 0)),
                "specificity_score": c.get("specificity", c.get("specificity_score", 0)),
                "novelty_score": c.get("novelty", c.get("novelty_score", 0)),
                "urgency_score": c.get("urgency", c.get("urgency_score", 0)),
                "actionability": c.get("final_actionability", 0),
                "source_count": c.get("source_count", 1),
                "price_data": c.get("price_data", {}),
                "key_entities": c.get("key_entities", []),
                "suggested_concepts": c.get("suggested_concepts", []),
                "merged_from": merged_from,
                "thesis": c.get("thesis", ""),
                "time_horizon": c.get("time_horizon", "?"),
                "mentioned_codes": c.get("mentioned_codes", []),
                "validation_note": c.get("validation_note", ""),
                "sonnet_validated": 0,
                "price_confirmed": 0,
            })
        save_catalyst_signals(db_signals)
        print(f"  DB: {len(db_signals)}条催化入库")

        db_maps = []
        for cname, mapped in stock_maps.items():
            for m in mapped:
                db_maps.append({
                    "date": today_str, "catalyst_name": cname,
                    "stock_code": m.get("code", ""), "stock_name": m.get("name", ""),
                    "mapping_method": m.get("method", m.get("mapping_method", "?")),
                    "matched_concept": m.get("concept", ""),
                    "relevance": m.get("relevance", ""),
                    "confidence": m.get("confidence", "medium"),
                })
        save_catalyst_stock_map(db_maps)
        print(f"  DB: {len(db_maps)}条标的映射入库")
    except Exception as e:
        print(f"  [WARN] DB save failed: {e}")

    # --- 生成报告 ---
    report_path = _generate_report(catalysts, stock_maps, today_str)
    _save_json(catalysts, stock_maps, today_str)
    print(f"  报告: {report_path}")

    # 输出摘要
    critical = [c for c in catalysts if c.get("final_actionability", 0) >= 60]
    if critical:
        print(f"\n  RED FLAG ({len(critical)} critical):")
        for c in critical:
            name = c.get('catalyst_name', '?').encode('ascii', 'replace').decode('ascii')
            print(f"    {name} [{c.get('final_actionability')}分]")
            mapped = stock_maps.get(c.get("catalyst_name", ""), [])
            if mapped:
                print(f"      stocks: {', '.join(m['code'] for m in mapped[:5])}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", "-d", type=str, default=date.today().isoformat())
    parser.add_argument("--phase", type=int, default=2, help="1=仅提取, 2=提取+验证+映射")
    parser.add_argument("--max-per-source", type=int, default=50,
                        help="每源最多处理条数(防Haiku过多调用)")
    args = parser.parse_args()
    main(args.date, args.phase, args.max_per_source)
