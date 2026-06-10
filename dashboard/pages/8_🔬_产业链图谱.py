"""产业链图谱 — ECharts 树图 + 三种视图 + 跨链关联 + 标的详情"""
import json, sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd

from dashboard.utils.data_bridge import (
    get_bom_chain_list, get_serenity_chain_detail, get_serenity_chain_summary,
    get_stock_fev_history, get_stocks_quotes_batch, get_segment_cross_chains,
)

st.set_page_config(page_title="产业链图谱", page_icon="🔬", layout="wide")

st.title("🔬 产业链图谱")
st.caption("L1 行业 → L2 层级 → L3 卡脖子节点 → L4 标的 · 树图钻取 + 三种视图 + 跨链关联")

# ============================================================
# Helpers
# ============================================================
def _tier_rank(t: str) -> int:
    t = t or ""
    if any(w in t for w in ["上游","材料","原料","衬底","粉体","硅片","光刻","气体","化学","金属","非金属"]):
        return 0
    if any(w in t for w in ["中游","制造","封装","模组","芯片","晶圆","设备","加工","电子","元件"]):
        return 1
    if any(w in t for w in ["下游","应用","终端","系统","整车","集成","消费"]):
        return 2
    return 3

def _choke_color(score: int) -> str:
    if score >= 8: return "#ef4444"
    if score >= 5: return "#f59e0b"
    return "#10b981"

def _choke_emoji(score: int) -> str:
    if score >= 9: return "🔴"
    if score >= 7: return "🟡"
    if score >= 5: return "🟠"
    return "🟢"

def _fev_color(fev: int) -> str:
    if fev >= 20: return "#8b5cf6"
    if fev >= 15: return "#a78bfa"
    return "#c4b5fd"

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.subheader("🔍 产业链")
    chains = get_bom_chain_list()
    if not chains:
        st.error("BOM 知识库暂无数据")
        st.stop()

    try:
        qp_chain = st.query_params.get("chain")
    except AttributeError:
        params = st.experimental_get_query_params()
        qp_chain = params.get("chain", [None])[0]
    default_idx = 0
    if qp_chain and qp_chain in chains:
        default_idx = chains.index(qp_chain)

    selected = st.selectbox("选择产业链", chains, index=default_idx)

    # 全貌概览
    summary = get_serenity_chain_summary()
    if summary:
        with st.expander("📊 全貌概览", expanded=False):
            for c in summary[:14]:
                flag = "🔴" if c["max_score"] >= 9 else ("🟡" if c["max_score"] >= 7 else "🟢")
                st.caption(f"{flag} {c['chain_name']} **{c['max_score']}** ({c['segment_count']}环节)")

    cross = get_segment_cross_chains()
    st.divider()
    st.subheader("🔗 跨链关联")
    if cross:
        with st.expander(f"{len(cross)} 个跨链环节", expanded=False):
            for name, cnames in sorted(cross.items(), key=lambda x: -len(x[1]))[:20]:
                st.caption(f"**{name}** → {', '.join(cnames[:3])}")
    else:
        st.caption("暂无跨链数据")

    st.divider()
    st.caption("🔴≥8 · 🟡5-7 · 🟢<5 · 🟣标的 · 🟠跨链")

# ============================================================
# Data
# ============================================================
detail = get_serenity_chain_detail(selected)
if not detail:
    st.info(f"「{selected}」暂无分析数据。去「🔗 产业链卡脖子」Tab 触发分析。")
    st.stop()

segments = detail.get("segments", [])
stocks_list = detail.get("stocks", [])

stock_codes = [s["code"] for s in stocks_list if s.get("code")]
quotes = get_stocks_quotes_batch(stock_codes) if stock_codes else {}
fev_hist = {c: get_stock_fev_history(c, 30) for c in stock_codes}

stocks_by_seg: dict[str, list[dict]] = defaultdict(list)
for s in stocks_list:
    seg = s.get("segment", "").strip() or "其他"
    stocks_by_seg[seg].append(s)
for seg_stocks in stocks_by_seg.values():
    seg_stocks.sort(key=lambda s: -s.get("fev_total", 0))

# ============================================================
# Stats
# ============================================================
chain_segments_in_cross = [s for s in segments if s.get("segment", "") in cross]

col1, col2, col3, col4, col5 = st.columns(5)
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
with col5:
    st.metric("跨链环节", len(chain_segments_in_cross))

