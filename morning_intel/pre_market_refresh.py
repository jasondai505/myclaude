"""9:00 盘前增量刷新 — ZSXQ + 唐史主任微博 + 日韩早盘 → Haiku 分析 → 微信推送"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

from llm import call as _llm_call

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
REVIEW_BASE = BASE.parent / "daily_review"
REPORT_DIR = BASE / "reports"

sys.path.insert(0, str(REVIEW_BASE))
from settings import MODEL_AUDIT, LLM_TIMEOUT, ZSYNC_MAX_PAGES
from notify import push


def _fetch_kr_jp() -> str:
    try:
        from data import fetch_kr_jp_markets
        data = fetch_kr_jp_markets()
        if not data:
            return "日韩数据暂不可用"
        lines = []
        for label, q in data.items():
            price = q.get("price", "N/A")
            chg = q.get("change_pct", 0)
            chg_5d = q.get("change_pct_5d", "N/A")
            lines.append(f"- {label}: {price} ({chg:+.2f}%) 5日: {chg_5d}")
        return "\n".join(lines)
    except Exception as e:
        return f"日韩数据获取失败: {e}"


def _fetch_weibo_delta() -> str | None:
    try:
        from weibo_watch import run as weibo_run
        result = weibo_run()
        new_count = result.get("new_posts", 0)
        if new_count == 0:
            return None
        return f"唐史主任司马迁 {new_count} 条新帖（已通过微博监控推送详情）"
    except Exception as e:
        return f"微博获取失败: {e}"


def _fetch_zsxq_delta(today: str) -> str | None:
    try:
        from zsxq_collector import sync as zsxq_sync
        from store import query_zsxq_by_date

        zsxq_sync(max_pages=ZSYNC_MAX_PAGES)
        posts = query_zsxq_by_date(today)

        state_path = REPORT_DIR / "intraday_state.json"
        cutoff = None
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            last_run = state.get("last_run")
            if last_run:
                cutoff = datetime.fromisoformat(last_run)

        new_posts = []
        for p in posts:
            ct = p.get("create_time", "")
            if ct and cutoff and ct > cutoff.isoformat():
                new_posts.append(p)

        if not new_posts:
            return None

        lines = [f"知识星球新增 {len(new_posts)} 帖"]
        for p in new_posts[:5]:
            title = (p.get("title") or "")[:100]
            author = p.get("author", "")
            lines.append(f"- {author}: {title}")
        return "\n".join(lines)
    except Exception as e:
        return f"星球获取失败: {e}"


def _read_morning_context(today: str) -> str:
    json_path = REPORT_DIR / f"morning_{today}.json"
    if not json_path.exists():
        return ""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        events = [e.get("name", "") for e in data.get("events", [])]
        return f"盘前已识别事件: {' | '.join(events[:3])}"
    except Exception:
        return ""


def _haiku_analyze(context: str) -> str | None:
    prompt = (
        "你是A股盘前监控助手。距离开盘还有约30分钟，以下是9:00盘前增量刷新数据。\n\n"
        f"{context}\n\n"
        "请简洁输出（每条1-2句）：\n"
        "1. 新增催化/变化：是否有值得关注的增量事件或信号\n"
        "2. 日韩情绪映射：日韩开盘方向对A股情绪的指引\n"
        "3. 今日开盘预判：综合给出开盘可能的热点方向\n\n"
        "保持简洁，不要客套。无增量信息就直说。"
    )

    out = _llm_call("scan", prompt, timeout=LLM_TIMEOUT).strip()
    return out if out and not out.startswith("[ERROR]") else None


def run(today: str = None) -> dict:
    if today is None:
        today = date.today().isoformat()
    now = datetime.now()

    print(f"[pre_market] {today} {now.strftime('%H:%M')} 盘前刷新...")

    # 1. 采集增量数据
    print("[pre_market] 采集增量...")
    morning_ctx = _read_morning_context(today)
    kr_jp = _fetch_kr_jp()
    weibo_delta = _fetch_weibo_delta()
    zsxq_delta = _fetch_zsxq_delta(today)

    # 2. 组装上下文
    parts = [kr_jp]
    if weibo_delta:
        parts.append(weibo_delta)
    if zsxq_delta:
        parts.append(zsxq_delta)
    if morning_ctx:
        parts.append(morning_ctx)

    context = "\n\n".join(parts)
    print(f"[pre_market] 上下文 {len(context)} 字符")

    # 3. Haiku 分析
    print("[pre_market] Haiku 分析...")
    verdict = _haiku_analyze(context)

    # 4. 推送
    pushed = False
    if verdict:
        time_str = now.strftime("%H:%M")
        content = f"**{time_str} 盘前刷新**\n\n{verdict[:500]}"
        pushed = push(f"\U0001F4E1 盘前刷新 {time_str}", content)
        if pushed:
            print(f"[pre_market] pushed: {verdict[:80]}")

    # 5. 写入记录
    record = {
        "time": now.isoformat(),
        "kr_jp": kr_jp[:200],
        "has_weibo": weibo_delta is not None,
        "has_zsxq": zsxq_delta is not None,
        "pushed": pushed,
    }
    rec_path = REPORT_DIR / f"pre_market_{today}.json"
    rec_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"status": "ok", "pushed": pushed, "verdict_len": len(verdict) if verdict else 0}


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    result = run(today=t)
    print(f"[pre_market] {result}")
