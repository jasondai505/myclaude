"""交易日简报 — 合并晨间情报+盘中验证+盘后复盘为一份完整日报"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
REPORT_DIR = BASE / "reports"
REVIEW_REPORT_DIR = BASE.parent / "daily_review" / "reports"

sys.path.insert(0, str(BASE))
from supply_chain import track_theme, init_db
from notify import daily_result as push_daily


def _read_frontmatter(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            # 尝试解析 JSON 数组
            if val.startswith("[") and val.endswith("]"):
                try:
                    arr = json.loads(val)
                    fm[key] = arr
                except json.JSONDecodeError:
                    fm[key] = val
            else:
                fm[key] = val
    return fm


def _fmt_list(val) -> str:
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val) if val else "—"


def _morning_summary(today: str) -> str:
    json_path = REPORT_DIR / f"morning_{today}.json"
    if not json_path.exists():
        return "_晨间报告未生成_"

    data = json.loads(json_path.read_text(encoding="utf-8"))
    lines = ["## 一、晨间情报 — 催化事件与标的"]
    lines.append("")
    lines.append(f"主题: {data.get('summary', '—')}")
    lines.append("")

    for ev in data.get("events", []):
        name = ev.get("name", "")
        confidence = ev.get("confidence", "")
        lines.append(f"### {name} (置信度: {confidence})")
        lines.append(f"{ev.get('narrative', '')[:300]}")
        lines.append("")

        segments = ev.get("sub_segments", [])
        if segments:
            seg_tags = []
            for seg in segments:
                d = seg.get("direction", "")
                icon = {"看多": "↑", "看空": "↓"}.get(d, "→")
                seg_tags.append(f"`{icon}{seg.get('name','')}`")
            lines.append("**细分环节**: " + " ".join(seg_tags))
            lines.append("")

        lines.append("| 代码 | 名称 | 方向 | 依据 |")
        lines.append("|------|------|------|------|")
        for s in ev.get("target_stocks", []):
            code = s.get("code", "")
            sname = s.get("name", "")
            direction = s.get("expected_direction", "")
            rationale = s.get("rationale", "")[:80]
            lines.append(f"| {code} | {sname} | {direction} | {rationale} |")
        lines.append("")

    return "\n".join(lines)


def _validation_summary(today: str) -> str:
    fm = _read_frontmatter(REPORT_DIR / f"validation_{today}.md")

    lines = ["## 二、盘中验证 — 假设检验"]
    lines.append("")

    if fm:
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 标的数 | {fm.get('total', '—')} |")
        lines.append(f"| 命中 | {fm.get('hit', '—')} |")
        lines.append(f"| 背离 | {fm.get('miss', '—')} |")
        lines.append(f"| 待定 | {fm.get('pending', '—')} |")
        lines.append(f"| 命中率 | {fm.get('hit_rate', '—')}% |")
    else:
        # 无 frontmatter: 尝试从旧格式报告提取
        vp = REPORT_DIR / f"validation_{today}.md"
        if vp.exists():
            text = vp.read_text(encoding="utf-8")
            m = re.search(r"标的数:\s*(\d+)\s*\|\s*命中:\s*(\d+)\s*\|\s*背离:\s*(\d+)\s*\|\s*待定:\s*(\d+)", text)
            if m:
                total, hit, miss, pending = int(m[1]), int(m[2]), int(m[3]), int(m[4])
                hr = round(hit / total * 100, 1) if total > 0 else 0
                lines.append("| 指标 | 数值 |")
                lines.append("|------|------|")
                lines.append(f"| 标的数 | {total} |")
                lines.append(f"| 命中 | {hit} |")
                lines.append(f"| 背离 | {miss} |")
                lines.append(f"| 待定 | {pending} |")
                lines.append(f"| 命中率 | {hr}% |")
            else:
                lines.append("_验证报告格式无法解析_")
        else:
            lines.append("_盘中验证未运行_")

    lines.append("")
    validate_path = REPORT_DIR / f"validation_{today}.md"
    if validate_path.exists():
        lines.append(f"> 详细验证: [validation_{today}.md](validation_{today}.md)")

    return "\n".join(lines)


def _review_summary(today: str) -> str:
    fm = _read_frontmatter(REVIEW_REPORT_DIR / f"review_{today}.md")
    if not fm:
        return "_盘后复盘未生成_"

    lines = ["## 三、盘后复盘 — 市场全景"]
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 市场情绪 | {fm.get('sentiment', '—')} |")
    lines.append(f"| 成交额(亿) | {fm.get('amount_yi', '—')} |")
    lines.append(f"| 涨停数 | {fm.get('limit_up', '—')} |")
    lines.append(f"| 上涨家数% | {fm.get('up_pct', '—')}% |")
    lines.append(f"| 北向资金(亿) | {fm.get('northbound', '—')} |")
    lines.append(f"| NVDA | {fm.get('nvda', '—')} |")
    lines.append(f"| 主线题材 | {_fmt_list(fm.get('mainline'))} |")
    lines.append(f"| 新兴题材 | {_fmt_list(fm.get('emerging'))} |")
    lines.append(f"| 退潮题材 | {_fmt_list(fm.get('fading'))} |")
    lines.append("")
    lines.append(f"> 完整复盘: ../../daily_review/reports/review_{today}.md")
    lines.append("")

    return "\n".join(lines)


def _theme_match(search_text: str, theme_name: str) -> bool:
    """检查晨间事件文本是否与盘后题材名有交集。"""
    e = search_text.lower()
    t = theme_name.lower()
    if t in e:
        return True
    # 拆括号: "光模块(CPO)" → 分别试 "光模块" 和 "CPO"
    base = re.sub(r"[（(][^)）]*[)）]", "", t).strip()
    if base and base in e:
        return True
    paren = re.findall(r"[（(]([^)）]*)[)）]", t)
    for p in paren:
        if p and len(p) >= 2 and p in e:
            return True
    return False


def _theme_cross_ref(today: str) -> str:
    """晨间事件 vs 盘后题材分类 交叉对照。"""
    morning_path = REPORT_DIR / f"morning_{today}.json"
    review_fm = _read_frontmatter(REVIEW_REPORT_DIR / f"review_{today}.md")

    if not morning_path.exists():
        return ""
    if not review_fm:
        return ""

    morning = json.loads(morning_path.read_text(encoding="utf-8"))
    events = morning.get("events", [])
    if not events:
        return ""

    mainline = review_fm.get("mainline", [])
    emerging = review_fm.get("emerging", [])
    fading = review_fm.get("fading", [])

    if isinstance(mainline, str):
        mainline = [t.strip() for t in mainline.split(",")]
    if isinstance(emerging, str):
        emerging = [t.strip() for t in emerging.split(",")]
    if isinstance(fading, str):
        fading = [t.strip() for t in fading.split(",")]

    matches: list[dict] = []
    init_db()
    for ev in events:
        ev_name = ev.get("name", "")
        confidence = ev.get("confidence", "")
        narrative = ev.get("narrative", "")
        segments = ev.get("sub_segments", [])

        # 子环节粒度匹配
        seg_hits: dict[str, dict] = {}
        for seg in segments:
            seg_name = seg.get("name", "")
            if not seg_name:
                continue
            seg_hits[seg_name] = {
                "direction": seg.get("direction", ""),
                "mainline": [t for t in mainline if _theme_match(seg_name, t)],
                "fading": [t for t in fading if _theme_match(seg_name, t)],
                "emerging": [t for t in emerging if _theme_match(seg_name, t)],
            }

        # 汇总到事件级别
        matched_mainline = []
        matched_fading = []
        matched_emerging = []
        for sh in seg_hits.values():
            for t in sh["mainline"]:
                if t not in matched_mainline:
                    matched_mainline.append(t)
            for t in sh["fading"]:
                if t not in matched_fading:
                    matched_fading.append(t)
            for t in sh["emerging"]:
                if t not in matched_emerging:
                    matched_emerging.append(t)

        # 无子环节时回退到叙事全文匹配
        if not segments:
            search_text = f"{ev_name} {narrative}"
            matched_mainline = [t for t in mainline if _theme_match(search_text, t)]
            matched_emerging = [t for t in emerging if _theme_match(search_text, t)]
            matched_fading = [t for t in fading if _theme_match(search_text, t)]

        total_seg = len(segments)
        fading_seg_count = sum(1 for sh in seg_hits.values() if sh["fading"])
        mainline_seg_count = sum(1 for sh in seg_hits.values() if sh["mainline"])
        emerging_seg_count = sum(1 for sh in seg_hits.values() if sh["emerging"])

        seg_detail_parts = []
        if fading_seg_count:
            names = [sn for sn, sh in seg_hits.items() if sh["fading"]]
            seg_detail_parts.append(f"退潮:{','.join(names[:3])}")
        if mainline_seg_count:
            names = [sn for sn, sh in seg_hits.items() if sh["mainline"]]
            seg_detail_parts.append(f"主线:{','.join(names[:3])}")
        if emerging_seg_count:
            names = [sn for sn, sh in seg_hits.items() if sh["emerging"]]
            seg_detail_parts.append(f"新兴:{','.join(names[:3])}")
        seg_detail = f"({fading_seg_count}+{mainline_seg_count}+{emerging_seg_count}/{total_seg}环节)" if segments else ""
        seg_summary = " | ".join(seg_detail_parts) if seg_detail_parts else ""

        ml_str = ", ".join(matched_mainline) if matched_mainline else ""
        fd_str = ", ".join(matched_fading) if matched_fading else ""
        em_str = ", ".join(matched_emerging) if matched_emerging else ""

        # 跟踪到 DB，取回带观察期缓冲的状态
        tr = track_theme(ev_name, today, confidence, ml_str, fd_str, em_str)
        day_n = tr["day_n"]
        status = tr["status"]

        has_mainline = bool(matched_mainline)
        has_fading = bool(matched_fading)
        has_emerging = bool(matched_emerging)

        if status == "confirmed":
            verdict = "确认(连续主线)"
        elif status == "weakening":
            verdict = "趋弱(连续退潮)"
        elif has_fading and day_n == 1:
            verdict = f"观察中(Day{day_n}·{fading_seg_count}/{total_seg}退潮)" if segments else f"观察中(Day{day_n}·退潮)"
        elif has_mainline and day_n == 1:
            verdict = f"观察中(Day{day_n}·{mainline_seg_count}/{total_seg}主线)" if segments else f"观察中(Day{day_n}·主线)"
        elif has_mainline and not has_fading:
            verdict = f"观察中(Day{day_n}·偏主线)"
        elif has_fading and not has_mainline:
            verdict = f"观察中(Day{day_n}·{fading_seg_count}/{total_seg}退潮)" if segments else f"观察中(Day{day_n}·偏退潮)"
        elif has_emerging:
            verdict = f"观察中(Day{day_n}·新兴)"
        else:
            verdict = f"观察中(Day{day_n})"

        all_themes = []
        if seg_summary:
            all_themes.append(seg_summary)
        elif matched_mainline:
            all_themes.append(f"主线: {', '.join(matched_mainline)}")
        elif matched_fading:
            all_themes.append(f"退潮: {', '.join(matched_fading)}")
        elif matched_emerging:
            all_themes.append(f"新兴: {', '.join(matched_emerging)}")

        matches.append({
            "event": ev_name[:60],
            "confidence": confidence,
            "themes": " | ".join(all_themes) if all_themes else "-",
            "verdict": verdict,
            "day_n": day_n,
            "seg_detail": seg_detail,
            "segments": segments,
            "seg_hits": seg_hits,
        })

    if not matches:
        return ""

    lines = [
        "",
        "### 晨间事件 vs 盘后题材交叉对照",
        "",
        "| 晨间事件 | 置信度 | 匹配盘后题材 | 判定 |",
        "|----------|--------|------------|------|",
    ]
    for m in matches:
        v = m["verdict"]
        if "确认" in v:
            icon = "✅"
        elif "趋弱" in v:
            icon = "⚠️"
        elif "观察中" in v:
            icon = "🔍"
        else:
            icon = "-"
        lines.append(
            f"| {m['event']} | {m['confidence']} | {m['themes']} | {icon} {m['verdict']} |"
        )

    # 子环节明细（有 sub_segments 的事件）
    seg_events = [m for m in matches if m.get("segments")]
    if seg_events:
        lines.extend(["", "### 子环节明细", ""])
        for m in seg_events:
            lines.append(f"**{m['event']}** ({len(m['segments'])}个环节{', '+m['seg_detail'] if m['seg_detail'] else ''}):")
            lines.append("")
            lines.append("| 子环节 | 方向 | 今日匹配 |")
            lines.append("|--------|------|---------|")
            for seg in m["segments"]:
                sn = seg.get("name", "")
                sd = seg.get("direction", "")
                sh = m["seg_hits"].get(sn, {})
                hits = []
                if sh.get("mainline"):
                    hits.append(f"主线: {', '.join(sh['mainline'])}")
                if sh.get("fading"):
                    hits.append(f"退潮: {', '.join(sh['fading'])}")
                if sh.get("emerging"):
                    hits.append(f"新兴: {', '.join(sh['emerging'])}")
                hit_str = " | ".join(hits) if hits else "-"
                icon = "⚠️" if sh.get("fading") else "✅" if sh.get("mainline") else ""
                lines.append(f"| {icon} {sn} | {sd} | {hit_str} |")
            lines.append("")

    weakening = sum(1 for m in matches if "趋弱" in m["verdict"])
    confirmed = sum(1 for m in matches if "确认" in m["verdict"])
    observing = sum(1 for m in matches if "观察中" in m["verdict"])
    lines.append("")
    if observing > 0:
        lines.append(f"> 🔍 {observing} 个题材处于观察期，")
    if weakening > 0:
        lines.append(f"> ⚠️ {weakening} 个连续退潮已确认趋弱，")
    if confirmed > 0:
        lines.append(f"> ✅ {confirmed} 个连续主线已确认，")
    lines.append("> 连续 ≥2 天同向信号才变更状态（规则：首日一律观察，不一日定生死）。")

    return "\n".join(lines)


def _accuracy_summary(today: str) -> str:
    fm = _read_frontmatter(REPORT_DIR / f"validation_{today}.md")

    lines = ["## 四、AI 预测准确性"]
    lines.append("")

    total = int(fm.get("total", 0)) if fm else 0
    hit = int(fm.get("hit", 0)) if fm else 0
    miss = int(fm.get("miss", 0)) if fm else 0

    if not fm:
        vp = REPORT_DIR / f"validation_{today}.md"
        if vp.exists():
            text = vp.read_text(encoding="utf-8")
            m = re.search(r"标的数:\s*(\d+)\s*\|\s*命中:\s*(\d+)\s*\|\s*背离:\s*(\d+)", text)
            if m:
                total, hit, miss = int(m[1]), int(m[2]), int(m[3])

    if total > 0:
        hit_rate = round(hit / total * 100, 1)
        verdict = "优秀" if hit_rate >= 70 else "良好" if hit_rate >= 50 else "待改善"
        lines.append(f"命中率 **{hit_rate}%** ({hit}/{total}) — {verdict}")
        if miss > 0:
            lines.append(f"背离 **{miss}** 只标的，需复盘的错误预测。")
    else:
        lines.append("_本日无有效预测数据_")

    cross_ref = _theme_cross_ref(today)
    if cross_ref:
        lines.append(cross_ref)
    lines.append("")

    return "\n".join(lines)


def run(today: str = None) -> Path | None:
    if today is None:
        today = date.today().isoformat()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [
        f"# 交易日简报 {today}",
        "",
        f"> 生成时间: {now}",
        "> 流水线: 晨间情报(6:00) → 盘中验证(10:30/14:00) → 盘后复盘(17:50)",
        "",
        _morning_summary(today),
        _validation_summary(today),
        _review_summary(today),
        _accuracy_summary(today),
        "---",
        "",
        "*自动生成，仅供参考，不构成投资建议。*",
    ]

    brief_path = REPORT_DIR / f"daily_brief_{today}.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"[daily_brief] 报告已生成: {brief_path}")

    # 微信推送
    fm = _read_frontmatter(REPORT_DIR / f"validation_{today}.md")
    total_v = int(fm.get("total", 0)) if fm else 0
    hit_v = int(fm.get("hit", 0)) if fm else 0
    hr = round(hit_v / total_v * 100, 1) if total_v > 0 else 0.0
    push_daily(today, hr, hit_v, total_v)

    return brief_path


if __name__ == "__main__":
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    result = run(today=today)
    if result:
        print(f"OK: {result}")
