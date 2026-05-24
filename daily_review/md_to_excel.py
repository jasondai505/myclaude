"""把 daily_review 的 markdown 报告转成 Excel

用法:
    python md_to_excel.py                       # 转最新报告
    python md_to_excel.py review_2026-05-19.md
"""
import sys
import re
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

REPORT_DIR = Path(__file__).parent / "reports"


def parse_md_tables(text: str) -> list[dict]:
    blocks = []
    current_section = "未分类"
    current_subsection = ""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        m = re.match(r"^##\s+(.+)$", ln)
        if m:
            current_section = m.group(1).strip().lstrip("*").rstrip()
            current_subsection = ""
            i += 1
            continue
        m = re.match(r"^###\s+(.+)$", ln)
        if m:
            current_subsection = m.group(1).strip()
            i += 1
            continue
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i+1].strip()):
            header = [c.strip() for c in ln.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if len(cells) == len(header):
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append({
                    "section": current_section,
                    "subsection": current_subsection,
                    "header": header,
                    "rows": rows,
                })
            continue
        i += 1
    return blocks


def safe_sheet_name(name: str, used: set) -> str:
    name = re.sub(r"[\\/*?:\[\]]", "", name)[:28]
    base = name
    n = 2
    while name in used:
        name = f"{base[:25]}_{n}"
        n += 1
    used.add(name)
    return name


def convert(md_path: Path, xlsx_path: Path):
    text = md_path.read_text(encoding="utf-8")
    blocks = parse_md_tables(text)
    print(f"解析到 {len(blocks)} 个表格")

    grouped: dict[str, list[dict]] = {}
    for b in blocks:
        grouped.setdefault(b["section"], []).append(b)

    used = set()
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for section, bs in grouped.items():
            sheet = safe_sheet_name(section, used)
            offset = 0
            for b in bs:
                df = pd.DataFrame(b["rows"], columns=b["header"])
                title = b["subsection"] or b["section"]
                pd.DataFrame([[title]]).to_excel(
                    writer, sheet_name=sheet, startrow=offset,
                    index=False, header=False)
                df.to_excel(writer, sheet_name=sheet, startrow=offset + 1, index=False)
                offset += len(df) + 4

    print(f"✓ 已生成: {xlsx_path}")


def main():
    if len(sys.argv) > 1:
        md_name = sys.argv[1]
        md_path = REPORT_DIR / md_name if not Path(md_name).is_absolute() else Path(md_name)
    else:
        mds = sorted(REPORT_DIR.glob("review_*.md"), reverse=True)
        if not mds:
            print("未找到报告")
            sys.exit(1)
        md_path = mds[0]

    if not md_path.exists():
        print(f"文件不存在: {md_path}")
        sys.exit(1)

    xlsx_path = md_path.with_suffix(".xlsx")
    print(f"输入: {md_path}")
    convert(md_path, xlsx_path)


if __name__ == "__main__":
    main()
