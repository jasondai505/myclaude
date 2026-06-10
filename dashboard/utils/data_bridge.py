"""数据桥接层 — 对现有模块的只读封装，不修改任何现有代码"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any
from dataclasses import dataclass, field
from collections import defaultdict


# ============================================================
# 工具函数
# ============================================================

def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")

def _parse_date_from_filename(p: Path) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
    return m.group(1) if m else None

def _safe_import(module_path: str, attr: str = None):
    """安全导入，失败返回 None"""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, attr) if attr else mod
    except Exception:
        return None

def _read_md_head(path: Path, lines: int = 60) -> str:
    """读 markdown 文件头部"""
    try:
        with open(path, encoding="utf-8") as f:
            return "".join(f.readline() for _ in range(lines))
    except Exception:
        return ""


# ============================================================
# 报告发现与解析
# ============================================================

@dataclass
class ReportInfo:
    path: Path
    date_str: str
    report_type: str
    title: str = ""
    summary: str = ""

def _find_reports(report_type: str, limit: int = 30) -> list[ReportInfo]:
    """查找各类报告文件"""
    patterns = {
        "review": "daily_review/reports/review_????-??-??.md",
        "advice": "daily_review/reports/advice_????-??-??.md",
        "wechat": "daily_review/reports/wechat_analysis_????-??-??.md",
        "bom": "bom_analyzer/reports/bom_*.md",
        "deep": "stock_deep/reports/deep_*.md",
        "marginal": "daily_review/reports/marginal_????-??-??.md",
    }
    glob_pat = patterns.get(report_type)
    if not glob_pat:
        return []

    files = sorted(PROJECT_ROOT.glob(glob_pat), reverse=True)[:limit]
    results = []
    for f in files:
        d = _parse_date_from_filename(f)
        if not d and report_type == "bom":
            d = f.stem.replace("bom_", "")[:10]
        if not d:
            d = ""
        results.append(ReportInfo(path=f, date_str=d, report_type=report_type))
    return results


def _extract_advice_summary(content: str) -> dict[str, Any]:
    """从 advice markdown 提取摘要"""
    result: dict[str, Any] = {"title": "", "key_points": [], "actions": [], "risks": []}

    title_m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if title_m:
        result["title"] = title_m.group(1).strip()

    for m in re.finditer(r"^###\s+(.+)$", content, re.MULTILINE):
        result["key_points"].append(m.group(1).strip())

    action_section = re.search(
        r"(?:操作建议|交易建议|仓位建议|行动建议).*?\n(.*?)(?=\n##|\Z)",
        content, re.DOTALL | re.IGNORECASE
    )
    if action_section:
        for line in action_section.group(1).strip().split("\n"):
            line = line.strip()
            if line and len(line) > 10:
                result["actions"].append(line)

    risk_section = re.search(
        r"(?:风险提示|风险|注意).*?\n(.*?)(?=\n##|\Z)",
        content, re.DOTALL | re.IGNORECASE
    )
    if risk_section:
        for line in risk_section.group(1).strip().split("\n"):
            line = line.strip()
            if line and len(line) > 5:
                result["risks"].append(line)

    return result


def _extract_review_summary(content: str) -> dict[str, Any]:
    """从复盘 markdown 提取摘要"""
    result: dict[str, Any] = {
        "market_emotion": "",
        "turnover": "",
        "up_down_ratio": "",
        "main_themes": [],
        "northbound": "",
        "sentiment": "",
    }

    for line in content.split("\n"):
        line = line.strip()
        if "成交额" in line and not result["turnover"]:
            result["turnover"] = line
        if "涨停" in line and "跌停" in line and not result["sentiment"]:
            result["sentiment"] = line
        if "北向" in line and not result["northbound"]:
            result["northbound"] = line
        if ("赚钱效应" in line or "市场情绪" in line) and not result["market_emotion"]:
            result["market_emotion"] = line
        if "主线" in line or "主升" in line:
            result["main_themes"].append(line)

    return result


# ============================================================
# 公共 API — 报告
# ============================================================

def list_reports(report_type: str = "review", limit: int = 30) -> list[dict[str, Any]]:
    """列出报告列表"""
    reports = _find_reports(report_type, limit)
    return [
        {
            "date": r.date_str,
            "path": str(r.path),
            "type": r.report_type,
            "exists": r.path.exists(),
            "size_kb": round(r.path.stat().st_size / 1024, 1) if r.path.exists() else 0,
        }
        for r in reports
    ]


def get_report_content(report_type: str, date_str: str = "") -> dict[str, Any]:
    """获取指定报告的完整内容"""
    if date_str:
        if report_type == "review":
            path = PROJECT_ROOT / f"daily_review/reports/review_{date_str}.md"
        elif report_type == "advice":
            path = PROJECT_ROOT / f"daily_review/reports/advice_{date_str}.md"
        elif report_type == "wechat":
            path = PROJECT_ROOT / f"daily_review/reports/wechat_analysis_{date_str}.md"
        elif report_type == "bom":
            return _get_bom_content(date_str)
        elif report_type == "marginal":
            path = PROJECT_ROOT / f"daily_review/reports/marginal_{date_str}.md"
        else:
            return {"error": f"未知报告类型: {report_type}", "content": ""}
    else:
        if report_type == "bom":
            date_str = _today_str()
            return _get_bom_content(date_str)
        reports = _find_reports(report_type, 1)
        if not reports:
            return {"error": f"未找到 {report_type} 报告", "content": ""}
        path = reports[0].path

    if not path.exists():
        return {"error": f"文件不存在: {path}", "content": ""}

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": f"读取失败: {e}", "content": ""}

    return {
        "content": content,
        "path": str(path),
        "date": date_str or _parse_date_from_filename(path) or "",
        "type": report_type,
    }


def _get_bom_content(date_str: str) -> dict[str, Any]:
    """获取指定日期的所有 BOM 报告，合并为一份"""
    files = sorted(PROJECT_ROOT.glob(f"bom_analyzer/reports/bom_*_{date_str}.md"))
    if not files:
        return {"error": f"未找到 {date_str} 的 BOM 报告", "content": ""}

    parts = [f"# BOM 产业链分析 {date_str}", "",
             f"> {len(files)} 个行业", ""]
    paths = []
    for f in files:
        industry = f.stem.replace("bom_", "").replace(f"_{date_str}", "")
        try:
            body = f.read_text(encoding="utf-8")
            body = re.sub(r"^#\s+.*$", f"## {industry}", body, count=1, flags=re.MULTILINE)
            parts.append(body)
            parts.append("")
            paths.append(str(f))
        except Exception:
            parts.append(f"## {industry}\n\n_读取失败_")
            parts.append("")

    return {
        "content": "\n".join(parts),
        "path": ", ".join(paths),
        "date": date_str,
        "type": "bom",
        "industry_count": len(files),
    }


def get_marginal_summary(date_str: str = "") -> dict[str, Any]:
    """获取边际变化日报摘要：方向计数 + 重点变化条目"""
    if not date_str:
        date_str = _today_str()
    path = PROJECT_ROOT / f"daily_review/reports/marginal_{date_str}.md"
    if not path.exists():
        return {"error": f"边际变化日报 {date_str} 尚未生成", "exists": False}

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": f"读取失败: {e}", "exists": False}

    result: dict[str, Any] = {
        "exists": True, "date": date_str, "path": str(path),
        "up_count": 0, "down_count": 0, "first_count": 0, "flat_count": 0,
        "up_changes": [], "down_changes": [],
    }

    # 解析摘要行: > 边际向好 **158** · 边际下滑 **91** · ...
    m = re.search(r"边际向好\s*\*?\*?(\d+)\*?\*?", content)
    if m: result["up_count"] = int(m.group(1))
    m = re.search(r"边际下滑\s*\*?\*?(\d+)\*?\*?", content)
    if m: result["down_count"] = int(m.group(1))
    m = re.search(r"首次记录\s*\*?\*?(\d+)\*?\*?", content)
    if m: result["first_count"] = int(m.group(1))
    m = re.search(r"符合预期\s*\*?\*?(\d+)\*?\*?", content)
    if m: result["flat_count"] = int(m.group(1))

    # 提取表格行，按所在章节归类
    current_section = ""
    for line in content.split("\n"):
        if line.startswith("## 边际向好"):
            current_section = "up"
        elif line.startswith("## 边际下滑"):
            current_section = "down"
        elif line.startswith("## ") and current_section:
            current_section = ""
        elif line.startswith("|") and current_section in ("up", "down") and "代码" not in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 5 and not parts[0].startswith("-"):
                entry = {"code": parts[0], "name": parts[1], "theme": parts[2], "desc": parts[3][:80]}
                if current_section == "up" and len(result["up_changes"]) < 8:
                    result["up_changes"].append(entry)
                elif current_section == "down" and len(result["down_changes"]) < 5:
                    result["down_changes"].append(entry)

    return result


def get_latest_market_snapshot() -> dict[str, Any]:
    """获取最新市场快照 — 从 SQLite 读取"""
    try:
        store = _safe_import("daily_review.store")
        if store is None:
            return {"error": "无法导入 daily_review.store"}

        db_path = PROJECT_ROOT / "daily_review/data/review.db"
        if not db_path.exists():
            return {"error": f"数据库不存在: {db_path}"}

        conn = store._get_conn()
        if conn is None:
            return {"error": "数据库连接失败"}

        cur = conn.execute(
            "SELECT trade_date FROM market_snapshots ORDER BY trade_date DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return {"error": "无市场快照数据"}

        latest_date = row[0]

        cur = conn.execute(
            "SELECT * FROM market_snapshots WHERE trade_date = ?", (latest_date,)
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        conn.close()

        return {"date": latest_date, "snapshots": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def get_latest_advice_summary() -> dict[str, Any]:
    """获取最新 advice 摘要"""
    reports = _find_reports("advice", 1)
    if not reports:
        return {"error": "未找到 advice 报告"}

    content = _read_md_head(reports[0].path, 120)
    summary = _extract_advice_summary(content)
    summary["date"] = reports[0].date_str
    summary["path"] = str(reports[0].path)
    return summary


def get_latest_bom_summary() -> dict[str, Any]:
    """获取最新 BOM 分析摘要"""
    try:
        chain_db = _safe_import("bom_analyzer.chain_db")
        if chain_db is None:
            return {"error": "无法导入 bom_analyzer.chain_db"}

        db_path = PROJECT_ROOT / "bom_analyzer/data/bom.db"
        if not db_path.exists():
            return {"error": f"BOM数据库不存在: {db_path}"}

        conn = chain_db._get_conn()

        cur = conn.execute("""
            SELECT DISTINCT industry, analyzed_at
            FROM chains
            ORDER BY analyzed_at DESC LIMIT 5
        """)
        industries = [{"industry": r[0], "date": r[1]} for r in cur.fetchall()]

        cur = conn.execute("""
            SELECT l.chain_id, l.segment_name, l.code, l.name, l.rank, l.moat_score
            FROM leaders l
            ORDER BY l.created_at DESC LIMIT 20
        """)
        leaders = []
        for r in cur.fetchall():
            leaders.append({
                "chain_id": r[0], "segment": r[1],
                "code": r[2], "name": r[3],
                "rank": r[4], "moat_score": r[5],
            })

        conn.close()

        bom_reports = _find_reports("bom", 3)
        report_info = [
            {"date": r.date_str, "path": str(r.path)} for r in bom_reports
        ]

        return {
            "industries": industries,
            "leaders": leaders,
            "reports": report_info,
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 公共 API — 流水线
# ============================================================

# 流水线 → orchestrator 映射
ORCHESTRATOR_MAP = {
    "review": "close",     # 收盘复盘 = close 流水线
    "advice": "pre",       # 盘前建议 = pre 流水线
    "bom": "bom",          # BOM = bom 流水线
    "collect": "collect",  # 采集 = collect 流水线
    "wechat": "wechat_only",
}

PIPELINE_CONFIG = {
    "review": {
        "name": "收盘复盘",
        "orchestrator": "close",
        "desc": "数据采集 → 复盘 → 简报",
    },
    "advice": {
        "name": "盘前建议",
        "orchestrator": "pre",
        "desc": "采集 → 公众号 → 摘要 → 追踪 → 解读 → 建议",
    },
    "bom": {
        "name": "BOM产业链分析",
        "orchestrator": "bom",
        "desc": "产业链拆解 + 龙头护城河评分",
    },
    "collect": {
        "name": "数据采集",
        "orchestrator": "collect",
        "desc": "10 源基本面数据采集",
    },
    "wechat": {
        "name": "公众号分析",
        "orchestrator": "wechat_only",
        "desc": "微信公众号两阶段 AI 分析",
    },
}


def run_pipeline(name: str, extra_args: list[str] | None = None) -> dict[str, Any]:
    """通过 orchestrator.py 触发流水线"""
    if name not in PIPELINE_CONFIG:
        return {"error": f"未知流水线: {name}", "available": list(PIPELINE_CONFIG.keys())}

    orch_name = PIPELINE_CONFIG[name]["orchestrator"]
    orch_path = PROJECT_ROOT / "orchestrator.py"
    if not orch_path.exists():
        return {"error": f"orchestrator.py 不存在: {orch_path}"}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = PROJECT_ROOT / "dashboard/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}_{ts}.log"

    args = [sys.executable, str(orch_path), orch_name]
    if extra_args:
        args.extend(extra_args)

    def _runner():
        try:
            with open(log_path, "w", encoding="utf-8") as log:
                log.write(f"=== {PIPELINE_CONFIG[name]['name']} ===\n")
                log.write(f"orchestrator: {orch_name}\n")
                log.write(f"启动: {datetime.now()}\n\n")
                proc = subprocess.Popen(
                    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=str(PROJECT_ROOT),
                    text=True, encoding="utf-8", errors="replace",
                )
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                proc.wait()
                log.write(f"\n退出码: {proc.returncode}\n")
                log.write(f"结束: {datetime.now()}\n")
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"\n异常: {e}\n")

    t = threading.Thread(target=_runner, daemon=True)
    t.start()

    return {"pipeline": name, "status": "started", "log_path": str(log_path)}


def get_pipeline_status() -> list[dict[str, Any]]:
    """获取所有流水线状态"""
    statuses = []
    for key, cfg in PIPELINE_CONFIG.items():
        s = {"key": key, "label": cfg["name"], "desc": cfg["desc"]}

        # 从日志推断状态
        log_dir = PROJECT_ROOT / "dashboard/logs"
        if log_dir.exists():
            logs = sorted(log_dir.glob(f"{key}_*.log"), reverse=True)
            if logs:
                latest = logs[0]
                s["last_run"] = datetime.fromtimestamp(
                    latest.stat().st_mtime
                ).isoformat()
                try:
                    tail = latest.read_text(encoding="utf-8")
                    if "退出码: 0" in tail:
                        s["status"] = "success"
                    elif "退出码:" in tail:
                        s["status"] = "failed"
                    elif "异常:" in tail:
                        s["status"] = "error"
                    else:
                        s["status"] = "running" if latest.stat().st_mtime > time.time() - 3600 else "unknown"
                except Exception:
                    s["status"] = "unknown"
                s["log_path"] = str(latest)
            else:
                s["status"] = "never_run"
        else:
            s["status"] = "never_run"

        # 回退：从报告文件推断
        if s.get("status") == "never_run":
            inferred = _infer_from_reports(key)
            if inferred:
                s["inferred_from_reports"] = inferred

        statuses.append(s)
    return statuses


def _infer_from_reports(name: str) -> dict[str, Any] | None:
    """从报告文件时间推断上次运行"""
    report_globs = {
        "review": "daily_review/reports/review_????-??-??.md",
        "advice": "daily_review/reports/advice_????-??-??.md",
        "bom": "bom_analyzer/reports/bom_*.md",
        "collect": "daily_review/reports/feeds/news_????-??-??.md",
    }
    pattern = report_globs.get(name)
    if not pattern:
        return None
    files = sorted(PROJECT_ROOT.glob(pattern), reverse=True)
    if files:
        return {
            "last_report": str(files[0]),
            "last_mtime": datetime.fromtimestamp(files[0].stat().st_mtime).isoformat(),
        }
    return None


def get_pipeline_log(pipeline: str, log_path: str | None = None) -> str:
    """获取流水线日志内容"""
    if log_path:
        p = Path(log_path)
    else:
        log_dir = PROJECT_ROOT / "dashboard/logs"
        logs = sorted(log_dir.glob(f"{pipeline}_*.log"), reverse=True)
        if not logs:
            return "无日志"
        p = logs[0]

    try:
        content = p.read_text(encoding="utf-8")
        if len(content) > 50000:
            content = content[-30000:]
            content = "... (截断前段) ...\n" + content
        return content
    except Exception as e:
        return f"读取日志失败: {e}"


# ============================================================
# 公共 API — 个股
# ============================================================

def get_stock_overview(code: str) -> dict[str, Any]:
    """获取个股概览"""
    try:
        data_mod = _safe_import("daily_review.data")
        if data_mod is None:
            return {"error": "无法导入 daily_review.data"}

        quotes = data_mod.fetch_stock_quotes([code])
        if not quotes:
            return {"error": f"未获取到 {code} 行情数据"}

        q = quotes.get(code, {})
        return {
            "code": code,
            "name": q.get("name", ""),
            "price": q.get("price", 0),
            "change_pct": q.get("change_pct", 0),
            "pe": q.get("pe_ttm", 0) or q.get("pe_static", 0) or 0,
            "pb": q.get("pb", 0) or 0,
            "market_cap": q.get("mcap_yi", 0) or 0,
            "turnover_rate": q.get("turnover_pct", 0),
            "volume": q.get("amount_wan", 0),
            "high": q.get("high", 0),
            "low": q.get("low", 0),
        }
    except Exception as e:
        return {"error": str(e)}


def get_stock_bom_position(code: str) -> list[dict[str, Any]]:
    """查询个股在 BOM 产业链中的位置"""
    try:
        chain_db = _safe_import("bom_analyzer.chain_db")
        if chain_db is None:
            return []

        db_path = PROJECT_ROOT / "bom_analyzer/data/bom.db"
        if not db_path.exists():
            return []

        conn = chain_db._get_conn()
        cur = conn.execute("""
            SELECT l.chain_id, l.segment_name, l.name, l.rank, l.moat_score,
                   l.core_strengths, l.risk_alerts
            FROM leaders l
            WHERE l.code = ?
            ORDER BY l.created_at DESC
        """, (code,))
        results = []
        for r in cur.fetchall():
            results.append({
                "chain_id": r[0], "segment": r[1],
                "name": r[2], "rank": r[3], "moat_score": r[4],
                "strengths": r[5], "risks": r[6],
            })
        conn.close()
        return results
    except Exception:
        return []


def get_stock_deep_reports(code: str) -> list[dict[str, Any]]:
    """获取个股深度分析报告列表"""
    reports = []
    pattern = f"stock_deep/reports/deep_{code}_*.md"
    for f in sorted(PROJECT_ROOT.glob(pattern), reverse=True)[:5]:
        reports.append({
            "date": _parse_date_from_filename(f) or "",
            "path": str(f),
            "size_kb": round(f.stat().st_size / 1024, 1),
        })
    return reports


def get_watchlist_status() -> list[dict[str, Any]]:
    """获取自选股简要状态"""
    try:
        config = _safe_import("daily_review.config")
        if config is None:
            return []

        codes = getattr(config, "WATCHLIST", [])
        if not codes:
            return []

        data_mod = _safe_import("daily_review.data")
        if data_mod is None:
            return [{"code": c, "error": "data模块不可用"} for c in codes]

        quotes = data_mod.fetch_stock_quotes(codes)
        if not quotes:
            return [{"code": c, "error": "行情获取失败"} for c in codes]

        results = []
        for code, q in quotes.items():
            results.append({
                "code": code,
                "name": q.get("name", ""),
                "price": q.get("price", 0),
                "change_pct": q.get("change_pct", 0),
                "pe": q.get("pe_ttm", 0) or q.get("pe_static", 0) or 0,
                "market_cap": q.get("mcap_yi", 0) or 0,
            })
        return results
    except Exception as e:
        return [{"error": str(e)}]


def get_config_info() -> dict[str, Any]:
    """获取系统配置摘要"""
    try:
        config = _safe_import("daily_review.config")
        if config is None:
            return {"error": "config 不可用"}

        return {
            "watchlist_count": len(getattr(config, "WATCHLIST", [])),
            "indices": getattr(config, "INDICES", {}),
            "global_indices": getattr(config, "GLOBAL_INDICES_EM", {}),
            "fev_thresholds": getattr(config, "FEV_THRESHOLDS", {}),
        }
    except Exception as e:
        return {"error": str(e)}


def get_market_index_snapshot() -> dict[str, Any]:
    """获取主要指数实时快照"""
    try:
        data_mod = _safe_import("daily_review.data")
        if data_mod is None:
            return {"error": "data 模块不可用"}
        indices = data_mod.fetch_indices()
        return {"indices": indices, "time": datetime.now().isoformat()}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 公共 API — 实时快扫
# ============================================================

_LIVE_SCAN_CACHE: dict[str, Any] = {}


def run_live_scan(force_refresh: bool = False) -> dict[str, Any]:
    """全A实时行情快扫，结果缓存到内存"""
    global _LIVE_SCAN_CACHE
    if not force_refresh and _LIVE_SCAN_CACHE.get("timestamp"):
        age = (datetime.now() - _LIVE_SCAN_CACHE["timestamp"]).total_seconds()
        if age < 30:
            return _LIVE_SCAN_CACHE

    t0 = time.perf_counter()
    try:
        scanner = _safe_import("daily_review.live_scanner")
        if scanner is None:
            return {"error": "无法导入 live_scanner 模块"}

        df = scanner.scan_all()
        if df.empty:
            return {"error": "未获取到数据（非交易时间？）", "count": 0, "elapsed": 0}

        elapsed = time.perf_counter() - t0
        result = {
            "data": df,
            "count": len(df),
            "elapsed": round(elapsed, 1),
            "timestamp": datetime.now(),
            "columns": df.columns.tolist(),
        }

        if "change_pct" in df.columns:
            chg = df["change_pct"].dropna()
            result["summary"] = {
                "up_count": int((chg > 0).sum()),
                "down_count": int((chg < 0).sum()),
                "limit_up": int((chg >= 9.9).sum()),
                "limit_down": int((chg <= -9.9).sum()),
                "avg_change": round(float(chg.mean()), 2),
                "median_change": round(float(chg.median()), 2),
                "top_gainer": None,
                "top_loser": None,
            }
            if not df.empty:
                top = df.nlargest(1, "change_pct").iloc[0]
                bot = df.nsmallest(1, "change_pct").iloc[0]
                result["summary"]["top_gainer"] = {
                    "code": str(top.get("code", "")), "name": str(top.get("name", "")),
                    "change_pct": float(top.get("change_pct", 0) or 0),
                }
                result["summary"]["top_loser"] = {
                    "code": str(bot.get("code", "")), "name": str(bot.get("name", "")),
                    "change_pct": float(bot.get("change_pct", 0) or 0),
                }

        _LIVE_SCAN_CACHE = result
        return result
    except Exception as e:
        return {"error": str(e), "elapsed": 0}


def get_cached_scan() -> dict[str, Any] | None:
    """获取缓存的扫描结果"""
    if _LIVE_SCAN_CACHE.get("timestamp"):
        age = (datetime.now() - _LIVE_SCAN_CACHE["timestamp"]).total_seconds()
        if age < 120:
            return _LIVE_SCAN_CACHE
    return None


# ============================================================
# 公共 API — Serenity 产业链知识库
# ============================================================

def get_serenity_chain_summary() -> list[dict[str, Any]]:
    try:
        from daily_review import serenity_kb
        serenity_kb.init_db()
        return serenity_kb.get_all_chain_summary()
    except Exception:
        return []


def get_serenity_stock_ranking(chain_name: str = "") -> list[dict[str, Any]]:
    try:
        from daily_review import serenity_kb
        serenity_kb.init_db()
        return serenity_kb.get_stock_scores(chain_name=chain_name)
    except Exception:
        return []


def get_serenity_chain_detail(chain_name: str) -> dict[str, Any] | None:
    try:
        from daily_review import serenity_kb
        serenity_kb.init_db()
        return {
            "chain_name": chain_name,
            "segments": serenity_kb.get_chain_snapshot(chain_name),
            "stocks": serenity_kb.get_stock_scores(chain_name=chain_name),
            "analysis": serenity_kb.get_latest_analysis(chain_name),
        }
    except Exception:
        return None


def get_serenity_recent_logs(days: int = 7) -> list[dict[str, Any]]:
    try:
        from daily_review import serenity_kb
        serenity_kb.init_db()
        return serenity_kb.get_recent_analyses(days=days)
    except Exception:
        return []


def get_stock_fev_history(code: str, days: int = 30) -> list[dict[str, Any]]:
    """获取个股 FEV 历史趋势"""
    try:
        from daily_review import serenity_kb
        serenity_kb.init_db()
        return serenity_kb.get_stock_history(code, days)
    except Exception:
        return []


def get_stocks_quotes_batch(codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量获取个股实时行情，返回 {code: {price, change_pct, pe, pb, market_cap, name}}"""
    if not codes:
        return {}
    try:
        data_mod = _safe_import("daily_review.data")
        if data_mod is None:
            return {}
        raw = data_mod.fetch_stock_quotes(codes)
        result = {}
        for code, q in raw.items():
            result[code] = {
                "name": q.get("name", ""),
                "price": q.get("price", 0),
                "change_pct": q.get("change_pct", 0),
                "pe": q.get("pe_ttm", 0) or q.get("pe_static", 0) or 0,
                "pb": q.get("pb", 0) or 0,
                "market_cap": q.get("mcap_yi", 0) or 0,
            }
        return result
    except Exception:
        return {}


