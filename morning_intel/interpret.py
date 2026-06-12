"""盘前解读引擎 — 多源语料 → Claude 推理 → 催化事件+供应链映射+标的假设"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from llm import call as _llm_call

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
REVIEW_BASE = BASE.parent / "daily_review"
FEEDS_DIR = REVIEW_BASE / "reports" / "feeds"
PROMPT_DIR = BASE / "prompts"
REPORT_DIR = BASE / "reports"
DROPS_DIR = BASE / "drops"

from settings import MODEL_INTERPRET, LLM_TIMEOUT, LLM_MAX_TOKENS, FEEDS_LOOKBACK_DAYS
from supply_chain import to_context, init_db
from notify import morning_brief as push_morning


def _read_file_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def _read_feed_smart(prefix: str, today: str, lookback_days: int) -> str:
    """读取 feed — 优先 SQLite 缓存，回退文件。"""
    try:
        sys.path.insert(0, str(REVIEW_BASE))
        import store
        store.init_feed_cache_table()
    except Exception:
        store = None

    for offset in range(lookback_days + 1):
        d = (date.fromisoformat(today) - timedelta(days=offset)).isoformat()
        if store:
            cached = store.get_feed_cache(prefix, d)
            if cached:
                return cached
        content = _read_file_safe(FEEDS_DIR / f"{prefix}_{d}.md")
        if content:
            return content
    return ""


def _read_feeds(today: str) -> dict[str, str]:
    """返回 {key: content} 映射到 prompt 变量名。优先 SQLite 缓存。"""
    feed_map = {
        "ZSXQ_CONTENT": "zsxq",
        "ANNOUNCEMENTS_CONTENT": "announcements",
        "NEWS_CONTENT": "news",
        "WECHAT_CONTENT": "wechat",
        "WEIBO_CONTENT": "weibo",
        "JIUYANG_CONTENT": "jiuyang",
    }
    result: dict[str, str] = {}
    for varname, prefix in feed_map.items():
        result[varname] = _read_feed_smart(prefix, today, FEEDS_LOOKBACK_DAYS)

    industry_parts = []
    for prefix in ("industry", "research"):
        c = _read_feed_smart(prefix, today, FEEDS_LOOKBACK_DAYS)
        if c:
            industry_parts.append(c)
    result["INDUSTRY_CONTENT"] = "\n\n".join(industry_parts)

    # 优先用星球两阶段分析替代 raw feed
    zsxq_analysis = _read_feed_smart("zsxq_analysis", today, FEEDS_LOOKBACK_DAYS)
    if zsxq_analysis:
        result["ZSXQ_CONTENT"] = zsxq_analysis

    _truncate_feeds(result)
    return result


_FEED_MAX_CHARS = {
    "ZSXQ_CONTENT": 10000,
    "JIUYANG_CONTENT": 5000,
    "WECHAT_CONTENT": 3000,
    "ANNOUNCEMENTS_CONTENT": 3000,
    "NEWS_CONTENT": 5000,
    "INDUSTRY_CONTENT": 6000,
}


def _truncate_feeds(feeds: dict[str, str]):
    for key, max_chars in _FEED_MAX_CHARS.items():
        content = feeds.get(key, "")
        if len(content) > max_chars:
            feeds[key] = content[:max_chars] + f"\n\n...（已截断，全文 {len(content)} 字符）"


def _read_drops() -> str:
    """读取 drops/ 目录下手动投放的 txt/md 文件，合并为一个文本块。"""
    if not DROPS_DIR.exists():
        return ""
    parts = []
    for f in sorted(DROPS_DIR.iterdir()):
        if f.suffix in (".txt", ".md"):
            content = _read_file_safe(f)
            if content:
                parts.append(f"### {f.stem}\n\n{content}")
    return "\n\n".join(parts)


def _read_cross_validation(today: str) -> str:
    """读取四源交叉验证结果（Haiku 已跑完的共识/分歧/多源标的）。"""
    path = FEEDS_DIR / f"primary_synthesis_{today}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    prev = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    path = FEEDS_DIR / f"primary_synthesis_{prev}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _render_prompt(template: str, today: str, feed_contents: dict[str, str],
                   drops_text: str, supply_chain_text: str) -> str:
    now = datetime.now()
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    minutes_left = max(0, int((market_open - now).total_seconds() // 60))

    prompt = template.replace("%%TIME%%", now.strftime("%Y-%m-%d %H:%M"))
    prompt = prompt.replace("%%MINUTES%%", str(minutes_left))

    for varname, content in feed_contents.items():
        placeholder = f"%%{varname}%%"
        content = content or "(暂无当日语料)"
        prompt = prompt.replace(placeholder, content)

    prompt = prompt.replace("%%DROPS_CONTENT%%", drops_text or "(暂无手动投放)")
    prompt = prompt.replace("%%SUPPLY_CHAIN_CONTEXT%%", supply_chain_text or "(暂无供应链映射)")

    return prompt


def _call_claude(prompt: str) -> str:
    return _llm_call("deep", prompt, max_tokens=LLM_MAX_TOKENS, timeout=LLM_TIMEOUT)


def _extract_json(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _clean_code(code: str) -> str:
    """确保代码是 6 位数字字符串。"""
    code = re.sub(r"[^0-9]", "", str(code))
    return code.zfill(6)


def _validate_output(data: dict) -> list[str]:
    """校验 JSON 输出，返回警告列表。"""
    warnings = []
    events = data.get("events", [])
    if not events:
        warnings.append("未包含任何事件")
    for i, ev in enumerate(events):
        name = ev.get("name", f"事件{i+1}")
        stocks = ev.get("target_stocks", [])
        for j, s in enumerate(stocks):
            code = s.get("code", "")
            if len(_clean_code(code)) != 6:
                warnings.append(f"{name} 标的[{j}] 代码无效: {code}")
    return warnings


def _load_name_map() -> dict[str, str]:
    """加载全A代码→名称映射"""
    import json
    cache = Path(__file__).parent.parent / "daily_review" / "data" / "stock_codes.json"
    try:
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            codes = data.get("codes", [])
            if codes:
                return {str(c["code"]).zfill(6): str(c["name"]) for c in codes}
    except Exception:
        pass
    try:
        from daily_review.live_scanner import _load_code_list
        df = _load_code_list()
        if not df.empty and "code" in df.columns and "name" in df.columns:
            return dict(zip(df["code"].astype(str).str.zfill(6), df["name"].astype(str)))
    except Exception:
        pass
    return {}


def _verify_and_fix(data: dict) -> tuple[dict, list[str]]:
    """双向交叉验证 LLM 输出的代码-名称对。

    策略: 代码和名称哪个对用哪个。都找不到真实匹配则移除。
    """
    name_map = _load_name_map()
    name_to_code = {v: k for k, v in name_map.items()}
    warnings = []
    if not name_map:
        warnings.append("无法加载股票代码表，跳过验证")
        return data, warnings

    events = data.get("events", [])
    fixed = 0
    removed = 0

    def _fix_one(item: dict, context: str):
        nonlocal fixed, removed
        code = _clean_code(item.get("code", ""))
        name = str(item.get("name", "")).strip()
        if not code or len(code) != 6 or not name:
            return

        real_name = name_map.get(code, "")
        real_code = name_to_code.get(name, "")

        if name == real_name:
            return  # 完全匹配，无需修正

        if real_code and real_code != code:
            # 名称匹配到了真实代码 → LLM编造了代码，用真实代码
            item["_original_code"] = code
            item["code"] = real_code
            warnings.append(f"{context} 代码修正: {code} {name} → {real_code} {name}")
            fixed += 1
        elif real_name:
            # 代码匹配到了真实名称 → LLM编造了名称，用真实名称
            item["_original_name"] = name
            item["name"] = real_name
            warnings.append(f"{context} 名称修正: {code} {name} → {code} {real_name}")
            fixed += 1
        else:
            # 代码和名称都找不到真实匹配 → 移除
            item["_invalid"] = True
            warnings.append(f"{context} 代码-名称均无效，已标记: {code} {name}")
            removed += 1

    for ev in events:
        for s in ev.get("target_stocks", []):
            _fix_one(s, f"[{ev.get('name','?')}]")

        for node in ev.get("supply_chain", []):
            if not isinstance(node, dict):
                continue
            for item in node.get("stocks", []):
                _fix_one(item, f"[供应链 {ev.get('name','?')}]")

    # 清理无效标的
    for ev in events:
        ev["target_stocks"] = [s for s in ev.get("target_stocks", [])
                                if not s.get("_invalid")]
        for node in ev.get("supply_chain", []):
            if not isinstance(node, dict):
                continue
            node["stocks"] = [s for s in node.get("stocks", [])
                             if not s.get("_invalid")]

    if fixed or removed:
        print(f"  [FIX] 修正 {fixed} 处, 移除 {removed} 处无效标的")
    return data, warnings


def _render_markdown(data: dict, today: str) -> str:
    """将 JSON 数据渲染为人类可读的 Markdown 报告。"""
    lines = []
    summary = data.get("summary", "")
    lines.append(f"# 晨间情报 {today}")
    lines.append("")
    if summary:
        lines.append(f"> {summary}")
        lines.append("")

    events = data.get("events", [])
    if events:
        lines.append("## 催化事件")
        lines.append("")
        for i, ev in enumerate(events):
            name = ev.get("name", f"事件 {i+1}")
            conf = ev.get("confidence", "")
            narrative = ev.get("narrative", "")

            lines.append(f"### {i+1}. {name}")
            if conf:
                lines.append(f"置信度: `{conf}`")
                lines.append("")
            if narrative:
                lines.append(narrative)
                lines.append("")

            subs = ev.get("sub_segments", [])
            if subs:
                lines.append("**细分环节**")
                lines.append("")
                lines.append("| 环节 | 方向 | 依据 |")
                lines.append("|------|------|------|")
                for s in subs:
                    lines.append(f"| {s.get('name', '')} | {s.get('direction', '')} | {s.get('rationale', '')} |")
                lines.append("")

            sc = ev.get("supply_chain", {})
            if sc:
                has_sc = any(v for v in sc.values())
                if has_sc:
                    lines.append("**供应链映射**")
                    lines.append("")
                    lines.append("| 环节 | 代码 | 名称 | 角色 |")
                    lines.append("|------|------|------|------|")
                    for tier, items in sc.items():
                        for item in items:
                            lines.append(f"| {tier} | {item.get('code', '')} | {item.get('name', '')} | {item.get('role', '')} |")
                    lines.append("")

            stocks = ev.get("target_stocks", [])
            if stocks:
                lines.append("**核心标的**")
                lines.append("")
                lines.append("| 代码 | 名称 | 方向 | 依据 |")
                lines.append("|------|------|------|------|")
                for s in stocks:
                    lines.append(
                        f"| {s.get('code', '')} | {s.get('name', '')} | "
                        f"{s.get('expected_direction', '')} | {s.get('rationale', '')} |"
                    )
                lines.append("")

            gap = ev.get("expectation_gap", "")
            if gap:
                lines.append(f"> **预期差**：{gap}")
                lines.append("")

    watch = data.get("watch_notes", [])
    if watch:
        lines.append("## 观察清单")
        lines.append("")
        for w in watch:
            lines.append(f"- {w}")
        lines.append("")

    risks = data.get("risk_flags", [])
    if risks:
        lines.append("## 风险提示")
        lines.append("")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*自动生成，仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def run(today: str = None, dry_run: bool = False) -> Path | None:
    """
    主入口：读取语料 → 渲染 prompt → 调用 Claude → 解析 JSON → 写报告。
    返回报告路径，失败返回 None。
    """
    if today is None:
        today = date.today().isoformat()

    init_db()

    # 1. 读取语料
    print(f"[interpret] 读取 {today} 语料...")
    feed_contents = _read_feeds(today)
    feed_contents["CROSS_VALIDATION_CONTENT"] = _read_cross_validation(today)
    drops_text = _read_drops()
    supply_text = to_context()

    # 2. 渲染 prompt
    tpl_path = PROMPT_DIR / "interpret_v2.txt"
    if not tpl_path.exists():
        print(f"[ERROR] prompt 模板不存在: {tpl_path}")
        return None
    template = tpl_path.read_text(encoding="utf-8")
    prompt = _render_prompt(template, today, feed_contents, drops_text, supply_text)

    if dry_run:
        out = REPORT_DIR / f"prompt_debug_{today}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt, encoding="utf-8")
        print(f"[dry-run] prompt 已写入 {out}")
        return None

    # 3. 调用 Claude
    print(f"[interpret] 调用 {MODEL_INTERPRET} (timeout={LLM_TIMEOUT}s)...")
    raw = _call_claude(prompt)
    print(f"[interpret] 原始输出 {len(raw)} 字符")

    # 4. 解析 JSON
    data = _extract_json(raw)
    if data is None:
        err_path = REPORT_DIR / f"morning_raw_{today}.txt"
        err_path.parent.mkdir(parents=True, exist_ok=True)
        err_path.write_text(raw, encoding="utf-8")
        print(f"[ERROR] 无法解析 JSON 输出，原始内容已存至 {err_path}")
        return None

    # 5. 校验
    warnings = _validate_output(data)
    for w in warnings:
        print(f"[WARN] {w}")

    # 5.5 代码-名称交叉验证
    data, fix_warnings = _verify_and_fix(data)
    for w in fix_warnings:
        print(f"[FIX] {w}")

    # 6. 渲染 Markdown 报告（YAML frontmatter 供 Obsidian Dataview 索引）
    events_count = len(data.get("events", []))
    stocks_count = sum(len(ev.get("target_stocks", [])) for ev in data.get("events", []))
    fm = (
        "---\n"
        f"date: {today}\n"
        'type: "晨间情报"\n'
        f"events_count: {events_count}\n"
        f"stocks_count: {stocks_count}\n"
        f"summary: \"{data.get('summary', '')[:200]}\"\n"
        "---\n\n"
    )
    md_body = _render_markdown(data, today)
    report_path = REPORT_DIR / f"morning_{today}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(fm + md_body, encoding="utf-8")
    print(f"[interpret] 报告已生成: {report_path} ({len(md_body)} 字符)")

    # 7. 额外保存纯 JSON 供 validate.py 解析
    json_path = REPORT_DIR / f"morning_{today}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[interpret] JSON 已保存: {json_path}")

    # 8. 微信推送
    ok = push_morning(data.get("summary", ""), events_count, stocks_count, data.get("events", []))
    print(f"[interpret] 微信推送: {'OK' if ok else 'FAIL'}")

    return report_path


if __name__ == "__main__":
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    dry = "--dry-run" in sys.argv
    result = run(today=today, dry_run=dry)
    if result:
        print(f"OK: {result}")
    elif not dry:
        print("FAIL: 未生成报告")
