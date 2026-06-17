"""四大主要语料源交叉验证 — Haiku 提取共识主题/分歧/新信号/多源标的。

读取当日 ZSXQ / 公众号分析 / 韭研脱水 / 唐史微博，
Haiku 结构化输出 -> reports/feeds/primary_synthesis_YYYY-MM-DD.md

用法:
  python primary_synthesis.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from utils import setup_console
setup_console()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import REPORT_DIR

FEEDS_DIR = REPORT_DIR / "feeds"
MODEL = "claude-haiku-4-5-20251001"
MAX_CHARS_PER_SOURCE = 4000
TIMEOUT = 120

SOURCES = {
    "知识星球": "zsxq",
    "公众号分析": None,
    "韭研脱水研报": "jiuyang",
    "唐史微博": "weibo",
}

_ZSXQ_ANALYSIS_PREFIX = "zsxq_analysis"


def _read_zsxq(target_date: str) -> str:
    """优先读星球两阶段分析结果，回退 raw feed。"""
    analysis_path = FEEDS_DIR / f"{_ZSXQ_ANALYSIS_PREFIX}_{target_date}.md"
    if analysis_path.exists():
        return analysis_path.read_text(encoding="utf-8")[:MAX_CHARS_PER_SOURCE]
    prev = date.fromisoformat(target_date) - timedelta(days=1)
    analysis_path = FEEDS_DIR / f"{_ZSXQ_ANALYSIS_PREFIX}_{prev.isoformat()}.md"
    if analysis_path.exists():
        return analysis_path.read_text(encoding="utf-8")[:MAX_CHARS_PER_SOURCE]
    return _read_source("zsxq", target_date)

_PROMPT = """你是 A 股投研助手。以下是今日四份主要情报源的内容：

{source_blocks}

请交叉验证，输出 JSON（只输出 JSON，不要其他文字）：

{{
  "consensus_themes": [
    {{
      "theme": "主题名",
      "sources": ["星球", "韭研"],
      "conviction": "高/中/低",
      "stocks": ["代码1", "代码2"],
      "thesis": "核心逻辑摘要（2-3句）",
      "catalyst": "近期催化及时间窗"
    }}
  ],
  "divergences": [
    {{
      "topic": "分歧话题",
      "bull_view": "看多逻辑及来源",
      "bear_view": "看空逻辑及来源",
      "our_take": "基于多源权重的判断（1-2句）"
    }}
  ],
  "new_signals": [
    {{
      "signal": "新信号描述",
      "source": "来源",
      "urgency": "高/中/低",
      "horizon": "短期/中期/长期",
      "related_stocks": ["代码"]
    }}
  ],
  "cross_validated_stocks": [
    {{
      "code": "6位代码",
      "name": "简称",
      "source_count": 2,
      "sources": ["韭研", "星球"],
      "common_thesis": "共同逻辑"
    }}
  ],
  "summary": "200字以内整体摘要 - 今日最重要的3件事，优先级排序"
}}

