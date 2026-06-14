"""PDF 正文提取验证脚本。

测试三条管线的 PDF→文本链路，不调 LLM，不改 DB。
"""
from __future__ import annotations

import sqlite3, sys, time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from daily_review.pdf_utils import (
    download_announcement_pdf,
    download_report_pdf,
)

DB = Path(__file__).parent / "data" / "review.db"


def _db_rows(query: str, params=()) -> list[dict]:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()
    return rows


def test_announcements(n: int = 20):
    """测试公告 PDF 提取（需 art_code）。"""
    rows = _db_rows(
        "SELECT date, code, title, art_code, content FROM announcements "
        "WHERE art_code IS NOT NULL AND art_code != '' "
        "ORDER BY date DESC LIMIT ?", (n,)
    )
    if not rows:
        print("❌ 公告表无 art_code，请先重新采集公告")
        return

    ok, cached, downloaded, fail = 0, 0, 0, 0
    for r in rows:
        cached_content = r.get("content", "")
        if cached_content:
            cached += 1
            ok += 1
            print(f"  [cache] {r['code']} {r['title'][:40]} ({len(cached_content)}字)")
            continue

        text = download_announcement_pdf(r["art_code"])
        if text:
            downloaded += 1
            ok += 1
            print(f"  [down]  {r['code']} {r['title'][:40]} ({len(text)}字)")
        else:
            fail += 1
            print(f"  [FAIL]  {r['code']} {r['title'][:40]} — PDF 或扫描件")

        time.sleep(0.3)

    print(f"\n公告: 样本{n} 成功{ok}(缓存{cached}+下载{downloaded}) 失败{fail}")


def test_research_reports(n: int = 20):
    """测试个股研报 PDF 提取（需 pdf_url + info_code）。"""
    rows = _db_rows(
        "SELECT code, name, title, pdf_url, info_code, body_text FROM research_reports "
        "WHERE pdf_url IS NOT NULL AND pdf_url != '' "
        "ORDER BY report_date DESC LIMIT ?", (n,)
    )
    if not rows:
        print("❌ 研报表无 pdf_url")
        return

    ok, cached, pdf, html, fail = 0, 0, 0, 0, 0
    for r in rows:
        cached_content = r.get("body_text", "")
        if cached_content:
            cached += 1
            ok += 1
            print(f"  [cache] {r['code']} {r['name']}: {r['title'][:40]} ({len(cached_content)}字)")
            continue

        text = download_report_pdf(r["pdf_url"], r.get("info_code", ""))
        if text:
            # 判断来源：PDF 还是 HTML
            source = _guess_source(text)
            if source == "pdf":
                pdf += 1
            else:
                html += 1
            ok += 1
            print(f"  [{source}]  {r['code']} {r['name']}: {r['title'][:40]} ({len(text)}字)")
        else:
            fail += 1
            print(f"  [FAIL]  {r['code']} {r['name']}: {r['title'][:40]} — PDF+HTML 双失败")

        time.sleep(0.5)

    print(f"\n个股研报: 样本{n} 成功{ok}(缓存{cached}+PDF{pdf}+HTML{html}) 失败{fail}")


def test_industry_reports(n: int = 30):
    """测试行业研报 PDF 提取（需 pdf_url + info_code）。"""
    rows = _db_rows(
        "SELECT industry_name, title, institution, pdf_url, info_code, body_text "
        "FROM industry_reports "
        "WHERE report_subtype='industry' AND pdf_url IS NOT NULL AND pdf_url != '' "
        "ORDER BY report_date DESC LIMIT ?", (n,)
    )
    if not rows:
        print("❌ 行业研报表无 pdf_url")
        return

    ok, cached, pdf, html, fail = 0, 0, 0, 0, 0
    for r in rows:
        cached_content = r.get("body_text", "")
        if cached_content:
            cached += 1
            ok += 1
            print(f"  [cache] {r['industry_name']}: {r['title'][:40]} ({len(cached_content)}字)")
            continue

        text = download_report_pdf(r["pdf_url"], r.get("info_code", ""))
        if text:
            source = _guess_source(text)
            if source == "pdf":
                pdf += 1
            else:
                html += 1
            ok += 1
            print(f"  [{source}]  {r['industry_name']}: {r['title'][:40]} ({len(text)}字)")
        else:
            fail += 1
            print(f"  [FAIL]  {r['industry_name']}: {r['title'][:40]}")

        time.sleep(0.5)

    print(f"\n行业研报: 样本{n} 成功{ok}(缓存{cached}+PDF{pdf}+HTML{html}) 失败{fail}")


