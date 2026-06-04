"""监控状态"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import streamlit as st
from datetime import date
from dashboard.utils.data_bridge import get_pipeline_status, get_pipeline_log, list_reports, _today_str

st.set_page_config(page_title="监控", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")

st.caption("**数据新鲜度**")
rows = []
for label, rtype in [("复盘","review"),("建议","advice"),("公众号","wechat"),("BOM","bom")]:
    reps = list_reports(rtype, 1)
    if reps and reps[0]["date"]:
        behind = (date.today() - date.fromisoformat(reps[0]["date"])).days
        s = "🟢" if behind <= 1 else ("🟡" if behind <= 3 else "🔴")
        rows.append({"数据": label, "最新": reps[0]["date"], "落后": behind, "状态": s})
    else:
        rows.append({"数据": label, "最新": "无", "落后": "-", "状态": "⚫"})
st.dataframe(rows, use_container_width=True, hide_index=True)

st.divider()
st.caption("**流水线**")
for s in get_pipeline_status():
    emoji = {"success":"✅","failed":"❌","running":"🔄"}.get(s.get("status",""),"⬜")
    ts = s.get("last_run", "")[:16].replace("T", " ") if s.get("last_run") else ""
    st.caption(f"{emoji} {s['label']}  {ts}")
    if s.get("log_path"):
        with st.expander("日志"):
            st.code(get_pipeline_log(s["key"], s["log_path"])[-3000:], language="log")