原则：
- 只提取投资相关内容，忽略纯生活/社会评论
- 多源共同指向的主题权重最高
- 分歧点标注出处的置信度
- stocks/code 字段必须填 6 位数字代码，无法确定则填 ""
- consensus_themes 限 3-5 个，挑最重要的"""


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


def _read_source(stem: str, target_date: str) -> str:
    path = FEEDS_DIR / f"{stem}_{target_date}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")[:MAX_CHARS_PER_SOURCE]
    prev = date.fromisoformat(target_date) - timedelta(days=1)
    path = FEEDS_DIR / f"{stem}_{prev.isoformat()}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")[:MAX_CHARS_PER_SOURCE]
    return ""


def _read_wechat(target_date: str) -> str:
    path = REPORT_DIR / "wechat_analysis" / f"wechat_analysis_{target_date}.md"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        marker = "## 逐篇拆解"
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
        return text[:MAX_CHARS_PER_SOURCE]
    return ""


def _call_haiku(prompt: str) -> dict | None:
    key = _load_api_key()
    if not key:
        print("  API key 不可用")
        return None

    try:
        from roles import get_client as _get_client, get_model
        client = _get_client("synthesis", timeout=TIMEOUT)
        model = get_model("synthesis")
        resp = client.messages.create(
            model=model,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
            timeout=TIMEOUT,
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        )
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start:end + 1])
            # L2: 校验所有股票代码
            from llm_validator import validate_codes as _vc
            invalid = 0
            for theme in data.get("consensus_themes", []):
                stocks = theme.get("stocks", [])
                if isinstance(stocks, list):
                    theme["stocks"] = [
                        s for s in stocks
                        if isinstance(s, str) and (len(s) == 6 and _vc([s]).get(s, {}).get("valid"))
                    ]
                    invalid += len(stocks) - len(theme["stocks"])
            for entry in data.get("cross_validated_stocks", []):
                code = entry.get("code", "")
                if code and not _vc([code]).get(code, {}).get("valid"):
                    entry["code"] = ""
                    entry["_invalid"] = True
                    invalid += 1
            if invalid:
                print(f"  [L2] 过滤 {invalid} 个无效代码")
            return data
    except json.JSONDecodeError as e:
        print(f"  JSON 解析失败: {e}")
    except Exception as e:
        print(f"  Haiku 调用失败: {e}")
    return None


def _write_report(data: dict, target_date: str) -> Path:
    path = FEEDS_DIR / f"primary_synthesis_{target_date}.md"
    buf = [
        f"# 四源交叉验证 {target_date}",
        "",
        f"> ZSXQ · 公众号 · 韭研脱水 · 唐史微博",
        "",
    ]

    s = data.get("summary", "")
    if s:
        buf.extend(["## 今日三件事", "", f"> {s}", ""])

    themes = data.get("consensus_themes", [])
    if themes:
        buf.append("## 共识主题")
        buf.append("")
        for t in themes:
            conv = t.get("conviction", "·")
            emoji = {"高": "🔥", "中": "📌", "低": "👀"}.get(conv, "·")
            srcs = " · ".join(t.get("sources", []))
            stocks = ", ".join(t.get("stocks", []))
            buf.append(f"### {emoji} {t.get('theme', '')}（{conv} / {srcs}）")
            buf.append("")
            buf.append(f"**逻辑**: {t.get('thesis', '')}")
            buf.append("")
            buf.append(f"**催化**: {t.get('catalyst', '')}")
            buf.append("")
            if stocks:
                buf.append(f"**标的**: {stocks}")
                buf.append("")

    divs = data.get("divergences", [])
    if divs:
        buf.append("## 源间分歧")
        buf.append("")
        for d in divs:
            buf.append(f"### ⚡ {d.get('topic', '')}")
            buf.append("")
            buf.append(f"- 🟢 **看多**: {d.get('bull_view', '')}")
            buf.append(f"- 🔴 **看空**: {d.get('bear_view', '')}")
            buf.append(f"- 🎯 **判断**: {d.get('our_take', '')}")
            buf.append("")

    signals = data.get("new_signals", [])
    if signals:
        buf.append("## 新信号")
        buf.append("")
        buf.append("| 信号 | 来源 | 紧迫度 | 时间维度 | 标的 |")
        buf.append("|------|------|--------|----------|------|")
        for sig in signals:
            stocks = ", ".join(sig.get("related_stocks", []))
            buf.append(
                f"| {sig.get('signal', '')} | {sig.get('source', '')} | "
                f"{sig.get('urgency', '')} | {sig.get('horizon', '')} | {stocks} |"
            )
        buf.append("")

    cv = data.get("cross_validated_stocks", [])
    if cv:
        buf.append("## 多源共同提及标的")
        buf.append("")
        buf.append("| 代码 | 名称 | 源数 | 来源 | 共同逻辑 |")
        buf.append("|------|------|:----:|------|---------|")
        for s in cv:
            buf.append(
                f"| {s.get('code', '')} | {s.get('name', '')} | "
                f"{s.get('source_count', 0)} | {'·'.join(s.get('sources', []))} | "
                f"{s.get('common_thesis', '')} |"
            )
        buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def synthesise(target_date: str) -> str | None:
    source_blocks = []

    for label, stem in SOURCES.items():
        if label == "知识星球":
            content = _read_zsxq(target_date)
        elif stem:
            content = _read_source(stem, target_date)
        else:
            content = _read_wechat(target_date)
        if content and "暂未生成" not in content and "不存在" not in content:
            source_blocks.append(f"=== {label} ===\n{content}")
        else:
            source_blocks.append(f"=== {label} ===\n（今日无数据）")

    prompt = _PROMPT.format(source_blocks="\n\n".join(source_blocks))

    print(f"  四源总长度: {len(prompt)} chars")
    data = _call_haiku(prompt)
    if not data:
        return None

    path = _write_report(data, target_date)
    print(f"  -> {path}")
    return str(path)


if __name__ == "__main__":
    target = date.today().isoformat()
    if len(sys.argv) >= 3 and sys.argv[1] == "--date":
        target = sys.argv[2]

    result = synthesise(target)
    if result:
        print(f"完成: {result}")
    else:
        print("失败")
