"""实时快扫 — 全A行情 5 秒级刷新"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import time
from datetime import datetime

import streamlit as st
import pandas as pd

from dashboard.utils.data_bridge import run_live_scan, get_cached_scan

st.set_page_config(page_title="实时快扫", page_icon="⚡", layout="wide", initial_sidebar_state="collapsed")

if "scan_result" not in st.session_state:
    cached = get_cached_scan()
    st.session_state.scan_result = cached
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False
if "last_scan_time" not in st.session_state:
    st.session_state.last_scan_time = None

# ---- 顶栏 ----
c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 1, 1])
with c1:
    st.title("⚡ 实时快扫")
with c2:
    if st.button("🔍 扫描全A", type="primary", use_container_width=True):
        with st.spinner("正在扫描全A 5200+ 只股票..."):
            st.session_state.scan_result = run_live_scan(force_refresh=True)
            st.session_state.last_scan_time = time.time()
        st.rerun()
with c3:
    auto = st.toggle("🔄 自动刷新", value=st.session_state.auto_refresh, key="auto_toggle")
    if auto != st.session_state.auto_refresh:
        st.session_state.auto_refresh = auto
        st.rerun()
with c4:
    a_interval = st.selectbox("间隔", ["30s", "60s", "120s"], index=1, label_visibility="collapsed", key="interval_select")
with c5:
    st.caption(f"🕐 {datetime.now().strftime('%H:%M:%S')}")

# ---- 自动刷新逻辑 ----
if st.session_state.auto_refresh:
    interval_map = {"30s": 30, "60s": 60, "120s": 120}
    interval = interval_map[a_interval]
    if st.session_state.last_scan_time is None or (time.time() - st.session_state.last_scan_time) > interval:
        st.session_state.scan_result = run_live_scan(force_refresh=True)
        st.session_state.last_scan_time = time.time()
    st.rerun()

result = st.session_state.scan_result

if result is None:
    st.info("点击「扫描全A」开始获取实时行情")
    st.stop()

if "error" in result:
    st.error(f"扫描失败: {result['error']}")
    if st.button("重试"):
        st.session_state.scan_result = None
        st.rerun()
    st.stop()

# ---- 摘要卡片 ----
s = result.get("summary", {})
if s:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("全A数量", f"{result['count']} 只")
    c2.metric("耗时", f"{result['elapsed']}s")
    c3.metric("上涨", f"{s.get('up_count', '-')} 只",
              delta=f"{s.get('avg_change', 0):+.1f}% 均值", delta_color="normal")
    c4.metric("下跌", f"{s.get('down_count', '-')} 只")
    c5.metric("涨停", f"{s.get('limit_up', '-')} 只")
    c6.metric("跌停", f"{s.get('limit_down', '-')} 只")

    if s.get("top_gainer"):
        tg = s["top_gainer"]
        tl = s.get("top_loser", {})
        st.caption(f"🔥 {tg['name']}({tg['code']}) {tg['change_pct']:+.1f}%  |  ❄️ {tl.get('name','')}({tl.get('code','')}) {tl.get('change_pct',0):+.1f}%")

st.divider()

# ---- 过滤器 ----
df: pd.DataFrame = result["data"]
if df.empty:
    st.warning("空数据")
    st.stop()

f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
with f1:
    min_chg = st.number_input("最小涨幅%", value=-100.0, step=0.5, key="min_chg")
with f2:
    min_amount = st.number_input("最小成交额(亿)", value=0.0, step=0.1, key="min_amount")
with f3:
    min_turnover = st.number_input("最小换手%", value=0.0, step=0.5, key="min_turnover")
with f4:
    search = st.text_input("代码/名称搜索", value="", key="search_code", placeholder="如 300069")

filtered = df.copy()
if "change_pct" in filtered.columns:
    filtered = filtered[filtered["change_pct"] >= min_chg]
if "amount_wan" in filtered.columns and min_amount > 0:
    filtered = filtered[filtered["amount_wan"] >= min_amount * 10000]
if "turnover_pct" in filtered.columns and min_turnover > 0:
    filtered = filtered[filtered["turnover_pct"] >= min_turnover]
if search:
    s_lower = search.lower()
    mask = filtered["code"].astype(str).str.contains(s_lower, na=False)
    if "name" in filtered.columns:
        mask |= filtered["name"].astype(str).str.contains(s_lower, na=False)
    filtered = filtered[mask]

st.caption(f"显示 {len(filtered)} / {len(df)} 只")

# ---- 数据表 ----
show_cols = ["code", "name", "price", "change_pct", "amount_wan",
             "turnover_pct", "pe_ttm", "pb", "mcap_yi", "vol_ratio"]
available = [c for c in show_cols if c in filtered.columns]
display = filtered[available].copy()

if "amount_wan" in display.columns:
    display["成交额(亿)"] = (display["amount_wan"] / 10000).round(1)
    display = display.drop(columns=["amount_wan"])
if "mcap_yi" in display.columns:
    display["市值(亿)"] = display["mcap_yi"].round(0).astype("Int64")
    display = display.drop(columns=["mcap_yi"])
if "price" in display.columns:
    display["现价"] = display["price"].round(2)
    display = display.drop(columns=["price"])
if "change_pct" in display.columns:
    display["涨跌幅%"] = display["change_pct"].round(2)
    display = display.drop(columns=["change_pct"])
if "turnover_pct" in display.columns:
    display["换手%"] = display["turnover_pct"].round(2)
    display = display.drop(columns=["turnover_pct"])
if "pe_ttm" in display.columns:
    display["PE"] = display["pe_ttm"].round(1)
    display = display.drop(columns=["pe_ttm"])
if "pb" in display.columns:
    display["PB"] = display["pb"].round(2)
    display = display.drop(columns=["pb"])
if "vol_ratio" in display.columns:
    display["量比"] = display["vol_ratio"].round(2)
    display = display.drop(columns=["vol_ratio"])

rename = {"code": "代码", "name": "名称"}
display = display.rename(columns={k: v for k, v in rename.items() if k in display.columns})

def _color_pct(val):
    try:
        v = float(val)
        if v > 0: return "color: #e74c3c; font-weight: bold"
        if v < 0: return "color: #27ae60"
    except (ValueError, TypeError):
        pass
    return ""

styled = display.style
if "涨跌幅%" in display.columns:
    styled = styled.map(_color_pct, subset=["涨跌幅%"])

st.dataframe(
    styled,
    use_container_width=True,
    hide_index=True,
    height=700,
)

if st.button("📥 导出 CSV"):
    csv = filtered.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("点击下载", csv, f"live_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", "text/csv")
