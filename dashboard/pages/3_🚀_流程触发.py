"""流程触发"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import streamlit as st
from dashboard.utils.data_bridge import run_pipeline, get_pipeline_status, get_pipeline_log, PIPELINE_CONFIG

st.set_page_config(page_title="触发", page_icon="🚀", layout="wide", initial_sidebar_state="collapsed")

statuses = {s["key"]: s for s in get_pipeline_status()}

for key, cfg in PIPELINE_CONFIG.items():
    s = statuses.get(key, {})
    status = s.get("status", "never_run")
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    with c1: st.caption(f"**{cfg['name']}** — {cfg['desc']}")
    with c2:
        badge = {"success":"✅","failed":"❌","running":"🔄","never_run":"⬜"}.get(status, status)
        st.caption(badge)
    with c3:
        extra = None
        if key == "review":
            d = st.text_input("日期", value="", key=f"d_{key}", placeholder="今天", label_visibility="collapsed")
            if d: extra = ["--date", d]
    with c4:
        if st.button("运行", key=f"run_{key}", use_container_width=True, disabled=(status=="running")):
            r = run_pipeline(key, extra)
            if "error" in r: st.error(r["error"])
            else: st.rerun()
    if s.get("log_path"):
        with st.expander("日志"):
            st.code(get_pipeline_log(key, s["log_path"])[-4000:], language="log")
