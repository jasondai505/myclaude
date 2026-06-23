"""深度投研洞见 失败重试。

修复两类：
  A) 正文 >= 200 字但 extractor 返回 None → 重试提取（降低门槛 + JSON 容错增强）
  B) JSON 解析失败 → 更强的容错恢复
"""
from __future__ import annotations

import json, os, re, sys, hashlib
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extractors.shendu import _EXTRACT_PROMPT, _get_client

RAW_PATH = Path(__file__).resolve().parent / "reports" / "serenity" / "shendu_raw" / "all_2026_raw.json"
OUT_DIR = Path(__file__).resolve().parent / "reports" / "serenity" / "shendu"
MIN_BODY = 200
MAX_TOKENS = 6000
TIMEOUT = 180
MODEL = os.getenv("DR_LLM_MODEL", "claude-sonnet-4-6-20250514")


def _fetch_body(date_str, topic):
    """用与 batch 相同的逻辑获取正文。"""
    # docx 优先
    import docx as docx_lib
    DOCX_DIR = Path(__file__).resolve().parent.parent / "深度投研洞见"
    for f in DOCX_DIR.iterdir():
        if not f.suffix == '.docx':
            continue
        try:
            doc = docx_lib.Document(str(f))
            text = '\n'.join([p.text for p in doc.paragraphs])
            dates = re.findall(r'(2026[-./年]\d{1,2}[-./月]\d{1,2})', text)
            dates_clean = []
            for d in dates:
                d = d.replace('年','-').replace('月','-').replace('/','-').replace('.','-')
                parts = d.split('-')
                if len(parts) == 3:
                    dates_clean.append(f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}")
            if dates_clean and min(dates_clean) == date_str:
                return text.strip()
        except Exception:
            pass

    # zsxq article 页面
    import urllib.request, random
    from zsxq_cross import load_cookie
    article_url = (topic.get("talk", {}).get("article", {}) or {}).get("article_url", "")
    if article_url:
        try:
            cookie = load_cookie()
            req = urllib.request.Request(article_url)
            req.add_header('Cookie', cookie)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            req.add_header('Accept', 'text/html,application/xhtml+xml')
            req.add_header('Origin', 'https://wx.zsxq.com')
            req.add_header('Referer', 'https://wx.zsxq.com/')
            resp = urllib.request.urlopen(req, timeout=15)
            html = resp.read().decode('utf-8', errors='replace')
            m = re.search(
                r'<div[^>]*class="[^"]*ql-editor[^"]*"[^>]*>(.*?)</div>\s*(?:</div>)?\s*<',
                html, re.DOTALL,
            )
            if m:
                text = re.sub(r'<[^>]+>', '\n', m.group(1))
                text = re.sub(r'\n{3,}', '\n\n', text).strip()
                return text
        except Exception:
            pass

    # talk.text 兜底
    talk_text = topic.get("talk", {}).get("text", "") or ""
    def decode_e(m):
        try: return unquote(m.group(1))
        except: return m.group(1)
    text = re.sub(r'<e[^>]+title="([^"]+)"[^>]*/>', decode_e, talk_text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


def _robust_parse_json(text: str) -> dict | None:
    """更强的 JSON 容错解析。"""
    # 模式1: ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except json.JSONDecodeError: pass

    # 模式2: 从第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        raw = text[start:end + 1]
        # 修复常见 JSON 格式问题
        raw = re.sub(r",\s*([}\]])", r"\1", raw)  # 尾部逗号
        raw = re.sub(r"(\d+)\.\s*([}\]])", r"\1\2", raw)  # 数字后跟 .
        raw = re.sub(r'(?<!")("(?:[^"\\]|\\.)*")\s*:', lambda m: m.group(0), raw)  # noop sanity
        # 修复未转义的双引号（在字符串值内部）
        raw = re.sub(r'(?<!\\)"([^"]*?)"(?=\s*[,}\]])', r'"\1"', raw)  # noop
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            # Try to truncate at the error position
            try:
                return json.loads(raw[:e.pos] + ']}')
            except:
                try:
                    return json.loads(raw[:e.pos-1] + ']}')
                except:
                    pass

    # 模式3: 逐行扫描找完整对象
    lines = text.split('\n')
    depth = 0
    obj_start = -1
    for i, line in enumerate(lines):
        for ch in line:
            if ch == '{': depth += 1
            elif ch == '}': depth -= 1
        if obj_start < 0 and '{' in line:
            obj_start = i
        if obj_start >= 0 and depth == 0:
            raw = '\n'.join(lines[obj_start:i+1])
            try: return json.loads(raw)
            except: pass
            obj_start = -1

    return None


def _extract_with_retry(body: str, title: str, date_str: str) -> dict | None:
    """带重试和增强 JSON 解析的提取。"""
    if len(body) < MIN_BODY:
        return None

    client = _get_client()
    prompt = _EXTRACT_PROMPT.format(body=body[:6000])

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                timeout=TIMEOUT,
            )
            parts = [block.text for block in resp.content
                     if hasattr(block, "text") and block.text]
            text = "\n".join(parts)
            break
        except Exception as e:
            if attempt == 2:
                print(f"    LLM 调用失败: {e}")
                return None
            continue

    data = _robust_parse_json(text)
    if data is None:
        # 最后一次尝试：把 text 原样打印帮助调试
        print(f"    JSON parse FAILED, text[:300]: {text[:300]}")
        return None

    data["title"] = title
    data["date"] = date_str
    return data


