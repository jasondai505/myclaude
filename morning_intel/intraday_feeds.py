"""盘中增量语料采集 — 星球增量 sync + drops/ 目录监控 + Haiku 实时分析 + 微信推送"""
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
DROPS_DIR = BASE / "drops"
STATE_PATH = REPORT_DIR / "intraday_state.json"

sys.path.insert(0, str(REVIEW_BASE))
from settings import ZSYNC_MAX_PAGES
from settings import MODEL_AUDIT, LLM_TIMEOUT
from notify import push


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"last_run": None}


def _save_state(state: dict):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _scan_drops(since: datetime | None) -> list[Path]:
    if not DROPS_DIR.exists():
        return []
    new_files = []
    for f in sorted(DROPS_DIR.iterdir()):
        if f.suffix not in (".txt", ".md"):
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if since is None or mtime > since:
            new_files.append(f)
    return new_files


def _format_zsxq_post(r: dict) -> str:
    title = (r.get("title") or "")[:120]
    author = r.get("author", "")
    ct = (r.get("create_time") or "")[:16]
    text = r.get("text") or ""
    body = text[len(title):].strip() if text.startswith(title) else text.strip()
    preview = body[:300].replace("\n", "\n> ") if body else ""

    codes_str = ""
    try:
        codes = json.loads(r.get("stock_codes") or "[]")
        if codes:
            codes_str = f" `{','.join(codes[:5])}`"
    except (json.JSONDecodeError, TypeError):
        pass

    buf = [f"### {ct} {author}: {title}{codes_str}"]
    if preview:
        buf.append(f"> {preview}")
    buf.append("")
    return "\n".join(buf)


def _haiku_analyze(delta_text: str) -> str | None:
    """Haiku 快速扫描增量帖子，判断是否有值得推送的内容。返回 None 表示无值得关注的内容。"""
    prompt = (
        "你是A股盘中监控助手。下面是盘中新增的知识星球帖子。\n"
        "如果有值得关注的增量信息，按以下格式回复：\n"
        "事件: <一句话描述>\n"
        "标的: <名称>(<6位代码>) <方向>\n"
        "（每事件最多3只标的，没有代码的不要列）\n"
        "如果没有值得关注的内容，只回复「无」。\n\n"
        f"{delta_text[:3000]}"
    )

    out = _llm_call("scan", prompt, timeout=LLM_TIMEOUT).strip()
    if not out or out == "无" or out.startswith("[ERROR]"):
        return None
    return out


def _morning_context(today: str) -> str:
    """读取今早盘前主题摘要，给 Haiku 做参考。"""
    json_path = REPORT_DIR / f"morning_{today}.json"
    if not json_path.exists():
        return ""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        summary = data.get("summary", "")
        events = [e.get("name", "") for e in data.get("events", [])]
        return f"盘前主题: {summary}\n盘前事件: {'; '.join(events[:3])}"
    except Exception:
        return ""


def run(today: str = None) -> dict:
    if today is None:
        today = date.today().isoformat()

    state = _load_state()
    last_run_str = state.get("last_run")
    last_run = datetime.fromisoformat(last_run_str) if last_run_str else None

    # 1. 增量同步星球
    try:
        from zsxq_collector import sync as zsxq_sync
        from store import query_zsxq_by_date
        zsxq_sync(max_pages=ZSYNC_MAX_PAGES)
    except Exception as e:
        print(f"[intraday] zsxq sync 失败: {e}")
        return {"status": "error", "message": str(e)}

    now = datetime.now()
    is_first_run = last_run is None

    # 2. 增量刷新微博
    new_weibo_posts = []
    if not is_first_run:
        try:
            import weibo_watch
            wb_result = weibo_watch.run()
            wb_posts = wb_result.get("posts_data", [])
            for p in wb_posts:
                ct = p.get("created_at", "")
                if ct and ct > (last_run_str or ""):
                    new_weibo_posts.append(p)
            if new_weibo_posts:
                print(f"[intraday] 微博新增 {len(new_weibo_posts)} 帖")
        except Exception as e:
            print(f"[intraday] weibo 刷新失败: {e}")

    # 3. 查询星球新增帖子（首次运行只设基线，不生成 delta）
    all_posts = query_zsxq_by_date(today)
    new_posts = []
    if not is_first_run:
        for p in all_posts:
            ct = p.get("create_time", "")
            if ct and ct > last_run.isoformat():
                new_posts.append(p)

    # 4. 扫描 drops/
    new_drops = [] if is_first_run else _scan_drops(last_run)

    new_count = len(new_posts) + len(new_drops) + len(new_weibo_posts)

    # 5. 写增量 delta + Haiku 分析 + 推送（首次运行跳过）
    pushed = False
    if new_count > 0:
        delta_path = REPORT_DIR / f"intraday_delta_{today}.md"
        lines = [
            f"# 盘中增量情报 {today} {now.strftime('%H:%M')}",
            f"星球: {len(new_posts)} | 微博: {len(new_weibo_posts)} | 投放: {len(new_drops)}",
            "",
        ]
        for p in new_posts:
            lines.append(_format_zsxq_post(p))

        for wp in new_weibo_posts:
            ts = wp.get("created_at", "")
            text = wp.get("text", "")[:300]
            lines.append(f"## 微博 {ts}")
            lines.append("")
            lines.append(text)
            lines.append("")

        for f in new_drops:
            content = f.read_text(encoding="utf-8").strip()
            if content:
                lines.append(f"## drop: {f.stem}")
                lines.append("")
                lines.append(content)
                lines.append("")

        delta_text = "\n".join(lines)
        delta_path.write_text(delta_text, encoding="utf-8")
        print(f"[intraday] delta 已写入: {delta_path} ({new_count} 条)")

        print("[intraday] Haiku 实时分析...")
        context = _morning_context(today)
        analysis_input = delta_text
        if context:
            analysis_input = f"{context}\n\n{delta_text}"
        verdict = _haiku_analyze(analysis_input)
        if verdict:
            now_str = now.strftime("%H:%M")
            emoji = "🟢" if "利好" in verdict or "机会" in verdict else "🟡"
            msg = (
                f"**{now_str} 盘中增量** (星球{len(new_posts)}/微博{len(new_weibo_posts)}/投放{len(new_drops)})\n\n"
                f"{verdict[:800]}"
            )
            pushed = push(f"🔔 盘中情报 {now_str}", msg)
            if pushed:
                print(f"[intraday] 已推送: {verdict[:80]}")

    # 5. 更新状态
    state["last_run"] = now.isoformat()
    state["new_posts"] = len(new_posts)
    state["new_drops"] = len(new_drops)
    _save_state(state)

    return {
        "status": "ok",
        "new_posts": len(new_posts),
        "new_drops": len(new_drops),
        "pushed": pushed,
    }


if __name__ == "__main__":
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    result = run(today=today)
    print(f"[intraday] {result}")
