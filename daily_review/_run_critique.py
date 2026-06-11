"""复盘后自动批判 — 读取今日复盘报告，调用 Claude 做五维压力测试。
用法：python daily_review/_run_critique.py [--date 2026-06-11]
"""
import argparse, json, os, sys
from datetime import date, datetime
from pathlib import Path

from anthropic import Anthropic

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MODEL = "claude-sonnet-4-6-20250514"
REPORT_DIR = BASE / "reports"
CRITIQUE_DIR = BASE / "critiques"
CRITIQUE_DIR.mkdir(parents=True, exist_ok=True)


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if key:
        return key
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            key = data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        except (json.JSONDecodeError, OSError):
            pass
    return key


def _read_report(trade_date: str) -> str | None:
    path = REPORT_DIR / f"review_{trade_date}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _theme_context() -> str:
    try:
        import store
        levels = store.load_theme_levels()
        if not levels:
            return "（无主题数据）"
        lines = ["| 主题 | 级别 | 连续天数 |", "|------|------|---------|"]
        for theme, info in sorted(levels.items(), key=lambda x: -x[1].get("level", 0)):
            lv = info.get("level", 1)
            days = info.get("consecutive_days", 0)
            lines.append(f"| {theme} | {lv} | {days} |")
        return "\n".join(lines)
    except Exception as e:
        return f"（主题数据获取失败: {e}）"


def _watchlist_context() -> str:
    try:
        import data
        from config import WATCHLIST
        quotes = data.fetch_batch_quotes(WATCHLIST)
        if not quotes:
            return "（无行情数据）"
        lines = ["| 代码 | 名称 | 涨跌幅 | 成交额(亿) | PE | PB |",
                 "|------|------|-------:|-----------:|----:|----:|"]
        for code in WATCHLIST:
            q = quotes.get(code)
            if not q:
                continue
            lines.append(
                f"| {code} | {q.get('name','')} | {q.get('change_pct',0):+.1f}% "
                f"| {q.get('amount_wan',0)/10000:.1f} "
                f"| {q.get('pe','-')} | {q.get('pb','-')} |"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"（行情数据获取失败: {e}）"


def build_prompt(report: str, trade_date: str) -> str:
    theme_ctx = _theme_context()
    stock_ctx = _watchlist_context()

    return f"""# 事实辩论 · 复盘压力测试

你是严苛的投委会评审委员。以下是一份 A 股每日复盘报告。你的唯一任务：**找出报告中的所有漏洞，让它站不住脚**。

## 分析框架：五维质疑

按以下五个维度逐层攻击。每个维度必须给出具体质疑，不允许泛泛而谈。

### 第一维：论点还原
先精炼报告的核心判断 1-3 句。暴露其中的模糊之处和未言明假设。

### 第二维：事实检验
逐条核查支撑数据。对涉及的每个事实性陈述检验：数字是否准确？来源是否可靠？换时间窗口结论还成立吗？

### 第三维：逻辑漏洞
识别论证链条中的因果谬误：相关性≠因果、线性外推、遗漏变量、幸存者偏差、后此谬误。

### 第四维：反向证据
主动寻找与报告观点相悖的证据：市场数据不支持的地方、历史对照、反对者核心论点、当前股价已定价多少。

### 第五维：风险情景
列出使报告核心判断完全失效的关键条件变化，含触发条件/影响程度/预警信号。

## 规则

- 不编造任何数字——没有数据就说「待验证」
- 质疑要具体：不说「逻辑有问题」，说「你把相关性当成了因果性，具体来说……」
- 总结部分列出用户可以核实的可操作问题，不是哲学辩论题
- 不给投资建议

## 复盘报告

日期：{trade_date}

{report[:12000]}

## 附加数据

### 主题排名（含连续天数）
{theme_ctx}

### 自选股当日表现
{stock_ctx}

---

请按以下模板输出：

# 事实辩论 · {trade_date} 复盘压力测试

## 论点还原
> 核心主张 + 隐含假设

## 一、事实检验
| # | 事实陈述 | 检验结果 | 问题 |

## 二、逻辑漏洞
### 漏洞 1：[名称]
- **问题**：
- **影响**：

## 三、反向证据

## 四、风险情景
| # | 风险情景 | 触发条件 | 影响程度 | 预警信号 |

## 总结：需要回答的 N 个问题
1. ...
2. ...

> ⚠️ 本报告目的为压力测试，不构成对观点的最终判断。"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    trade_date = args.date

    report = _read_report(trade_date)
    if not report:
        print(f"复盘报告不存在: reports/review_{trade_date}.md")
        sys.exit(1)

    prompt = build_prompt(report, trade_date)
    output_path = CRITIQUE_DIR / f"{trade_date}.md"

    api_key = _load_api_key()
    if not api_key:
        print("[ERROR] 未找到 API key")
        sys.exit(1)

    print(f"调用 Claude 生成批判报告 (date={trade_date})...")
    try:
        client = Anthropic(
            api_key=api_key,
            base_url="https://api.deepseek.com/anthropic",
        )
        resp = client.messages.create(
            model=MODEL,
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
            timeout=300,
        )
        parts = []
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                parts.append(block.text)
        output = "\n".join(parts)
    except Exception as e:
        output = f"[ERROR] LLM 调用失败: {e}"

    output_path.write_text(output, encoding="utf-8")
    print(f"批判报告已保存: critiques/{trade_date}.md")


if __name__ == "__main__":
    main()
