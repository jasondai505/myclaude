"""个股查询"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import streamlit as st
from dashboard.utils.data_bridge import get_stock_overview, get_stock_bom_position, get_stock_deep_reports, get_watchlist_status

st.set_page_config(page_title="个股", page_icon="🔍", layout="wide", initial_sidebar_state="collapsed")

c1, c2 = st.columns([4, 1])
with c1: code = st.text_input("代码", placeholder="300476", key="code", label_visibility="collapsed").strip()
with c2: search = st.button("查询", use_container_width=True, type="primary")

with st.expander("自选股"):
    wl = get_watchlist_status()
    if wl and "error" not in wl[0]:
        cols = st.columns(10)
        for i, s in enumerate(wl[:30]):
            with cols[i%10]:
                if st.button(f"{s.get('name','')}\n{s['code']}", key=f"w_{s['code']}"):
                    st.session_state.code = s["code"]; search = True

if not code or not search: st.stop()

ov = get_stock_overview(code)
if "error" in ov: st.error(ov["error"]); st.stop()

cols = st.columns(6)
cap = ov.get("market_cap", 0)
items = [("价格", f"{ov.get('price',0):.2f}"), ("涨跌", f"{ov.get('change_pct',0):+.1f}%"),
    ("PE", f"{ov.get('pe',0):.1f}"), ("PB", f"{ov.get('pb',0):.2f}"),
    ("市值", f"{cap/10000:.0f}亿" if cap else "-"), ("换手", f"{ov.get('turnover_rate',0):.2f}%")]
for i, (label, val) in enumerate(items):
    with cols[i]: st.metric(label, val)

st.divider()
bom = get_stock_bom_position(code)
if bom:
    st.caption("**BOM产业链位置**")
    for bp in bom[:5]:
        moat = bp.get("moat_score", 0)
        st.caption(f"{bp.get('chain_id','')} | {bp.get('segment','')} | #{bp.get('rank',0)} | 护城河{'⭐'*min(5,max(1,int(moat/2)))}")

deep = get_stock_deep_reports(code)
if deep:
    st.divider()
    st.caption("**深度分析报告**")
    for dr in deep[:3]:
        if st.button(f"📄 {dr['date']} ({dr['size_kb']:.0f}KB)", key=f"dr_{dr['date']}"):
            content = Path(dr["path"]).read_text(encoding="utf-8")
            st.markdown(content[:15000])
