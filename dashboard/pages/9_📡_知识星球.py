"""知识星球帖子流 — 时间线 / 热度排序 / 标的筛选"""
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "daily_review"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd

from dashboard.utils.data_bridge import get_zsxq_posts, get_zsxq_stats

st.set_page_config(page_title="知识星球", page_icon="📡", layout="wide")

st.title("📡 知识星球帖子流")
st.caption(f"自动采集 · 最近更新: {get_zsxq_stats().get('last_fetch', '?')}")

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.subheader("🔍 筛选")
    days = st.slider("时间范围（天）", 1, 30, 14)
    sort_by = st.radio("排序", ["time", "heat"],
                       format_func={"time": "🕐 时间线", "heat": "🔥 热度"}.get)
    code = st.text_input("标的代码", placeholder="输入6位代码")

    stats = get_zsxq_stats()
    if "error" not in stats:
        st.metric("总帖数", stats["total"])
        st.metric("今日", stats["today"])
        st.metric("近7天日均", f"{stats['week_avg']}帖")
        with st.expander("📝 高频作者"):
            for a in stats.get("top_authors", [])[:8]:
                st.caption(f"{a['name']} ({a['count']}帖)")

# ============================================================
# Main
# ============================================================
posts = get_zsxq_posts(days=days, sort_by=sort_by, stock_filter=code.strip())

if not posts:
    st.info("暂无帖子" + (f"（含代码 {code}）" if code else ""))
    st.stop()

df = pd.DataFrame(posts)
df["date"] = pd.to_datetime(df["create_time"]).dt.strftime("%m-%d %H:%M")
df["热度"] = (df["readers_count"].fillna(0)
              + df["likes_count"].fillna(0) * 2
              + df["comments_count"].fillna(0) * 3)

st.caption(f"共 {len(posts)} 帖")

tab1, tab2 = st.tabs(["📋 列表", "📊 摘要"])

with tab1:
    for _, row in df.iterrows():
        heat = int(row.get("热度", 0))
        bar = "🔥" * min(5, max(0, heat // 10)) if heat > 0 else ""
        author = f"`{row['author']}`" if row.get("author") else ""
        tmap = {"research": "📄", "general": "💬", "question": "❓"}
        ttag = tmap.get(row.get("topic_type", ""), "")

        cols = st.columns([8, 1])
        with cols[0]:
            st.markdown(f"**{row['title']}**  {author}  {ttag}  {bar}")
            cap = f"{row['date']}  ·  👁 {row.get('readers_count',0)}  ·  ❤️ {row.get('likes_count',0)}  ·  💬 {row.get('comments_count',0)}"
            st.caption(cap)

            import data
            codes_text = list(data.extract_codes_from_text(row.get("text", "") or ""))
            try:
                codes_json = json.loads(row.get("stock_codes", "[]") or "[]")
            except Exception:
                codes_json = []
            all_codes = list(dict.fromkeys(codes_json + codes_text))[:10]
            if all_codes:
                st.caption("🏷 " + " · ".join(f"`{c}`" for c in all_codes))

            with st.expander("全文"):
                text = row.get("text", "") or ""
                st.markdown(text[:3000])
                if len(text) > 3000:
                    st.caption(f"...（共 {len(text)} 字）")

        with cols[1]:
            if heat > 0:
                st.metric("🔥", str(heat))

        st.divider()

with tab2:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("按作者统计")
        st.bar_chart(df["author"].value_counts().head(10))
    with c2:
        st.subheader("每日帖数")
        df["day"] = pd.to_datetime(df["create_time"]).dt.strftime("%m-%d")
        st.bar_chart(df["day"].value_counts().sort_index())
