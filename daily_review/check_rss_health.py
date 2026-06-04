"""WeWe-RSS 健康检查 — 检测可达性+数据新鲜度，异常时微信告警。

退出码: 0=健康, 1=不可达, 2=数据陈旧(>24h), 3=数据空
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent / "morning_intel"))
from notify import push

RSS_URL = "http://111.231.44.12:4000/feeds/all.json?limit=3"
TIMEOUT = 30
STALE_HOURS = 24


def _now():
    return datetime.now(timezone.utc)


def check() -> tuple[int, str, str | None]:
    """返回 (exit_code, status_label, latest_article_time_or_none)"""

    try:
        req = Request(RSS_URL, headers={"User-Agent": "RSS-HealthCheck/1.0",
                      "Accept": "application/json"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return 1, f"RSS 不可达: {e}", None

    items = data.get("items", [])
    if not items:
        return 3, "RSS 返回空（无文章）", None

    latest = items[0]
    dm = latest.get("date_modified", "")
    if dm:
        try:
            latest_dt = datetime.strptime(dm[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            latest_dt = None
    else:
        latest_dt = None

    if latest_dt is None:
        return 2, "无法解析最新文章时间", dm[:19] if dm else None

    age = _now() - latest_dt
    latest_str = latest_dt.strftime("%Y-%m-%d %H:%M UTC")

    if age > timedelta(hours=STALE_HOURS):
        return 2, f"数据陈旧: 最新文章 {latest_str}（{age.total_seconds()/3600:.0f}h 前）", latest_str

    return 0, f"健康: 最新 {latest_str}", latest_str


def main():
    code, msg, latest = check()

    tz_8 = timezone(timedelta(hours=8))
    now_str = _now().astimezone(tz_8).strftime("%Y-%m-%d %H:%M")

    if code == 0:
        print(f"[{now_str}] ✅ {msg}")
    else:
        print(f"[{now_str}] ❌ [{code}] {msg}")
        icon = "🔴" if code == 1 else "⚠️"
        push(
            f"{icon} 公众号RSS异常",
            f"状态: {msg}\n\n请检查 WeWe-RSS 是否需要重新登录: http://111.231.44.12:4000/dash"
        )

    sys.exit(code)


if __name__ == "__main__":
    main()
