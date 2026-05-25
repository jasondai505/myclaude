"""共享工具函数"""
import re
import sys
import os


def is_headless() -> bool:
    """Task Scheduler Session 0 或管道重定向场景，无控制台可交互"""
    return (
        os.environ.get("HEADLESS", "") == "1"
        or not sys.stdout.isatty()
    )


def setup_console():
    if is_headless():
        return
    if sys.platform == "win32":
        os.system("")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def progress_bar(iterable, desc: str = "", unit: str = "", total: int | None = None, **kwargs):
    """headless 时用简单 print，交互模式用 tqdm"""
    if is_headless():
        items = list(iterable)
        n = len(items)
        print(f"  {desc}: {n} {unit}", flush=True)
        for i, item in enumerate(items):
            if n >= 20 and i % max(1, n // 5) == 0:
                print(f"    {desc} {i}/{n}", flush=True)
            yield item
        print(f"    {desc} {n}/{n} done", flush=True)
    else:
        from tqdm import tqdm as _tqdm
        yield from _tqdm(
            iterable, desc=desc, unit=unit, total=total,
            bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
        )


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


def extract_rsi(desc: str) -> float:
    m = re.search(r"RSI=(\d+)", desc)
    return float(m.group(1)) if m else 0
