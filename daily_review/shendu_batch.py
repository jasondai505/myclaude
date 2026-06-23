"""深度投研洞见 全量批量提取器 v2。

策略:
  1. docx 文件 (16篇) → python-docx 提取全文
  2. 其余 99 篇 → zsxq article_url → ql-editor div 提取
  3. 全部 115 篇 → Sonnet 结构化提取 → shendu/*.json
"""
from __future__ import annotations

import json, os, re, sys, time, hashlib, urllib.request, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extractors.shendu import extract as shendu_extract

RAW_PATH = Path(__file__).resolve().parent / "reports" / "serenity" / "shendu_raw" / "all_2026_raw.json"
DOCX_DIR = Path(__file__).resolve().parent.parent / "深度投研洞见"
OUT_DIR = Path(__file__).resolve().parent / "reports" / "serenity" / "shendu"
WORKERS = 6
TIMEOUT = 180
MAX_BODY_CHARS = 8000


# ============================================================
# 文本提取层
# ============================================================

def _load_docx_map() -> dict[str, str]:
    """扫 docx 目录 → {date_str: full_text}。从正文提取日期匹配。"""
    import docx as docx_lib
    docx_map = {}
    if not DOCX_DIR.exists():
        return docx_map
    for f in DOCX_DIR.iterdir():
        if not f.suffix == '.docx':
            continue
        try:
            doc = docx_lib.Document(str(f))
            text = '\n'.join([p.text for p in doc.paragraphs])
            # 提取正文中的日期
            dates = re.findall(r'(2026[-./年]\d{1,2}[-./月]\d{1,2})', text)
            dates_clean = []
            for d in dates:
                d = d.replace('年', '-').replace('月', '-').replace('/', '-').replace('.', '-')
                parts = d.split('-')
                if len(parts) == 3:
                    dates_clean.append(f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}")
            if dates_clean:
                # 取最早的日期作为发布日期
                date_str = min(dates_clean)
                docx_map[date_str] = text.strip()
        except Exception as e:
            print(f"  [DOCX] 读取失败: {f.name}: {e}")
    return docx_map


def _fetch_article_text(url: str, cookie: str) -> str:
    """从 zsxq article 页面提取 ql-editor 正文。"""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url)
        req.add_header('Cookie', cookie)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Accept', 'text/html,application/xhtml+xml')
        req.add_header('Origin', 'https://wx.zsxq.com')
        req.add_header('Referer', 'https://wx.zsxq.com/')
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return ""

    # Quill 编辑器内容
    m = re.search(
        r'<div[^>]*class=\"[^\"]*ql-editor[^\"]*\"[^>]*>(.*?)</div>\s*(?:</div>)?\s*<',
        html, re.DOTALL,
    )
    if not m:
        return ""
    text = re.sub(r'<[^>]+>', '\n', m.group(1))
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text


def _decode_title(raw_text: str) -> str:
    """从 zsxq <e> 标签中提取标题。"""
    from urllib.parse import unquote
    m = re.search(r'title="([^"]*)"', raw_text)
    if m:
        return unquote(m.group(1))
    return re.sub(r'<[^>]+>', '', raw_text)[:100]


