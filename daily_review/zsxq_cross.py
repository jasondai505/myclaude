"""知识星球 × 盈利预测选股 交叉验证

用法:
    python run.py --cross              # 今天
    python run.py --cross --date 2026-05-15
"""
import sys
import os
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    os.system("")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import REPORT_DIR

COOKIE_PATH = Path(__file__).parent.parent / "cookie.txt"
GROUP_ID = "28855458518111"


def load_cookie():
    return COOKIE_PATH.read_text(encoding="utf-8").strip()


def fetch_topics(cookie, count=20, end_time=None):
    import random
    url = f"https://api.zsxq.com/v2/groups/{GROUP_ID}/topics?scope=all&count={count}"
    if end_time:
        url += f"&end_time={end_time}"
    req = urllib.request.Request(url)
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Origin", "https://wx.zsxq.com")
    req.add_header("Referer", "https://wx.zsxq.com/")
    req.add_header("X-Request-Id", str(random.randint(100000, 999999)))
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read().decode("utf-8"))


def fetch_recent_topics(cookie, pages=50):
    all_topics = []
    end_time = None
    for i in range(pages):
        try:
            data = fetch_topics(cookie, 20, end_time)
        except Exception:
            time.sleep(1)
            try:
                data = fetch_topics(cookie, 20, end_time)
            except Exception:
                break
        if not data.get("succeeded"):
            break
        topics = data.get("resp_data", {}).get("topics", [])
        if not topics:
            break
        all_topics.extend(topics)
        ct = topics[-1]["create_time"]
        end_time = ct.replace("+0800", "%2B0800")
        time.sleep(1)
    return all_topics


def clean_zsxq_text(text):
    text = re.sub(r'<e type="[^"]*"[^/]*/>', '', text)
    text = re.sub(r'\[.*?\]', '', text)
    return text


def analyze_zsxq_topics(topics, trade_date=None):
    """提取星球帖子中的板块/个股/催化剂信息，供每日复盘使用"""
    if not topics:
        return None

    if trade_date:
        topics = [t for t in topics if t.get("create_time", "")[:10] == trade_date]
    if not topics:
        return None

    highlights = []
    stock_mentions = {}

    for t in topics:
        raw = t.get("talk", {}).get("text", "") or ""
        text = clean_zsxq_text(raw)
        ctime = t.get("create_time", "")[:16]
        author = t.get("talk", {}).get("owner", {}).get("name", "")
        readers = t.get("readers_count", 0)

        title_line = text.split("\n")[0][:100] if text else ""

        is_review = "复盘" in title_line or "综述" in title_line
        is_recommend = any(kw in text[:200] for kw in ["推荐", "看好", "强call", "强烈"])
        is_research = "【" in title_line and "】" in title_line

        if is_review:
            highlights.append({
                "type": "review",
                "title": title_line,
                "author": author,
                "time": ctime,
                "readers": readers,
                "text": text[:500],
            })
        elif is_recommend or is_research:
            highlights.append({
                "type": "research",
                "title": title_line,
                "author": author,
                "time": ctime,
                "readers": readers,
                "text": text[:300],
            })

        code_pattern = re.compile(r'[036]\d{5}')
        for code in code_pattern.findall(text):
            if code not in stock_mentions:
                stock_mentions[code] = []
            stock_mentions[code].append(title_line[:80])

    highlights.sort(key=lambda x: x["readers"], reverse=True)

    return {
        "topic_count": len(topics),
        "highlights": highlights[:20],
        "stock_mentions": stock_mentions,
    }


def cross_reference(topics, earnings_path):
    df = pd.read_excel(earnings_path, sheet_name=0)

    all_texts = []
    for t in topics:
        raw = t.get("talk", {}).get("text", "") or ""
        text = clean_zsxq_text(raw)
        ctime = t.get("create_time", "")[:16]
        title = raw[:80].replace("\n", " ")
        all_texts.append({"text": text, "raw": raw, "time": ctime, "title": title})

    full_text = "\n".join(a["text"] for a in all_texts)

    matches = []
    for _, row in df.iterrows():
        name = str(row["名称"])
        code = str(int(row["代码"])).zfill(6)

        if name not in full_text and code not in full_text:
            continue

        contexts = []
        for a in all_texts:
            if name in a["text"] or code in a["text"]:
                snippet = clean_zsxq_text(a["raw"][:200]).replace("\n", " ")
                contexts.append({"time": a["time"], "snippet": snippet})

        matches.append({
            "code": code,
            "name": name,
            "industry": row.get("行业", ""),
            "price": row.get("现价", 0),
            "cagr": row.get("CAGR", 0),
            "fpe": row.get("前瞻PE", 0),
            "chg10": row.get("10日%", None),
            "sh_latest": row.get("最新股东数", None),
            "sh_max": row.get("2年最高股东", None),
            "sh_ratio": row.get("股东比值", None),
            "mention_count": len(contexts),
            "contexts": contexts,
        })

    return matches


