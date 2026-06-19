"""将 advice markdown 转为 PNG 并上传到远程服务器。
用法:
    python daily_review/upload_advice.py                              # 今天
    python daily_review/upload_advice.py 2026-06-16                   # 指定日期
    python daily_review/upload_advice.py path/to/advice_2026-06-16.md # 指定文件
"""
import sys, json, base64
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from md_to_image_post import md_to_image

URL = "http://data.xiaoxinren.cn:9003/Api/Agent/PostDaily"


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    base = Path(__file__).resolve().parent / "reports" / "advice"

    if arg and arg.endswith(".md"):
        md_path = Path(arg)
        if not md_path.is_absolute():
            md_path = Path.cwd() / md_path
    elif arg:
        md_path = base / f"advice_{arg}.md"
    else:
        today = date.today().isoformat()
        md_path = base / f"advice_{today}.md"
        if not md_path.exists():
            md_path = base / f"advice_{today}_0730.md"

    if not md_path.exists():
        print(f"[SKIP] advice 文件不存在: {md_path}")
        return

    print(f"Converting {md_path} to PNG...")
    png_bytes = md_to_image(str(md_path))
    print(f"PNG: {len(png_bytes)} bytes ({len(png_bytes)/1024:.1f} KB)")

    b64 = base64.b64encode(png_bytes).decode()
    print(f"Base64: {len(b64)} chars")

    print(f"POSTing to {URL}...")
    try:
        r = requests.post(URL, data=json.dumps(b64),
                         headers={"Content-Type": "application/json"}, timeout=60)
        print(f"status={r.status_code} body={r.text[:200]}")
        if r.ok:
            print("[OK] 上传成功")
        else:
            print(f"[FAIL] 上传失败: {r.status_code}")
    except Exception as e:
        print(f"[FAIL] 上传异常: {e}")
        raise


if __name__ == "__main__":
    main()
