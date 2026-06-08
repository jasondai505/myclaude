"""Hook 入口 — 读 CLAUDE_TOOL_INPUT，后台启动 _remind_pending.py"""
import json, os, subprocess, sys
from datetime import datetime

_DEBUG = os.path.join(os.path.dirname(__file__), "_hook_debug.log")

def _log(msg: str):
    try:
        with open(_DEBUG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

def main():
    _log("hook called")
    questions = None
    # PreToolUse/PostToolUse 通过 stdin JSON 传递 tool_input
    try:
        stdin_data = json.loads(sys.stdin.read())
        questions = stdin_data.get("tool_input", {}).get("questions")
        _log(f"stdin: {len(questions)} questions" if questions else "stdin: no questions in tool_input")
    except Exception as e:
        _log(f"stdin read error: {e}")
    # fallback: env var (旧版兼容)
    if not questions:
        raw = os.environ.get("CLAUDE_TOOL_INPUT", "")
        if raw:
            _log(f"env fallback, len={len(raw)}")
            try:
                questions = json.loads(raw)
            except json.JSONDecodeError as e:
                _log(f"env json error: {e}")
                return
    if not questions:
        _log("no questions found")
        return
    _log(f"launching remind for {len(questions)} questions")
    subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "_remind_pending.py"), json.dumps(questions)],
        creationflags=0x00000008,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _log("launched")

if __name__ == "__main__":
    main()
