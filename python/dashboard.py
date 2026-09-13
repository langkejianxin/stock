# -*- coding: utf-8 -*-
"""
ETF 策略监控台 —— 本地/远程 Web 服务

访问控制(两层, 依次判定):
  1) 127.0.0.1 / ::1  →  免认证(服务器本机、SSH 隧道)
  2) 否则必须带 token →  公网访问需要 ?token=xxxx 或 Cookie

  token 规则(见 token_lib.py): token = "<ts>-<md5(私钥 + ts)>"
     - ts = 生成时刻的 Unix 时间戳; 链接默认 **10 分钟内**有效
     - 私钥存于 config/token_secret.txt(自动生成, 权限 600)
     - 校验通过后写入 Cookie(浏览会话, 默认 10 分钟),
       这样页面内部的 /api/data、/echarts.min.js 等请求无需重复带 token
     - 校验失败一律【静默拒绝】(空 404 / 直接断连, 不提示、不暴露机制)
     - 链接按需索取: 给收件邮箱发一封邮件, check_mail_and_reply.py 每分钟检查并回复

启动: python3 dashboard.py [端口]               默认 8756
      STOCK_BIND=0.0.0.0 python3 dashboard.py  # 监听所有网卡(远程部署)
访问(本机):   http://127.0.0.1:8756
访问(公网):   http://<host>:8756/?token=<当前小时token>
"""
import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fetch_nav
import strategy_lib
import token_lib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # 项目根(含 data/ config/)
SECRET_FILE = os.path.join(ROOT, "config", "token_secret.txt")

_cli = [a for a in sys.argv[1:] if not a.startswith("--")]   # 忽略 --gen-secret 等开关
PORT = int(_cli[0]) if _cli else int(os.environ.get("STOCK_PORT", "8756"))
BIND = os.environ.get("STOCK_BIND", "127.0.0.1")    # 远程部署设为 0.0.0.0
CACHE_TTL = 60  # 秒; 数据每天才变一次, 60s 缓存足够

ALWAYS_ALLOW = {"127.0.0.1", "::1", "localhost"}   # 本机/隧道/反代(免认证)

COOKIE_NAME = "st"

_cache = {"ts": 0.0, "data": None}


# ==================== token 认证 ====================
def load_secret():
    """读取私钥 a(config/token_secret.txt); 不存在则返回 None(此时公网一律拒绝)。"""
    try:
        with open(SECRET_FILE, encoding="utf-8") as f:
            s = f.read().strip()
            return s or None
    except OSError:
        return None


SECRET = load_secret()          # 启动时加载一次(改私钥需重启服务)


def verify_token(tok):
    """校验访问 token(见 token_lib): "<ts>-<md5(私钥+ts)>", 默认 10 分钟有效。"""
    return token_lib.verify(SECRET, tok)


def cookie_max_age():
    """点击链接后 Cookie 的存活秒数(浏览会话时长)。"""
    return token_lib.COOKIE_TTL


# ==================== 客户端 IP ====================
def client_ip(handler):
    """取真实客户端 IP。
    直连来源是本机(127.0.0.1)时, 若带 X-Real-IP / X-Forwarded-For(反向代理注入),
    则以该头为准 —— 这样"经反代进来"的请求不会被误判成本机而免认证。
    头只在这两种情况下才被信任(外部直连伪造无效, 因为那时 peer 不是本机)。"""
    peer = handler.client_address[0]
    if peer in ALWAYS_ALLOW:
        xr = handler.headers.get("X-Real-IP") or handler.headers.get("X-Forwarded-For") or ""
        if xr:
            return xr.split(",")[0].strip()
    return peer


# 非授权访问的拒绝方式(不泄露任何信息):
#   "404"  返回空 body 的 404, 看起来像该路径不存在(默认)
#   "drop" 直接断开连接, 不给任何 HTTP 响应
DENY_MODE = os.environ.get("STOCK_DENY_MODE", "404").lower()


def cookie_value(header, name):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def authenticate(handler):
    """认证。返回 (是否通过, 来自 URL 的 token 或 None, 客户端 IP)。"""
    ip = client_ip(handler)
    # 1) 本机 / SSH 隧道 -> 免认证
    if ip in ALWAYS_ALLOW:
        return True, None, ip
    # 2) 公网: 必须带 token(优先 URL 参数, 其次 Cookie)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
    tok = (qs.get("token") or [None])[0]
    from_url = tok
    if not tok:
        tok = cookie_value(handler.headers.get("Cookie"), COOKIE_NAME)
    if verify_token(tok):
        return True, from_url, ip
    return False, None, ip


# ==================== 数据 ====================
def build_data():
    t0 = time.time()
    r1 = strategy_lib.consensus_result()
    r2 = strategy_lib.channel_result()
    return {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "computed_ms": int((time.time() - t0) * 1000),
        "strategy1": r1,
        "strategy2": r2,
        "annual": strategy_lib.annual_returns(r1, r2),   # 每年度: 当年日收益相加(不复利)
    }


