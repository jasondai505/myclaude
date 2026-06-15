"""Advice HTTP 服务 — 端口 8900，供外部模块拉取最新盘前建议。

用法:
    python daily_review/advice_server.py              # 前台启动
    python daily_review/advice_server.py --daemon     # 后台启动（覆盖旧进程）

端点:
    GET /              → 最新 advice 完整 Markdown
    GET /json          → JSON {date, time, content, stocks: [{code, name, track, ...}]}
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

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
ADVICE_DIR = BASE / "reports" / "advice"
PORT = 8900


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

        if path == "/json":
            advice_date = latest.stem.replace("advice_", "")
            stocks = _parse_stocks(content)
            # 去除不可打印字符，防止 JSON 编码失败
            clean_content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", content)
            self._send(200, json.dumps({
                "date": advice_date,
                "file": str(latest),
                "stocks": stocks,
            }, ensure_ascii=False, indent=2), "application/json")
        elif path == "/stocks":
            advice_date = latest.stem.replace("advice_", "")
            stocks = _parse_stocks(content)
            self._send(200, json.dumps({
                "date": advice_date, "stocks": stocks,
            }, ensure_ascii=False, indent=2), "application/json")
        elif path == "/full":
            self._send(200, content, "text/markdown; charset=utf-8")
        else:
            self._send(200, content, "text/markdown; charset=utf-8")

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