def _clean_talk_text(raw_text: str) -> str:
    """清洗 zsxq talk.text (摘要文本)。"""
    from urllib.parse import unquote

    def decode_e(m):
        try:
            return unquote(m.group(1))
        except Exception:
            return m.group(1)

    text = re.sub(r'<e[^>]+title="([^"]+)"[^>]*/>', decode_e, raw_text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


# ============================================================
# 提取调度
# ============================================================

def _extract_one(args: tuple) -> dict | None:
    """对单篇文章执行提取。args = (topic, body_text)"""
    topic, body = args
    title = _decode_title(topic.get("talk", {}).get("text", "") or "")
    date_str = topic.get("create_time", "")[:10]
    topic_id = topic.get("topic_id", "")

    if len(body) < 200:
        print(f"  [{date_str}] SKIP (body={len(body)}): {title[:50]}")
        return None

    print(f"  [{date_str}] EXTRACT ({len(body)}字): {title[:50]}...")
    try:
        data = shendu_extract(body, title, date_str)
        if data:
            data["topic_id"] = topic_id
            data["title_clean"] = title
            data["date"] = date_str
            data["body_length"] = len(body)
        return data
    except Exception as e:
        print(f"  [{date_str}] ERROR: {title[:50]}: {e}")
        return None


def _slug(title: str, date_str: str) -> str:
    h = hashlib.md5(f"{date_str}:{title}".encode()).hexdigest()[:6]
    safe = re.sub(r'[^\w]', '_', title)[:30]
    return f"{date_str}_{safe}_{h}"


def main():
    from zsxq_cross import load_cookie

    if not RAW_PATH.exists():
        print(f"找不到原始数据: {RAW_PATH}")
        sys.exit(1)

    with open(RAW_PATH, encoding='utf-8') as f:
        all_topics = json.load(f)

    # 去重
    seen = set()
    unique = []
    for t in all_topics:
        key = (t.get("create_time", "")[:10],
               _decode_title(t.get("talk", {}).get("text", "") or ""))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    if len(unique) < len(all_topics):
        print(f"去重: {len(all_topics)} → {len(unique)} 篇")
    all_topics = unique
    all_topics.sort(key=lambda x: x.get("create_time", ""))

    # ====== Step 1: 准备正文 ======
    print("Step 1/3: 准备正文...")
    docx_map = _load_docx_map()
    print(f"  docx: {len(docx_map)} 篇全文")
    # 打印 docx 日期列表
    for d in sorted(docx_map):
        print(f"    {d}: {len(docx_map[d])}字")

    cookie = load_cookie()
    extraction_queue = []  # [(topic, body)]

    docx_used = 0
    fetch_used = 0
    talk_used = 0

    for t in all_topics:
        date_str = t.get("create_time", "")[:10]
        # 1) docx 优先
        if date_str in docx_map:
            body = docx_map[date_str]
            extraction_queue.append((t, body))
            docx_used += 1
            continue
        # 2) zsxq article 页面
        article_url = (t.get("talk", {}).get("article", {}) or {}).get("article_url", "")
        if article_url:
            body = _fetch_article_text(article_url, cookie)
            if len(body) >= 200:
                extraction_queue.append((t, body))
                fetch_used += 1
                continue
        # 3) talk.text 摘要兜底
        talk_text = _clean_talk_text(t.get("talk", {}).get("text", "") or "")
        if len(talk_text) >= 200:
            extraction_queue.append((t, talk_text))
            talk_used += 1

    print(f"  正文来源: docx={docx_used} fetch={fetch_used} talk={talk_used} "
          f"→ 合计 {len(extraction_queue)} 篇进入提取")

    # ====== Step 2: 并行提取 ======
    print(f"\nStep 2/3: 并行提取 ({WORKERS} workers)...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    errors = []
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_extract_one, args): args for args in extraction_queue}
        for f in as_completed(futures):
            args = futures[f]
            topic = args[0]
            done += 1
            try:
                data = f.result()
                if data:
                    slug = _slug(data.get("title_clean", ""), data.get("date", ""))
                    out_path = OUT_DIR / f"shendu_{slug}.json"
                    out_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    results.append(data)
                    chains = len(data.get("chains_involved", []))
                    vps = len(data.get("variant_perceptions", []))
                    date_str = data.get("date", "")
                    title = data.get("title_clean", "")[:40]
                    print(f"  [{done}/{len(extraction_queue)}] OK [{date_str}] ({chains}链 {vps}VP) {title}")
                else:
                    date_str = topic.get("create_time", "")[:10]
                    errors.append({"date": date_str, "reason": "extract returned None"})
                    print(f"  [{done}/{len(extraction_queue)}] NULL [{date_str}]")
            except Exception as e:
                date_str = topic.get("create_time", "")[:10]
                errors.append({"date": date_str, "reason": str(e)[:100]})
                print(f"  [{done}/{len(extraction_queue)}] FAIL [{date_str}]: {e}")

    elapsed = time.time() - t0

    # ====== Step 3: 汇总 ======
    print(f"\n{'='*60}")
    print(f"完成: {len(results)} 成功 / {len(errors)} 失败 / {len(extraction_queue)} 总计")
    print(f"耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    cost_est = len(results) * 0.35
    print(f"成本估算: ~${cost_est:.0f} (@$0.35/篇 Sonnet)")

    summary_path = OUT_DIR / "_summary.json"
    summary = {
        "total": len(extraction_queue),
        "success": len(results),
        "errors": errors,
        "cost_estimate": round(cost_est, 1),
        "elapsed_s": round(elapsed, 0),
        "docx_used": docx_used,
        "fetch_used": fetch_used,
        "talk_used": talk_used,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写错误列表
    if errors:
        err_path = OUT_DIR / "_errors.json"
        err_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"错误列表: {err_path}")

    print(f"汇总: {summary_path}")
    print(f"输出: {OUT_DIR}/")


if __name__ == "__main__":
    main()
