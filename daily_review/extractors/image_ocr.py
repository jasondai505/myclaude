"""通用图片语料 OCR 管道 — 监控 serenity/ 目录，OCR → 去重 → 分类 → LLM 提取。

用法:
    python extractors/image_ocr.py                          # 增量：只处理新图
    python extractors/image_ocr.py --all                    # 全量重处理
    python extractors/image_ocr.py --dir serenity           # 指定目录

架构:
    1. Windows.Media.Ocr 引擎 (winrt) — 本地免费
    2. MD5 去重 — ocr_tracker DB 表记录已处理图片
    3. 源分类 — 识别 Shendu(Bruce文章) vs Serenity(Reddit情报) vs Chart
    4. LLM 提取 — 分别走 shendu/serenity extractor
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import sqlite3
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent
TRACKER_DB = BASE / "data" / "ocr_tracker.db"

SERENITY_DIRS = [
    BASE.parent / "serenity",
]

# ── OCR Engine ────────────────────────────────────────────

async def _ocr_one(filepath: str) -> str:
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage import StorageFile

    sf = await StorageFile.get_file_from_path_async(str(Path(filepath).resolve()))
    stream = await sf.open_read_async()
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    result = await engine.recognize_async(bitmap)
    return result.text


def ocr_file(filepath: str) -> str:
    return asyncio.run(_ocr_one(filepath))


def ocr_batch(filepaths: list[str]) -> list[dict]:
    async def _batch():
        results = []
        for fp in filepaths:
            try:
                text = await _ocr_one(fp)
                results.append({"file": fp, "text": text, "ok": True})
            except Exception as e:
                results.append({"file": fp, "text": "", "ok": False, "error": str(e)})
        return results
    return asyncio.run(_batch())


# ── Dedup Tracker ─────────────────────────────────────────

def _init_tracker():
    TRACKER_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(TRACKER_DB)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS ocr_tracker (
            file_hash TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_mtime REAL,
            ocr_chars INTEGER,
            ocr_text TEXT,
            source_type TEXT,
            extracted_at TEXT,
            analysis_done INTEGER DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ocr_path ON ocr_tracker(file_path)")
        # Add ocr_text column if missing (migration from v1)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ocr_tracker)").fetchall()]
        if "ocr_text" not in cols:
            conn.execute("ALTER TABLE ocr_tracker ADD COLUMN ocr_text TEXT")


def _file_hash(filepath: str) -> str:
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:16]


def is_new(filepath: str) -> bool:
    _init_tracker()
    fh = _file_hash(filepath)
    with sqlite3.connect(str(TRACKER_DB)) as conn:
        r = conn.execute("SELECT 1 FROM ocr_tracker WHERE file_hash=?", (fh,)).fetchone()
        return r is None


def mark_done(filepath: str, ocr_chars: int, source_type: str = "", ocr_text: str = ""):
    _init_tracker()
    fh = _file_hash(filepath)
    mtime = Path(filepath).stat().st_mtime
    with sqlite3.connect(str(TRACKER_DB)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ocr_tracker "
            "(file_hash, file_path, file_mtime, ocr_chars, source_type, extracted_at, analysis_done, ocr_text) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (fh, filepath, mtime, ocr_chars, source_type, datetime.now().isoformat(), 0, ocr_text),
        )


# ── Source Classification ─────────────────────────────────

def classify(text: str) -> str:
    """识别语料类型: shendu / serenity / chart / unknown"""
    if len(text) < 100:
        return "chart" if any(kw in text for kw in ["$", "%", "CHART"]) else "unknown"
    if "aleabitoreddit" in text or "SerenIty @" in text:
        return "serenity"
    if any(kw in text for kw in ["供给刚性", "预期差", "估值分层", "核心仓", "弹性层", "久期"]):
        return "shendu"
    if re.search(r"[一-龥]{4,}", text):
        return "shendu"  # Chinese content → likely Bruce article
    return "unknown"


# ── Main Pipeline ─────────────────────────────────────────

def scan_dir(dirpath: str) -> list[str]:
    """扫描目录返回新图片列表（未OCR过的）。"""
    p = Path(dirpath)
    if not p.exists():
        return []
    _init_tracker()
    images = sorted(
        f for f in p.glob("*.png")
        if f.stat().st_size > 10000  # skip tiny files
    )
    new = [str(img) for img in images if is_new(str(img))]
    return new


def process_image(filepath: str) -> dict | None:
    """处理单张图片: OCR → 分类 → 返回结果（含文本）。"""
    text = ocr_file(filepath)
    if not text or len(text) < 50:
        mark_done(filepath, len(text), "empty", text)
        return None

    source = classify(text)
    mark_done(filepath, len(text), source, text)
    return {
        "file": filepath,
        "chars": len(text),
        "source_type": source,
        "text": text,
        "hash": _file_hash(filepath),
    }


def process_new(dirpath: str = None) -> list[dict]:
    """增量处理：扫描新图，OCR + 分类。"""
    dirs = [dirpath] if dirpath else [str(d) for d in SERENITY_DIRS]
    results = []
    for d in dirs:
        new_files = scan_dir(d)
        if not new_files:
            print(f"[image_ocr] {d}: 无新图片")
            continue
        print(f"[image_ocr] {d}: {len(new_files)} 张新图")
        for fp in new_files:
            r = process_image(fp)
            if r:
                results.append(r)
                print(f"  {r['source_type']:>10} | {r['chars']:5d}c | {Path(fp).name}")
    return results


# ── CLI ───────────────────────────────────────────────────

def main():
    do_all = "--all" in sys.argv
    target_dir = None
    for i, arg in enumerate(sys.argv):
        if arg == "--dir" and i + 1 < len(sys.argv):
            target_dir = sys.argv[i + 1]

    if do_all:
        _init_tracker()
        with sqlite3.connect(str(TRACKER_DB)) as conn:
            conn.execute("DELETE FROM ocr_tracker")
        print("[image_ocr] 重置去重记录，全量处理")

    results = process_new(target_dir)
    if results:
        by_type = {}
        for r in results:
            by_type.setdefault(r["source_type"], []).append(r)
        print(f"\n[image_ocr] 完成: {len(results)} 张")
        for t, items in by_type.items():
            print(f"  {t}: {len(items)} 张, {sum(i['chars'] for i in items)} 字符")
    else:
        print("[image_ocr] 无新图片")


if __name__ == "__main__":
    main()
