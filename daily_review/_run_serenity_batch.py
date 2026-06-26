"""Serenity 批量提取 — Haiku 单篇提取 + 海外产业链情报日报"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
import sqlite3, json, re
from datetime import date
from pathlib import Path
from llm import _load_api_key
import anthropic

TRACKER_DB = Path(__file__).parent / "data" / "ocr_tracker.db"
OUT_DIR = Path(__file__).parent / "reports" / "serenity"
OUT_DIR.mkdir(parents=True, exist_ok=True)
today = date.today().isoformat()


def _render_markdown(results: list[dict], date_str: str) -> Path | None:
    """从 Haiku 提取结果生成海外产业链情报日报。"""
    if not results:
        return None

    out_path = OUT_DIR / f"serenity_daily_{date_str}.md"
    buf = [
        f"# 海外产业链情报日报 {date_str}",
        "",
        f"> {len(results)} 条情报 | 来源: Serenity (@aleabitoreddit)",
        "",
    ]

    # Collect high-significance entities (have tickers)
    high_entities = []
    seen_entities = set()
    for r in results:
        for c in r.get("companies", []):
            name = c.get("name", "")
            ticker = c.get("ticker", "")
            if ticker and ticker.startswith("$") and name not in seen_entities:
                seen_entities.add(name)
                high_entities.append(c)

    if high_entities:
        buf.append("## 重点海外标的")
        buf.append("")
        for c in high_entities[:20]:
            buf.append(f"- **{c.get('ticker', '')}** {c.get('name', '')} | {c.get('sector', '半导体/光电子')}")
        buf.append("")

    # Supply chain nodes extracted from theses
    all_nodes = []
    for r in results:
        nodes = r.get("supply_chain_nodes", [])
        if isinstance(nodes, list):
            for n in nodes:
                if isinstance(n, str) and n and n not in all_nodes:
                    all_nodes.append(n)
                elif isinstance(n, dict) and n.get("node") and n["node"] not in all_nodes:
                    all_nodes.append(n["node"])

    if all_nodes:
        buf.append(f"## 供应链关键词 ({len(all_nodes)})")
        buf.append("")
        buf.append(", ".join(all_nodes[:30]))
        buf.append("")

    # A-share relevant items
    high_rel = [r for r in results if r.get("a_relevance") == "high"]
    if high_rel:
        buf.append(f"## A 股高相关 ({len(high_rel)}条)")
        buf.append("")
        for r in high_rel:
            scn = r.get("summary_cn", "")
            thesis = r.get("thesis", "")[:200]
            line = f"- {scn}" if scn else f"- {thesis}"
            buf.append(line)
        buf.append("")

    # Per-tweet digest (limit to avoid oversized reports)
    buf.append("## 逐条摘要")
    buf.append("")
    for i, r in enumerate(results):
        scn = r.get("summary_cn", "")[:150]
        thesis = r.get("thesis", "")[:250]
        companies = ", ".join(
            (c.get("ticker", "") or c.get("name", ""))
            for c in r.get("companies", [])[:5]
        )
        date_s = r.get("date", "")[:10]
        buf.append(f"### [{i+1}] {date_s}")
        buf.append("")
        if companies:
            buf.append(f"**标的**: {companies}")
            buf.append("")
        if thesis:
            buf.append(f"{thesis}")
            buf.append("")
        if scn:
            buf.append(f"> {scn}")
            buf.append("")

    out_path.write_text("\n".join(buf), encoding="utf-8")
    return out_path


# ============================================================
# 主流程
# ============================================================

client = anthropic.Anthropic(api_key=_load_api_key())

conn = sqlite3.connect(str(TRACKER_DB)); conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT file_path, ocr_text FROM ocr_tracker "
    "WHERE source_type='serenity' AND analysis_done=0 "
    "AND ocr_text IS NOT NULL AND ocr_text != ''"
).fetchall()
conn.close()
print(f"Pending: {len(rows)} images")

all_results = []
for i, r in enumerate(rows):
    prompt = """Extract key info from this Reddit post. Tolerate OCR typos. Return ONLY JSON (no markdown):

""" + r["ocr_text"][:2500] + """

{"date":"YYYY-MM-DD","companies":[{"name":"","ticker":"","sector":""}],"thesis":"","supply_chain_nodes":[],"a_relevance":"high/medium/low","summary_cn":""}"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text") and b.text)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                obj["_file"] = Path(r["file_path"]).name
                all_results.append(obj)
            except json.JSONDecodeError:
                pass
    except Exception as e:
        print(f"  [{i+1}] ERROR: {e}")

    conn = sqlite3.connect(str(TRACKER_DB))
    conn.execute("UPDATE ocr_tracker SET analysis_done=1 WHERE file_path=?", (r["file_path"],))
    conn.commit(); conn.close()

    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{len(rows)} done, {len(all_results)} extracted")

print(f"\nDone: {len(all_results)}/{len(rows)} extracted")

# Save JSON
out = OUT_DIR / f"serenity_extract_{today}.json"
out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

# Generate standalone markdown report
md_path = _render_markdown(all_results, today)
if md_path:
    print(f"  日报: {md_path}")

print(f"\nSaved to {out}")
