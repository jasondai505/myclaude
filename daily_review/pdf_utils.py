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


def download_announcement_pdf(art_code: str) -> str | None:
    """下载公告 PDF 并提取文本（截断 MAX_ANNOUNCEMENT_CHARS 字）。"""
    if not art_code:
        return None
    url = ANNOUNCEMENT_PDF_URL.format(art_code=art_code)
    pdf_bytes = _download_pdf(url)
    if not pdf_bytes:
        return None
    text = extract_text_from_pdf_bytes(pdf_bytes)
    if not text:
        return None
    text = _clean_text(text)
    if len(text) > MAX_ANNOUNCEMENT_CHARS:
        text = text[:MAX_ANNOUNCEMENT_CHARS // 2] + "\n...(中略)...\n" + text[-MAX_ANNOUNCEMENT_CHARS // 2:]
    return text


def download_report_pdf(pdf_url: str) -> str | None:
    """下载研报 PDF 并提取文本（截断 MAX_REPORT_CHARS 字）。"""
    if not pdf_url:
        return None
    pdf_bytes = _download_pdf(pdf_url)
    if not pdf_bytes:
        return None
    text = extract_text_from_pdf_bytes(pdf_bytes)
    if not text:
        return None
    text = _clean_text(text)
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS // 2] + "\n...(中略)...\n" + text[-MAX_REPORT_CHARS // 2:]
    return text
