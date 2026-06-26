"""微信公众号文章深度 AI 分析（两阶段）。

阶段一 Haiku: 逐篇拆解（核心论点+关键数据+A股标的+自选关联）
阶段二 Sonnet: 综合研判（交叉印证+确信度+行动建议）
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))
import store
from config import REPORT_DIR, WATCHLIST

MODEL_SONNET = os.getenv("DR_LLM_MODEL", "claude-sonnet-4-6-20250514")
MODEL_HAIKU = "claude-haiku-4-5-20251001"
TIMEOUT = 90
MAX_BODY_CHARS = 1200
FETCH_DELAY_MIN = 3.0
FETCH_DELAY_MAX = 6.0

UA_POOL = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.43",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 MicroMessenger/8.0.46",
    "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 Chrome/118.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.42",
]


def _alert(msg: str):
    try:
        from morning_intel.notify import push
        push("公众号分析告警", msg)
    except Exception:
        pass


from daily_review.llm import _load_api_key


def _get_client():
    from anthropic import Anthropic
    return Anthropic(api_key=_load_api_key(), base_url="https://api.deepseek.com/anthropic", timeout=TIMEOUT)


def _scrape_article(url: str) -> str:
    headers = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://mp.weixin.qq.com/",
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=60) as resp:
            # 逐块读取，够 500KB 就停，避免 IncompleteRead 大文件
            chunks = []
            total = 0
            max_read = 500 * 1024
            while total < max_read:
                try:
                    chunk = resp.read(min(8192, max_read - total))
                except Exception:
                    break  # 连接断开，用已读内容
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            html = b"".join(chunks).decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    [skip] {e}")
        return ""
    m = re.search(r'id="js_content"[^>]*>(.*?)(</div>|$)', html, re.DOTALL)
    if not m:
        m = re.search(r'class="rich_media_content[^"]*"[^>]*>(.*?)(</div>|$)',
                      html, re.DOTALL)
    if not m:
        return ""
    text = m.group(1)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"微信扫一扫.*$", "", text)
    text = re.sub(r"关注该公众号.*$", "", text)
    return text[:MAX_BODY_CHARS]


# ============================================================
# 阶段一：逐篇深度拆解（Haiku）
# ============================================================

_S1 = """你是 A 股基本面分析师。分析以下微信公众号文章。

来源: {feed}  日期: {date}
标题: {title}
正文: {body}

输出 JSON:
{{
  "title": "原标题",
  "feed": "来源",
  "thesis": "核心论点（1-2 句，引用原文关键数据和逻辑）",
  "key_facts": ["关键事实1", "关键事实2"],
  "tickers": [{{"code": "股票代码或名称", "name": "简称", "relevance": "关联逻辑"}}],
  "category": "AI算力/半导体/AIPC/新能源/消费/宏观/地产/电力/其他",
  "relevance_score": 1-5,
  "one_liner": "一句话投资摘要"
}}
只输出 JSON。"""


def _analyze_single(client, feed: str, pub_date: str, title: str,
                    body: str) -> dict:
    prompt = _S1.format(feed=feed, date=pub_date[:10], title=title,
                        body=body or "（无正文）")
    try:
        resp = client.messages.create(
            model=MODEL_HAIKU, max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text")
        data = _extract_json(text) or {}
        # L2: 校验 tickers 代码
        from llm_validator import validate_codes as _vc
        for t in data.get("tickers", []):
            code = t.get("code", "")
            if code and not _vc([code]).get(code, {}).get("valid"):
                t["code"] = ""
                t["_invalid"] = True
        return data
    except Exception as e:
        print(f"      [Haiku err] {e}")
        return {}


# ============================================================
# 阶段二：综合研判（Sonnet）
# ============================================================

_S2 = """你是 A 股基本面投资分析师。今天是 {today}。

以下是近 3 天微信公众号文章的逐篇深度拆解：

{articles_json}

我的自选股池（仅供参考我的关注方向）：{watchlist}

