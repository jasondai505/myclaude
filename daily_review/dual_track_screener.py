"""双轨筛选器 — FEV (格雷厄姆) + G-Factor (费雪) 独立并行，分席位输出。

核心原则: 两个轨不合成总分。不是调权重，是分席位。

用法:
    python dual_track_screener.py                    # 当天双轨筛选
    python dual_track_screener.py --top-n 10 5       # FEV轨10只 + G每维5只
    python dual_track_screener.py --min-fev 18       # FEV最低阈值
    python dual_track_screener.py --export           # 输出到Markdown
"""
from __future__ import annotations

import json, sqlite3, sys
from pathlib import Path
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
FEVAL_DB = BASE / "data" / "serenity.db"
GFACTOR_DB = BASE / "data" / "gfactor.db"


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def load_fev(min_score: int = 0, top_n: int = 50) -> list[dict]:
    """加载最新 FEV 评分。"""
    if not FEVAL_DB.exists():
        return []
    conn = sqlite3.connect(str(FEVAL_DB))
    conn.row_factory = sqlite3.Row
    d = _today()
    rows = conn.execute(
        "SELECT code, name, f_score, e_score, v_score, fev_total, f_note, e_note, v_note, date "
        "FROM feval_scores WHERE date=? AND fev_total >= ? ORDER BY fev_total DESC LIMIT ?",
        (d, min_score, top_n),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT code, name, f_score, e_score, v_score, fev_total, f_note, e_note, v_note, date "
            "FROM feval_scores WHERE fev_total >= ? "
            "AND date=(SELECT MAX(date) FROM feval_scores) "
            "ORDER BY fev_total DESC LIMIT ?",
            (min_score, top_n),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_gfactor() -> dict[str, dict]:
    """加载最新 G-Factor 四维评分。"""
    if not GFACTOR_DB.exists():
        return {}
    conn = sqlite3.connect(str(GFACTOR_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT code, name, g1_score, g1_note, g2_score, g2_note, "
        "g3_score, g3_note, g4_score, g4_note "
        "FROM gfactor_scores WHERE date=(SELECT MAX(date) FROM gfactor_scores)"
    ).fetchall()
    conn.close()
    return {r["code"]: dict(r) for r in rows}


def screen(fev_top_n: int = 7, g_per_dim: int = 3, min_fev: int = 15,
           min_g: int = 6) -> dict:
    """双轨筛选: 轨A(FEV top N) + 轨B(G各维≥阈值各取top N)。"""
    fev_all = load_fev(min_score=min_fev, top_n=max(fev_top_n, 100))
    g_all = load_gfactor()

    # 轨A: FEV (格雷厄姆)
    track_a = fev_all[:fev_top_n]
    a_codes = {s["code"] for s in track_a}

    # 轨B: G-Factor 四维独立 (费雪)
    dims = [
        ("G1_成长质量", "g1_score"),
        ("G2_催化密度", "g2_score"),
        ("G3_叙事强度", "g3_score"),
        ("G4_机构动量", "g4_score"),
    ]
    track_b: dict[str, list] = {}
    for dim_name, dim_key in dims:
        qualified = [
            (code, data) for code, data in g_all.items()
            if data.get(dim_key, 0) >= min_g
        ]
        qualified.sort(key=lambda x: -x[1][dim_key])
        track_b[dim_name] = qualified[:g_per_dim]

    b_codes = set()
    for entries in track_b.values():
        for code, _ in entries:
            b_codes.add(code)

    # 去重合并
    overlap = a_codes & b_codes
    all_codes = a_codes | b_codes

    return {
        "track_a": {
            "label": "FEV 格雷厄姆轨 (质量+合理价格)",
            "stocks": track_a,
            "count": len(track_a),
        },
        "track_b": {
            "label": "G-Factor 费雪轨 (成长动能·四维独立)",
            "dims": {
                dim_name: [{"code": code, "name": data["name"],
                            "score": data[dim_key]} for code, data in entries]
                for dim_name, dim_key, entries in [
                    (dn, dk, track_b[dn]) for dn, dk in dims
                ]
            },
            "count": len(b_codes),
        },
        "overlap": sorted(overlap),
        "total": len(all_codes),
    }


def print_screen(result: dict):
    """终端输出双轨筛选结果。"""
    print(f"\n{'='*60}")
    print(f"  双轨筛选结果 ({_today()})")
    print(f"{'='*60}")

    # 轨A
    ta = result["track_a"]
    print(f"\n--- 轨A: {ta['label']} ---")
    print(f"    ({ta['count']} 只)")
    for s in ta["stocks"]:
        print(f"  {s['code']} {s['name']:<8} "
              f"F={s['f_score']} E={s['e_score']} V={s['v_score']} "
              f"FEV={s['fev_total']}  {s.get('f_note','')[:40]}")

    # 轨B
    tb = result["track_b"]
    print(f"\n--- 轨B: {tb['label']} ---")
    print(f"    (4维合计 {tb['count']} 只独立标的)")
    for dim_name, entries in tb["dims"].items():
        if not entries:
            print(f"  {dim_name}: 无达标标的")
            continue
        print(f"  {dim_name}:")
        for e in entries:
            print(f"    {e['code']} {e['name']:<8} ={e['score']}")

    # 交叉
    if result["overlap"]:
        print(f"\n  双轨交集: {', '.join(result['overlap'])}")
    print(f"\n  总入选: {result['total']} 只 (去重后)")


def export_markdown(result: dict, path: Path | None = None):
    """导出为 Markdown 文件。"""
    if path is None:
        path = BASE / "reports" / f"dual_track_{_today()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# 双轨筛选 {_today()}",
        "",
        "> FEV (格雷厄姆) + G-Factor (费雪) 双轨并行。不可公度的维度不强行加权。",
        "",
    ]

    ta = result["track_a"]
    lines.append(f"## 轨A: {ta['label']} ({ta['count']}只)")
    lines.append("")
    lines.append("| 代码 | 名称 | F | E | V | FEV | 备注 |")
    lines.append("|------|------|---|---|---|-----|------|")
    for s in ta["stocks"]:
        lines.append(f"| {s['code']} | {s['name']} | {s['f_score']} | {s['e_score']} | "
                     f"{s['v_score']} | **{s['fev_total']}** | {s.get('f_note','')[:30]} |")
    lines.append("")

    tb = result["track_b"]
    lines.append(f"## 轨B: {tb['label']} ({tb['count']}只)")
    lines.append("")
    for dim_name, entries in tb["dims"].items():
        lines.append(f"### {dim_name}")
        if not entries:
            lines.append("_无达标标的_")
        else:
            lines.append("| 代码 | 名称 | 得分 |")
            lines.append("|------|------|------|")
            for e in entries:
                lines.append(f"| {e['code']} | {e['name']} | **{e['score']}** |")
        lines.append("")

    if result["overlap"]:
        lines.append(f"## 双轨交集")
        lines.append(f"`{'`, `'.join(result['overlap'])}`")
        lines.append("")

    lines.append(f"*{result['total']} 只标的入选 (去重后)*")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n已导出: {path}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="双轨筛选器")
    p.add_argument("--top-n", type=int, nargs=2, default=[7, 3],
                   help="FEV轨N G每维N (默认 7 3)")
    p.add_argument("--min-fev", type=int, default=15, help="FEV最低阈值 (默认15)")
    p.add_argument("--min-g", type=int, default=6, help="G-Factor每维最低 (默认6)")
    p.add_argument("--export", action="store_true", help="导出Markdown")
    args = p.parse_args()

    result = screen(
        fev_top_n=args.top_n[0], g_per_dim=args.top_n[1],
        min_fev=args.min_fev, min_g=args.min_g,
    )
    print_screen(result)

    if args.export:
        export_markdown(result)
