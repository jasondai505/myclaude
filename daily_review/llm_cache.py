"""LLM 响应缓存 — 内容哈希去重，7 天 TTL，节省重复推理成本。

用法:
    from llm_cache import cached_call
    result = cached_call(client, model, system_prompt, user_content, max_tokens=1024)
    if result is not None:
        return result  # 缓存命中
    # 否则正常调用 LLM 并 store() 缓存结果
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "llm_cache.db"
TTL_SECONDS = 7 * 86400  # 7 天


def _conn():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, response TEXT, created REAL)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_created ON cache(created)")
    return db


def _key(model: str, system: str, user: str) -> str:
    """SHA256 哈希：模型 + 系统提示前 200 字符 + 用户内容前 500 字符。"""
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(system[:200].encode())
    h.update(user[:500].encode())
    return h.hexdigest()


def get(model: str, system: str, user: str) -> str | None:
    """查询缓存，过期返回 None。"""
    k = _key(model, system, user)
    try:
        db = _conn()
        row = db.execute("SELECT response, created FROM cache WHERE key=?", (k,)).fetchone()
        db.close()
        if row:
            age = time.time() - row[1]
            if age < TTL_SECONDS:
                return row[0]
    except Exception:
        pass
    return None


def store(model: str, system: str, user: str, response: str):
    """写入缓存。"""
    k = _key(model, system, user)
    try:
        db = _conn()
        db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (k, response, time.time()))
        db.commit()
        db.close()
    except Exception:
        pass


def cleanup():
    """清理过期缓存。"""
    cutoff = time.time() - TTL_SECONDS
    try:
        db = _conn()
        db.execute("DELETE FROM cache WHERE created < ?", (cutoff,))
        db.commit()
        db.close()
    except Exception:
        pass
