"""报告浏览"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import streamlit as st
from dashboard.utils.data_bridge import list_reports, get_report_content

st.set_page_config(page_title="报告", page_icon="📊", layout="wide", initial_sidebar_state="collapsed")

c1, c2 = st.columns([2, 3])
with c1:
    rt = st.selectbox("类型", ["review","advice","bom","wechat"],
        format_func=lambda x: {"review":"复盘","advice":"建议","bom":"BOM","wechat":"公众号"}[x])
with c2:
    reports = list_reports(rt, 60)
    dates = [r["date"] for r in reports if r["date"]]
    sel = st.selectbox("日期", dates, index=0) if dates else None

if sel:
    r = get_report_content(rt, sel)
    content = r.get("content", "")
    st.caption(f"{len(content):,}字  |  {r.get('path','')}")
    with st.container(height=680):
        st.markdown(content, unsafe_allow_html=False)
