"""微博语料监控 — 唐史主任司马迁 → 方向+标的提取 → 微信推送"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

from llm import call as _llm_call

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
REPORT_DIR = BASE / "reports"
STATE_PATH = REPORT_DIR / "weibo_state.json"

from settings import WEIBO_COOKIE, WEIBO_UID, MODEL_AUDIT, LLM_TIMEOUT
from notify import push

API_POSTS = "https://weibo.com/ajax/statuses/mymblog"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": WEIBO_COOKIE,
    "Referer": f"https://weibo.com/u/{WEIBO_UID}",
    "X-Requested-With": "XMLHttpRequest",
}


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"last_check": None}


def _save_state(state: dict):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_posts(page: int = 1) -> list[dict]:
    try:
        r = requests.get(API_POSTS, params={"uid": WEIBO_UID, "page": page, "feature": 0},
                         headers=HEADERS, timeout=15)
        data = r.json()
        if data.get("ok") != 1:
            print(f"[weibo] API error: {data}")
            return []
        return data.get("data", {}).get("list", [])
    except Exception as e:
        print(f"[weibo] fetch error: {e}")
        return []


def _clean_html(text: str) -> str:
    text = text.replace("<br />", "\n").replace("<br/>", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _format_post(p: dict) -> str:
    ts = p.get("created_at", "")
    text = _clean_html(p.get("text_raw", p.get("text", "")))
    reposts = p.get("reposts_count", 0)
    return f"[{ts}] reposts={reposts}\n{text[:500]}"


def _haiku_analyze(posts_text: str) -> str | None:
    prompt = (
        "你是A股盘中监控助手。下面是唐史主任司马迁的最新微博帖子。\n"
        "他擅长宏观大局观+产业审美，帖子往往隐含方向判断。\n"
        "如果有投资相关的内容：用1-2句话提炼他的观点/方向，并尝试识别涉及的板块/赛道。\n"
        "如果帖子与二级市场投资无关（纯生活/社会评论）：只回复「无关」。\n"
        "保持简洁，不要客套。\n\n"
        f"{posts_text[:3000]}"
    )

    out = _llm_call("synthesis", prompt, timeout=LLM_TIMEOUT).strip()
    if not out or out == "无关" or out.startswith("[ERROR]"):
        return None
    return out


def run() -> dict:
    now = datetime.now()
    state = _load_state()
    last_check_str = state.get("last_check")

    posts = _fetch_posts(page=1)
    if not posts:
        return {"status": "error", "message": "no posts", "posts_data": []}

    # 首次运行只设基线
    last_post_id = state.get("last_post_id")
    if last_post_id is None:
        latest_id = max(
            (int(p.get("id", 0)) for p in posts),
            default=0
        )
        state["last_post_id"] = latest_id
        state["last_check"] = now.isoformat()
        _save_state(state)
        print(f"[weibo] baseline set, {len(posts)} posts, last_id: {latest_id}")
        posts_data = [
            {
                "created_at": p.get("created_at", ""),
                "text": _clean_html(p.get("text_raw", p.get("text", ""))),
                "reposts_count": p.get("reposts_count", 0),
                "id": p.get("id", ""),
            }
            for p in posts
        ]
        return {"status": "ok", "new_posts": len(posts), "pushed": False, "posts_data": posts_data}

    # Diff by post ID (reliable, no timezone issues)
    new_posts = [p for p in posts if int(p.get("id", 0)) > last_post_id]

    if new_posts:
        state["last_post_id"] = max(int(p.get("id", 0)) for p in new_posts)
    state["last_check"] = now.isoformat()
    _save_state(state)

    if not new_posts:
        return {"status": "ok", "new_posts": 0, "pushed": False, "posts_data": []}

    print(f"[weibo] {len(new_posts)} new posts")
    posts_text = "\n---\n".join(_format_post(p) for p in new_posts)

    print("[weibo] Haiku analyzing...")
    verdict = _haiku_analyze(posts_text)
    pushed = False
    if verdict:
        content = f"**唐史主任司马迁** {len(new_posts)}条新帖\n\n> {verdict[:300]}"
        pushed = push(f"微博情报", content)
        if pushed:
            print(f"[weibo] pushed: {verdict[:80]}")

    posts_data = [
        {
            "created_at": p.get("created_at", ""),
            "text": _clean_html(p.get("text_raw", p.get("text", ""))),
            "reposts_count": p.get("reposts_count", 0),
            "id": p.get("id", ""),
        }
        for p in new_posts
    ]

    return {
        "status": "ok",
        "new_posts": len(new_posts),
        "pushed": pushed,
        "verdict": verdict,
        "posts_data": posts_data,
    }


if __name__ == "__main__":
    result = run()
    print(f"[weibo] {result}")
