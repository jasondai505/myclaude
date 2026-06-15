"""Advice HTTP 服务 — 端口 8900，供外部模块拉取最新盘前建议。

用法:
    python daily_review/advice_server.py              # 前台启动
    python daily_review/advice_server.py --daemon     # 后台启动（覆盖旧进程）

端点:
    GET /              → 最新 advice 网页 (Markdown→HTML)
    GET /md            → 原始 Markdown
    GET /json          → JSON {date, stocks: [{code, name, track, fev, delta, fevd}]}
    GET /health        → {"status": "ok", "last_update": "..."}
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import markdown

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
ADVICE_DIR = BASE / "reports" / "advice"
PORT = 8900

HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>盘前建议 {date}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 1200px; margin: 0 auto; padding: 20px;
         background: #0d1117; color: #c9d1d9; line-height: 1.6; }}
  h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
  h2 {{ color: #f0883e; margin-top: 32px; }}
  h3 {{ color: #d2a8ff; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
  th {{ background: #161b22; color: #8b949e; padding: 8px 10px; text-align: center;
        border: 1px solid #30363d; }}
  td {{ padding: 8px 10px; border: 1px solid #30363d; }}
  tr:nth-child(even) {{ background: #161b22; }}
  code {{ background: #161b22; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
  pre {{ background: #161b22; padding: 12px; border-radius: 6px; overflow-x: auto; }}
  blockquote {{ border-left: 3px solid #58a6ff; margin: 12px 0; padding: 8px 16px;
                background: #161b22; color: #8b949e; }}
  a {{ color: #58a6ff; }}
  details {{ margin: 10px 0; }}
  summary {{ cursor: pointer; color: #8b949e; }}
  .header {{ text-align: center; padding: 16px; background: #161b22; border-radius: 8px;
             margin-bottom: 24px; }}
  .header p {{ color: #8b949e; margin: 4px 0; }}
  .footer {{ text-align: center; color: #484f58; font-size: 12px; margin-top: 40px;
             border-top: 1px solid #30363d; padding-top: 16px; }}
</style>
</head>
<body>
<div class="header">
  <p>📡 盘前建议 · 自动生成 · 每日 08:30 前更新</p>
  <p>API: <a href="/json">/json</a> · <a href="/md">/md</a> · <a href="/health">/health</a></p>
</div>
{body}
<div class="footer">盘前建议 {date} · 数据来源: 星球/公众号/韭研/公告/新闻 · 仅供研究参考不构成投资建议</div>
</body>
</html>"""


def _latest_advice() -> Path | None:
    """返回最新的 advice 文件路径"""
    if not ADVICE_DIR.exists():
        return None
    files = sorted(ADVICE_DIR.glob("advice_*.md"), reverse=True)
    return files[0] if files else None


def _parse_stocks(content: str) -> list[dict]:
    """从 advice Markdown 中解析精选标的"""
    stocks = []
    in_table = False
    for line in content.split("\n"):
        if "精选标的" in line and "🎯" in line:
            in_table = True
            continue
        if in_table:
            if line.startswith("> 候选池") or line.startswith("<details>"):
                break
            m = re.search(r"\*\*(.+?)\((\d{6})\)\*\*", line)
            if m:
                name, code = m.group(1).strip(), m.group(2)
                cols = line.split("|")
                track = cols[3].strip() if len(cols) > 3 else ""
                fev = cols[4].strip() if len(cols) > 4 else ""
                delta = cols[5].strip() if len(cols) > 5 else ""
                fevd = cols[6].strip().strip("*") if len(cols) > 6 else ""
                stocks.append({
                    "code": code, "name": name, "track": track,
                    "fev": fev, "delta": delta, "fevd": fevd,
                })
    return stocks


class AdviceHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, content: str, content_type: str = "text/plain; charset=utf-8"):
        content_bytes = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(content_bytes))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content_bytes)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/health":
            f = _latest_advice()
            self._send(200, json.dumps({
                "status": "ok",
                "last_update": f.stem.replace("advice_", "") if f else "none",
                "file": str(f) if f else None,
            }, ensure_ascii=False, indent=2), "application/json")
            return

        latest = _latest_advice()
        if not latest:
            self._send(503, "暂无 advice 数据")
            return

        try:
            content = latest.read_text(encoding="utf-8")
        except Exception as e:
            self._send(500, f"读取失败: {e}")
            return

        advice_date = latest.stem.replace("advice_", "")

        if path == "/json" or path == "/stocks":
            stocks = _parse_stocks(content)
            self._send(200, json.dumps({
                "date": advice_date, "stocks": stocks,
            }, ensure_ascii=False, indent=2), "application/json")
        elif path == "/md":
            self._send(200, content, "text/markdown; charset=utf-8")
        else:
            # / → HTML 网页
            clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", content)
            html_body = markdown.markdown(clean, extensions=["tables", "fenced_code"])
            html = HTML_TPL.format(date=advice_date, body=html_body)
            self._send(200, html, "text/html; charset=utf-8")

    def log_message(self, format, *args):
        print(f"  [{self.log_date_time_string()}] {args[0]}")


def _is_running(port: int = PORT) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def main():
    daemon = "--daemon" in sys.argv
    if daemon:
        # 后台模式：如果已在运行则 kill 旧进程，启动新进程
        import subprocess
        if _is_running():
            print(f"  端口 {PORT} 已占用，尝试停止旧进程...")
            # Windows: 查找并杀掉占用端口的进程
            try:
                result = subprocess.run(
                    f'netstat -ano | findstr :{PORT} | findstr LISTENING',
                    shell=True, capture_output=True, text=True, timeout=5)
                if result.stdout:
                    pid = result.stdout.strip().split()[-1]
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True,
                                   capture_output=True, timeout=5)
                    time.sleep(1)
            except Exception:
                pass

        # 后台启动
        script = Path(__file__).resolve()
        subprocess.Popen(
            [sys.executable, str(script)],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print(f"  advice_server 已在后台启动 (端口 {PORT})")
        return

    server = HTTPServer(("0.0.0.0", PORT), AdviceHandler)
    latest = _latest_advice()
    print(f"Advice HTTP 服务已启动 → http://localhost:{PORT}")
    print(f"  最新建议: {latest.stem if latest else '无'}")
    print(f"  端点: /  /json  /health")
    print(f"  Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