def _slug(title: str, date_str: str) -> str:
    h = hashlib.md5(f"retry:{date_str}:{title}".encode()).hexdigest()[:6]
    safe = re.sub(r'[^\w]', '_', title)[:30]
    return f"{date_str}_{safe}_{h}"


def main():
    # 读取错误列表
    err_path = OUT_DIR / "_errors.json"
    if not err_path.exists():
        print("No _errors.json found")
        return

    with open(err_path, encoding='utf-8') as f:
        errors = json.load(f)

    fail_dates = set(e['date'] for e in errors)

    with open(RAW_PATH, encoding='utf-8') as f:
        all_topics = json.load(f)

    # 只重试失败的
    retry_topics = [t for t in all_topics if t.get('create_time','')[:10] in fail_dates]

    print(f"Retrying {len(retry_topics)} failed articles...")

    fixed = 0
    still_fail = 0
    for t in sorted(retry_topics, key=lambda x: x.get('create_time','')):
        date_str = t.get('create_time','')[:10]
        talk = t.get('talk',{}).get('text','') or ''
        m = re.search(r'title="([^"]*)"', talk)
        title = unquote(m.group(1)) if m else re.sub(r'<[^>]+>', '', talk)[:100]
        title_clean = title

        body = _fetch_body(date_str, t)
        print(f"  [{date_str}] body={len(body)}: {title[:50]}...")

        if len(body) < MIN_BODY:
            print(f"    → SKIP (body too short: {len(body)})")
            still_fail += 1
            continue

        data = _extract_with_retry(body, title_clean, date_str)
        if data:
            data["title_clean"] = title_clean
            data["body_length"] = len(body)
            data["topic_id"] = t.get("topic_id", "")
            slug = _slug(title_clean, date_str)
            out_path = OUT_DIR / f"shendu_{slug}.json"
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            vps = len(data.get('variant_perceptions', []))
            print(f"    → OK ({vps} VP)")
            fixed += 1
        else:
            print(f"    → FAIL (extraction returned None)")
            still_fail += 1

    print(f"\nRetry done: {fixed} fixed, {still_fail} still failing")

    # Update summary
    summary_path = OUT_DIR / "_summary.json"
    if summary_path.exists():
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        summary['retry_fixed'] = fixed
        summary['retry_still_fail'] = still_fail
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
