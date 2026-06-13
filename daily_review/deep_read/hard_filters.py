"""Stage 1：四层 Python 硬筛选 — 零 LLM 成本。

筛选顺序：类型 → 领域 → 硬门槛 → 动机标注
原则：不确定时放行（conservative），把最终判断留给 LLM。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import store
from .knowledge_base import (
    load_hunting_ground,
    is_chokepoint_announcement,
    get_chokepoint_context,
)

# ============================================================
# 第一层：类型过滤
# ============================================================

SKIP_TYPES = {
    "董事会决议", "监事会决议", "董事会会议决议",
    "股东大会决议", "股东大会通知", "股东大会延期",
    "股东大会会议通知", "股东大会提示性公告",
    "公司章程", "公司章程修订", "制度规则", "管理办法",
    "独立董事", "独立董事述职", "独立董事提名人",
    "独立董事候选人", "独立董事声明",
    "会计师事务所", "审计委员会", "审计委员会履职",
    "内部控制评价", "内部控制审计",
    "募集资金存放", "募集资金使用", "募集资金置换",
    "担保公告", "担保额度", "对外担保",
    "委托理财", "理财公告",
    "为子公司提供担保", "提供担保的公告",
    "关于选举", "关于聘任", "关于更换",
    "投资者关系活动记录表", "投资者关系",
    "异常波动", "股票交易异常波动",
    "现金管理", "结构性存款",
    # 全市场过滤：M&A 配套程序性文件（主公告已覆盖，这些是噪音）
    "审计报告及财务报表", "审计报告", "资产评估报告", "评估报告",
    "法律意见书", "核查意见", "核查报告", "保荐机构",
    "权益分派实施", "权益分派实施公告",
    "证券变动月报表", "證券變動月報表",
    "公司章程修订", "公司章程修正",
    # 债券/融资配套文件
    "信用评级报告", "跟踪评级报告",
}

# 正则跳过模式：标题匹配任一模式则跳过
# 注意：PRIORITY_TYPES 先匹配，重要公告不会被这里的规则拦截
SKIP_PATTERNS = [
    # 会议决议/选举/规则
    r"(?:董事|监事|股东)会.{0,5}(?:决议|决议公告|会议决议|会议通知|补充通知)",
    r"(?:董事|监事)会.{0,10}(?:议事规则|工作细则|议事规则|议事规则)",
    r"(?:董事|监事|独立董事).{0,5}(?:换届|选举|候选人|简历)",
    # 可转债（除首次预案外都是噪音）
    r"可转债.*(?:赎回|停止转股|停止交易|转股价格调整|调整转股|开始转股|实施|摘牌)",
    r"(?:调整|修正).*可转债.*转股价格",
    r"可转债.*(?:保荐书|上市保荐书|发行保荐书|法律意见|核查意见)",
    r"(?:保荐书|发行保荐书|上市保荐书)",  # 保荐文件
    # 融资/信用/套保
    r"授信额度|综合授信|申请授信",
    r"融资租赁",
    r"套期保值|期货套保|远期.*(?:结汇|售汇)",
    r"闲置募集资金.*(?:现金管理|理财|存款|补充流动)",
    # 工商/地址变更
    r"变更.*(?:注册地址|经营范围|住所).*工商变更|工商变更登记",
    # 高管/薪酬
    r"高管.*(?:辞职|离职|调整|聘任).{0,10}公告",
    r"(?:董事|监事|财务负责人).{0,5}(?:辞职|离职|辞任)",
    r"薪酬.*(?:方案|管理|考核|制度)",
    # 募集资金
    r"募集资金.*(?:存放|使用|置换|管理).*(?:报告|公告|核查|鉴证)",
    r"变更.*募集资金.*(?:用途|项目)",
    # 独立董事/内控
    r"独立董事.*(?:述职|声明|提名人|候选人)",
    r"内部控制.*(?:评价|审计|鉴证)",
    # 担保
    r"(?:为|向).*(?:子公司|全资子公司|控股子公司|参股).*(?:提供担保|担保)",
    r"子公司.*担保",
    # 理财/投资
    r"委托理财|结构性存款",
    # 转让/过户/解散
    r"非交易过户|内部转让",
    r"解散.*(?:进展|清算|注销)",
    # H股/月报表
    r"H股.*(?:月报表|证券变动|公告)",
    r"證券變動月報表",
    # 进度/说明
    r"摊薄.*回报.*填补措施",
    r"关于.*(?:调整|修订).*(?:说明|公告)",
    # 注册/备案
    r"变更.*注册资本.*工商登记",
    r"公司章程.*备案",
]

PRIORITY_TYPES = {
    "收购", "资产重组", "重大资产重组", "要约收购",
    "吸收合并", "对外投资",
    "回购", "回购股份", "回购报告书", "回购实施",
    "股权激励", "员工持股计划",
    "定向增发", "非公开发行", "配股",
    "业绩预告", "业绩快报", "业绩修正",
    "立案调查", "立案告知", "行政处罚", "行政监管",
    "监管函", "问询函", "关注函",
    "诉讼", "仲裁", "财产保全",
    "实控人变更", "控股股东变更", "权益变动",
    "控制权变更", "一致行动人",
    "减持计划", "减持结果", "减持股份",
    "增持计划", "增持结果", "增持股份",
    "重大合同", "战略合作", "战略合作协议", "框架协议",
    "停牌", "复牌", "退市风险", "风险警示",
}


def _pass_type_filter(ann: dict) -> bool:
    """类型过滤：跳过常规公告，保留关键类别。"""
    ann_type = ann.get("ann_type", ann.get("type", ""))
    title = ann.get("ann_title", ann.get("title", ""))

    combined = f"{ann_type} {title}"

    # 优先类型：直接放行
    for pt in PRIORITY_TYPES:
        if pt in combined:
            return True

    # 跳过类型（精确匹配）
    for st in SKIP_TYPES:
        if st in combined:
            return False

    # 跳过类型（正则匹配）
    for pat in SKIP_PATTERNS:
        if re.search(pat, combined):
            return False

    # 类型不明确的，放行
    return True


# ============================================================
# 第二层：领域过滤
# ============================================================

def _pass_domain_filter(ann: dict, hunting_codes: set[str]) -> bool:
    """领域过滤：股票在猎场内，或公告涉及卡脖子环节。"""
    code = str(ann.get("code", "")).zfill(6)
    name = ann.get("name", "")
    title = ann.get("ann_title", ann.get("title", ""))
    full_text = ann.get("ann_full_text", "")

    if code in hunting_codes:
        return True

    # 跨界收购场景：非猎场股票，但公告涉及卡脖子环节
    # 检查范围：公告标题 + 正文（前2000字）
    search_text = f"{title} {full_text[:2000]}" if full_text else title
    cp_matches = is_chokepoint_announcement(search_text, code, name)
    if cp_matches:
        ann["_chokepoint_matches"] = cp_matches
        return True

    return False


# ============================================================
# 第三层：硬门槛一刀切
# ============================================================

def _extract_amount(text: str) -> Optional[float]:
    """从公告文本中提取交易金额（亿元或万元）。"""
    if not text:
        return None
    # 优先匹配含「交易金额」「作价」「对价」的上下文
    patterns = [
        r"(?:交易金额|交易作价|作价|对价|转让价款|收购价款)[^\d]{0,10}?([\d,]+\.?\d*)\s*(?:亿|万)?元?",
        r"(?:标的.*?(?:作价|估值|交易金额))[^\d]{0,20}?([\d,]+\.?\d*)\s*(?:亿|万)?元?",
        r"([\d,]+\.?\d*)\s*(?:亿|万)\s*(?:元|人民币).{0,10}(?:收购|购买|受让)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                # 判断单位
                context = text[max(0, m.start() - 5):m.end() + 10]
                if "万" in context and "亿" not in context[:m.start() - max(0, m.start() - 5)]:
                    val = val / 10000  # 万元 → 亿元
                return val
            except ValueError:
                continue
    return None


def _extract_committed_profit(text: str) -> Optional[float]:
    """提取标的公司承诺的首年净利润（亿元或万元）。"""
    if not text:
        return None
    patterns = [
        r"(?:承诺|预计|保证).{0,10}(?:净利润|扣非.{0,5}净利润)[^\d]{0,5}?([\d,]+\.?\d*)\s*(?:亿|万)?元?",
        r"(?:业绩承诺|利润承诺|业绩对赌).{0,20}(?:首年|第一年|202[56]年)[^\d]{0,10}?([\d,]+\.?\d*)\s*(?:亿|万)?元?",
        r"([\d,]+\.?\d*)\s*(?:亿|万)\s*(?:元|人民币).{0,15}(?:净利润|扣非净利润)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if "万" in text[max(0, m.start() - 5):m.end() + 10]:
                    val = val / 10000
                return val
            except ValueError:
                continue
    return None


def _get_net_assets(code: str) -> Optional[float]:
    """从 financial_indicators 表获取最新一期净资产（亿元）。"""
    try:
        rows = store.query_valuation_cache(code, "financial_indicators")
        if rows and isinstance(rows, dict):
            bv = rows.get("bv_per_share")  # 每股净资产
            total = rows.get("total_asset_growth")  # 这不是总资产..
            # 实际上需要总资产和负债来计算净资产，这里先用简化方式
            pass
    except Exception:
        pass
    # financial_indicators 表没有直接的总资产/净资产字段
    # 使用 store 查询代替
    return None


def _get_acquirer_profit(code: str) -> Optional[float]:
    """从 financial_indicators 表获取最新一期净利润（亿元）。"""
    try:
        with store._conn() as conn:
            row = conn.execute(
                "SELECT profit_yoy, report_date FROM financial_indicators "
                "WHERE code = ? ORDER BY report_date DESC LIMIT 1",
                (str(code).zfill(6),),
            ).fetchone()
        # profit_yoy 是同比增长率，不是净利润绝对值
        # 需要其他数据源获取绝对值
    except Exception:
        pass
    return None


def _has_recent_goodwill_impairment(code: str, lookback_years: int = 3) -> bool:
    """检查过去 N 年是否有商誉暴雷记录。"""
    # 从公告历史中搜索商誉减值相关公告
    try:
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT title FROM announcements WHERE code = ? "
                "AND (title LIKE '%商誉%' OR title LIKE '%减值%') "
                "AND date >= date('now', ? || ' years')",
                (str(code).zfill(6), f"-{lookback_years}"),
            ).fetchall()
        return len(rows) > 0
    except Exception:
        return False


def _pass_hard_gates(ann: dict) -> tuple[bool, dict]:
    """硬门槛一刀切。返回 (通过?, 提取的数据)。

    原则：数据提取失败时放行（不确定时不拦截）。
    """
    text = ann.get("ann_full_text", "")
    details = {}

    if len(text) < 100:
        return True, {"pass": True, "reason": "text_too_short_to_judge"}

    # 金额关
    amount = _extract_amount(text)
    if amount is not None:
        details["extracted_amount_yi"] = round(amount, 2)
        net_assets = _get_net_assets(ann["code"])
        if net_assets and net_assets > 0 and amount / net_assets < 0.05:
            details["pass"] = False
            details["reason"] = "amount_too_small"
            return False, details

    # 盈利关
    committed = _extract_committed_profit(text)
    if committed is not None:
        details["extracted_committed_profit_yi"] = round(committed, 2)

    # 治理关：商誉暴雷
    if _has_recent_goodwill_impairment(ann["code"]):
        details["pass"] = False
        details["reason"] = "goodwill_blacklist"
        return False, details

    details["pass"] = True
    return True, details


# ============================================================
# 第四层：动机标注（不拦截）
# ============================================================

MOTIVE_RED_FLAGS = [
    (r"(?:此前|此前曾|此前公告|原计划).{0,40}(?:终止|取消|撤回|暂停)", "attitude_reversal"),
    (r"(?:收购|投资|参股|设立).{0,20}(?:半导体|芯片|AI|人工智能|机器人|新能源)", "cross_industry"),
    (r"(?:保留意见|无法表示意见|否定意见|强调事项段|持续经营.{0,5}重大不确定性)", "non_standard_audit"),
    (r"(?:董事|监事|高管|财务负责人|总经理|副总).{0,5}(?:辞职|离职|辞任)", "executive_departure"),
    (r"关联交易.{0,40}(?:定价|公允|评估|差异)", "related_party_pricing"),
    (r"(?:减持|增持).{0,10}(?:计划|进展|结果|完成)", "insider_trading"),
    (r"(?:收到|涉及).{0,10}(?:立案|处罚|行政监管|监管函|问询函|关注函)", "regulatory_action"),
    (r"延期披露|推迟.{0,5}披露|无法在规定时间内", "delayed_disclosure"),
]


def _annotate_motive_flags(ann: dict) -> list[str]:
    """标注异常动机信号，不拦截，仅标记传给 LLM。"""
    text = ann.get("ann_full_text", "")
    title = ann.get("ann_title", ann.get("title", ""))
    combined = f"{title} {text[:2000]}"
    flags = []
    for pat, flag_name in MOTIVE_RED_FLAGS:
        if re.search(pat, combined):
            flags.append(flag_name)
    return flags


# ============================================================
# 主入口
# ============================================================

def stage1_filter(announcements: list[dict]) -> list[dict]:
    """四层筛选主函数。

    输入: 公告列表（每条含 code, name, ann_title, ann_type, ann_url, ann_full_text）
    输出: 通过筛选的公告列表，附带了 stage1_details、hunting_domain、chokepoint_key、motive_flags
    """
    hunting_codes = load_hunting_ground()

    qualified = []
    for ann in announcements:
        ann.setdefault("code", ann.get("code", ""))
        ann.setdefault("name", ann.get("name", ""))

        # Layer 1
        if not _pass_type_filter(ann):
            continue

        # Layer 2
        if not _pass_domain_filter(ann, hunting_codes):
            continue

        # Layer 3
        passed, gate_details = _pass_hard_gates(ann)
        if not passed:
            continue

        # Layer 4
        motive_flags = _annotate_motive_flags(ann)

        # 附加上下文
        ctx = get_chokepoint_context(
            ann["code"],
            ann.get("ann_title", ann.get("title", "")),
            ann.get("ann_full_text", ""),
        )

        qualified.append({
            **ann,
            "stage1_details": json.dumps({**gate_details, "motive_flags": motive_flags}),
            "hunting_domain": ctx["domains"][0] if ctx["domains"] else "",
            "chokepoint_key": ctx["chokepoints"][0]["key"] if ctx["chokepoints"] else "",
            "motive_flags": motive_flags,
            "chokepoint_context": ctx,
        })

    return qualified
