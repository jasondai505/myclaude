"""AskUserQuestion 超时提醒 — 3分钟无回复则微信推送。

由 PostToolUse hook 后台触发。
用法: python _remind_pending.py '<json_questions>'
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta


def _push(title: str, content: str):
    try:
        import requests
        token = "9cdb736206654981a8b230bee39ee56d"
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": title, "content": content,
                  "topic": "morning_intel"},
            timeout=10,
        )
        ok = r.json().get("code") == 200
        if not ok:
            print(f"[remind] push fail: {r.json()}")
        return ok
    except Exception as e:
        print(f"[remind] push error: {e}")
        return False


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--file":
        try:
            data = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        except Exception:
            return
    else:
        raw = sys.argv[1] if len(sys.argv) > 1 else ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

    questions = data if isinstance(data, list) else [data]
    if not questions:
        return

    header = questions[0].get("header", "待选择")
    q_text = questions[0].get("question", "需要你的决定")

    time.sleep(180)

    tz8 = timezone(timedelta(hours=8))
    now_str = datetime.now(tz8).strftime("%H:%M")
    _push(
        f"⏰ [{header}] 需要你的选择",
        f"问题: {q_text}\n\n已等待 3 分钟无回复，请在 Claude Code 中做出选择。\n触发时间: {now_str}"
    )


if __name__ == "__main__":
    main()