def get_data(force=False):
    now = time.time()
    if force or now - _cache["ts"] > CACHE_TTL:
        _cache["data"] = build_data()
        _cache["ts"] = now
    return _cache["data"]


def do_refresh():
    """增量抓取基金净值(两策略均按累计净值计算), 返回每只的抓取结果。"""
    results = []
    for code in fetch_nav.FUND_CODES:
        try:
            added, latest = fetch_nav.update_fund(code)
            results.append({"code": code, "added": added, "latest": latest})
        except Exception as exc:  # noqa: BLE001
            results.append({"code": code, "error": str(exc)})
    return results


class Handler(BaseHTTPRequestHandler):
    # 用 HTTP/1.1(浏览器友好), 显式关闭连接; 不暴露服务端身份
    protocol_version = "HTTP/1.1"
    server_version = ""
    sys_version = ""

    _KNOWN = ("/", "/index.html", "/api/data", "/api/refresh",
              "/echarts.min.js", "/favicon.ico", "/healthz")

    def version_string(self):
        return ""

    def send_response(self, code, message=None):
        """覆盖默认实现: 只发状态行 + Date, 不发 Server 头。"""
        self.log_request(code)
        self.send_response_only(code, message)
        self.send_header("Date", self.date_time_string())

    def log_message(self, fmt, *args):  # 服务端日志(客户端看不到), 带来源 IP
        sys.stderr.write("[%s] %s %s\n" % (self.log_date_time_string(),
                                           self.client_address[0], fmt % args))

    def _send(self, code, body, ctype, extra_headers=None):
        self.send_response(code)
        # 即使 body 为空也发 Content-Type + inline: 否则浏览器可能把无类型响应当文件下载
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", "inline")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _deny(self):
        """未授权: 静默拒绝, 不给任何提示(不暴露认证机制、不回显 IP)。"""
        if DENY_MODE == "drop":
            self.close_connection = True          # 直接断开, 无任何响应
            return
        self._send(404, b"", "text/plain")        # 空 404, 看起来像路径不存在

    def _auth(self):
        """认证; 通过则返回 (True, 附加响应头), 否则静默拒绝并返回 (False, None)。"""
        ok, url_tok, ip = authenticate(self)
        if not ok:
            sys.stderr.write("[deny] %s\n" % ip)  # 仅服务端记录
            self._deny()
            return False, None
        extra = []
        if url_tok:
            # URL 带 token 访问 -> 种 Cookie(到本小时结束), 页面内请求免重复带 token
            extra.append(("Set-Cookie",
                          f"{COOKIE_NAME}={url_tok}; Path=/; Max-Age={cookie_max_age()}; "
                          f"HttpOnly; SameSite=Lax"))
        return True, extra

    def do_GET(self):
        ok, extra = self._auth()
        if not ok:
            return

        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8", extra)
        elif path == "/api/data":
            force = "refresh=1" in self.path
            body = json.dumps(get_data(force), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", extra)
        elif path == "/api/refresh":
            # 先增量抓取最新净值, 再强制重算并返回(供页面"抓取并刷新"按钮调用)
            fetched = do_refresh()
            data = dict(get_data(force=True))
            data["fetched"] = fetched
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", extra)
        elif path == "/echarts.min.js":
            with open(os.path.join(HERE, "echarts.min.js"), "rb") as f:
                self._send(200, f.read(), "application/javascript", extra)
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon", extra)
        elif path == "/healthz":
            self._send(200, b"ok", "text/plain", extra)
        else:
            self._send(404, b"", "text/plain", extra)   # 同样空 body

    # ---- 其他方法: 同样先认证, 拒绝时静默 ----
    def do_HEAD(self):
        ok, extra = self._auth()
        if not ok:
            return
        path = self.path.split("?")[0]
        self._send(200 if path in self._KNOWN else 404, b"", "text/plain", extra)

    def _other(self):
        ok, extra = self._auth()
        if not ok:
            return
        self._send(404, b"", "text/plain", extra)

    do_POST = do_PUT = do_DELETE = do_OPTIONS = _other


def main():
    if SECRET is None:
        print(f"[warn] 未找到私钥 {SECRET_FILE} -> 公网 token 认证不可用(仅本机可访问)。"
              f"运行 `python3 dashboard.py --gen-secret` 生成。", flush=True)
    else:
        print(f"[ok] token 认证已启用: 127.0.0.1 免认证, 公网需 ?token=<ts-md5(私钥+ts)> "
              f"(有效期 {int(token_lib.TTL/60)} 分钟)", flush=True)
        print(f"[ok] 示例 token: {token_lib.make_token(SECRET)}", flush=True)
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"ETF 策略监控台已启动: http://{BIND}:{PORT}  (Ctrl+C 停止)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)


if __name__ == "__main__":
    if "--gen-secret" in sys.argv:
        made = gen_secret()
        print("已生成私钥" if made else "私钥已存在", SECRET_FILE)
        sys.exit(0)
    main()
