"""PDF 下载 + 文本提取小工具。

三条管线共用：公告、个股研报、行业研报。
"""
from __future__ import annotations

import io
import random
import re
import time
from functools import lru_cache

import pdfplumber
import requests

MAX_ANNOUNCEMENT_CHARS = 4000
MAX_REPORT_CHARS = 3000
PDF_DOWNLOAD_TIMEOUT = 30
ANNOUNCEMENT_PDF_URL = "https://pdf.dfcfw.com/pdf/h2_{art_code}_1.pdf"

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
]


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """pdfplumber 提取 PDF 全部文字，返回单个字符串。"""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            texts = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texts.append(t)
            return "\n".join(texts)
    except Exception:
        return ""


def _clean_text(text: str) -> str:
    """清理 PDF 提取文本：合并多余空白、修复断行。"""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _download_pdf(url: str) -> bytes | None:
    """下载 PDF，失败返回 None。"""
    headers = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "application/pdf,*/*",
        "Referer": "https://data.eastmoney.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=PDF_DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        if b"%PDF" not in resp.content[:1024]:
            return None
        return resp.content
    except Exception:
        return None


def download_announcement_pdf(art_code: str, stock_code: str = "") -> str | None:
    """下载公告 PDF 并提取文本（截断 MAX_ANNOUNCEMENT_CHARS 字）。

    自动降级：PDF → HTML 详情页
    """
    if not art_code:
        return None
    url = ANNOUNCEMENT_PDF_URL.format(art_code=art_code)
    pdf_bytes = _download_pdf(url)
    if pdf_bytes:
        text = extract_text_from_pdf_bytes(pdf_bytes)
        if text:
            text = _clean_text(text)
            if len(text) > MAX_ANNOUNCEMENT_CHARS:
                text = text[:MAX_ANNOUNCEMENT_CHARS // 2] + "\n...(中略)...\n" + text[-MAX_ANNOUNCEMENT_CHARS // 2:]
            return text
    # PDF 失败 → HTML 详情页降级
    if stock_code:
        return _download_announcement_html(stock_code, art_code)
    return None


def _download_announcement_html(stock_code: str, art_code: str) -> str | None:
    """从东财公告详情页提取正文（HTML → 纯文本）。"""
    url = f"https://data.eastmoney.com/notices/detail/{stock_code}/{art_code}.html"
    headers = {"User-Agent": random.choice(UA_POOL), "Referer": "https://data.eastmoney.com/"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        html = resp.text
    except Exception:
        return None

    # 尝试从嵌入式 JSON 提取（东财页面用 JS 渲染，正文可能在 script 标签里）
    m = re.search(r'"noticeContent"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if not m:
        m = re.search(r'noticeContent["\']?\s*[:=]\s*["\'](.+?)["\']\s*[,;}]', html, re.DOTALL)
    if m:
        content = m.group(1).replace("\\n", "\n").replace("\\t", " ").replace("\\r", "")
        content = re.sub(r'\\u[0-9a-fA-F]{4}', lambda x: chr(int(x.group(0)[2:], 16)), content)
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"&nbsp;", " ", content)
        content = re.sub(r"\s+", " ", content).strip()
        if len(content) > 100:
            if len(content) > MAX_ANNOUNCEMENT_CHARS:
                content = content[:MAX_ANNOUNCEMENT_CHARS]
            return content

    # 回退: innerHTML 正文区段
    m = re.search(r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.+?)</div>', html, re.DOTALL)
    if m:
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 100:
            if len(text) > MAX_ANNOUNCEMENT_CHARS:
                text = text[:MAX_ANNOUNCEMENT_CHARS]
            return text

    return None


def download_report_pdf(pdf_url: str, info_code: str = "") -> str | None:
    """下载研报 PDF 并提取文本（截断 MAX_REPORT_CHARS 字）。

    自动降级：PDF → HTML详情页
    """
    if not pdf_url:
        return None
    pdf_bytes = _download_pdf(pdf_url)
    if not pdf_bytes:
        fixed = _fix_report_pdf_url(pdf_url)
        if fixed and fixed != pdf_url:
            pdf_bytes = _download_pdf(fixed)
    if pdf_bytes:
        text = extract_text_from_pdf_bytes(pdf_bytes)
        if text:
            text = _clean_text(text)
            if len(text) > MAX_REPORT_CHARS:
                text = text[:MAX_REPORT_CHARS // 2] + "\n...(中略)...\n" + text[-MAX_REPORT_CHARS // 2:]
            return text
    # PDF 失败 → HTML 详情页降级
    if info_code:
        return _download_report_html(info_code)
    return None


def _download_report_html(info_code: str) -> str | None:
    """从东财研报详情页提取正文（HTML → 纯文本）。"""
    url = f"https://data.eastmoney.com/report/zw_stock.jshtml?infocode={info_code}"
    headers = {"User-Agent": random.choice(UA_POOL), "Referer": "https://data.eastmoney.com/"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        text = resp.text
    except Exception:
        return None
    m = re.search(r'class="ctx-body[^"]*"[^>]*>(.*?)</div>', text, re.DOTALL)
    if not m:
        return None
    clean = re.sub(r"<script[^>]*>.*?</script>", "", m.group(1), flags=re.DOTALL)
    clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"&nbsp;", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > MAX_REPORT_CHARS:
        clean = clean[:MAX_REPORT_CHARS]
    return clean or None


def _fix_report_pdf_url(url: str) -> str | None:
    """修复损坏的研报 PDF URL：encode_url 中多余的 / 段。"""
    m = re.search(r'/h3_(.+?)_1\.pdf$', url)
    if not m:
        return None
    encoded = m.group(1)
    if "/" in encoded:
        encoded = encoded.split("/")[0]
        return re.sub(r'/h3_.+_1\.pdf$', f'/h3_{encoded}_1.pdf', url)
    return None
