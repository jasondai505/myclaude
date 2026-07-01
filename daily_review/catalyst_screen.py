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

sys.path.insert(0, str(Path(__file__).parent.parent))
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

BATCH_SIZE = 15
TOP_CATALYSTS_FOR_MAPPING = 5

# ============================================================
# 数据加载
# ============================================================
MAX_CONCEPT_COVERAGE = 0.05  # 覆盖率>5%的概念视为噪音，排除

_filtered_map_cache = None

def _load_multi_map():
    global _filtered_map_cache
    if _filtered_map_cache is not None:
        return _filtered_map_cache
    if not MULTI_MAP_PATH.exists():
        return None
    mm = json.loads(MULTI_MAP_PATH.read_text(encoding="utf-8"))
    _filtered_map_cache = _filter_noisy_concepts(mm)
    return _filtered_map_cache

def _filter_noisy_concepts(mm: dict) -> dict:
    """排除覆盖率>5%的噪音概念（如由帖子共现自动生成的伪概念）"""
    stocks = mm.get("stocks", {})
    if not stocks:
        return mm
    total = len(stocks)
    # 计算每个概念的标的数
    concept_counts = defaultdict(int)
    for concepts in stocks.values():
        for c in concepts:
            concept_counts[c] += 1
    noisy = {c for c, cnt in concept_counts.items()
             if cnt / total > MAX_CONCEPT_COVERAGE}
    if noisy:
        filtered = {}
        for code, concepts in stocks.items():
            clean = [c for c in concepts if c not in noisy]
            if clean:
                filtered[code] = clean
        mm = {**mm, "stocks": filtered}
        names = sorted(noisy, key=lambda c: -concept_counts[c])[:8]
        print(f"  [L0] 过滤 {len(noisy)} 个噪音概念 ({total}→{len(filtered)}标的): "
              f"{', '.join(f'{n}({concept_counts[n]})' for n in names)}")
    return mm

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

提取以下类型的催化（⚠️ supply_shock/price_spike 优先级最高，最容易漏检，仔细扫描每一条）：
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

        # 注入名称→代码速查
        import data as _cd
        nm = _cd._load_name_to_code_map()
        name_hints = []
        if nm:
            seen = set()
            for m in re.finditer(r"[一-鿿]{2,6}", batch_text):
                n = m.group()
                c = nm.get(n)
                if c and n not in seen:
                    seen.add(n)
                    name_hints.append(f"{n}={c}")
        hint_text = "\n".join(name_hints) if name_hints else ""
        prompt = _EXTRACT_PROMPT + "\n来源: " + source_type
        if hint_text:
            prompt += f"\n\n名称→代码速查（帖子中出现的股票名称对应6位代码）:\n{hint_text}"
        prompt += "\n\n" + batch_text

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

def _validate_stock_mapping(mapping: dict, catalyst: dict) -> dict:
    """校验 LLM 输出的标的映射是否合理。行业不匹配+F EV=0 → rejected"""
    code = mapping.get("code", "")
    if not code:
        mapping["confidence"] = "rejected"
        mapping["reject_reason"] = "无代码"
        return mapping

    checks = []
    # 1. 行业一致性: 标的主概念 vs 催化关键实体
    try:
        from config import STOCK_PRIMARY_CONCEPT
        stock_concept = STOCK_PRIMARY_CONCEPT.get(code, "")
        key_entities = {e.get("name", "").lower() for e in
                        catalyst.get("key_entities", [])}
        suggested = {c.lower() for c in catalyst.get("suggested_concepts", [])}
        all_catalyst_terms = key_entities | suggested
        # 检查标的主概念是否与催化术语有交集
        if stock_concept and all_catalyst_terms:
            overlap = any(
                t in stock_concept or stock_concept in t
                for t in all_catalyst_terms
            )
            if not overlap:
                checks.append(f"行业无交集({stock_concept})")
    except ImportError:
        pass

    # 2. FEV 校验
    try:
        import sqlite3
        db = Path(__file__).parent / "data" / "serenity.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            fev = conn.execute(
                "SELECT fev_total FROM feval_scores "
                "WHERE code=? AND date=(SELECT MAX(date) FROM feval_scores)",
                (code,)
            ).fetchone()
            conn.close()
            fev_val = fev[0] if fev else 0
            if fev_val == 0:
                checks.append("无FEV")
    except Exception:
        pass

    # 3. 名称校验: 标的名称是否含有催化关键词
    name = mapping.get("name", "").lower()
    catalyst_name = catalyst.get("catalyst_name", "").lower()
    name_keywords = set(catalyst_name.replace("、", " ").replace("，", " ").split())
    name_match = any(kw in name for kw in name_keywords if len(kw) >= 2)

    if not name_match and len([c for c in checks if "行业" in c]) > 0 and any("无FEV" in c for c in checks):
        mapping["confidence"] = "rejected"
        mapping["reject_reason"] = "; ".join(checks)
    elif not name_match:
        mapping["confidence"] = "low"
        mapping["_warning"] = "; ".join(checks) if checks else "名称无关键词匹配"

    return mapping
