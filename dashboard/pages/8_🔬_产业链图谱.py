"""产业链图谱 — ECharts 树图 · 跨链关联 · 点击钻取"""
import json
import sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd

from dashboard.utils.data_bridge import (
    get_bom_chain_list, get_serenity_chain_detail, get_serenity_chain_summary,
)

st.set_page_config(page_title="产业链图谱", page_icon="🔬", layout="wide")

st.title("🔬 产业链图谱")
st.caption("L1 行业 → L2 层级 → L3 卡脖子节点 → L4 标的 · 支持钻取 & 跨链关联")

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.subheader("🔍 产业链")
    chains = get_bom_chain_list()
    if not chains:
        st.error("BOM 知识库暂无数据")
        st.stop()
    selected = st.selectbox("选择产业链", chains)

    st.divider()
    st.subheader("🔗 跨链关联")
    st.caption("同一环节出现在多条链中时标记")

    # 预计算跨链
    all_segments: dict[str, list[str]] = defaultdict(list)
    for c in chains:
        d = get_serenity_chain_detail(c)
        if not d:
            continue
        for seg in (d.get("segments") or []):
            name = seg.get("segment", "").strip()
            if name and len(name) >= 2:
                all_segments[name].append(c)

    cross = {k: v for k, v in all_segments.items() if len(v) >= 2}
    if cross:
        with st.expander(f"已发现 {len(cross)} 个跨链环节", expanded=True):
            for name, cnames in sorted(cross.items(), key=lambda x: -len(x[1]))[:20]:
                st.caption(f"**{name}** → {', '.join(cnames[:3])}")

# ============================================================
# Data
# ============================================================
detail = get_serenity_chain_detail(selected)
if not detail:
    st.info(f"「{selected}」暂无分析数据")
    st.stop()

segments = detail.get("segments", [])
stocks_list = detail.get("stocks", [])

# ============================================================
# Build ECharts tree
# ============================================================

def _tier_rank(t: str) -> int:
    t = t or ""
    if any(w in t for w in ["上游", "材料", "原料", "衬底", "粉体", "硅片", "光刻", "气体", "化学", "金属", "非金属"]):
        return 0
    if any(w in t for w in ["中游", "制造", "封装", "模组", "芯片", "晶圆", "设备", "加工", "电子", "元件"]):
        return 1
    if any(w in t for w in ["下游", "应用", "终端", "系统", "整车", "集成", "消费"]):
        return 2
    return 3

def build_tree(chain_name: str) -> dict:
    tier_map = defaultdict(list)
    for s in segments:
        t = s.get("tier", "").strip()
        if not t:
            t = "其他"
        tier_map[t].append(s)

    children = []
    for tier_name in sorted(tier_map.keys(), key=_tier_rank):
        segs = tier_map[tier_name]
        tier_node = {
            "name": tier_name,
            "itemStyle": {"color": "#6366f1", "borderColor": "#4f46e5"},
            "children": [],
        }
        for seg in sorted(segs, key=lambda s: -s.get("global_chokepoint_score", 0)):
            score = seg.get("global_chokepoint_score", 0)
            seg_name = seg.get("segment", "?")[:30]
            is_cross = seg_name in cross
            color = "#ef4444" if score >= 9 else ("#f59e0b" if score >= 7 else "#10b981")
            if is_cross:
                seg_name = "🔗 " + seg_name

            seg_node = {
                "name": seg_name,
                "value": score,
                "itemStyle": {"color": color, "borderColor": color},
                "tooltip_data": {
                    "supply": (seg.get("supply_status") or "")[:80],
                    "scene": seg.get("scene") or "",
                    "mapping": (seg.get("a_stock_mapping") or "")[:80],
                    "cross_chains": cross.get(seg.get("segment", ""), []),
                },
                "children": [],
                "collapsed": score < 7,
            }

            matched = sorted(
                [s for s in stocks_list if s.get("fev_total", 0) > 0],
                key=lambda s: -s.get("fev_total", 0),
            )[:4]
            for m in matched:
                fev = m.get("fev_total", 0)
                sc = "#8b5cf6" if fev >= 20 else ("#a78bfa" if fev >= 15 else "#c4b5fd")
                seg_node["children"].append({
                    "name": f"{m['code']} {m['name'][:6]}",
                    "value": fev,
                    "itemStyle": {"color": sc, "borderColor": sc},
                    "tooltip_data": {
                        "f_score": m.get("f_score", 0),
                        "e_score": m.get("e_score", 0),
                        "v_score": m.get("v_score", 0),
                        "fev": fev,
                    },
                })

            tier_node["children"].append(seg_node)
        children.append(tier_node)

    return {
        "name": chain_name,
        "itemStyle": {"color": "#1e293b", "borderColor": "#0f172a", "borderWidth": 2},
        "children": children,
        "collapsed": False,
    }

