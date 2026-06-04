"""A股量化仪表盘 — 首页"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import streamlit as st
from datetime import datetime
from dashboard.utils.data_bridge import (
    get_latest_advice_summary, get_latest_bom_summary,
    get_pipeline_status, get_market_index_snapshot, _today_str,
)

st.set_page_config(page_title="A股仪表盘", page_icon="📊", layout="wide",
                   initial_sidebar_state="collapsed")

with st.sidebar:
    st.caption(f"📊 {_today_str()}")
    if st.button("⚡ 实时快扫", use_container_width=True):
        st.switch_page("pages/6_⚡_实时快扫.py")
    if st.button("🔄 流程触发", use_container_width=True):
        st.switch_page("pages/3_🚀_流程触发.py")

# === 指数快照（单行紧凑）===
idx_data = get_market_index_snapshot()
if "error" not in idx_data and idx_data.get("indices"):
    indices = list(idx_data["indices"].items())[:6]
    cols = st.columns(len(indices))
    for i, (name, info) in enumerate(indices):
        with cols[i]:
            chg = info.get("change_pct", 0)
            st.metric(name, f"{info.get('price',0):.0f}",
                      delta=f"{chg:+.2f}%", delta_color="normal")

st.divider()

# === Tab 切换 ===
tab1, tab2, tab3 = st.tabs(["建议", "BOM", "流水线"])

with tab1:
    a = get_latest_advice_summary()
    if a.get("key_points"):
        for pt in a["key_points"][:6]:
            st.markdown(f"- {pt}")
    elif "error" in a:
        st.caption("暂无")

with tab2:
    b = get_latest_bom_summary()
    if b.get("industries"):
        for ind in b["industries"][:5]:
            st.caption(f"**{ind['industry']}**")
    elif "error" in b:
        st.caption("暂无")

with tab3:
    ps_list = get_pipeline_status()
    if ps_list:
        cols = st.columns(len(ps_list))
        for i, ps in enumerate(ps_list):
            with cols[i]:
                emoji = {"success":"✅","failed":"❌","running":"🔄"}.get(ps.get("status",""),"⬜")
                st.caption(f"{emoji} {ps['label']}")