def get_segment_cross_chains() -> dict[str, list[str]]:
    """计算所有产业链的跨链环节: {segment_name: [chain_names]}"""
    all_segments: dict[str, list[str]] = defaultdict(list)
    chains = get_bom_chain_list()
    for c in chains:
        d = get_serenity_chain_detail(c)
        if not d:
            continue
        for seg in (d.get("segments") or []):
            name = seg.get("segment", "").strip()
            if name and len(name) >= 2:
                all_segments[name].append(c)
    return {k: v for k, v in all_segments.items() if len(v) >= 2}


def get_bom_chain_list() -> list[str]:
    try:
        from bom_analyzer import chain_db
        chain_db.init_db()
        return chain_db.list_industries()
    except Exception:
        return []


def trigger_serenity_analysis(chain_name: str, force_full: bool = False) -> dict[str, Any]:
    try:
        from daily_review import serenity_kb
        serenity_kb.init_db()
        serenity_kb.update_chain_kb(chain_name, force_full=force_full)
        analysis = serenity_kb.get_latest_analysis(chain_name)
        return {
            "ok": True,
            "msg": f"「{chain_name}」分析完成",
            "report": analysis.get("full_report_path", "") if analysis else "",
        }
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def get_serenity_full_report(chain_name: str) -> str | None:
    """读取某产业链的完整 Markdown 报告内容"""
    try:
        from daily_review import serenity_kb
        serenity_kb.init_db()
        analysis = serenity_kb.get_latest_analysis(chain_name)
        if analysis and analysis.get("full_report_path"):
            path = Path(analysis["full_report_path"])
            if path.exists():
                return path.read_text(encoding="utf-8")
        return None
    except Exception:
        return None