请综合研判，输出 JSON:
{{
  "market_narrative": "当前市场核心叙事（3-4 句，共识+分歧+情绪）",
  "themes": [
    {{
      "name": "主题",
      "conviction": "高/中/低",
      "article_indices": [1,2,5],
      "feeds": ["号1","号2"],
      "thesis": "核心逻辑（引用原文数据和事件）",
      "catalyst": "近期催化及时间节点",
      "horizon": "短期/中期/长期",
      "related_stocks": ["关联的A股代码"],
      "risk": "主要风险"
    }}
  ],
  "cross_validation": [
    {{
      "theme": "主题",
      "consensus_view": "一致看法",
      "divergent_view": "分歧或反对意见",
      "our_take": "基于自选股持仓的判断"
    }}
  ],
  "watchlist_alerts": [
    {{
      "code": "股票代码",
      "signal": "正面/负面/关注",
      "reason": "具体逻辑（引用原文数据）",
      "urgency": "高/中/低"
    }}
  ],
  "action_items": [
    {{
      "action": "建议操作",
      "target": "标的代码",
      "rationale": "理由",
      "priority": 1-5
    }}
  ],
  "key_question": "当前最需要回答的关键问题",
  "summary": "200 字整体摘要"
}}
只输出 JSON。article_indices 指向上方拆解编号。"""


def _synthesize(client, articles: list[dict], today: str) -> dict:
    prompt = _S2.format(today=today,
                        articles_json=json.dumps(articles, ensure_ascii=False, indent=2),
                        watchlist=", ".join(WATCHLIST))
    try:
        resp = client.messages.create(
            model=MODEL_SONNET, max_tokens=12000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text")
        return _extract_json(text) or {}
    except Exception as e:
        print(f"  [Sonnet err] {e}")
        return {}


def _extract_json(text: str) -> dict | None:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON 解析失败: {e}")
        return None


# ============================================================
# 阶段三：引擎客观验证
# ============================================================

def _enrich_with_engine(data: dict, single_results: list[dict]) -> dict:
    """用 ThemeStockEngine 客观验证主题→标的映射，记录 L3 定性信号"""
    if not data or not data.get("themes"):
        return data

    try:
        from theme_stock import ThemeStockEngine
        from theme_stock.store import ThemeStockStore
        engine = ThemeStockEngine()
        store = engine._store
    except Exception as e:
        print(f"  [WARN] 引擎不可用: {e}")
        return data

    # 写入 L3 信号: Haiku 提取的 ticker
    signal_count = 0
    for sr in single_results:
        if not sr:
            continue
        for tk in sr.get("tickers", []):
            code = str(tk.get("code", "")).strip()
            if not code or not re.match(r"^\d{6}$", code):
                continue
            try:
                store.add_signal(
                    code=code, theme=sr.get("category", "其他"),
                    signal_type="wechat_article", direction="neutral",
                    strength=0.3, detail=tk.get("relevance", ""),
                    source_url="", market="A", ttl_days=7,
                )
                signal_count += 1
            except Exception:
                pass
    if signal_count:
        print(f"  L3 信号: {signal_count} 条")

    # 用引擎查询每个主题的客观标的
    for t in data["themes"]:
        name = t.get("name", "")
        if not name:
            continue
        try:
            r = engine.query(name, limit=15)
            t["engine_stocks"] = [
                {
                    "code": e.code, "name": e.name, "market": e.market,
                    "score": e.score, "tier": e.tier, "segment": e.segment,
                    "role": e.role, "moat_total": e.moat_total,
                    "tier_label": e.tier_label,
                    "sources": [{"source": s.source, "detail": s.detail}
                               for s in e.sources],
                }
                for e in r.stocks
            ]
            t["engine_total"] = r.total
            t["chain_context"] = r.chain_context
            if r.stocks:
                print(f"  [{name}] 引擎→{len(r.stocks)}只 (共{r.total})")
            else:
                print(f"  [{name}] 引擎→无匹配 (LLM={len(t.get('related_stocks',[]))}只)")
        except Exception as e:
            print(f"  [WARN] 引擎查询 [{name}] 失败: {e}")

    engine.close()
    return data


# ============================================================

def _build_name_map(single_results: list[dict], data: dict) -> dict[str, str]:
    codes = set()
    for sr in single_results:
        if not sr: continue
        for tk in sr.get("tickers", []):
            c = str(tk.get("code", ""))
            if re.match(r"^\d{6}$", c): codes.add(c)
    for t in data.get("themes", []):
        for c in t.get("related_stocks", []):
            c = str(c)
            if re.match(r"^\d{6}$", c): codes.add(c)
    for a in data.get("watchlist_alerts", []):
        c = str(a.get("code", ""))
        if re.match(r"^\d{6}$", c): codes.add(c)
    for a in data.get("action_items", []):
        target = str(a.get("target", ""))
        for m in re.finditer(r"\b(\d{6})\b", target): codes.add(m.group(1))
    if not codes: return {}
    try:
        from data import fetch_stock_quotes
        quotes = fetch_stock_quotes(list(codes), batch_size=30)
        return {c: q.get("name", "") for c, q in quotes.items()}
    except Exception:
        return {}


def _fmt_code(c: str, nm: dict[str, str]) -> str:
    c = str(c)
    name = nm.get(c, "")
    return f"{c} {name}" if name else c


def _write_report(data: dict, single_results: list[dict],
                  today: str, fetched: int, failed: int):
    nm = _build_name_map(single_results, data)
    path = REPORT_DIR / "wechat_analysis" / f"wechat_analysis_{today}.md"
    n_feeds = len(set(a.get("feed", "") for a in single_results if a))
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    buf = [
        f"# 公众号深度分析 {today} {datetime.now().strftime('%H:%M')}", "",
        f"> {len(single_results)} 篇 | {n_feeds} 个来源 | {fetched} 篇有正文 | 生成于 {now_ts}",
    ]
    if failed:
        buf[-1] += f" | {failed} 篇无正文"
    shendu_reports_list = data.get("shendu_reports", [])
    if shendu_reports_list:
        buf.append(f"> 🔬 深度投研洞见独立报告: {len(shendu_reports_list)} 篇 | 见 reports/shendu/")
    buf.append("")
    _existing_report = path.exists()

    # 来源概览
    feed_counts: dict[str, int] = {}
    for a in single_results:
        if a:
            f = a.get("feed", "?")
            feed_counts[f] = feed_counts.get(f, 0) + 1
    buf.append("## 来源概览")
    buf.append("")
    buf.append("| 来源 | 篇数 |")
    buf.append("|------|------|")
    for f, c in sorted(feed_counts.items(), key=lambda x: -x[1]):
        buf.append(f"| {f} | {c} |")
    buf.append("")

    n = data.get("market_narrative", "")
    if n:
        buf.extend(["## 市场叙事", "", n, ""])

    themes = data.get("themes", [])
    if themes:
        buf.append("## 核心主题")
        buf.append("")
        for t in themes:
            conv = t.get("conviction", "·")
            emoji = {"高": "🔥", "中": "📌", "低": "👀"}.get(conv, "·")
            indices = t.get("article_indices", [])
            idx_str = ""
            if indices:
                idx_str = " （#" + ", #".join(str(i) for i in indices) + "）"
            buf.append(f"### {emoji} {t.get('name','')} （{conv}确信度）{idx_str}")
            buf.append("")
            for key, label in [("thesis", "逻辑"), ("catalyst", "催化"),
                               ("horizon", "时间维度"), ("risk", "风险")]:
                v = t.get(key, "")
                if v:
                    buf.append(f"- **{label}**: {v}")
            feeds = t.get("feeds", [])
            if feeds:
                buf.append(f"- **来源**: {', '.join(feeds)}")
            # 引擎客观标的（主表）
            eng = t.get("engine_stocks", [])
            if eng:
                buf.append(f"- **关联标的** (引擎, {len(eng)}只):")
                buf.append("")
                buf.append("| 代码 | 名称 | 位置 | 置信度 | 来源 |")
                buf.append("|------|------|------|--------|------|")
                for e in eng[:10]:
                    pos = f"{e.get('tier','')}/{e.get('segment','')}" if e.get("tier") else "-"
                    srcs = ", ".join(s["source"].replace("concept_","").replace("chain_","")
                                    for s in e.get("sources", [])[:2])
                    m = "🔵" if e.get("market") == "HK" else "🇺🇸" if e.get("market") == "US" else ""
                    buf.append(f"| {e['code']} | {m}{e['name']} | {pos} | "
                              f"{e['score']:.0%} | {srcs} |")
                buf.append("")

            # Sonnet LLM 原始输出（折叠，审计用）
            llm_stocks = t.get("related_stocks", [])
            if llm_stocks:
                llm_list = ', '.join(_fmt_code(s, nm) for s in llm_stocks)
                buf.append(f"- <details><summary>🤖 LLM 原始输出: {llm_list}</summary>")
                if eng:
                    eng_codes = {e["code"] for e in eng}
                    new_codes = [s for s in llm_stocks if str(s) not in eng_codes]
                    if new_codes:
                        buf.append(f"  > ⚠️ 引擎未覆盖: {', '.join(_fmt_code(s, nm) for s in new_codes)}")
                buf.append("</details>")
                buf.append("")
            elif not eng:
                buf.append("- **关联标的**: 无")
                buf.append("")

    cross = data.get("cross_validation", [])
    if cross:
        buf.append("## 交叉验证")
        buf.append("")
        for c in cross:
            buf.append(f"### {c.get('theme', '')}")
            buf.append("")
            for key, label in [("consensus_view", "一致看法"),
                               ("divergent_view", "分歧/补充"),
                               ("our_take", "我们的判断")]:
                v = c.get(key, "")
                if v:
                    buf.append(f"- **{label}**: {v}")
            buf.append("")

    alerts = data.get("watchlist_alerts", [])
    if alerts:
        buf.append("## 自选股预警")
        buf.append("")
        buf.append("| 代码 | 信号 | 逻辑 | 紧迫度 |")
        buf.append("|------|------|------|--------|")
        for a in alerts:
            sig = a.get("signal", "·")
            sig_emoji = {"正面": "🟢", "负面": "🔴", "关注": "🟡"}.get(sig, "·")
            buf.append(f"| {_fmt_code(a.get('code',''), nm)} | {sig_emoji} {sig} | "
                       f"{a.get('reason','')} | {a.get('urgency','')} |")
        buf.append("")

    actions = data.get("action_items", [])
    if actions:
        buf.append("## 行动建议")
        buf.append("")
        for a in sorted(actions, key=lambda x: x.get("priority", 99)):
            target = str(a.get("target", ""))
            target_fmt = ", ".join(_fmt_code(t.strip(), nm) for t in target.split(",") if t.strip())
            buf.append(f"- **P{a.get('priority','?')}** [{target_fmt}] "
                       f"{a.get('action','')} — {a.get('rationale','')}")
        buf.append("")

    q = data.get("key_question", "")
    if q:
        buf.extend(["## 关键问题", "", f"> {q}", ""])

    s = data.get("summary", "")
    if s:
        buf.extend(["## 整体摘要", "", s, ""])

    # 逐篇拆解详情（按来源分组）
    buf.append("## 逐篇拆解")
    buf.append("")
    feed_order = []
    for sr in single_results:
        if sr:
            f = sr.get("feed", "?")
            if f not in feed_order:
                feed_order.append(f)

    global_idx = 0
    for feed in feed_order:
        feed_articles = [(i, a) for i, a in enumerate(single_results) if a and a.get("feed", "?") == feed]
        buf.append(f"### {feed} ({len(feed_articles)} 篇)")
        buf.append("")
        for orig_i, sr in feed_articles:
            global_idx += 1
            i = orig_i + 1  # 1-based index matching Sonnet article_indices
            title = sr.get("title", "?")
            thesis = sr.get("thesis", "")
            facts = sr.get("key_facts", [])
            tickers = sr.get("tickers", [])
            cat = sr.get("category", "?")
            score = sr.get("relevance_score", 0)
            oneliner = sr.get("one_liner", "")

            buf.append(f"### #{i} [{feed}] {title}")
            buf.append("")
            buf.append(f"**{cat}** | {'★' * score}{'☆' * (5 - score)}")
            buf.append("")
            if thesis:
                buf.append(f"**论点**: {thesis}")
                buf.append("")
            if facts:
                for f in facts:
                    buf.append(f"- {f}")
                buf.append("")
            if tickers:
                buf.append("| 标的 | 关联逻辑 |")
                buf.append("|------|---------|")
                for tk in tickers:
                    buf.append(f"| {_fmt_code(tk.get('code',''), nm)} | {tk.get('relevance','')} |")
                buf.append("")
            if oneliner:
                buf.append(f"> {oneliner}")
                buf.append("")

    path.parent.mkdir(parents=True, exist_ok=True)

    if _existing_report:
        old_content = path.read_text(encoding="utf-8")
        detail_start = None
        for i, line in enumerate(buf):
            if line.startswith("## 逐篇拆解"):
                detail_start = i
                break
        if detail_start is not None:
            new_sections = "\n".join(buf[detail_start:])
            merged = old_content.rstrip() + "\n\n---\n\n## 🆕 增量更新 " + now_ts + "\n\n" + new_sections
            path.write_text(merged, encoding="utf-8")
        else:
            path.write_text("\n".join(buf), encoding="utf-8")
    else:
        path.write_text("\n".join(buf), encoding="utf-8")
    print(f"\n  报告: {path}")
    return path


# ============================================================
# 主流程
# ============================================================

def main():
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=14)).isoformat()

    print(f"公众号深度分析（两阶段）| {since} ~ {today}")

    store.init_feeds_tables()
    all_rows = store.query_wechat_articles(since, unanalyzed_only=False)
    new_rows = store.query_wechat_articles(since, unanalyzed_only=True)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    recent_any = store.query_wechat_articles(yesterday, unanalyzed_only=False)
    latest_dates = sorted(set(r["pub_date"][:10] for r in all_rows), reverse=True)
    latest_str = latest_dates[0] if latest_dates else "无"
    if not recent_any:
        print(f"  ⚠️ RSS 数据可能滞后！最新文章日期: {latest_str}，距今天已超过1天")
        print(f"  → 请先刷新 WeWe-RSS 后再跑 daily_collect --source wechat")
    else:
        print(f"  最新文章日期: {latest_str}")

    skipped = len(all_rows) - len(new_rows)
    if skipped > 0:
        print(f"  已分析跳过: {skipped} 篇")
    if not new_rows:
        print(f"  无新文章（近3天共{len(all_rows)}篇，均已分析）")
        return
    rows = new_rows

    # 独角兽智库/情报 去重: 同标题只分析一次
    seen_titles = set()
    deduped = []
    skipped_dup = 0
    for r in rows:
        t = r.get("title", "").strip()[:40]
        feed = r.get("feed_source", "").strip()
        if feed in ("独角兽智库", "独角兽情报"):
            if t in seen_titles:
                skipped_dup += 1
                continue
            seen_titles.add(t)
        deduped.append(r)
    if skipped_dup:
        print(f"  独角兽去重: 跳过 {skipped_dup} 篇重复标题")
    rows = deduped

    key = _load_api_key()
    if not key:
        print("  API key 不可用")
        return

    from roles import get_client as _get_client

    # 正文已在采集阶段抓全，直接用 DB 里的 description
    articles_with_body = []
    shendu_articles = []
    for r in rows:
        body = (r.get("description") or "").strip()
        feed = r.get("feed_source", "").strip()
        a = {
            "feed": feed or "未分类",
            "date": (r.get("pub_date") or "")[:10],
            "title": r.get("title", "").strip(),
            "body": body,
        }
        articles_with_body.append(a)
        if feed == "深度投研洞见" and len(body) > 500:
            shendu_articles.append(a)
    fetched = sum(1 for a in articles_with_body if a["body"])
    failed = len(articles_with_body) - fetched
    print(f"  正文: {fetched} 成功, {failed} 缺失")

    # 深度投研洞见 → 专用结构化提取器（独立管道，跳过 S1/S2）
    shendu_reports = []
    if shendu_articles:
        print(f"\n  深度投研洞见: {len(shendu_articles)} 篇 → 结构化提取...")
        from extractors.shendu import (
            extract as shendu_extract,
            inject_to_serenity,
            render_markdown,
        )
        for sa in shendu_articles:
            try:
                data = shendu_extract(sa["body"], sa["title"], sa["date"])
                if data:
                    inject_to_serenity(data)
                    report_path = render_markdown(data)
                    shendu_reports.append(report_path)
                    print(f"    ✓ {sa['title'][:40]}... → {report_path.name if report_path else 'no report'}")
            except Exception as e:
                print(f"    ✗ {sa['title'][:30]}...: {e}")

    # 深度投研洞见 从标准管道中排除（已独立分析）
    shendu_feeds = {"深度投研洞见"}
    s1_articles = [a for a in articles_with_body if a["feed"] not in shendu_feeds]

    # 韭研脱水研报 → 催化事件提取器
    jiuyan_articles = [a for a in articles_with_body
                       if a["feed"] == "韭研脱水研报" and len(a["body"]) > 200]
    if jiuyan_articles:
        total_events = 0
        for ja in jiuyan_articles:
            try:
                from extractors.jiuyan import extract as jiuyan_extract, inject_to_catalyst_screen
                events = jiuyan_extract(ja["body"], ja["title"], ja["date"])
                if events:
                    n = inject_to_catalyst_screen(events)
                    total_events += n
            except Exception as e:
                pass
        if total_events:
            print(f"  韭研脱水: {len(jiuyan_articles)} 篇 → {total_events} 催化事件 → catalyst_screen")

    # 寻找低估 → 价格信号 + 催化剂日历
    xunzhao_articles = [a for a in articles_with_body
                        if a["feed"] == "寻找低估" and len(a["body"]) > 200]
    if xunzhao_articles:
        for xa in xunzhao_articles:
            try:
                from extractors.xunzhao import extract as xz_extract, inject as xz_inject, format_summary
                data = xz_extract(xa["body"], xa["title"], xa["date"])
                if data:
                    r = xz_inject(data)
                    print(f"  寻找低估: {xa['date']} → "
                          f"{r['price']}价格/{r['calendar']}日历/{r['earnings']}业绩")
            except Exception as e:
                pass

    # 阶段一（排除深度投研洞见，已独立分析）
    if s1_articles:
        print(f"\n  阶段一: 逐篇拆解 (synthesis, {len(s1_articles)}篇)...")
        s1_client = _get_client("synthesis", timeout=TIMEOUT)
        single_results = []
        for i, a in enumerate(s1_articles):
            sr = _analyze_single(s1_client, a["feed"], a["date"], a["title"], a["body"])
            single_results.append(sr)
            score = sr.get("relevance_score", 0)
            stars = "★" * score + "☆" * (5 - score)
            print(f"    [{i+1}/{len(s1_articles)}] {stars} "
                  f"{a['title'][:30]}...")
    else:
        print(f"\n  阶段一: 无标准管道文章（仅深度投研洞见，已独立分析）")
        single_results = []

    # 阶段二
    if single_results:
        print(f"\n  阶段二: 综合研判 (deep)...")
        s2_client = _get_client("deep", timeout=TIMEOUT)
        data = _synthesize(s2_client, single_results, today)
        if not data:
            print("  Sonnet 不可用")
            _write_report({}, single_results, today, fetched, failed)
            return

        # 阶段三: 引擎客观验证
        print(f"\n  阶段三: 引擎客观验证...")
        data = _enrich_with_engine(data, single_results)
    else:
        print(f"\n  阶段二/三: 跳过（仅深度投研洞见，已独立分析）")
        data = {}

    # 注入深度投研洞见独立报告链接
    if shendu_reports:
        data["shendu_reports"] = [str(r) for r in shendu_reports]

    _write_report(data, single_results, today, fetched, failed)
    store.mark_wechat_analyzed(articles_with_body)
    shendu_info = f" + {len(shendu_reports)}篇深度投研独立报告" if shendu_reports else ""
    print(f"  完成（已标记 {len(articles_with_body)} 篇为已分析{shendu_info}）")


if __name__ == "__main__":
    main()