tree_data = build_tree(selected)

# ============================================================
# Stats
# ============================================================
col1, col2, col3, col4 = st.columns(4)
with col1:
    max_score = max((s.get("global_chokepoint_score", 0) for s in segments), default=0)
    st.metric("卡脖子最高", f"{max_score}/10")
with col2:
    st.metric("环节数", len(segments))
with col3:
    st.metric("标的数", len(stocks_list))
with col4:
    linked = sum(1 for s in segments if s.get("segment", "") in cross)
    st.metric("跨链环节", linked)

# ============================================================
# ECharts
# ============================================================
html = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
</head><body>
<div id="chart" style="width:100%;height:750px;"></div>
<script>
var data = {json.dumps(tree_data, ensure_ascii=False)};
var chart = echarts.init(document.getElementById('chart'));

function walkTree(node) {{
    var m = node.tooltip_data || {{}};
    var extra = '';
    if (m.supply) extra += '<br/>供给: ' + m.supply;
    if (m.scene) extra += '<br/>场景: ' + m.scene;
    if (m.mapping) extra += '<br/>A股映射: ' + m.mapping;
    if (m.cross_chains && m.cross_chains.length)
        extra += '<br/>🔗 也出现于: ' + m.cross_chains.join(', ');
    if (m.fev)
        extra += '<br/>F=' + m.f_score + ' E=' + m.e_score + ' V=' + m.v_score + ' FEV=' + m.fev;
    node.tooltip = {{ formatter: '<b>' + node.name + '</b>' + extra }};
    if (node.children) node.children.forEach(walkTree);
}}
walkTree(data);

chart.setOption({{
    tooltip: {{ trigger: 'item', triggerOn: 'mousemove' }},
    series: [{{
        type: 'tree', data: [data],
        top: '2%', left: '8%', bottom: '2%', right: '20%',
        symbol: 'circle',
        symbolSize: function(v) {{ return v.value ? Math.min(28, 8+v.value*1.4) : 14; }},
        orient: 'LR', expandAndCollapse: true,
        animationDuration: 400, animationDurationUpdate: 500,
        label: {{
            position: 'left', verticalAlign: 'middle', align: 'right',
            fontSize: 11, color: '#1e293b',
            formatter: function(p) {{
                return p.value ? p.name+' ['+p.value+']' : p.name;
            }}
        }},
        leaves: {{ label: {{ position: 'right', align: 'left', fontSize: 10 }} }},
        emphasis: {{ focus: 'descendant', label: {{ fontSize: 13, fontWeight: 'bold' }} }},
    }}]
}});

window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
<div id="node-info" style="position:fixed;bottom:20px;right:20px;
background:#1e293b;color:#f1f5f9;padding:12px 16px;border-radius:8px;
font-size:12px;max-width:320px;display:none;"></div>
<script>
var info = document.getElementById('node-info');
chart.on('click', function(p) {{
    var m = (p.data||{{}}).tooltip_data || {{}};
    var h = '<b>'+p.data.name+'</b>';
    if (m.supply) h += '<br/>供给: '+m.supply;
    if (m.scene) h += '<br/>场景: '+m.scene;
    if (m.mapping) h += '<br/>映射: '+m.mapping;
    if (m.cross_chains && m.cross_chains.length)
        h += '<br/>🔗 跨链: '+m.cross_chains.join(', ');
    if (m.fev) h += '<br/>F='+m.f_score+' E='+m.e_score+' V='+m.v_score+' FEV='+m.fev;
    info.innerHTML = h; info.style.display = 'block';
}});
document.addEventListener('click', function(e) {{
    if (!e.target.closest('canvas')) info.style.display = 'none';
}});
</script>
</body></html>
"""

st.components.v1.html(html, height=820, scrolling=False)

with st.expander("📖 图例"):
    c = st.columns(4)
    c[0].caption("🔴 红 — 卡脖子 ≥9\n🟡 黄 — 7-8\n🟢 绿 — <7")
    c[1].caption("🟣 紫 — 标的(FEV)\n🔵 蓝 — 层级")
    c[2].caption("🔗 前缀 — 跨链环节\n🖱 悬停 — 详情\n🖱 点击 — 聚焦")

# ============================================================
# Stock table
# ============================================================
st.divider()
st.subheader(f"📈 {selected} · 标的 FEV")

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