# ============================================================
# 公共 API — 知识星球
# ============================================================

def get_zsxq_posts(days: int = 14, sort_by: str = "time",
                   stock_filter: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """获取知识星球帖子列表"""
    import sqlite3
    db = PROJECT_ROOT / "daily_review/data/review.db"
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        where = "WHERE create_time >= ?"
        params = [cutoff]
        if stock_filter:
            where += " AND stock_codes LIKE ?"
            params.append(f"%{stock_filter}%")
        order = "ORDER BY create_time DESC" if sort_by == "time" else \
                "ORDER BY (readers_count + likes_count * 2 + comments_count * 3) DESC"
        sql = f"SELECT * FROM zsxq_topics {where} {order} LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_zsxq_stats() -> dict[str, Any]:
    """知识星球统计摘要"""
    import sqlite3
    db = PROJECT_ROOT / "daily_review/data/review.db"
    if not db.exists():
        return {"error": "数据库不可用"}
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        today = date.today().isoformat()
        total = conn.execute("SELECT COUNT(*) as c FROM zsxq_topics").fetchone()["c"]
        today_count = conn.execute(
            "SELECT COUNT(*) as c FROM zsxq_topics WHERE create_time >= ?", (today,)
        ).fetchone()["c"]
        last_fetch = conn.execute(
            "SELECT MAX(fetched_at) as m FROM zsxq_topics"
        ).fetchone()["m"] or ""
        authors = conn.execute(
            "SELECT author, COUNT(*) as c FROM zsxq_topics GROUP BY author ORDER BY c DESC LIMIT 10"
        ).fetchall()
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        week_count = conn.execute(
            "SELECT COUNT(*) as c FROM zsxq_topics WHERE create_time >= ?", (week_ago,)
        ).fetchone()["c"]
        conn.close()
        return {
            "total": total,
            "today": today_count,
            "week_avg": round(week_count / 7, 1),
            "last_fetch": last_fetch[:16] if last_fetch else "无",
            "top_authors": [{"name": r["author"], "count": r["c"]} for r in authors],
        }
    except Exception as e:
        return {"error": str(e)}
