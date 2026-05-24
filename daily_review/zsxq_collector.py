"""知识星球调研纪要采集模块

用法:
    python zsxq_collector.py sync [--pages N]        # 同步帖子（增量）
    python zsxq_collector.py search <关键词>          # 按关键词搜索
    python zsxq_collector.py search --code 300476    # 按股票代码搜索
    python zsxq_collector.py recent [--days N]       # 最近N天帖子
    python zsxq_collector.py stats                   # 采集统计
"""
import sys
import os
import json
import re
import time
import random
import argparse

if sys.platform == "win32":
    os.system("")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from tqdm import tqdm
from zsxq_cross import load_cookie, fetch_topics, clean_zsxq_text, COOKIE_PATH
import store


def _classify_topic(title: str, text: str) -> str:
    if any(kw in title for kw in ["复盘", "综述", "总结"]):
        return "review"
    if any(kw in text[:200] for kw in ["推荐", "看好", "强call", "强烈"]):
        return "research"
    if "【" in title and "】" in title:
        return "research"
    return "general"


def _extract_stock_codes(text: str) -> list[str]:
    return list(set(re.findall(r'[036]\d{5}', text)))


def _parse_topic(raw: dict) -> dict:
    talk = raw.get("talk", {})
    raw_text = talk.get("text", "") or ""
    text = clean_zsxq_text(raw_text)
    title = text.split("\n")[0][:100] if text else ""
    author = talk.get("owner", {}).get("name", "")

    return {
        "topic_id": str(raw.get("topic_id", "")),
        "create_time": raw.get("create_time", ""),
        "author": author,
        "title": title,
        "text": text,
        "topic_type": _classify_topic(title, text),
        "readers_count": raw.get("readers_count", 0),
        "likes_count": raw.get("likes_count", 0),
        "comments_count": raw.get("comments_count", 0),
        "stock_codes": _extract_stock_codes(text),
    }


def sync(max_pages=50, full=False, before=None):
    cookie = load_cookie()
    store.init_zsxq_table()

    total_new = 0
    total_seen = 0
    end_time = before.replace("+0800", "%2B0800") if before else None

    pbar = tqdm(range(max_pages), desc="拉取星球帖子", unit="页",
                bar_format="  {desc}: {n_fmt}/{total_fmt}页 [{elapsed}] 新增{postfix}")
    pbar.set_postfix_str("0条")

    consecutive = 0
    for _ in pbar:
        data = None
        for attempt in range(3):
            try:
                data = fetch_topics(cookie, 20, end_time)
                if data and not data.get("succeeded"):
                    if attempt < 2:
                        time.sleep(30 + random.random() * 10)
                        data = None
                        continue
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(5)
                else:
                    print(f"\n  API请求失败（3次重试）: {e}")

        if data is None:
            break

        if not data.get("succeeded"):
            print(f"\n  API限流，已拉取 {total_seen} 条")
            break

        topics_raw = data.get("resp_data", {}).get("topics", [])
        if not topics_raw:
            break

        parsed = [_parse_topic(t) for t in topics_raw]
        existing = store.zsxq_batch_existing([p["topic_id"] for p in parsed])
        new_topics = [p for p in parsed if p["topic_id"] not in existing]

        if new_topics:
            store.save_zsxq_topics_batch(new_topics)
            total_new += len(new_topics)

        total_seen += len(parsed)
        pbar.set_postfix_str(f"{total_new}条")

        if not new_topics and not full:
            break

        ct = topics_raw[-1]["create_time"]
        end_time = ct.replace("+0800", "%2B0800")

        consecutive += 1
        if consecutive % 3 == 0:
            time.sleep(25 + random.random() * 10)
        else:
            time.sleep(3 + random.random() * 2)

    pbar.close()
    print(f"  同步完成: 扫描 {total_seen} 条，新增 {total_new} 条")
    return total_new


def search(keyword=None, code=None, date_from=None, date_to=None, limit=50):
    store.init_zsxq_table()
    results = store.search_zsxq(keyword=keyword, code=code,
                                date_from=date_from, date_to=date_to, limit=limit)
    if not results:
        print("未找到匹配的帖子")
        return

    print(f"找到 {len(results)} 条帖子:\n")
    for r in results:
        _print_topic(r)


