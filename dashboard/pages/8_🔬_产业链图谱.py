"""产业链图谱 — L1行业 → L2层级 → L3环节/卡脖子 → L4标的，逐层展开"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd

from dashboard.utils.data_bridge import (
    get_bom_chain_list, get_serenity_chain_detail,
    get_serenity_chain_summary,
)

st.set_page_config(page_title="产业链图谱", page_icon="🔬", layout="wide")

st.title("🔬 产业链图谱")
st.caption("L1 行业 → L2 层级 → L3 环节/卡脖子节点 → L4 标的 FEV 评分")

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.subheader("🔍 选择产业链")
    chains = get_bom_chain_list()
    if not chains:
        st.error("BOM 知识库暂无数据")
        st.stop()

    selected = st.selectbox("产业链", chains, key="graph_chain")

    view_mode = st.radio(
        "展开模式",
        ["层级", "卡脖子热点", "场景分类"],
        help="层级: 按上游→中游→下游展开\n"
             "卡脖子热点: 按卡脖子分降序，≥9标红\n"
             "场景分类: 按场景A/B/C分组"
    )

    summary = get_serenity_chain_summary()
    if summary:
        with st.expander("📊 全貌概览"):
            for c in summary[:14]:
                flag = "🔴" if c["max_score"] >= 9 else ("🟡" if c["max_score"] >= 7 else "🟢")
                st.caption(f"{flag} {c['chain_name']} **{c['max_score']}** ({c['segment_count']}环节)")

# ============================================================
# Main
# ============================================================
detail = get_serenity_chain_detail(selected)
if not detail:
    st.info(f"「{selected}」暂无分析数据。去「🔗 产业链卡脖子」Tab 触发分析。")
    st.stop()

segments = detail.get("segments", [])
stocks_list = detail.get("stocks", [])

st.subheader(f"📐 {selected} · 产业链结构")

col1, col2, col3, col4 = st.columns(4)
with col1:
    max_score = max((s.get("global_chokepoint_score", 0) for s in segments), default=0)
    st.metric("卡脖子最高", f"{max_score}/10")
with col2:
    st.metric("环节数", len(segments))
with col3:
    tiers = sorted(set(s.get("tier", "?") for s in segments))
    st.metric("层级数", len(tiers))
with col4:
    st.metric("标的数", len(stocks_list))

st.divider()

# ============================================================
# 三种视图
# ============================================================

def _tier_rank(t: str) -> int:
    if any(w in t for w in ["上游", "材料", "原料", "衬底", "粉体", "硅片", "光刻", "气体"]):
        return 0
    if any(w in t for w in ["中游", "制造", "封装", "模组", "芯片", "晶圆", "设备"]):
        return 1
    if any(w in t for w in ["下游", "应用", "终端", "系统", "整车"]):
        return 2
    return 3

if view_mode == "层级":
    tier_map = {}
    for s in segments:
        t = s.get("tier", "其他")
        tier_map.setdefault(t, []).append(s)

    for tier_name in sorted(tier_map.keys(), key=_tier_rank):
        segs = tier_map[tier_name]
        segs.sort(key=lambda s: s.get("global_chokepoint_score", 0), reverse=True)

        with st.expander(f"🏗 {tier_name}  ({len(segs)} 环节)", expanded=True):
            for seg in segs:
                score = seg.get("global_chokepoint_score", 0)
                bar = "🔴" * min(5, score) if score >= 7 else "🟢" * max(1, score // 2)
                supply = seg.get("supply_status", "") or ""
                scene = seg.get("scene", "") or ""

                cols = st.columns([5, 1, 3])
                with cols[0]:
                    st.markdown(f"**{seg.get('segment', '?')}**  {bar}")
                    tags = []
                    if supply:
                        tags.append(supply[:80])
                    if scene:
                        tags.append(f"[场景{scene[0]}]" if scene else "")
                    if tags:
                        st.caption(" · ".join(tags))
                with cols[1]:
                    st.metric("卡脖子", f"{score}/10")
                with cols[2]:
                    st.caption("**相关标的**")
                    for m in sorted(stocks_list, key=lambda x: x.get("fev_total", 0), reverse=True)[:3]:
                        st.caption(
                            f"`{m['code']}` {m['name']} "
                            f"F{m['f_score']}E{m['e_score']}V{m['v_score']} "
                            f"**{m['fev_total']}**"
                        )

elif view_mode == "卡脖子热点":
    ranked = sorted(segments, key=lambda s: s.get("global_chokepoint_score", 0), reverse=True)
    for seg in ranked:
        score = seg.get("global_chokepoint_score", 0)
        if score == 0:
            continue
        flag = "🔴" if score >= 9 else ("🟡" if score >= 7 else "🟢")
        cols = st.columns([5, 1, 2, 2])
        with cols[0]:
            st.markdown(f"{flag} **{seg.get('segment', '?')}**")
            st.caption(f"{seg.get('tier', '')} · {(seg.get('supply_status', '') or '')[:60]}")
        with cols[1]:
            st.metric("卡脖子", f"{score}/10")
        with cols[2]:
            st.caption((seg.get("a_stock_mapping", "") or seg.get("scene", ""))[:50])
        with cols[3]:
            st.caption("**FEV TOP**")
            for m in sorted(stocks_list, key=lambda x: x.get("fev_total", 0), reverse=True)[:2]:
                st.caption(f"`{m['code']}` {m['name'][:6]} {m['fev_total']}")
        st.divider()

else:  # 场景分类
    scene_map = {"A": [], "B": [], "C": [], "?": []}
    for seg in segments:
        s = seg.get("scene", "?") or "?"
        key_scene = s[0] if s else "?"
        scene_map.setdefault(key_scene, []).append(seg)

    scene_labels = {
        "A": ("🟢 场景A · 直接受益", "中国是这层的关键供应商"),
        "B": ("🟡 场景B · 国产替代", "海外垄断，国内在追赶"),
        "C": ("🔴 场景C · 不做", "没有中国标的，纯概念"),
        "?": ("⚪ 未分类", ""),
    }
    for key in ["A", "B", "C", "?"]:
        segs = scene_map.get(key, [])
        if not segs:
            continue
        label, desc = scene_labels[key]
        with st.expander(f"{label}  ({len(segs)} 环节)", expanded=key != "?"):
            if desc:
                st.caption(desc)
            for seg in segs:
                score = seg.get("global_chokepoint_score", 0)
                cols = st.columns([6, 1, 2])
                with cols[0]:
                    st.markdown(f"**{seg.get('segment', '?')}**  ({seg.get('tier', '')})")
                    m = seg.get("a_stock_mapping", "")
                    if m:
                        st.caption(f"→ {m[:80]}")
                with cols[1]:
                    st.metric("卡", f"{score}")
                with cols[2]:
                    st.caption((seg.get("supply_status", "") or "")[:60])

# ============================================================
# Bottom
# ============================================================
st.divider()
st.subheader(f"📈 {selected} · 标的 FEV 评分")

if stocks_list:
    df = pd.DataFrame(stocks_list)
    df = df.rename(columns={
        "code": "代码", "name": "名称",
        "f_score": "F", "e_score": "E", "v_score": "V", "fev_total": "FEV",
    })
    show = [c for c in ["代码", "名称", "F", "E", "V", "FEV"] if c in df.columns]
    df = df[show].sort_values("FEV", ascending=False)
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={"FEV": st.column_config.ProgressColumn("FEV", max_value=30, format="%d/30")})
else:
    st.info("暂无标的评分数据")
