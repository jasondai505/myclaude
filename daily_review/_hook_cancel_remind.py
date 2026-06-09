"""ElicitationResult 触发 — 用户已回复，取消 3 分钟倒计时提醒"""
import os, signal

_PID_FILE = os.path.join(os.path.dirname(__file__), "_remind_pid.txt")


def main():
    try:
        if not os.path.exists(_PID_FILE):
            return
        with open(_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        pass
    except Exception:
        pass
    finally:
        try:
            os.remove(_PID_FILE)
        except Exception:
            pass


if __name__ == "__main__":
    main()