def recent(days=7, limit=50):
    store.init_zsxq_table()
    results = store.recent_zsxq(days=days, limit=limit)
    if not results:
        print(f"最近 {days} 天无帖子")
        return

    print(f"最近 {days} 天共 {len(results)} 条帖子:\n")
    for r in results:
        _print_topic(r)


def stats():
    store.init_zsxq_table()
    s = store.zsxq_stats()
    if s["total"] == 0:
        print("数据库为空，请先运行 sync")
        return

    print(f"{'='*50}")
    print(f"  知识星球采集统计")
    print(f"{'='*50}")
    print(f"  总帖子数: {s['total']}")
    print(f"  时间范围: {s['earliest'][:10]} ~ {s['latest'][:10]}")
    print(f"  作者数:   {s['authors']}")
    print(f"\n  类型分布:")
    for t, cnt in s["by_type"].items():
        label = {"review": "复盘/综述", "research": "研报/推荐", "general": "其他"}.get(t, t)
        print(f"    {label}: {cnt}")

    top = store.zsxq_top_authors(10)
    if top:
        print(f"\n  发帖最多的作者:")
        for name, cnt in top:
            print(f"    {name}: {cnt}条")
    print(f"{'='*50}")


def _append_topic_md(lines: list, r: dict):
    title = r.get("title", "")[:80]
    readers = r.get("readers_count", 0)
    codes_str = ""
    if r.get("stock_codes"):
        try:
            codes = json.loads(r["stock_codes"]) if isinstance(r["stock_codes"], str) else r["stock_codes"]
            if codes:
                codes_str = f" `{','.join(codes[:5])}`"
        except (json.JSONDecodeError, TypeError):
            pass
    lines.append(f"**{title}**{codes_str}（{readers}阅读）\n")
    text = r.get("text", "") or ""
    body = text[len(title):].strip() if text.startswith(title) else text.strip()
    if body:
        preview = body[:500].replace("\n", "\n> ")
        lines.append(f"> {preview}")
        if len(body) > 500:
            lines.append(f"> ...（共{len(body)}字）")
    lines.append("")


def export(days=7, date_from=None, date_to=None):
    from config import REPORT_DIR
    from datetime import datetime, timedelta
    from collections import defaultdict

    store.init_zsxq_table()

    if date_from:
        results = store.search_zsxq(date_from=date_from, date_to=date_to, limit=99999)
    else:
        results = store.recent_zsxq(days=days, limit=99999)

    if not results:
        print("无帖子可导出")
        return ""

    by_date = defaultdict(list)
    for r in results:
        d = r["create_time"][:10] if r.get("create_time") else "unknown"
        by_date[d].append(r)

    dates_sorted = sorted(by_date.keys(), reverse=True)
    date_range = f"{dates_sorted[-1]}~{dates_sorted[0]}" if len(dates_sorted) > 1 else dates_sorted[0]

    lines = []
    lines.append(f"# 知识星球调研纪要 — {date_range}")
    lines.append(f"> 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 帖子数: {len(results)} | 日期范围: {date_range}\n")

    type_counts = defaultdict(int)
    all_codes = defaultdict(int)
    for r in results:
        type_counts[r.get("topic_type", "general")] += 1
        if r.get("stock_codes"):
            try:
                codes = json.loads(r["stock_codes"]) if isinstance(r["stock_codes"], str) else r["stock_codes"]
                for c in codes:
                    all_codes[c] += 1
            except (json.JSONDecodeError, TypeError):
                pass

    type_labels = {"research": "研报/推荐", "review": "复盘/综述", "general": "其他"}
    type_str = "、".join(f"{type_labels.get(k,k)} {v}条" for k, v in type_counts.items())
    lines.append(f"**分布**: {type_str}\n")

    if all_codes:
        top_codes = sorted(all_codes.items(), key=lambda x: -x[1])[:20]
        lines.append(f"**高频股票代码**: {', '.join(f'{c}({n}次)' for c, n in top_codes)}\n")

    for date in dates_sorted:
        posts = by_date[date]
        lines.append(f"---\n## {date}（{len(posts)}条）\n")

        research = [p for p in posts if p.get("topic_type") == "research"]
        others = [p for p in posts if p.get("topic_type") != "research"]

        if research:
            lines.append("### 研报/推荐\n")
            for r in research:
                _append_topic_md(lines, r)
            lines.append("")

        if others:
            lines.append("### 纪要/资讯\n")
            for r in others:
                _append_topic_md(lines, r)
            lines.append("")

    report_name = f"zsxq_{dates_sorted[0]}.md"
    report_path = REPORT_DIR / report_name
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✅ 报告已导出: {report_path}")
    return str(report_path)


