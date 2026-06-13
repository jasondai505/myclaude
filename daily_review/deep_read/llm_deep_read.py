"""Stage 2：LLM 精读 — Haiku 结构化提取 + Sonnet 五维评分。

Haiku: 从公告原文提取结构化数据（金额/利润承诺/PE/利益关系）
Sonnet: 五维评分（核心矛盾/信息增量/利益一致性/治理信号/场景校准）
"""
from __future__ import annotations

import json
import re

_HAIKU_EXTRACT_PROMPT = """你是A股公告信息提取专家。从以下公告中提取**客观的结构化数据**。

只提取事实，不判断投资价值。提取不到就填 null，不要编造。

公告全文：
{announcement_text}

股票信息：
代码: {code}  名称: {name}

返回 JSON（只返回 JSON，不要其他内容）：
{{
  "transaction_summary": "交易摘要，1-2句话",
  "transaction_amount_yi": "交易金额（亿元），数字",
  "payment_method": "现金/换股/混合/不适用",
  "target_business": "标的公司主营业务（1句话）",
  "target_profit_commitment_first_year_yi": "标的首年承诺净利润（亿元），数字或null",
  "target_profit_commitment_three_year": "三年累计承诺净利润（亿元），数字或null",
  "acquisition_pe": "收购PE（交易对价/首年承诺净利），数字或null",
  "buyer_seller_relation": "买方与卖方关系：关联方/第三方/含关联方",
  "cross_industry_flag": true/false,
  "acquirer_main_business": "收购方当前主营业务（1句话）",
  "key_quantitative_data": "其他关键定量数据（市占率、客户集中度、毛利率等），列表或null",
  "insider_actions": "公告中提及的内部人行为（增持/减持/认购定增等），列表或null",
  "governance_notes": "公告中提及的治理相关事项（问询函/处罚/诉讼/审计意见），列表或null",
  "key_excerpts": "公告关键段落原文摘录（最长500字）"
}}"""

_SONNET_SCORE_PROMPT = """你是A股投研分析师。对以下公告进行五维深度评分。

## 公告信息
- 股票: {name}（{code}）
- 公告标题: {ann_title}
- 公告类型: {ann_type}

## Haiku 提取的结构化数据
{haiku_extraction}

## 股票基本面
{stock_context}

## 卡脖子环节上下文
{chokepoint_context}

## 五维评分框架

### 1. 核心矛盾（满分40）
是否触及企业长期自由现金流创造能力？
- 35-40: 第二增长曲线确认、护城河质变、致命风险解除
- 25-34: 显著的竞争优势变化，但确定性有待验证
- 15-24: 有正面影响但非结构性
- 0-14: 一次性损益、纸面富贵、无关紧要

### 2. 信息增量（满分30）
公告提供了多少「此前不可知」的定量数据？
- 25-30: 首次披露关键经营数据（标的利润/市占率），消除重大不确定性
- 18-24: 提供了新的定量信息，但部分关键数据仍缺失
- 10-17: 有一定增量但以定性描述为主
- 0-9: 复述已知信息、空洞描述

### 3. 利益一致性（满分15）
内部人是共同承担风险还是在收割？
- 13-15: 大股东全额认购定增/实控人大额增持/回购注销
- 9-12: 利益格局中性，无明显的正向或负向信号
- 5-8: 存在小股东利益摊薄风险
- 0-4: 一边回购一边减持/低价给关联方增发/明显收割

### 4. 治理信号（满分10）
公告本身传递的管理层画像。
- 8-10: 坦诚认错+量化改进措施/问询函回复具体且有力
- 5-7: 中性，措辞标准
- 2-4: 推诿外部因素/避重就轻
- 0-1: 百般辩解/疑似误导性陈述

### 5. 场景校准（满分5）
当下时点这个公告的重要性。
- 5: 精准命中市场主线+产业链关键环节，时机完美
- 3-4: 方向正确但不在最佳窗口
- 1-2: 与市场当前关注点偏差较大
- 0: 与市场方向背道而驰

## 输出格式
返回 JSON（只返回 JSON，不要其他内容）：
{{
  "core_contradiction_score": 0-40,
  "core_contradiction_thesis": "核心矛盾分析，2-3句",
  "info_delta_score": 0-30,
  "info_delta_details": "信息增量分析，2-3句",
  "interest_alignment_score": 0-15,
  "interest_alignment_analysis": "利益格局分析，2-3句",
  "governance_signal_score": 0-10,
  "governance_signal_details": "治理信号分析，2-3句",
  "scenario_calibration_score": 0-5,
  "scenario_calibration_rationale": "场景校准分析，2-3句",
  "total_score": 0-100,
  "investment_thesis": "投资论述（200-300字），聚焦：这个公告在卡脖子环节中的位置，以及多久可能被市场定价",
  "time_horizon": "immediate/week/month/quarter",
  "risk_factors": ["具体风险1", "具体风险2", ...],
  "comparable_precedents": "历史可比案例简述，1-2句"
}}"""


def _parse_json(text: str):
    """从 LLM 输出中提取 JSON。"""
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


