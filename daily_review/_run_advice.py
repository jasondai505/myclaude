"""Run claude -p with rendered prompt template, capture output to log + advice file.

Eliminates batch escaping headaches — template rendering and output handling in Python.
Called by morning_advice.bat Step 4.
"""
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure stdout can handle UTF-8 characters from claude output (bat redirects to file)
sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent


def main():
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    yesterday = sys.argv[2] if len(sys.argv) > 2 else (date.today() - timedelta(days=1)).isoformat()

    tpl = (BASE / "claude_prompt.txt").read_text(encoding="utf-8")
    prompt = tpl.replace("%%TODAY%%", today).replace("%%YESTERDAY%%", yesterday)

    advice_path = BASE / "reports" / f"advice_{today}.md"

    try:
        result = subprocess.run(
            ["claude.cmd", "-p", prompt, "--dangerously-skip-permissions"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=600, cwd=str(BASE),
        )
        output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
    except subprocess.TimeoutExpired:
        output = "[TIMEOUT] claude -p did not complete within 10 minutes"
    except FileNotFoundError:
        output = "[ERROR] claude CLI not found on PATH"
    except Exception as e:
        output = f"[ERROR] claude invocation failed: {e}"

    # stdout goes to batch redirect (morning_advice.bat >> _cron_advice.log)
    print(output)

    if output.strip() and len(output) > 500:
        advice_path.write_text(output, encoding="utf-8")
        print("[INFO] advice saved from stdout")
    elif output.strip():
        print("[WARN] advice output too short, not saving (likely error response)")

    print(f"  advice output: {len(output)} chars")


if __name__ == "__main__":
    main()