# ============================================================
# ECharts Tree + Cross-chain Graph + Detail Panel
# ============================================================
st.subheader("🌳 产业链树图")

def build_tree(chain_name: str) -> dict:
    tier_map = defaultdict(list)
    for s in segments:
        t = s.get("tier", "").strip() or "其他"
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
            seg_name_raw = seg.get("segment", "?")[:30]
            is_cross = seg_name_raw in cross
            seg_name = ("🔗 " if is_cross else "") + seg_name_raw

            seg_node = {
                "name": seg_name,
                "value": score,
                "itemStyle": {"color": _choke_color(score), "borderColor": _choke_color(score)},
                "tooltip_data": {
                    "supply": (seg.get("supply_status") or "")[:80],
                    "scene": seg.get("scene") or "",
                    "mapping": (seg.get("a_stock_mapping") or "")[:80],
                    "cross_chains": cross.get(seg_name_raw, []),
                },
                "children": [],
                "collapsed": score < 7,
            }

            # P0-3: cross-chain reference nodes
            if is_cross:
                other_chains = [c for c in cross[seg_name_raw] if c != chain_name]
                for oc in other_chains[:3]:
                    seg_node["children"].append({
                        "name": f"📎 {oc}",
                        "value": 0,
                        "itemStyle": {"color": "#fbbf24", "borderColor": "#d97706", "borderType": "dashed"},
                        "symbol": "diamond", "symbolSize": 8,
                        "tooltip_data": {"cross_ref": oc, "segment": seg_name_raw},
                        "collapsed": True,
                    })

            # P0-4: stock children with embedded quote + FEV history
            seg_stocks = stocks_by_seg.get(seg_name_raw, [])[:5]
            for m in seg_stocks:
                code = m.get("code", "")
                fev = m.get("fev_total", 0)
                q = quotes.get(code, {})
                fh = fev_hist.get(code, [])

                seg_node["children"].append({
                    "name": f"{code} {m.get('name','')[:6]}",
                    "value": fev,
                    "itemStyle": {"color": _fev_color(fev), "borderColor": _fev_color(fev)},
                    "tooltip_data": {
                        "code": code, "name": m.get("name", ""),
                        "f_score": m.get("f_score", 0), "e_score": m.get("e_score", 0),
                        "v_score": m.get("v_score", 0), "fev": fev,
                        "price": q.get("price", 0) if q else 0,
                        "change_pct": q.get("change_pct", 0) if q else 0,
                        "pe": q.get("pe", 0) if q else 0,
                        "pb": q.get("pb", 0) if q else 0,
                        "market_cap": q.get("market_cap", 0) if q else 0,
                        "fev_history": [{"date": h.get("date",""), "fev": h.get("fev_total",0)} for h in fh],
                    },
                })

            tier_node["children"].append(seg_node)
        children.append(tier_node)

    return {
        "name": chain_name,
        "itemStyle": {"color": "#1e293b", "borderColor": "#0f172a", "borderWidth": 2},
        "children": children, "collapsed": False,
    }

tree_data = build_tree(selected)

# Cross-chain graph
cross_graph = None
if chain_segments_in_cross:
    graph_nodes, graph_links, node_ids = [], [], set()
    graph_nodes.append({"id": selected, "name": selected, "symbolSize": 36,
                        "itemStyle": {"color": "#1e293b"}, "category": 0})
    node_ids.add(selected)
    for seg in chain_segments_in_cross:
        sname = seg.get("segment", "")
        related = cross.get(sname, [])
        sid = f"seg_{sname}"
        if sid not in node_ids:
            graph_nodes.append({"id": sid, "name": sname[:12],
                                "symbolSize": 14 + seg.get("global_chokepoint_score", 0) * 1.5,
                                "itemStyle": {"color": _choke_color(seg.get("global_chokepoint_score", 0))},
                                "category": 1})
            node_ids.add(sid)
        graph_links.append({"source": selected, "target": sid})
        for rc in related:
            if rc == selected: continue
            if rc not in node_ids:
                graph_nodes.append({"id": rc, "name": rc, "symbolSize": 24,
                                    "itemStyle": {"color": "#94a3b8"}, "category": 2})
                node_ids.add(rc)
            graph_links.append({"source": sid, "target": rc})
    cross_graph = {"nodes": graph_nodes, "links": graph_links}

cross_graph_json = json.dumps(cross_graph, ensure_ascii=False) if cross_graph else "null"

