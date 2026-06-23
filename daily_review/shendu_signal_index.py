"""Shendu 信号存档索引。

从 shendu/*.json 生成 shendu_signal_index.json，
每条 VP 独立一行，供 Dashboard/Advice/行情复活快速检索。
"""
from __future__ import annotations

import json
from pathlib import Path

SHENDU_DIR = Path(__file__).resolve().parent / "reports" / "serenity" / "shendu"
INDEX_PATH = SHENDU_DIR / "shendu_signal_index.json"


def build_index() -> list[dict]:
    signals = []
    for f in sorted(SHENDU_DIR.iterdir()):
        if not f.name.startswith("shendu_2026") or f.name == "shendu_signal_index.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        date_str = data.get("date", "")
        title = data.get("title_clean", "") or data.get("title", "")

        # 每条 VP 独立存档
        for vp in data.get("variant_perceptions", []):
            signals.append({
                "source": "shendu",
                "date": date_str,
                "article": title[:80],
                "type": "variant_perception",
                "consensus": vp.get("consensus", ""),
                "variant": vp.get("variant", ""),
                "confidence": vp.get("confidence", ""),
                "falsification": vp.get("falsification", ""),
            })

        # 风险信号
        for r in data.get("risk_signals", []):
            signals.append({
                "source": "shendu",
                "date": date_str,
                "article": title[:80],
                "type": "risk",
                "risk_type": r.get("type", ""),
                "target": r.get("target", ""),
                "detail": r.get("detail", ""),
            })

        # 承重判断
        lbj = data.get("load_bearing_judgment", "")
        if lbj:
            signals.append({
                "source": "shendu",
                "date": date_str,
                "article": title[:80],
                "type": "load_bearing_judgment",
                "judgment": lbj[:200],
            })

        # 标的映射
        for v in data.get("valuation_spectrum", []):
            for i, code in enumerate(v.get("codes", [])):
                name = (v.get("names", []) or [""])[i] if i < len(v.get("names", []) or []) else ""
                signals.append({
                    "source": "shendu",
                    "date": date_str,
                    "article": title[:80],
                    "type": "stock_mapping",
                    "code": code,
                    "name": name,
                    "tier": v.get("tier", ""),
                    "chain_segment": v.get("chain_segment", ""),
                })

        # 产业链 + 主题
        for chain in data.get("chains_involved", []):
            signals.append({
                "source": "shendu",
                "date": date_str,
                "article": title[:80],
                "type": "chain",
                "chain": chain,
            })
        for theme in data.get("themes", []):
            signals.append({
                "source": "shendu",
                "date": date_str,
                "article": title[:80],
                "type": "theme",
                "theme": theme,
            })

    INDEX_PATH.write_text(json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8")
    return signals


if __name__ == "__main__":
    signals = build_index()
    print(f"生成 shendu_signal_index.json: {len(signals)} 条信号")
    # 按类型统计
    from collections import Counter
    types = Counter(s["type"] for s in signals)
    for t, c in types.most_common():
        print(f"  {t}: {c}")
