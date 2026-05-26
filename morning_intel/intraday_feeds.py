"""盘中增量语料采集 — 星球增量 sync + drops/ 目录监控"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
REVIEW_BASE = BASE.parent / "daily_review"
REPORT_DIR = BASE / "reports"
DROPS_DIR = BASE / "drops"
STATE_PATH = REPORT_DIR / "intraday_state.json"

sys.path.insert(0, str(REVIEW_BASE))
from settings import ZSYNC_MAX_PAGES


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

    # 2. 查询新增帖子（首次运行只设基线，不生成 delta）
    all_posts = query_zsxq_by_date(today)
    new_posts = []
    if not is_first_run:
        for p in all_posts:
            ct = p.get("create_time", "")
            if ct and ct > last_run.isoformat():
                new_posts.append(p)

    # 3. 扫描 drops/
    new_drops = [] if is_first_run else _scan_drops(last_run)

    new_count = len(new_posts) + len(new_drops)

    # 4. 写增量 delta（首次运行跳过）
    if new_count > 0:
        delta_path = REPORT_DIR / f"intraday_delta_{today}.md"
        lines = [
            f"# 盘中增量情报 {today} {now.strftime('%H:%M')}",
            f"新增星球帖子: {len(new_posts)} | 新增投放: {len(new_drops)}",
            "",
        ]
        for p in new_posts:
            lines.append(_format_zsxq_post(p))

        for f in new_drops:
            content = f.read_text(encoding="utf-8").strip()
            if content:
                lines.append(f"## drop: {f.stem}")
                lines.append("")
                lines.append(content)
                lines.append("")

        delta_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[intraday] delta 已写入: {delta_path} ({new_count} 条)")

    # 5. 更新状态
    state["last_run"] = now.isoformat()
    state["new_posts"] = len(new_posts)
    state["new_drops"] = len(new_drops)
    _save_state(state)

    return {"status": "ok", "new_posts": len(new_posts), "new_drops": len(new_drops)}


if __name__ == "__main__":
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    result = run(today=today)
    print(f"[intraday] {result}")