def _keyword_match_stocks(catalyst_name: str, key_entities: list[dict]) -> list[dict]:
    """Layer 1: 关键词匹配 multi_concept_map 中的概念→反查股票"""
    mm = _load_multi_map()
    if not mm:
        return []

    search_terms = [catalyst_name.lower()] + [
        e["name"].lower() for e in (key_entities or [])
    ]

    # 关键词→规范概念名扩展
    try:
        from daily_review.data import map_keyword_to_concepts
        expanded = list(search_terms)
        for t in search_terms:
            for c in map_keyword_to_concepts(t):
                if c.lower() not in expanded:
                    expanded.append(c.lower())
        search_terms = expanded
    except ImportError:
        pass

    concept_stocks = defaultdict(list)
    for code, concepts in mm["stocks"].items():
        for c in concepts:
            concept_stocks[c].append(code)

    matched = []
    seen = set()
    code_hits: dict[str, int] = {}
    for term in search_terms:
        for concept, codes in concept_stocks.items():
            if term in concept.lower():
                for code in codes:
                    if code not in seen:
                        seen.add(code)
                        code_hits[code] = 1
                        matched.append({
                            "code": code, "concept": concept,
                            "method": "keyword_match", "confidence": "medium",
                        })
                    else:
                        code_hits[code] = code_hits.get(code, 0) + 1

    # 按命中关键词数降序，多词命中=更相关
    matched.sort(key=lambda m: -code_hits.get(m["code"], 0))
    return matched[:30]  # 上限30只，避免噪音

def _build_stock_context(codes: set[str]) -> dict[str, str]:
    """为标的列表构建防幻觉上下文: code → '名称 | 主业:xxx | FEV:N'"""
    if not codes:
        return {}
    # 名称映射
    try:
        import data
        name_map = data._load_name_to_code_map()
    except Exception:
        name_map = {}
    code_to_name = {v: k for k, v in name_map.items()}
    # 主概念
    try:
        from config import STOCK_PRIMARY_CONCEPT
    except ImportError:
        STOCK_PRIMARY_CONCEPT = {}
    # FEV
    fev_map = {}
    try:
        import sqlite3
        db = Path(__file__).parent / "data" / "serenity.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT code, fev_total FROM feval_scores "
                "WHERE date=(SELECT MAX(date) FROM feval_scores)"
            ).fetchall()
            fev_map = {r["code"]: r["fev_total"] for r in rows}
            conn.close()
    except Exception:
        pass

    ctx = {}
    for code in codes:
        name = code_to_name.get(code, "?")
        industry = STOCK_PRIMARY_CONCEPT.get(code, "?")
        fev = fev_map.get(code, 0)
        ctx[code] = f"{name} | 主业:{industry} | FEV:{fev}"
    return ctx


