"""共享工具函数"""
import sys
import os


def setup_console():
    if sys.platform == "win32":
        os.system("")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def is_st(name: str) -> bool:
    return bool(name) and name.startswith(("ST", "*ST"))


def safe_str(row, col: str, default: str = "") -> str:
    v = row.get(col, default)
    return str(v) if v is not None else default


def safe_float(row, col: str, default: float = 0.0) -> float:
    v = row.get(col, 0)
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return default
