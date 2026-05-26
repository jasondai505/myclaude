"""晨间情报+盘面验证 — CLI 入口

用法:
  python run_morning.py --phase pre              # 盘前解读
  python run_morning.py --phase intraday         # 盘中验证
  python run_morning.py --phase post             # 盘后审计 (Phase 2)
  python run_morning.py --phase full             # 完整流水线
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

from interpret import run as run_interpret
from validate import run as run_validate


def run_audit(today: str):
    print("[audit] Phase 2 — 尚未实现，跳过")
    return None


PHASES = {
    "pre": ("盘前解读", run_interpret),
    "intraday": ("盘中验证", run_validate),
    "post": ("盘后审计", run_audit),
}


def main():
    parser = argparse.ArgumentParser(description="晨间情报+盘面验证")
    parser.add_argument("--phase", choices=["pre", "intraday", "post", "full"],
                        required=True, help="运行阶段")
    parser.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--dry-run", action="store_true", help="仅渲染 prompt 不调用 LLM")
    args = parser.parse_args()

    today = args.date or date.today().isoformat()

    if args.phase == "full":
        for phase_key in ("pre", "intraday", "post"):
            label, fn = PHASES[phase_key]
            print(f"\n{'='*40}\n  {label}\n{'='*40}")
            kwargs = {"today": today}
            if phase_key == "pre":
                kwargs["dry_run"] = args.dry_run
            result = fn(**kwargs)
            if result:
                print(f"  -> {result}")
            else:
                print(f"  -> 跳过/失败")
    else:
        label, fn = PHASES[args.phase]
        print(f"\n{'='*40}\n  {label}\n{'='*40}")
        kwargs = {"today": today}
        if args.phase == "pre":
            kwargs["dry_run"] = args.dry_run
        result = fn(**kwargs)
        if result:
            print(f"  -> {result}")
        else:
            print(f"  -> 跳过/失败")


if __name__ == "__main__":
    main()
