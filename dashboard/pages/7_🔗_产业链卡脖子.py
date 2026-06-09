"""产业链卡脖子分析 — Serenity 框架 + FEV 评分"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "daily_review"))

import streamlit as st
import pandas as pd

from dashboard.utils.data_bridge import (
    get_serenity_chain_summary,
    get_serenity_stock_ranking,
    get_serenity_chain_detail,
    get_serenity_recent_logs,
    get_bom_chain_list,
    trigger_serenity_analysis,
    get_serenity_full_report,
)

st.set_page_config(page_title="产业链卡脖子", page_icon="🔗", layout="wide")

st.title("🔗 产业链卡脖子分析")
st.caption("Serenity 框架 × FEV 评分 · 全球供应链反推 → A股映射")

tab1, tab2, tab3, tab4 = st.tabs(["📊 总览", "🏆 标的排行", "🔍 产业链详情", "⚡ 手动分析"])

# ============================================================
# Tab 1: 总览
# ============================================================
with tab1:
    summary = get_serenity_chain_summary()
    logs = get_serenity_recent_logs()

    col1, col2 = st.columns(2)
    with col1:
        st.metric("已分析产业链", len(summary) if summary else 0)
    with col2:
        st.metric("近 7 天分析次数", len(logs) if logs else 0)

    if summary:
        st.subheader("产业链卡脖子排行")
        df = pd.DataFrame(summary)
        df = df.rename(columns={
            "chain_name": "产业链", "max_score": "卡脖子分",
            "segment_count": "环节数", "last_updated": "最近更新"
        })
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"卡脖子分": st.column_config.ProgressColumn(
                         "卡脖子分", max_value=10, format="%d/10")})
    else:
        st.info("暂无分析数据。去「⚡ 手动分析」跑第一条产业链。")

    if logs:
        st.subheader("最近分析记录")
        df_logs = pd.DataFrame(logs)
        st.dataframe(df_logs[["chain_name", "date", "trigger"]].rename(
            columns={"chain_name": "产业链", "date": "日期", "trigger": "触发"}),
            use_container_width=True, hide_index=True)


# ============================================================
# Tab 2: 标的排行
# ============================================================
with tab2:
    ranking = get_serenity_stock_ranking()
    if ranking:
        chains = sorted(set(r.get("chain_name", "") for r in ranking))
        filter_chain = st.selectbox("筛选产业链", ["全部"] + chains, key="stock_filter")
        filtered = [r for r in ranking if filter_chain == "全部" or r.get("chain_name") == filter_chain]

        st.subheader(f"标的 FEV + 卡脖子排行（{len(filtered)} 只）")
        if filtered:
            df = pd.DataFrame(filtered)
            df = df.rename(columns={
                "code": "代码", "name": "名称", "chain_name": "产业链",
                "chokepoint_score": "卡脖子", "fev_total": "FEV",
                "f_score": "F", "e_score": "E", "v_score": "V",
                "scene": "场景", "date": "日期"
            })
            show = [c for c in ["代码", "名称", "产业链", "卡脖子", "F", "E", "V", "FEV", "场景"] if c in df.columns]
            st.dataframe(df[show], use_container_width=True, hide_index=True,
                         column_config={
                             "卡脖子": st.column_config.ProgressColumn("卡脖子", max_value=10),
                             "FEV": st.column_config.ProgressColumn("FEV", max_value=15),
                         })
    else:
        st.info("暂无标的评分数据")


# ============================================================
# Tab 3: 产业链详情
# ============================================================
with tab3:
    chains = get_bom_chain_list()
    if chains:
        selected = st.selectbox("选择产业链", chains, key="detail_chain")
        if selected:
            detail = get_serenity_chain_detail(selected)
            if detail:
                st.subheader(f"{selected} · 供应链结构")
                segments = detail.get("segments", [])
                if segments:
                    df_seg = pd.DataFrame(segments)
                    df_seg = df_seg.rename(columns={
                        "tier": "层级", "segment": "环节",
                        "global_chokepoint_score": "卡脖子分",
                        "supply_status": "供给状态",
                        "a_stock_mapping": "A股映射", "scene": "场景"
                    })
                    show_s = [c for c in ["层级", "环节", "卡脖子分", "供给状态", "A股映射", "场景"] if c in df_seg.columns]
                    st.dataframe(df_seg[show_s], use_container_width=True, hide_index=True)

                st.subheader("A 股标的 FEV 评分")
                stocks = detail.get("stocks", [])
                if stocks:
                    df_st = pd.DataFrame(stocks)
                    df_st = df_st.rename(columns={
                        "code": "代码", "name": "名称", "fev_total": "FEV",
                        "f_score": "F", "e_score": "E", "v_score": "V", "scene": "场景"
                    })
                    show_st = [c for c in ["代码", "名称", "F", "E", "V", "FEV", "场景"] if c in df_st.columns]
                    st.dataframe(df_st[show_st], use_container_width=True, hide_index=True)

                analysis = detail.get("analysis", {})
                if analysis:
                    with st.expander("📄 查看完整分析报告", expanded=True):
                        report_content = get_serenity_full_report(selected)
                        if report_content:
                            st.markdown(report_content)
                        else:
                            st.caption(f"日期: {analysis.get('date', '')}  触发: {analysis.get('trigger', '')}")
                            st.text(analysis.get("layer1_summary", "")[:500] or "暂无摘要")
            else:
                st.info(f"「{selected}」暂无分析数据")
    else:
        st.info("BOM 知识库暂无数据")


# ============================================================
# Tab 4: 手动分析
# ============================================================
with tab4:
    st.subheader("触发产业链分析")
    chains = get_bom_chain_list()
    if chains:
        col1, col2 = st.columns([3, 1])
        with col1:
            target = st.selectbox("目标产业链", chains, key="trigger_chain")
        with col2:
            force = st.checkbox("强制全量", value=False, help="忽略 7 天缓存，重新跑完整三层")

        if st.button("🚀 开始分析", type="primary", use_container_width=True):
            with st.spinner(f"正在分析「{target}」... 约需 30-60 秒"):
                result = trigger_serenity_analysis(target, force_full=force)
            if result.get("ok"):
                st.success(result.get("msg", "分析完成"))
                if result.get("report"):
                    st.caption(f"📄 {result['report']}")
            else:
                st.error(result.get("msg", "分析失败"))
    else:
        st.warning("BOM 知识库暂无数据。请先运行 BOM 日更流水线。")
