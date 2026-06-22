"""Serenity 产业链卡脖子分析引擎 — 全球供应链反推 → A股映射 → FE验证。

三层架构：
  1. analyze_global_chain() — 全球供应链卡脖子节点分析
  2. map_to_a_shares()     — A 股映射标的筛选
  3. validate_with_fe()    — FE 框架深度验证（复用现有 FEV 评分）

所有 LLM 调用走数据注入模式：Python 预取数据 → 拼入 prompt → 调 LLM。
任何失败返回空字符串，不让复盘流程 hang 或崩。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent))

MODEL = os.getenv("DR_LLM_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT = 45
MAX_TOKENS = 3000


# ============================================================
# API key
# ============================================================

def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    # Path.home() 在 schtasks 非交互式执行时可能解析到错误路径
    for p in (
        Path.home() / ".claude" / "settings.json",
        Path("C:/Users/daixin/.claude/settings.json"),
        Path(os.environ.get("USERPROFILE", "")) / ".claude" / "settings.json",
    ):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                key = data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
                if key:
                    return key
            except (json.JSONDecodeError, OSError):
                pass
    return ""


# ============================================================
# Data pre-fetch helpers (all return Markdown text for injection)
# ============================================================

def _fetch_stock_data(codes: list[str]) -> str:
    try:
        import data
        quotes = data.fetch_stock_quotes(codes)
    except Exception:
        return "_行情数据暂不可用_"

    lines = ["| 代码 | 名称 | 现价 | 涨跌幅 | PE(TTM) | PB | 市值(亿) |",
             "|------|------|------|--------|---------|-----|----------|"]
    for code in codes:
        q = quotes.get(code, {})
        if not q:
            continue
        lines.append(
            f"| {code} | {q.get('name', code)} | {q.get('price', '-')} | "
            f"{q.get('change_pct', 0):+.2f}% | {q.get('pe_ttm', '-')} | "
            f"{q.get('pb', '-')} | {q.get('mcap_yi', '-')} |"
        )
    return "\n".join(lines)


def _fetch_chain_data(industry: str) -> str:
    try:
        from bom_analyzer import chain_db
        chain_db.init_db()
        result = chain_db.query_industry(industry)
    except Exception:
        return f"_「{industry}」产业链数据暂不可用_"

    if not result.get("segments"):
        return f"_「{industry}」暂无产业链数据_"

    lines = [f"## {industry} 产业链结构", ""]
    for seg in result["segments"]:
        lines.append(f"- **{seg.get('tier', '')} → {seg.get('segment', '')}**")
        if seg.get("description"):
            lines.append(f"  {seg['description']}")
        leaders = seg.get("leaders", [])
        if leaders:
            names = ", ".join(l["stock_name"] for l in leaders[:5])
            lines.append(f"  龙头: {names}")
    return "\n".join(lines)


def _fetch_theme_data() -> str:
    try:
        import data
        heat = data.fetch_concept_heat(top_n=15)
    except Exception:
        return "_题材数据暂不可用_"

    if not heat:
        return "_暂无题材数据_"

    lines = ["| 题材 | 热度 |", "|------|------|"]
    for h in heat[:15]:
        lines.append(f"| {h.get('name', '')} | {h.get('score', '-')} |")
    return "\n".join(lines)


def _fetch_bom_industry_list() -> list[str]:
    try:
        from bom_analyzer import chain_db
        chain_db.init_db()
        return chain_db.list_industries()
    except Exception:
        return []


def _fetch_fev_scores(codes: list[str]) -> str:
    try:
        from engine_stocks import score_fev
        from config import WATCHLIST
        lines = ["| 代码 | 名称 | F | E | V | FEV总分 |",
                 "|------|------|---|---|---|---------|"]
        for code in codes:
            info = WATCHLIST.get(code, {})
            name = info.get("name", code)
            try:
                fev = score_fev(code, name, {})
                if fev:
                    lines.append(
                        f"| {code} | {name} | {fev.f_score} | {fev.e_score} | "
                        f"{fev.v_score} | {fev.fev_total} |"
                    )
                else:
                    lines.append(f"| {code} | {name} | - | - | - | - |")
            except Exception:
                lines.append(f"| {code} | {name} | - | - | - | - |")
        return "\n".join(lines)
    except Exception:
        return "_FEV 评分暂不可用_"


# ============================================================
# LLM call helper
# ============================================================

def _call_llm(prompt: str) -> str:
    api_key = _load_api_key()
    if not api_key:
        return ""
    try:
        from anthropic import Anthropic
    except ImportError:
        return ""

    try:
        client = Anthropic(api_key=api_key, timeout=TIMEOUT)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as e:
        print(f"  [WARN] serenity LLM 调用失败: {e}")
        return ""


def _load_skill() -> str:
    skill_path = BASE.parent / "traders_cn" / "serenity_cn.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return ""


# ============================================================
# Public API
# ============================================================

def analyze_global_chain(chain_name: str) -> str:
    """第一层：全球供应链卡脖子节点分析。"""
    skill = _load_skill()
    chain_data = _fetch_chain_data(chain_name)
    theme_data = _fetch_theme_data()
    available = _fetch_bom_industry_list()
    available_str = ", ".join(available) if available else "（暂无 BOM 数据）"

    prompt = f"""{skill}

