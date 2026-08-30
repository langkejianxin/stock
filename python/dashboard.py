# -*- coding: utf-8 -*-
"""
ETF 策略监控台 —— 本地 Web 服务
  - 实时计算两个策略(动量20日轮动 / 滚500·95%分位通道)的最新收益率与信号
  - 数据来自本地 *_基金净值.csv(每日由 fetch_nav.py 更新)
  - 零第三方依赖(标准库 http.server + 本地 ECharts)

启动: /opt/homebrew/bin/python3.9 dashboard.py [端口]   默认 8756
访问: http://127.0.0.1:8756
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import strategy_lib

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8756
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_TTL = 60  # 秒; 数据每天才变一次, 60s 缓存足够

_cache = {"ts": 0.0, "data": None}


def build_data():
    t0 = time.time()
    r1 = strategy_lib.momentum_result()
    r2 = strategy_lib.channel_result()
    return {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "computed_ms": int((time.time() - t0) * 1000),
        "strategy1": r1,
        "strategy2": r2,
    }


def get_data(force=False):
    now = time.time()
    if force or now - _cache["ts"] > CACHE_TTL:
        _cache["data"] = build_data()
        _cache["ts"] = now
    return _cache["data"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 精简日志
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/data":
            force = "refresh=1" in self.path
            body = json.dumps(get_data(force), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/echarts.min.js":
            with open(os.path.join(HERE, "echarts.min.js"), "rb") as f:
                self._send(200, f.read(), "application/javascript")
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"ETF 策略监控台已启动: http://127.0.0.1:{PORT}  (Ctrl+C 停止)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)


if __name__ == "__main__":
    main()