def _build_stock_context(code: str, name: str) -> str:
    """构建股票基本面上下文（用于 Sonnet 评分）。"""
    try:
        import store as _store
        with _store._conn() as conn:
            row = conn.execute(
                "SELECT roe, gross_margin, net_margin, debt_ratio, "
                "revenue_yoy, profit_yoy, report_date "
                "FROM financial_indicators WHERE code = ? "
                "ORDER BY report_date DESC LIMIT 1",
                (str(code).zfill(6),),
            ).fetchone()
        if row:
            return (
                f"最新财报({row['report_date']}): ROE={row['roe']}%, "
                f"毛利率={row['gross_margin']}%, 净利率={row['net_margin']}%, "
                f"负债率={row['debt_ratio']}%, 营收同比={row['revenue_yoy']}%, "
                f"净利同比={row['profit_yoy']}%"
            )
    except Exception:
        pass
    return "（财务数据暂无）"


def _build_chokepoint_text(ctx: dict) -> str:
    """构建卡脖子环节上下文文本。"""
    if not ctx:
        return "（无特定卡脖子环节关联）"
    parts = []
    if ctx.get("domains"):
        parts.append(f"猎场领域: {', '.join(ctx['domains'])}")
    if ctx.get("chokepoints"):
        for cp in ctx["chokepoints"]:
            parts.append(
                f"卡脖子环节: {cp['label']} "
                f"(匹配关键词: {', '.join(cp.get('matched_keywords', []))})"
            )
    if ctx.get("concepts"):
        parts.append(f"相关概念板块: {', '.join(ctx['concepts'])}")
    return "\n".join(parts) if parts else "（无特定卡脖子环节关联）"


def _haiku_extract_structured(ann: dict, client, model) -> dict:
    """Haiku 第一阶段：从公告原文提取结构化数据。"""
    full_text = ann.get("ann_full_text", ann.get("title", ""))
    if len(full_text) > 4000:
        full_text = full_text[:2000] + "\n...(中略)...\n" + full_text[-2000:]

    prompt = _HAIKU_EXTRACT_PROMPT.format(
        announcement_text=full_text,
        code=ann.get("code", ""),
        name=ann.get("name", ""),
    )

    try:
        resp = client.messages.create(
            model=model, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return _parse_json(text)
    except Exception as e:
        print(f"  [WARN] Haiku extract failed for {ann.get('code')}: {e}")
        return {}


def _sonnet_score_dimensions(ann: dict, haiku_result: dict,
                             stock_context: str, chokepoint_context: str,
                             client, model) -> dict:
    """Sonnet 第二阶段：五维评分。"""
    prompt = _SONNET_SCORE_PROMPT.format(
        name=ann.get("name", ""),
        code=ann.get("code", ""),
        ann_title=ann.get("ann_title", ann.get("title", "")),
        ann_type=ann.get("ann_type", ann.get("type", "")),
        haiku_extraction=json.dumps(haiku_result, ensure_ascii=False, indent=2),
        stock_context=stock_context,
        chokepoint_context=chokepoint_context,
    )

    try:
        resp = client.messages.create(
            model=model, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        result = _parse_json(text)
        # 确保 total_score 是五个维度之和
        dims = [
            result.get("core_contradiction_score", 0),
            result.get("info_delta_score", 0),
            result.get("interest_alignment_score", 0),
            result.get("governance_signal_score", 0),
            result.get("scenario_calibration_score", 0),
        ]
        if not result.get("total_score") or result["total_score"] < sum(dims) * 0.5:
            result["total_score"] = sum(dims)
        return result
    except Exception as e:
        print(f"  [WARN] Sonnet score failed for {ann.get('code')}: {e}")
        return {}


def deep_read_announcement(ann: dict) -> dict | None:
    """对单条公告执行完整的两阶段 LLM 精读。

    返回: 五维评分结果 dict，失败返回 None。
          包含 haiku_extraction 和 sonnet_scoring 原始 JSON。
    """
    from roles import get_client, get_model

    haiku_client = get_client("scan", timeout=60)
    haiku_model = get_model("scan")
    sonnet_client = get_client("deep", timeout=90)
    sonnet_model = get_model("deep")

    stock_context = _build_stock_context(ann["code"], ann.get("name", ""))
    chokepoint_context = _build_chokepoint_text(ann.get("chokepoint_context", {}))

    haiku_result = _haiku_extract_structured(ann, haiku_client, haiku_model)
    if not haiku_result:
        return None

    sonnet_result = _sonnet_score_dimensions(
        ann, haiku_result, stock_context, chokepoint_context,
        sonnet_client, sonnet_model,
    )
    if not sonnet_result:
        return None

    return {
        **sonnet_result,
        "haiku_extraction": json.dumps(haiku_result, ensure_ascii=False),
        "sonnet_scoring": json.dumps(sonnet_result, ensure_ascii=False),
    }


def deep_read_batch(announcements: list[dict]) -> list[dict]:
    """批量精读多条公告。返回每条的结果列表（含原始公告字段+评分字段）。"""
    results = []
    for ann in announcements:
        print(f"  [deep_read] 正在精读: {ann.get('code')} {ann.get('name')} — {ann.get('ann_title', '')[:40]}...")
        scored = deep_read_announcement(ann)
        if scored:
            from config import DEEP_READ_RULES
            min_score = DEEP_READ_RULES.get("min_deep_read_score", 60)
            scored["passed_threshold"] = scored.get("total_score", 0) >= min_score
            results.append({**ann, **scored})
        else:
            print(f"  [deep_read] 精读失败，跳过: {ann.get('code')}")
    return results
