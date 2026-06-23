"""深度投研洞见 结构化提取器。

从每篇文章中提取：
  预期差五条、承重判断、证伪清单、估值分层、久期光谱、筹码异常、标的映射

输出结构化 JSON，可直接注入 Serenity KB + advice ChokeMap/5W。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TIMEOUT = 120
MAX_TOKENS = 4000
MODEL = os.getenv("DR_LLM_MODEL", "claude-sonnet-4-6-20250514")

_EXTRACT_PROMPT = """你是 A 股基本面分析师。从以下投资报告中提取结构化信息。

报告正文:
{body}

输出 JSON（严格遵守此 schema，勿编造、勿添加字段）：

```json
{{
  "thesis": "核心论点（一句话，引用原文关键判断）",
  "variant_perceptions": [
    {{
      "id": 1,
      "consensus": "市场共识是什么",
      "variant": "本文的不同判断是什么",
      "confidence": "高/中/低",
      "falsification": "如果什么条件成立，这个预期差被证伪"
    }}
  ],
  "load_bearing_judgment": "全篇最承重的单一判断（错了全文逻辑崩塌的那条）",
  "valuation_spectrum": [
    {{
      "tier": "核心仓/弹性层/规避",
      "codes": ["代码1", "代码2"],
      "names": ["名称1", "名称2"],
      "pe_range": "PE区间",
      "logic": "一句话逻辑",
      "chain_segment": "所属产业链环节"
    }}
  ],
  "duration_spectrum": [
    {{
      "duration": "短期/中期/长期",
      "years": "2026/2027/2029+",
      "theme": "主题名",
      "note": "一句话说明为什么是这个久期"
    }}
  ],
  "risk_signals": [
    {{
      "type": "内部人减持/拥挤度过高/估值透支/技术替代/政策反转/业绩不达预期",
      "target": "涉及标的或主题",
      "detail": "一句话说明"
    }}
  ],
  "chains_involved": ["产业链1", "产业链2"],
  "themes": ["主题关键词1", "主题关键词2"],
  "serenity_inject": {{
    "chains_to_update": ["需更新标的映射的产业链"],
    "expectation_gap_signals": [
      {{
        "chain_segment": "产业链环节",
        "gap_type": "高预期差/走势先行/三重共振",
        "detail": "一句话说明"
      }}
    ]
  }},
  "advice_inject": {{
    "chokemap_signals": ["可注入 ChokeMap 的边际变化信号"],
    "w5_risks": ["可注入 W5 风险排雷的新风险"],
    "w3_gaps": ["可注入 W3 预期差的新预期差"]
  }}
}}
```

规则:
1. 所有代码必须是6位数字。如果原文只写名称，从上下文中推断代码。不确定就留空 codes 数组。
2. variant_perceptions 至少提取 3 条，最多 7 条。
3. valuation_spectrum 按 tier 分三组：核心仓（业绩兑现+估值合理）、弹性层（逻辑对但贵/未兑现）、规避（因果错配/伪相关）。
4. risk_signals 只写原文明确提到的，不要编造。
5. 如果某个字段原文确实没有，填空数组 [] 或空字符串 ""。
6. 只输出 JSON，不要输出任何解释文字。"""


def _get_client():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from llm import _load_api_key
    from anthropic import Anthropic
    return Anthropic(api_key=_load_api_key(), timeout=TIMEOUT)


def extract(body: str, title: str = "", date_str: str = "") -> dict | None:
    """从一篇文章中提取结构化信息。

    Returns:
        dict with all extracted fields, or None if extraction fails.
    """
    if len(body) < 500:
        return None

    client = _get_client()
    prompt = _EXTRACT_PROMPT.format(body=body[:6000])  # 6000字上限，防token溢出

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                timeout=TIMEOUT,
            )
            parts = [block.text for block in resp.content
                     if hasattr(block, "text") and block.text]
            text = "\n".join(parts)
            break
        except Exception as e:
            if attempt == 2:
                print(f"  [深度投研] LLM 调用失败: {e}")
                return None
            continue

    # Parse JSON — 尝试多种匹配模式
    data = None
    # 模式1: ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 模式2: 直接 { ... }
    if data is None:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    # 模式3: 从第一个 { 到最后一个 }，容错尾部逗号
    if data is None:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            raw = text[start:end + 1]
            # 修复常见 JSON 格式问题
            raw = re.sub(r",\s*([}\]])", r"\1", raw)  # 尾部逗号
            raw = re.sub(r"(\d+)\.\s*([}\]])", r"\1\2", raw)  # 数字后跟 . 而非 ,
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                pass
    if data is None:
        print(f"  [深度投研] JSON 解析失败, text[:200]: {text[:200]}")
        return None

    data["title"] = title
    data["date"] = date_str
    return data


def inject_to_serenity(data: dict) -> bool:
    """将提取结果注入 Serenity 体系。

    写 JSON 到 reports/serenity/shendu/，供 advice/ChokeMap 引用。
    """
    if not data:
        return False

    try:
        out_dir = Path(__file__).resolve().parent.parent / "reports" / "serenity" / "shendu"
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = data.get("date", "unknown")
        out_path = out_dir / f"shendu_{date_str}.json"
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        chains = data.get("chains_involved", [])
        vps = data.get("variant_perceptions", [])
        print(f"  [深度投研→Serenity] {out_path.name}: "
              f"{len(chains)}链, {len(vps)}预期差, {len(data.get('risk_signals',[]))}风险")
        return True
    except Exception as e:
        print(f"  [深度投研→Serenity] 写入失败: {e}")
        return False


def format_for_advice(data: dict) -> str:
    """将提取结果格式化为可注入 advice prompt 的文本。"""
    if not data:
        return ""

    lines = [f"## 深度投研洞见: {data.get('title', '')}", ""]

    thesis = data.get("thesis", "")
    if thesis:
        lines.append(f"**核心论点**: {thesis}")
        lines.append("")

    vps = data.get("variant_perceptions", [])
    if vps:
        lines.append("### 预期差")
        for vp in vps:
            lines.append(f"- **{vp.get('consensus','')}** → {vp.get('variant','')} "
                         f"(置信度:{vp.get('confidence','')})")
            if vp.get("falsification"):
                lines.append(f"  证伪条件: {vp['falsification']}")
        lines.append("")

    risks = data.get("risk_signals", [])
    if risks:
        lines.append("### 风险信号")
        for r in risks:
            lines.append(f"- [{r.get('type','')}] {r.get('target','')}: {r.get('detail','')}")
        lines.append("")

    vs = data.get("valuation_spectrum", [])
    if vs:
        lines.append("### 估值分层")
        for v in vs:
            names = ", ".join(v.get("names", []) or [])
            lines.append(f"- **{v.get('tier','')}**: {names} "
                         f"(PE {v.get('pe_range','')}) - {v.get('logic','')}")
        lines.append("")

    lbj = data.get("load_bearing_judgment", "")
    if lbj:
        lines.append(f"**承重判断**: {lbj}")
        lines.append("")

    return "\n".join(lines)
