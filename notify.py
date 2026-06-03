"""微信通知 CLI — 通过 PushPlus 发送微信消息
用法: python notify.py "标题" "内容"
"""
from __future__ import annotations

import sys

sys.path.insert(0, "morning_intel")
from notify import push

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python notify.py \"标题\" \"内容\"")
        sys.exit(1)

    title = sys.argv[1]
    content = sys.argv[2]
    ok = push(title, content)
    if ok:
        print(f"[OK] 已发送: {title}")
    else:
        print(f"[FAIL] 发送失败: {title}")
        sys.exit(1)