def _guess_source(text: str) -> str:
    """根据文本特征猜测来源：PDF 提取 vs HTML 降级。"""
    # PDF 提取通常有分页/断行特征，HTML 降级是连续段落
    lines = text.split("\n")
    short_lines = sum(1 for l in lines if 10 < len(l) < 80)
    if short_lines > len(lines) * 0.4:
        return "pdf"
    return "html"


def test_db_state():
    """检查 DB 中三条管线的正文覆盖率。"""
    print("=" * 60)
    print("DB 正文覆盖率统计")
    print("=" * 60)

    # 公告
    ann = _db_rows("SELECT COUNT(*) AS n FROM announcements")[0]["n"]
    ann_with_ac = _db_rows(
        "SELECT COUNT(*) AS n FROM announcements WHERE art_code IS NOT NULL AND art_code != ''"
    )[0]["n"]
    ann_with_content = _db_rows(
        "SELECT COUNT(*) AS n FROM announcements WHERE content IS NOT NULL AND content != ''"
    )[0]["n"]
    print(f"公告: {ann}条, art_code {ann_with_ac}({_pct(ann_with_ac, ann)}), 已缓存正文 {ann_with_content}({_pct(ann_with_content, ann)})")

    # 个股研报
    rr = _db_rows("SELECT COUNT(*) AS n FROM research_reports")[0]["n"]
    rr_with_pdf = _db_rows(
        "SELECT COUNT(*) AS n FROM research_reports WHERE pdf_url IS NOT NULL AND pdf_url != ''"
    )[0]["n"]
    rr_with_body = _db_rows(
        "SELECT COUNT(*) AS n FROM research_reports WHERE body_text IS NOT NULL AND body_text != ''"
    )[0]["n"]
    print(f"个股研报: {rr}条, pdf_url {rr_with_pdf}({_pct(rr_with_pdf, rr)}), 已缓存正文 {rr_with_body}({_pct(rr_with_body, rr)})")

    # 行业研报
    ir = _db_rows("SELECT COUNT(*) AS n FROM industry_reports WHERE report_subtype='industry'")[0]["n"]
    ir_with_pdf = _db_rows(
        "SELECT COUNT(*) AS n FROM industry_reports "
        "WHERE report_subtype='industry' AND pdf_url IS NOT NULL AND pdf_url != ''"
    )[0]["n"]
    ir_with_body = _db_rows(
        "SELECT COUNT(*) AS n FROM industry_reports "
        "WHERE report_subtype='industry' AND body_text IS NOT NULL AND body_text != ''"
    )[0]["n"]
    print(f"行业研报: {ir}条, pdf_url {ir_with_pdf}({_pct(ir_with_pdf, ir)}), 已缓存正文 {ir_with_body}({_pct(ir_with_body, ir)})")


def _pct(part, total):
    if not total:
        return "—"
    return f"{part*100/total:.0f}%"


if __name__ == "__main__":
    test_db_state()
    print()

    print("=" * 60)
    print("公告 PDF 提取测试")
    print("=" * 60)
    test_announcements(10)

    print()
    print("=" * 60)
    print("个股研报 PDF 提取测试")
    print("=" * 60)
    test_research_reports(10)

    print()
    print("=" * 60)
    print("行业研报 PDF 提取测试")
    print("=" * 60)
    test_industry_reports(15)