def _print_topic(r: dict):
    ct = r["create_time"][:16] if r.get("create_time") else ""
    tp = {"review": "[复盘]", "research": "[研报]"}.get(r.get("topic_type", ""), "")
    title = r.get("title", "")[:60]
    author = r.get("author", "")
    readers = r.get("readers_count", 0)
    codes_str = ""
    if r.get("stock_codes"):
        try:
            codes = json.loads(r["stock_codes"]) if isinstance(r["stock_codes"], str) else r["stock_codes"]
            if codes:
                codes_str = f"  [{','.join(codes[:5])}]"
        except (json.JSONDecodeError, TypeError):
            pass
    print(f"  {ct} {tp} {author}: {title}{codes_str}  ({readers}阅读)")


def main():
    parser = argparse.ArgumentParser(description="知识星球调研纪要采集")
    sub = parser.add_subparsers(dest="cmd")

    p_sync = sub.add_parser("sync", help="同步帖子")
    p_sync.add_argument("--pages", type=int, default=50, help="最大拉取页数")
    p_sync.add_argument("--full", action="store_true", help="全量同步（不跳过已有帖子）")
    p_sync.add_argument("--before", type=str, help="从指定时间往前拉（如 2026-05-14T00:28:06.140+0800）")

    p_search = sub.add_parser("search", help="搜索帖子")
    p_search.add_argument("keyword", nargs="?", help="关键词")
    p_search.add_argument("--code", help="股票代码")
    p_search.add_argument("--from", dest="date_from", help="起始日期 YYYY-MM-DD")
    p_search.add_argument("--to", dest="date_to", help="截止日期 YYYY-MM-DD")
    p_search.add_argument("--limit", type=int, default=50)

    p_recent = sub.add_parser("recent", help="最近帖子")
    p_recent.add_argument("--days", type=int, default=7)
    p_recent.add_argument("--limit", type=int, default=50)

    p_export = sub.add_parser("export", help="导出为Markdown报告")
    p_export.add_argument("--days", type=int, default=7, help="导出最近N天")
    p_export.add_argument("--from", dest="date_from", help="起始日期 YYYY-MM-DD")
    p_export.add_argument("--to", dest="date_to", help="截止日期 YYYY-MM-DD")

    sub.add_parser("stats", help="采集统计")

    args = parser.parse_args()

    if args.cmd == "sync":
        if not COOKIE_PATH.exists():
            print(f"  未找到cookie文件: {COOKIE_PATH}")
            return
        before = args.before
        if not before and args.full:
            import sqlite3
            try:
                conn = sqlite3.connect(store.DB_PATH)
                row = conn.execute("SELECT MIN(create_time) FROM zsxq_topics").fetchone()
                conn.close()
                if row and row[0]:
                    before = row[0]
                    print(f"  从最早记录 {before[:16]} 往前补充...")
            except Exception:
                pass
        sync(max_pages=args.pages, full=args.full, before=before)
    elif args.cmd == "search":
        if not args.keyword and not args.code:
            print("请提供关键词或 --code")
            return
        search(keyword=args.keyword, code=args.code,
               date_from=args.date_from, date_to=args.date_to,
               limit=args.limit)
    elif args.cmd == "recent":
        recent(days=args.days, limit=args.limit)
    elif args.cmd == "export":
        export(days=args.days, date_from=args.date_from, date_to=args.date_to)
    elif args.cmd == "stats":
        stats()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
