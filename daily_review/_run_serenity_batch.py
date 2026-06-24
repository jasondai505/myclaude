"""Serenity 批量提取 — Haiku 单篇提取 + 汇总"""
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
        # Extract JSON object
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

    # Mark done
    conn = sqlite3.connect(str(TRACKER_DB))
    conn.execute("UPDATE ocr_tracker SET analysis_done=1 WHERE file_path=?", (r["file_path"],))
    conn.commit(); conn.close()

    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{len(rows)} done, {len(all_results)} extracted")

print(f"\nDone: {len(all_results)}/{len(rows)} extracted")

# Save
out = OUT_DIR / f"serenity_extract_{today}.json"
out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

# Summary
nodes = {}
for r in all_results:
    for n in r.get("supply_chain_nodes", []):
        k = n.get("node") or n if isinstance(n, str) else "?"
        nodes[k] = n if isinstance(n, dict) else {}
print(f"\nUnique supply chain nodes: {len(nodes)}")
for k in sorted(nodes.keys())[:20]:
    print(f"  - {k}")

print(f"\nSaved to {out}")