---
## 数据注入

### 目标产业链
{chain_name}

### BOM 产业链现有数据
{chain_data}

### 当前 A 股题材热度（供参考关注度）
{theme_data}

### BOM 知识库已有产业链
{available_str}

---
## 任务

对「{chain_name}」执行**第一层：全球供应链卡脖子分析**。

从全球科技前沿产品反推供应链，逐层分析每层的供应商数量、替代难度、扩产周期、供需缺口。
找到最不可替代的卡脖子节点。按 SKILL 强制输出模板中的「第一层」格式输出。

**这是全球视角，不是 A 股视角。先不管 A 股有没有标的。**
产业链数据可能不完整——用你的知识补充关键层级，但标注哪些是你补充的。
"""
    return _call_llm(prompt)


def map_to_a_shares(chain_name: str, global_nodes_text: str = "") -> str:
    """第二层：A 股映射分析。将全球卡脖子节点映射到 A 股标的。"""
    skill = _load_skill()

    if not global_nodes_text:
        global_nodes_text = analyze_global_chain(chain_name)
        if not global_nodes_text:
            return ""

    chain_data = _fetch_chain_data(chain_name)
    codes = _get_chain_codes(chain_name)
    stock_data = _fetch_stock_data(codes) if codes else "_该产业链暂无 BOM 标的_"

    prompt = f"""{skill}

---
## 数据注入

### 第一层分析结果（全球卡脖子节点）
{global_nodes_text[:4000]}

### BOM 产业链数据
{chain_data}

### A 股相关标的行情
{stock_data}

---
## 任务

基于全球卡脖子节点分析，执行**第二层：A 股映射分析**。

对每个全球卡脖子节点，找出 A 股映射标的，按三类场景分类：
- **场景A（直接受益）**：中国是这层的关键供应商
- **场景B（国产替代）**：海外垄断但国内在追赶
- **场景C（纯概念）**：没有中国标的 → 不做

按 SKILL 强制输出模板中的「第二层」格式输出。场景C 标注「不做」，不要推荐。
"""
    return _call_llm(prompt)


def validate_with_fe(codes: list[str], chain_name: str = "") -> str:
    """第三层：FE 框架深度验证。"""
    if not codes:
        return "_无待验证标的_"

    skill = _load_skill()
    stock_data = _fetch_stock_data(codes)
    fev_data = _fetch_fev_scores(codes)

    prompt = f"""{skill}

---
## 数据注入

### 产业链
{chain_name or '（未指定）'}

### 标的行情与估值
{stock_data}

### 现有 FEV 评分
{fev_data}

---
## 任务

对以下 A 股标的执行**第三层：FE 基本面验证**：
{', '.join(codes)}

逐项过：
1. FEV 三脚凳：F（基本面）/ E（预期差）/ V（估值）各评分
2. Focus Five：有机收入增长、利润率轨迹、资本密集度、资本配置、终值认知
3. 催化剂路径：什么事件会让市场向你的判断靠拢？

按 SKILL 强制输出模板中的「第三层」格式输出。直接给结论：值得跟踪 / 等信号 / 放弃。
"""
    return _call_llm(prompt)


def analyze_full_chain(chain_name: str) -> str:
    """完整三层分析：全球 → A股映射 → FE验证。"""
    parts = [f"# Serenity 产业链卡脖子分析：{chain_name}", ""]

    layer1 = analyze_global_chain(chain_name)
    parts.append(layer1 or "_第一层分析暂不可用_")
    parts.append("")

    layer2 = map_to_a_shares(chain_name, layer1)
    parts.append(layer2 or "_第二层分析暂不可用_")
    parts.append("")

    codes = _get_chain_codes(chain_name)
    if codes:
        layer3 = validate_with_fe(codes, chain_name)
        parts.append(layer3 or "_第三层分析暂不可用_")
        parts.append("")

    parts.append("---")
    parts.append(f"*分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    parts.append("*基于公开数据，不构成投资建议。*")

    return "\n".join(parts)


def _get_chain_codes(chain_name: str) -> list[str]:
    try:
        from bom_analyzer import chain_db
        chain_db.init_db()
        result = chain_db.query_industry(chain_name)
        codes = []
        seen = set()
        for leader in result.get("leaders", []):
            code = leader.get("stock_code", "")
            if code and code not in seen:
                codes.append(code)
                seen.add(code)
        return codes
    except Exception:
        return []