def _haiku_map_stocks(client, model, catalyst: dict, keyword_results: list[dict]) -> list[dict]:
    """Layer 3: LLM 直接映射标的（仅 Top 5 催化），注入标的上下文防幻觉"""
    mm = _load_multi_map()
    if not mm:
        return keyword_results

    # 构建候选标的集合并注入上下文
    candidate_codes = {r["code"] for r in keyword_results[:30]}
    stock_ctx = _build_stock_context(candidate_codes)

    # 准备概念摘要
    suggested = catalyst.get("suggested_concepts", [])
    related_concepts = set(suggested)
    for kr in keyword_results[:20]:
        related_concepts.add(kr["concept"])

    concept_summary = []
    for c in list(related_concepts)[:10]:
        stocks = [code for code, concepts in mm["stocks"].items() if c in concepts][:5]
        concept_summary.append(f"- {c}: {', '.join(stocks)}")

    # 带上下文的候选清单
    ctx_lines = []
    for r in keyword_results[:20]:
        code = r["code"]
        ctx_lines.append(
            f"{code} {stock_ctx.get(code, '?')} [{r['concept']}]"
        )
    ctx_text = "\n".join(ctx_lines)

    prompt = f"""你是A股概念板块专家。给定一个催化事件，从候选标的中筛选真正受益的标的。

催化: {catalyst.get('catalyst_name')}
逻辑: {catalyst.get('thesis', '')[:300]}
类型: {catalyst.get('catalyst_type')}

候选标的（代码 名称 | 主业 | FEV评分 [匹配概念]）:
{ctx_text}

筛选规则:
1. 主业与催化逻辑无关的标的 → 必须排除
2. FEV=0 且主业不匹配的 → 排除
3. 只保留有真实产业链关联的标的

输出JSON（只输出真正相关的标的，不相关的不要输出）:
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

def _audit_stock_maps(stock_maps: dict) -> dict:
    """审计催化剂→标的映射质量"""
    total = sum(len(v) for v in stock_maps.values())
    if total == 0:
        return {"total_mappings": 0, "llm_direct_pct": 0, "rejected": 0,
                "no_fev": 0, "health": "ok"}
    llm_direct = sum(1 for v in stock_maps.values() for m in v
                     if m.get("method") == "llm_direct")
    rejected = sum(1 for v in stock_maps.values() for m in v
                   if m.get("confidence") == "rejected")
    no_fev = sum(1 for v in stock_maps.values() for m in v
                 if m.get("_warning", "").find("无FEV") >= 0)
    llm_pct = llm_direct / total * 100
    return {
        "total_mappings": total,
        "llm_direct_pct": round(llm_pct),
        "rejected": rejected,
        "no_fev": no_fev,
        "health": "warn" if llm_pct > 30 else "ok",
    }


# ============================================================
# 报告生成
# ============================================================
def _get_chain_ctx(codes: list[str]) -> dict[str, list[str]]:
    from theme_stock import get_chain_context
    return get_chain_context(codes)


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
            # Enrich with chain positions
            codes = [m.get("code", "") for m in mapped if m.get("code")]
            chain_ctx = _get_chain_ctx(codes)

            w()
            w(f"| 代码 | 名称 | 产业链位置 | 概念 | 关联逻辑 | 置信度 |")
            w(f"|------|------|-----------|------|----------|--------|")
            for m in mapped[:10]:
                code = m.get("code", "?")
                chains = chain_ctx.get(code, [])
                chain_str = chains[0] if chains else "—"
                w(f"| {code} | {m.get('name','?')} | {chain_str} | {m.get('concept','?')} | {m.get('relevance','?')} | {m.get('confidence','?')} |")
        w()

    # Summary
    w("---")
    json_cat = catalysts[0] if catalysts else {}
    w(f"\n**今日综判**: {json_cat.get('summary','') or '（无高行动性催化）'}")
    w(f"\n**盘面影响**: {json_cat.get('market_implication','') or '—'}")

    out = CATALYST_DIR / f"catalyst_screen_{today}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out

def _load_valid_code_set() -> set[str]:
    try:
        cache = json.loads((Path(__file__).parent / "data" / "stock_codes.json").read_text(encoding="utf-8"))
        return {c["code"] for c in cache.get("codes", [])}
    except Exception:
        return set()


def _validate_mentioned_codes(catalysts: list[dict]) -> tuple[int, int]:
    """校验并过滤 mentioned_codes 中的无效代码。返回 (过滤前总数, 过滤掉数)。"""
    valid = _load_valid_code_set()
    if not valid:
        return (0, 0)
    before = 0
    removed = 0
    for cat in catalysts:
        codes = cat.get("mentioned_codes", [])
        before += len(codes) if isinstance(codes, list) else 0
        if isinstance(codes, list):
            cat["mentioned_codes"] = [c for c in codes if re.match(r"^\d{6}$", str(c)) and str(c) in valid]
            removed += len(codes) - len(cat["mentioned_codes"])
    return (before, removed)


def _save_json(catalysts: list[dict], stock_maps: dict, today: str, audit: dict = None):
    out = CATALYST_DIR / f"catalyst_screen_{today}.json"
    data = {"date": today, "catalysts": catalysts, "stock_maps": stock_maps,
            "stock_map_audit": audit or {}}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ============================================================
# Chain Segment Tagging (产业链图谱匹配)
# ============================================================

def _load_chain_maps_for_tagging() -> dict:
    """从 chain_map DB 加载产业链映射（替代旧 XLSX 解析）。

    Returns: {plate: {l1: {l2: {"names": set(), "reasons": []}}}}
    """
    try:
        from theme_stock.store import ThemeStockStore
        store = ThemeStockStore()
        store.init_db()
    except ImportError:
        return {}
    chains: dict[str, dict] = {}
    for row in store._get_conn().execute(
        """SELECT DISTINCT industry, tier, segment, name
           FROM chain_map WHERE map_type='chain' AND market='A'
           ORDER BY industry, tier, segment"""
    ).fetchall():
        plate = row["industry"]
        l1 = row["tier"] or ""
        l2 = row["segment"] or "" or "-"
        name = row["name"] or ""
        chains.setdefault(plate, {}).setdefault(l1, {}).setdefault(l2, {"names": set(), "reasons": []})
        chains[plate][l1][l2]["names"].add(name)
    return chains


def _tag_chain_segments(catalysts: list[dict]) -> None:
    """For each catalyst, add chain_segments field matching thesis/entities to chain maps."""
    chains = _load_chain_maps_for_tagging()
    if not chains:
        return

    for cat in catalysts:
        thesis = (cat.get("thesis", "") or "") + " " + (cat.get("catalyst_name", "") or "")
        catalyst_type = cat.get("catalyst_type", "") or ""
        entities = " ".join(e.get("name", "") for e in cat.get("key_entities", []))
        search_text = f"{thesis} {catalyst_type} {entities}"

        matches = []
        for plate, l1_map in chains.items():
            for l1, l2_map in l1_map.items():
                for l2, info in l2_map.items():
                    seg_text = f"{plate} {l1} {l2} " + " ".join(info["names"])
                    # Match: check if any chain keyword appears in catalyst text
                    seg_kws = set()
                    seg_kws.add(l2 if l2 != "-" else l1)
                    for name in info["names"]:
                        seg_kws.add(name)
                    for kw in seg_kws:
                        if len(kw) >= 2 and kw in search_text:
                            tag = f"{l1}>{l2}" if l2 and l2 != "-" else l1
                            if tag not in matches:
                                matches.append(tag)
                            break

        cat["chain_segments"] = matches


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

    b1, r1 = _validate_mentioned_codes(all_extractions)
    if r1:
        print(f"    mentioned_codes校验: {b1}个代码中过滤{r1}个无效")

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

    # --- 产业链图谱标签 ---
    _tag_chain_segments(catalysts)
    tagged = sum(1 for c in catalysts if c.get("chain_segments"))
    print(f"    链标签: {tagged}/{len(catalysts)}条已匹配")

    print(f"    最终筛选后(>=20分): {len(catalysts)}条")
    for c in catalysts[:5]:
        print(f"      #{c.get('rank')} {c.get('catalyst_name','?')} 行动性={c.get('final_actionability',0)}")

    # --- Phase 3: 标的映射（仅 Top 5）---
    stock_maps = {}
    rejected_total = 0
    if phase >= 2:
        print("  [Phase 3] 标的映射...")
        for cat in catalysts[:TOP_CATALYSTS_FOR_MAPPING]:
            name = cat.get("catalyst_name", "")
            kw = _keyword_match_stocks(name, cat.get("key_entities", []))
            full = _haiku_map_stocks(scan_client, scan_model, cat, kw)
            # L2: 逐条校验
            validated = [_validate_stock_mapping(m, cat) for m in full]
            rejected = [m for m in validated if m.get("confidence") == "rejected"]
            kept = [m for m in validated if m.get("confidence") != "rejected"]
            if rejected:
                rejected_total += len(rejected)
                rej_codes = ", ".join(f"{m['code']}({m.get('reject_reason','?')})" for m in rejected[:5])
                print(f"    {name}: {len(kept)}只标的 (L2过滤{len(rejected)}: {rej_codes})")
            else:
                print(f"    {name}: {len(kept)}只标的")
            stock_maps[name] = kept

    # --- L3: 映射质量审计 ---
    audit = _audit_stock_maps(stock_maps)
    print(f"  [L3] 映射质量: {audit['total_mappings']}只, "
          f"llm_direct={audit['llm_direct_pct']:.0f}%, "
          f"rejected={audit['rejected']}, no_fev={audit['no_fev']}, "
          f"健康度={audit['health']}")

    # --- 入库 ---
    try:
        from store import save_catalyst_signals, save_catalyst_stock_map, init_db
        init_db()
        b3, r3 = _validate_mentioned_codes(catalysts)
        if r3:
            print(f"    入库前mentioned_codes校验: {b3}个代码中过滤{r3}个无效")
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
    _save_json(catalysts, stock_maps, today_str, audit)
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