html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;margin:0;}}
  #chart{{width:100%;height:650px;}}
  #cross-chart{{width:100%;height:240px;margin-top:6px;}}
  .cross-title{{text-align:center;color:#64748b;font-size:12px;margin-top:6px;}}
  #detail-panel{{position:fixed;top:0;right:-420px;width:400px;height:100vh;
    background:#0f172a;color:#e2e8f0;padding:20px;z-index:9999;
    transition:right 0.3s;overflow-y:auto;box-shadow:-4px 0 24px rgba(0,0,0,.4);font-size:13px;}}
  #detail-panel.open{{right:0;}}
  #detail-panel .close-btn{{position:absolute;top:8px;right:12px;background:none;border:none;color:#94a3b8;font-size:20px;cursor:pointer;}}
  #detail-panel h2{{font-size:18px;margin:0 0 4px;}}
  #detail-panel .code{{color:#64748b;font-size:12px;}}
  #detail-panel .price{{font-size:28px;font-weight:700;margin:8px 0;}}
  #detail-panel .chg-up{{color:#10b981;}} #detail-panel .chg-down{{color:#ef4444;}}
  #detail-panel .metric-row{{display:flex;gap:12px;margin:8px 0;}}
  #detail-panel .metric{{background:#1e293b;border-radius:8px;padding:8px 12px;flex:1;text-align:center;}}
  #detail-panel .metric .label{{color:#64748b;font-size:10px;}}
  #detail-panel .metric .val{{font-size:16px;font-weight:600;}}
  #detail-panel .fev-bar{{display:flex;gap:6px;margin:12px 0;}}
  #detail-panel .fev-item{{flex:1;text-align:center;}}
  #detail-panel .fev-item .bar{{height:6px;border-radius:3px;margin-top:4px;}}
  #detail-panel #sparkline{{width:100%;height:120px;margin-top:8px;}}
  #overlay{{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.3);z-index:9998;display:none;}}
  #overlay.show{{display:block;}}
</style></head><body>
<div id="overlay" onclick="closePanel()"></div>
<div id="detail-panel">
  <button class="close-btn" onclick="closePanel()">✕</button>
  <div id="panel-content"></div>
  <div id="sparkline"></div>
</div>
<div id="chart"></div>
<div class="cross-title">{'🔗 跨链关联网络（可拖拽 · 点击跳转）' if cross_graph else ''}</div>
<div id="cross-chart"></div>
<script>
var treeData = {json.dumps(tree_data, ensure_ascii=False)};
var crossGraph = {cross_graph_json};
function walkTree(node){{
    var m=node.tooltip_data||{{}},extra='';
    if(m.cross_ref){{extra='<br/>📎 点击跳转到「'+m.cross_ref+'」产业链';}}
    else if(m.code){{extra='<br/>'+m.name;
        if(m.price)extra+='<br/>💰 '+m.price.toFixed(2)+' <span style="color:'+(m.change_pct>=0?'#10b981':'#ef4444')+'">'+(m.change_pct>=0?'+':'')+m.change_pct.toFixed(2)+'%</span>';
        if(m.pe>0)extra+='<br/>PE:'+m.pe.toFixed(1)+' PB:'+m.pb.toFixed(1);
        if(m.market_cap>0)extra+=' 市值:'+(m.market_cap>10000?(m.market_cap/10000).toFixed(0)+'万亿':m.market_cap.toFixed(0)+'亿');
        extra+='<br/>F='+m.f_score+' E='+m.e_score+' V='+m.v_score+' <b>FEV='+m.fev+'</b>';}}
    else{{if(m.supply)extra+='<br/>供给:'+m.supply;
        if(m.scene)extra+='<br/>场景:'+m.scene;
        if(m.mapping)extra+='<br/>A股映射:'+m.mapping;
        if(m.cross_chains&&m.cross_chains.length)extra+='<br/>🔗 也出现于:'+m.cross_chains.join(', ');}}
    node.tooltip={{formatter:'<b>'+node.name+'</b>'+extra,trigger:'item'}};
    if(node.children)node.children.forEach(walkTree);
}}
walkTree(treeData);
var treeChart=echarts.init(document.getElementById('chart'));
treeChart.setOption({{tooltip:{{trigger:'item',triggerOn:'mousemove',confine:true}},
    series:[{{type:'tree',data:[treeData],top:'2%',left:'6%',bottom:'2%',right:'18%',
        symbol:'circle',symbolSize:function(v){{return v.value?Math.min(28,8+v.value*1.4):12;}},
        orient:'LR',expandAndCollapse:true,animationDuration:400,animationDurationUpdate:500,
        label:{{position:'left',verticalAlign:'middle',align:'right',fontSize:11,color:'#1e293b',
            formatter:function(p){{return p.value?p.name+' ['+p.value+']':p.name;}}}},
        leaves:{{label:{{position:'right',align:'left',fontSize:10}}}},
        emphasis:{{focus:'descendant',label:{{fontSize:13,fontWeight:'bold'}}}}}}]}});
