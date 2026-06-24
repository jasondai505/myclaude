"""Serenity 全球半导体供应链情报提取器。

从 Reddit/OCR 语料中提取:
  - 关键实体 (公司/技术/产品)
  - 供应链节点 (卡脖子环节)
  - A 股映射
  - 时间敏感度

用法:
    python extractors/serenity_extract.py                    # 增量处理
    python extractors/serenity_extract.py --all              # 全量
"""
from __future__ import annotations

import json, re, sys, sqlite3, hashlib
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent
TRACKER_DB = BASE / "data" / "ocr_tracker.db"
OUT_DIR = BASE / "reports" / "serenity"
OUT_DIR.mkdir(parents=True, exist_ok=True)

EXTRACT_PROMPT = """你是全球半导体供应链分析师。从以下 Reddit 帖子中提取结构化情报。

帖子内容（OCR识别，可能有错字）:
{body}

输出 JSON:
```json
{{
  "date": "YYYY-MM-DD",
  "key_entities": [
    {{"name": "公司/产品名", "type": "company/technology/product", "ticker": "$TICKER或空", "significance": "high/medium/low"}}
  ],
  "supply_chain_nodes": [
    {{"node": "供应链环节(如MEMS Foundry/Photonics/CW Laser/CXL/HBM/etc)", "direction": "bottleneck/expansion/substitution/risk", "detail": "一句话描述", "a_share_relevance": "high/medium/low", "a_share_codes": ["相关A股代码"]}}
  ],
  "macro_signals": [
    {{"signal": "信号描述", "impact": "positive/negative/neutral"}}
  ],
  "themes": ["主题标签1", "主题标签2"],
  "urgency": "high/medium/low",
  "summary_cn": "中文一句话摘要"
}}
```

注意: a_share_codes 必须是6位数字代码。不确定的留空数组。"""


def _load_ocr_texts(source_type: str = "serenity", max_chars: int = 50000) -> list[dict]:
    with sqlite3.connect(str(TRACKER_DB)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT file_path, ocr_text FROM ocr_tracker WHERE source_type=? AND analysis_done=0 AND ocr_text IS NOT NULL AND ocr_text != ''",
            (source_type,)
        ).fetchall()
    if not rows:
        return []

    texts = []
    total = 0
    for r in rows:
        text = r["ocr_text"] or ""
        if text and total + len(text) <= max_chars:
            texts.append({"file": r["file_path"], "text": text})
            total += len(text)

    return texts


def _call_llm(prompt: str, model: str = "claude-sonnet-4-6-20250514") -> dict | None:
    from llm import _load_api_key
    import anthropic

    key = _load_api_key()
    if not key:
        print("  [WARN] 无 API key")
        return None

    client = anthropic.Anthropic(api_key=key)
    try:
        resp = client.messages.create(
            model=model, max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            b.text for b in resp.content
            if hasattr(b, "text") and b.text
        )
        # Extract JSON
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        m = re.search(r"(\{.*\})", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    except Exception as e:
        print(f"  [WARN] LLM error: {e}")
    return None


def _dedup_title(text: str) -> str:
    """Extract stable title-ish hash for dedup."""
    # Take first meaningful line
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 20]
    key = lines[0][:80] if lines else text[:80]
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _mark_analyzed(filepath: str):
    with sqlite3.connect(str(TRACKER_DB)) as conn:
        conn.execute(
            "UPDATE ocr_tracker SET analysis_done=1 WHERE file_path=?",
            (filepath,)
        )


def run(max_chars: int = 50000, model: str = "claude-sonnet-4-6-20250514"):
    texts = _load_ocr_texts("serenity", max_chars)
    if not texts:
        print("[serenity_extract] 无新 Serenity 语料")
        return None

    today = date.today().isoformat()
    print(f"[serenity_extract] {len(texts)} 篇, 开始 LLM 提取...")

    # Batch all texts together
    combined = "\n\n---\n\n".join(
        f"[{i+1}] {t['text'][:3000]}" for i, t in enumerate(texts)
    )

    prompt = EXTRACT_PROMPT.format(body=combined[:15000])
    result = _call_llm(prompt, model)

    if result:
        result["source"] = "serenity_reddit"
        result["processed_at"] = today
        result["ocr_files"] = [t["file"] for t in texts]

        out_path = OUT_DIR / f"serenity_extract_{today}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {out_path}")

        # Build markdown summary
        _write_summary(result, today)

        for t in texts:
            _mark_analyzed(t["file"])

        return result

    print("  [WARN] LLM 提取返回空")
    return None


def _write_summary(data: dict, today: str):
    lines = [
        f"# Serenity 全球半导体供应链情报 {today}",
        "",
        f"> 来源: Reddit r/SerenIty | 处理时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 关键实体",
    ]
    for e in data.get("key_entities", []):
        ticker = f" ({e.get('ticker')})" if e.get("ticker") else ""
        lines.append(f"- **{e['name']}**{ticker} [{e.get('significance','?')}] — {e.get('type','?')}")

    lines.extend(["", "## 供应链节点"])
    for n in data.get("supply_chain_nodes", []):
        codes = ", ".join(n.get("a_share_codes", []))
        lines.append(f"- **{n['node']}** [{n.get('direction','?')}] — {n.get('detail','')}")
        if codes:
            lines.append(f"  → A股: {codes}")

    lines.extend(["", "## 宏观信号"])
    for s in data.get("macro_signals", []):
        icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(s.get("impact", ""), "")
        lines.append(f"- {icon} {s.get('signal','')}")

    lines.extend(["", "## 主题", ", ".join(data.get("themes", []))])
    lines.extend(["", f"## 摘要", data.get("summary_cn", "")])

    out = OUT_DIR / f"serenity_brief_{today}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {out}")


def main():
    do_all = "--all" in sys.argv
    if do_all:
        with sqlite3.connect(str(TRACKER_DB)) as conn:
            conn.execute("UPDATE ocr_tracker SET analysis_done=0")
        print("[serenity_extract] 重置分析标记")

    run()


if __name__ == "__main__":
    main()