def _fmt(val, fmt, fallback="N/A"):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return fallback
    return fmt.format(val)


def render_cross_report(trade_date, matches, topic_count, date_range):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append(f"# 交叉验证：盈利预测 × 知识星球 — {trade_date}")
    lines.append(f"> 生成时间: {now}")
    lines.append(f"> 星球帖子: {topic_count} 条（{date_range}）| 交叉命中: **{len(matches)}** 只\n")

    lines.append("## 命中列表（按10日涨幅排序）\n")
    lines.append("| # | 代码 | 名称 | 行业 | 现价 | CAGR | 前瞻PE | 10日% | 股东比 | 提及次数 |")
    lines.append("|--:|------|------|------|-----:|-----:|-------:|------:|-------:|---------:|")

    for i, m in enumerate(matches, 1):
        cagr = _fmt(m["cagr"], "{:.0%}")
        fpe = _fmt(m["fpe"], "{:.1f}")
        chg = _fmt(m["chg10"], "{:+.1%}")
        sh = _fmt(m["sh_ratio"], "{:.2f}")
        lines.append(
            f"| {i} | {m['code']} | {m['name']} | {m['industry']} "
            f"| {m['price']:.2f} | {cagr} | {fpe} | {chg} | {sh} | {m['mention_count']} |"
        )

    lines.append("\n## 星球提及详情\n")
    for m in matches:
        lines.append(f"### {m['name']}（{m['code']}）\n")
        for c in m["contexts"][:3]:
            lines.append(f"- **{c['time']}** {c['snippet'][:150]}")
        lines.append("")

    lines.append("---")
    lines.append("*本报告由交叉验证模型自动生成，仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def run_cross(trade_date=None):
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    cookie = load_cookie()

    print("[1/3] 拉取知识星球最近帖子...")
    topics = fetch_recent_topics(cookie, pages=50)
    if not topics:
        print("  ✗ 未拉到帖子，请检查cookie.txt是否过期")
        print(f"  Cookie路径: {COOKIE_PATH}")
        return ""
    dates = sorted(set(t["create_time"][:10] for t in topics))
    date_range = f"{dates[0]} ~ {dates[-1]}" if len(dates) > 1 else dates[0]
    print(f"  ✓ {len(topics)} 条帖子（{date_range}）")

    earnings_path = REPORT_DIR / "earnings" / f"earnings_{trade_date}.xlsx"
    if not earnings_path.exists():
        print(f"  ✗ 未找到盈利预测报告: {earnings_path}")
        print("  请先运行: python run.py --earnings")
        return ""

    print("[2/3] 加载盈利预测筛选结果...")
    df = pd.read_excel(earnings_path, sheet_name=0)
    print(f"  ✓ {len(df)} 只命中股")

    print("[3/3] 交叉验证...")
    matches = cross_reference(topics, earnings_path)
    matches.sort(key=lambda x: x.get("chg10") or -999, reverse=True)
    print(f"  ✓ {len(matches)} 只交叉命中\n")

    if not matches:
        print("无交叉命中")
        return ""

    # 控制台摘要
    print("=" * 70)
    print(f"{'名称':8s} {'代码':8s} {'行业':10s} {'CAGR':>6s} {'fPE':>6s} {'10日%':>7s} {'股东比':>6s} {'提及':>4s}")
    print("-" * 70)
    for m in matches:
        cagr = _fmt(m["cagr"], "{:.0%}")
        fpe = _fmt(m["fpe"], "{:.1f}")
        chg = _fmt(m["chg10"], "{:+.1%}")
        sh = _fmt(m["sh_ratio"], "{:.2f}")
        print(f"{m['name']:8s} {m['code']:8s} {m.get('industry',''):10s} "
              f"{cagr:>6s} {fpe:>6s} {chg:>7s} {sh:>6s} {m['mention_count']:>4d}")

    # 生成报告
    md = render_cross_report(trade_date, matches, len(topics), date_range)
    report_path = REPORT_DIR / f"cross_{trade_date}.md"
    report_path.write_text(md, encoding="utf-8")

    print(f"\n{'='*50}")
    print(f"  ✅ 交叉验证完成！")
    print(f"  📄 报告: {report_path}")
    print(f"  命中: {len(matches)} 只")
    print(f"{'='*50}")

    return str(report_path)


if __name__ == "__main__":
    run_cross()
