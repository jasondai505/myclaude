"""Hook 入口 — 由 Elicitation 事件触发，后台启动 _remind_pending.py"""
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
    """Try multiple paths to extract questions from hook input JSON."""
    if isinstance(data, list) and data and isinstance(data[0], dict) and "question" in data[0]:
        return data
    if not isinstance(data, dict):
        return None
    ti = data.get("tool_input", {})
    if isinstance(ti, dict):
        qs = ti.get("questions")
        if qs:
            return qs
    for key in ("questions", "elicitation"):
        qs = data.get(key)
        if isinstance(qs, list) and qs:
            return qs
    return None


def main():
    _log("hook called")

    # diagnostic: log relevant env keys
    for key in sorted(os.environ):
        if "CLAUDE" in key.upper() or "HOOK" in key.upper() or "TOOL" in key.upper():
            _log(f"env: {key}={os.environ[key][:200]}")

    questions = None

    raw = os.environ.get("CLAUDE_TOOL_INPUT", "")
    if raw:
        _log(f"CLAUDE_TOOL_INPUT present, len={len(raw)}")
        try:
            questions = _extract_questions(json.loads(raw))
        except json.JSONDecodeError as e:
            _log(f"env json error: {e}")

    if not questions:
        stdin_raw = _read_stdin(timeout=0.5)
        _log(f"stdin read: {'data' if stdin_raw else 'EMPTY'}, len={len(stdin_raw) if stdin_raw else 0}")
        if stdin_raw and stdin_raw.strip():
            try:
                data = json.loads(stdin_raw)
                questions = _extract_questions(data)
                _log(f"stdin: {len(questions)} questions" if questions else "stdin: no questions found in data")
            except Exception as e:
                _log(f"stdin parse error: {e}")

    if not questions:
        _log("no questions found, exiting")
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
