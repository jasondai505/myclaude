"""Hook 入口 — PreToolUse 匹配 AskUserQuestion，后台启动 _remind_pending.py"""
import json, os, subprocess, sys, threading
from datetime import datetime

_DEBUG = os.path.join(os.path.dirname(__file__), "_hook_debug.log")
_PID_FILE = os.path.join(os.path.dirname(__file__), "_remind_pid.txt")


def _log(msg: str):
    try:
        with open(_DEBUG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _read_stdin(timeout=0.5):
    result = []

    def target():
        try:
            data = sys.stdin.read()
            if data:
                result.append(data)
        except Exception:
            pass

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    return result[0] if result else None


def _extract_questions(data):
    if not isinstance(data, (dict, list)):
        return None
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and "question" in data[0]:
            return data
        return None
    ti = data.get("tool_input", {})
    if isinstance(ti, dict) and ti.get("questions"):
        return ti["questions"]
    qs = data.get("questions")
    if isinstance(qs, list) and qs:
        return qs
    return None


def main():
    _log("hook called")

    raw = None
    source = None

    stdin_raw = _read_stdin(timeout=1.0)
    if stdin_raw and stdin_raw.strip():
        raw = stdin_raw
        source = "stdin"

    if not raw:
        env_raw = os.environ.get("CLAUDE_TOOL_INPUT", "")
        if env_raw and env_raw.strip():
            raw = env_raw
            source = "env"

    if not raw:
        _log("no input from stdin or env, exiting")
        return

    _log(f"input from {source}, len={len(raw)}")
    questions = None
    try:
        data = json.loads(raw)
        questions = _extract_questions(data)
        _log(f"extracted: {len(questions)} questions" if questions else "extracted: 0 questions")
    except Exception as e:
        _log(f"parse error: {e}")
        return

    if not questions:
        return

    _log(f"launching remind for {len(questions)} questions")
    p = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "_remind_pending.py"),
         json.dumps(questions)],
        creationflags=0x00000008,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with open(_PID_FILE, "w") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    _log(f"launched pid={p.pid}")


if __name__ == "__main__":
    main()
