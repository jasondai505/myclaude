"""Render claude_prompt.txt → SDK 直调 Claude → 输出 advice 报告。
Called by morning_advice.bat Step 5.
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from anthropic import Anthropic

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
MODEL = "claude-sonnet-4-6-20250514"


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if key:
        return key
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            key = data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        except (json.JSONDecodeError, OSError):
            pass
    return key


def main():
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    yesterday = sys.argv[2] if len(sys.argv) > 2 else (date.today() - timedelta(days=1)).isoformat()

    tpl = (BASE / "claude_prompt.txt").read_text(encoding="utf-8")
    prompt = tpl.replace("%%TODAY%%", today).replace("%%YESTERDAY%%", yesterday)

    advice_path = BASE / "reports" / f"advice_{today}.md"

    try:
        client = Anthropic(
            api_key=_load_api_key(),
            base_url="https://api.deepseek.com/anthropic",
        )
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
            timeout=600,
        )
        parts = []
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                parts.append(block.text)
        output = "\n".join(parts)
    except Exception as e:
        output = f"[ERROR] LLM 调用失败: {e}"

    print(output)

    if output.strip() and len(output) > 500:
        advice_path.write_text(output, encoding="utf-8")
        print("[INFO] advice saved from stdout")
    elif output.strip():
        print("[WARN] advice output too short, not saving (likely error response)")

    print(f"  advice output: {len(output)} chars")


if __name__ == "__main__":
    main()
