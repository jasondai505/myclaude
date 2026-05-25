"""Run claude -p with rendered prompt template, capture output to log + advice file.

Eliminates batch escaping headaches — template rendering and output handling in Python.
Called by morning_advice.bat Step 4.
"""
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / "reports" / "_cron_advice.log"


def main():
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    yesterday = sys.argv[2] if len(sys.argv) > 2 else (date.today() - timedelta(days=1)).isoformat()

    tpl = (BASE / "claude_prompt.txt").read_text(encoding="utf-8")
    prompt = tpl.replace("%%TODAY%%", today).replace("%%YESTERDAY%%", yesterday)

    advice_path = BASE / "reports" / f"advice_{today}.md"

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--dangerously-skip-permissions"],
            capture_output=True, text=True, timeout=600,
            cwd=str(BASE),
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
    except subprocess.TimeoutExpired:
        output = "[TIMEOUT] claude -p did not complete within 10 minutes"
    except FileNotFoundError:
        output = "[ERROR] claude CLI not found on PATH"
    except Exception as e:
        output = f"[ERROR] claude invocation failed: {e}"

    with open(LOG, "a", encoding="utf-8") as f:
        f.write(output + "\n")

    if not advice_path.exists() and output.strip():
        advice_path.write_text(output, encoding="utf-8")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[INFO] advice saved from stdout (claude did not write file)\n")

    print(f"  advice output: {len(output)} chars")


if __name__ == "__main__":
    main()
