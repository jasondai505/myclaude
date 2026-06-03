"""WeWe-RSS 存活性检测 — 断连时 PushPlus 通知"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding="utf-8")

RSS_URL = "http://111.231.44.12:4000/feeds/all.json?limit=10"
TIMEOUT = 30


def _check() -> dict:
    try:
        req = Request(RSS_URL, headers={"User-Agent": "WeWeWatchdog/1.0",
                      "Accept": "application/json"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"alive": False, "article_count": 0, "latest_date": "",
                "error": str(e)}

    items = data.get("items", [])
    if not items:
        return {"alive": True, "article_count": 0, "latest_date": "无",
                "warning": "RSS 可达但无文章（可能需要刷新公众号登录）"}

    latest = items[0]
    pub = latest.get("date_modified", "")[:10]
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d")
    recent = sum(1 for it in items
                 if (it.get("date_modified", "")[:10]) >= cutoff)

    return {"alive": True, "article_count": len(items), "recent_24h": recent,
            "latest_date": pub}


def _push(msg: str):
    try:
        from morning_intel.notify import push
        push("WeWe-RSS 监控", msg)
    except ImportError:
        pass


def main():
    result = _check()

    if result["alive"] and result.get("recent_24h", 0) > 0:
        print(f"[OK] RSS 正常, 最近24h {result['recent_24h']} 篇, "
              f"最新 {result['latest_date']}")
        return 0

    if result["alive"] and result.get("recent_24h", 0) == 0:
        msg = (f"⚠️ WeWe-RSS 24h 内无新文章\n"
               f"最新文章: {result['latest_date']}\n"
               f"可能需要重新登录微信公众号")
        print(f"[WARN] {msg}")
        _push(msg)
        return 0

    err = result.get("error", "未知")
    msg = f"🔴 WeWe-RSS 不可达\n错误: {err}\n请检查 Docker 容器状态"
    print(f"[DEAD] {msg}")
    _push(msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