treeChart.on('click',function(p){{
    var m=(p.data||{{}}).tooltip_data||{{}};
    if(m.cross_ref){{var q=new URLSearchParams(window.parent.location.search);q.set('chain',m.cross_ref);window.parent.location.search=q.toString();return;}}
    if(m.code){{openDetailPanel(m);return;}}
}});
if(crossGraph){{
    var crossChart=echarts.init(document.getElementById('cross-chart'));
    crossChart.setOption({{tooltip:{{trigger:'item',formatter:function(p){{return p.name;}}}},
        series:[{{type:'graph',layout:'force',force:{{repulsion:300,edgeLength:[120,260]}},roam:true,draggable:true,
            data:crossGraph.nodes,
            links:crossGraph.links.map(function(l){{return{{source:l.source,target:l.target,lineStyle:{{color:'#475569',curveness:0.2,width:1}}}};}}),
            categories:[{{name:'当前链'}},{{name:'共享环节'}},{{name:'关联链'}}],
            label:{{show:true,fontSize:10,color:'#cbd5e1'}},
            emphasis:{{focus:'adjacency',label:{{fontSize:13,fontWeight:'bold'}}}},
            scaleLimit:{{min:0.6,max:2.5}}}}]}});
    crossChart.on('click',function(p){{if(p.dataType==='node'){{var nid=p.data.id||p.name||'';if(nid.indexOf('seg_')===0)return;if(nid!=='{selected}'){{var q=new URLSearchParams(window.parent.location.search);q.set('chain',nid);window.parent.location.search=q.toString();}}}}}});
}}
var sparkChart=null;
function openDetailPanel(m){{
    var panel=document.getElementById('detail-panel'),overlay=document.getElementById('overlay');
    var content=document.getElementById('panel-content');
    var chgCls=m.change_pct>=0?'chg-up':'chg-down';
    var capStr=m.market_cap>10000?(m.market_cap/10000).toFixed(1)+'万亿':(m.market_cap>0?m.market_cap.toFixed(0)+'亿':'--');
    content.innerHTML='<h2>'+m.name+'</h2><div class="code">'+m.code+'</div>'
        +'<div class="price">'+(m.price?m.price.toFixed(2):'--')+' <span class="'+chgCls+'" style="font-size:16px">'+(m.change_pct>=0?'+':'')+m.change_pct.toFixed(2)+'%</span></div>'
        +'<div class="metric-row"><div class="metric"><div class="label">PE</div><div class="val">'+(m.pe>0?m.pe.toFixed(1):'--')+'</div></div>'
        +'<div class="metric"><div class="label">PB</div><div class="val">'+(m.pb>0?m.pb.toFixed(1):'--')+'</div></div>'
        +'<div class="metric"><div class="label">市值</div><div class="val">'+capStr+'</div></div></div>'
        +'<div style="margin-top:16px;font-size:14px;font-weight:600">FEV 评分</div>'
        +'<div class="fev-bar"><div class="fev-item"><span>F</span><div class="bar" style="background:#3b82f6;width:'+(m.f_score*10)+'%"></div><span style="font-size:11px">'+m.f_score+'/10</span></div>'
        +'<div class="fev-item"><span>E</span><div class="bar" style="background:#10b981;width:'+(m.e_score*10)+'%"></div><span style="font-size:11px">'+m.e_score+'/10</span></div>'
        +'<div class="fev-item"><span>V</span><div class="bar" style="background:#f59e0b;width:'+(m.v_score*10)+'%"></div><span style="font-size:11px">'+m.v_score+'/10</span></div>'
        +'<div class="fev-item"><span>FEV</span><div class="bar" style="background:#8b5cf6;width:'+(m.fev/30*100)+'%"></div><span style="font-size:11px;font-weight:700">'+m.fev+'/30</span></div></div>';
    panel.classList.add('open');overlay.classList.add('show');
    var hist=m.fev_history||[];
    if(hist.length>=2){{if(!sparkChart)sparkChart=echarts.init(document.getElementById('sparkline'));
        sparkChart.setOption({{grid:{{top:8,right:12,bottom:20,left:36}},
            xAxis:{{type:'category',data:hist.map(function(h){{return h.date.slice(5);}}),axisLine:{{lineStyle:{{color:'#475569'}}}},axisLabel:{{fontSize:9,color:'#94a3b8'}}}},
            yAxis:{{type:'value',min:0,max:30,splitLine:{{lineStyle:{{color:'#1e293b'}}}},axisLabel:{{fontSize:9,color:'#94a3b8'}}}},
            series:[{{type:'line',data:hist.map(function(h){{return h.fev;}}),smooth:true,symbol:'circle',symbolSize:6,
                lineStyle:{{color:'#8b5cf6',width:2}},itemStyle:{{color:'#a78bfa'}},
                areaStyle:{{color:new echarts.graphic.LinearGradient(0,0,0,1,[{{offset:0,color:'rgba(139,92,246,.3)'}},{{offset:1,color:'rgba(139,92,246,.02)'}}])}},
                markLine:{{silent:true,symbol:'none',data:[{{yAxis:15,lineStyle:{{color:'#f59e0b',type:'dashed',width:1}}}}],label:{{formatter:'15',fontSize:9,color:'#f59e0b'}}}}}}]}});
        document.getElementById('sparkline').style.display='block';}}
    else{{document.getElementById('sparkline').style.display='none';if(sparkChart){{sparkChart.dispose();sparkChart=null;}}}}
}}
function closePanel(){{document.getElementById('detail-panel').classList.remove('open');document.getElementById('overlay').classList.remove('show');}}
window.addEventListener('resize',function(){{treeChart.resize();if(crossGraph)echarts.getInstanceByDom(document.getElementById('cross-chart')).resize();}});
</script></body></html>"""

st.components.v1.html(html, height=960 if cross_graph else 720, scrolling=False)

# ============================================================
# Three view tabs (restored from initial P0)
# ============================================================
st.divider()
st.subheader(f"📐 {selected} · 分析视图")

tab1, tab2, tab3 = st.tabs(["🔥 卡脖子热点", "🏷 场景分类", "📈 标的 FEV"])

# --- Tab 1: 卡脖子热点 ---
with tab1:
    ranked = sorted(segments, key=lambda s: s.get("global_chokepoint_score", 0), reverse=True)
    if not ranked or all(s.get("global_chokepoint_score", 0) == 0 for s in ranked):
        st.info("暂无卡脖子评分数据")
    else:
        for seg in ranked:
            score = seg.get("global_chokepoint_score", 0)
            if score == 0:
                continue
            cols = st.columns([4, 1, 2, 2])
            with cols[0]:
                st.markdown(f"{_choke_emoji(score)} **{seg.get('segment', '?')}**")
                st.caption(f"{seg.get('tier', '')} · {(seg.get('supply_status', '') or '')[:60]}")
            with cols[1]:
                st.metric("卡脖子", f"{score}/10")
            with cols[2]:
                st.caption((seg.get("a_stock_mapping", "") or seg.get("scene", ""))[:50])
            with cols[3]:
                seg_stocks = stocks_by_seg.get(seg.get("segment", ""), [])
                st.caption("**FEV TOP**")
                for m in sorted(seg_stocks, key=lambda x: x.get("fev_total", 0), reverse=True)[:2]:
                    st.caption(f"`{m['code']}` {m['name'][:6]} {m['fev_total']}")
            st.divider()

# --- Tab 2: 场景分类 ---
with tab2:
    scene_map: dict[str, list] = {"A": [], "B": [], "C": [], "?": []}
    for seg in segments:
        s = seg.get("scene", "?") or "?"
        key = s[0] if s else "?"
        scene_map.setdefault(key, []).append(seg)

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

# --- Tab 3: 标的 FEV ---
with tab3:
    if stocks_list:
        df = pd.DataFrame(stocks_list)
        df = df.rename(columns={
            "code": "代码", "name": "名称", "segment": "环节",
            "f_score": "F", "e_score": "E", "v_score": "V", "fev_total": "FEV",
        })
        show = [c for c in ["代码", "名称", "环节", "F", "E", "V", "FEV"] if c in df.columns]
        df = df[show].sort_values("FEV", ascending=False)
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"FEV": st.column_config.ProgressColumn("FEV", max_value=30, format="%d/30")})
    else:
        st.info("暂无标的评分数据")
