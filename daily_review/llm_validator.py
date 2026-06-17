"""LLM 输出校验 — 全项目共享基础设施。

所有 LLM 管线的输出校验统一走这个模块，不再各自实现。
覆盖两类输出：结构化 JSON（code/score/signal）和自由文本 Markdown。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PROJECT = Path(__file__).resolve().parent.parent

_re_code = re.compile(r"\b(\d{6})\b")
_re_code_name = re.compile(r"([一-鿿]{2,8})\s*[（(]\s*(\d{6})\s*[）)]")
_re_score = re.compile(r"(?:FEV|Δ|delta|G\d)\s*[=:：]?\s*(-?\d+)", re.IGNORECASE)


def _load_valid_codes() -> tuple[set[str], dict[str, str], dict[str, str]]:
    """加载全市场有效代码、名称映射、行业映射。返回 (codes, code_to_name, code_to_industry)。"""
    code_to_name: dict[str, str] = {}
    code_to_industry: dict[str, str] = {}

    # 名称→代码 (data.py)
    try:
        sys.path.insert(0, str(PROJECT / "daily_review"))
        import data
        name_to_code = data._load_name_to_code_map()
        code_to_name = {v: k for k, v in name_to_code.items()}
    except Exception:
        pass

    # 主概念
    try:
        from config import STOCK_PRIMARY_CONCEPT
        code_to_industry = dict(STOCK_PRIMARY_CONCEPT)
    except ImportError:
        pass

    return set(code_to_name.keys()), code_to_name, code_to_industry


def validate_codes(codes: list[str]) -> dict[str, dict]:
    """校验股票代码是否存在，返回 {code: {name, industry, valid, note}}。

    不存在的代码标记 valid=False 并附带原因。
    """
    valid_set, code_to_name, code_to_industry = _load_valid_codes()
    result: dict[str, dict] = {}
    for code in codes:
        code = str(code).strip()
        if not re.match(r"^\d{6}$", code):
            result[code] = {"name": "?", "industry": "?", "valid": False,
                           "note": "非6位代码格式"}
        elif code not in valid_set:
            result[code] = {"name": "?", "industry": "?", "valid": False,
                           "note": "代码不存在于全市场列表"}
        else:
            result[code] = {"name": code_to_name.get(code, "?"),
                           "industry": code_to_industry.get(code, "?"),
                           "valid": True, "note": ""}
    return result


def validate_name_code_pairs(pairs: list[tuple[str, str]]) -> list[dict]:
    """校验 (名称, 代码) 配对。名称不匹配的用真实名称自动修正。

    返回 [{code, llm_name, real_name, match, corrected_name}]
    """
    codes = [p[1] for p in pairs]
    validated = validate_codes(codes)
    results = []
    for (llm_name, code), (_, v) in zip(pairs, validated.items()):
        real_name = v.get("name", "?")
        match = (llm_name == real_name) if real_name != "?" else None
        results.append({
            "code": code, "llm_name": llm_name, "real_name": real_name,
            "match": match or False,
            "corrected_name": real_name if not match else llm_name,
            "valid": v["valid"],
        })
    return results


def extract_and_validate_codes(text: str) -> dict:
    """从自由文本中提取所有股票代码并逐条校验。

    支持格式: 名称(代码)、名称（代码）、纯6位代码。
    返回 {codes: [{code,name,valid,...}], mismatches: [...], unknown: [...], total}
    """
    valid_set, code_to_name, code_to_industry = _load_valid_codes()

    # 双通道提取: 正则6位代码 + 名称(代码)格式
    raw_codes = set(_re_code.findall(text))

    extracted: list[dict] = []
    mismatches: list[dict] = []
    unknown: list[str] = []

    for code in raw_codes:
        if code not in valid_set:
            unknown.append(code)
            continue
        # 检查文本中这个代码附近是否有名称，名称是否匹配
        real_name = code_to_name.get(code, "?")
        industry = code_to_industry.get(code, "?")
        # 在代码前后50字范围查找名称
        idx = text.find(code)
        context = text[max(0, idx - 50): idx + 56] if idx >= 0 else ""
        name_match = real_name in context if real_name != "?" else False

        entry = {"code": code, "name": real_name, "industry": industry,
                 "valid": True, "name_in_context": name_match}
        extracted.append(entry)
        if not name_match and real_name != "?":
            mismatches.append(entry)

    return {
        "codes": extracted,
        "mismatches": mismatches,
        "unknown": unknown,
        "total_raw": len(raw_codes),
        "total_valid": len(extracted),
        "total_unknown": len(unknown),
    }


def validate_score_range(scores: list[dict], field: str, lo: int, hi: int) -> list[dict]:
    """分数范围校验+clamp。越界的 clamp 到边界并标记 _clamped=True。"""
    for s in scores:
        val = s.get(field, 0)
        if not isinstance(val, (int, float)):
            try:
                val = int(val)
            except (ValueError, TypeError):
                val = 0
        if val < lo:
            s[field] = lo
            s["_clamped"] = True
        elif val > hi:
            s[field] = hi
            s["_clamped"] = True
    return scores


def audit_llm_output(name: str, text: str) -> dict:
    """一站式审计：从 LLM 输出文本中提取代码→校验→生成审计报告。

    返回 {name, total_codes, valid_codes, unknown_codes, hallucination_rate, health, details}
    """
    result = extract_and_validate_codes(text)
    total = result["total_raw"]
    valid = result["total_valid"]
    unknown = result["total_unknown"]

    rate = unknown / max(total, 1) * 100

    # 检查是否有信号级别的幻觉（如减持没有比例、利好没有具体内容）
    signal_warnings = []
    if "减持" in text:
        jianchi_sentences = [s for s in text.split("。") if "减持" in s]
        for s in jianchi_sentences[:3]:
            if not re.search(r"\d+\.?\d*%", s) and not re.search(r"比例|l%", s):
                signal_warnings.append(f"减持信号缺少比例: {s[:60]}...")

    return {
        "name": name,
        "total_codes": total,
        "valid_codes": valid,
        "unknown_codes": unknown,
        "unknown_list": result["unknown"][:10],
        "mismatch_count": len(result["mismatches"]),
        "hallucination_rate": round(rate, 1),
        "signal_warnings": signal_warnings,
        "health": "ok" if rate < 5 else ("warn" if rate < 10 else "critical"),
    }


def _check_llm_quality() -> tuple[bool, str]:
    """供 output_audit.py 调用的 DB 检查函数。
    扫描今日所有 LLM 产出文件，汇总校验结果。
    """
    today = date.today().isoformat()
    report_dir = PROJECT / "daily_review" / "reports"
    scan_files = [
        ("advice", report_dir / "advice" / f"advice_{today}.md"),
        ("zsxq_analysis", report_dir / "zsxq_analysis" / f"zsxq_analysis_{today}.md"),
        ("wechat_analysis", report_dir / "wechat_analysis" / f"wechat_analysis_{today}.md"),
        ("primary_synthesis", report_dir / "feeds" / f"primary_synthesis_{today}.md"),
    ]

    total_codes = 0
    total_unknown = 0
    file_results = []
    for fname, fpath in scan_files:
        if not fpath.exists():
            file_results.append(f"{fname}: 文件不存在")
            continue
        text = fpath.read_text(encoding="utf-8")
        audit = audit_llm_output(fname, text)
        total_codes += audit["total_codes"]
        total_unknown += audit["unknown_codes"]
        file_results.append(
            f"{fname}: {audit['valid_codes']}/{audit['total_codes']}有效"
            + (f" (未知:{audit['unknown_list'][:3]})" if audit["unknown_codes"] else "")
        )

    rate = total_unknown / max(total_codes, 1) * 100
    summary = f"LLM输出: {total_codes - total_unknown}/{total_codes}有效({rate:.1f}%幻觉)"
    detail = "; ".join(file_results)

    if rate > 5:
        return False, f"{summary} | {detail}"
    return True, f"{summary} (OK)"


if __name__ == "__main__":
    if "--audit-all" in sys.argv:
        ok, msg = _check_llm_quality()
        print(msg)
        sys.exit(0 if ok else 1)
    else:
        # 快速测试
        print("=== llm_validator 自检 ===")
        test_codes = ["000001", "600006", "999999", "abc123"]
        print(f"validate_codes({test_codes}):")
        result = validate_codes(test_codes)
        for code, info in result.items():
            status = "✅" if info["valid"] else "❌"
            print(f"  {status} {code}: {info['name']} ({info['industry']}) {info['note']}")

        print(f"\n_score_range test: [{{'delta': 15}}, {{'delta': -5}}, {{'delta': 0}}]")
        scores = [{"delta": 15}, {"delta": -5}, {"delta": 0}]
        validate_score_range(scores, "delta", -10, 10)
        for s in scores:
            clamped = " (clamped)" if s.get("_clamped") else ""
            print(f"  delta={s['delta']}{clamped}")
