#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发送"带访问 token 的链接"邮件。

token 规则见 token_lib.py:  "<ts>-<md5(私钥+ts)>", 链接 **10 分钟内**有效。

本模块既可作为库被 check_mail_and_reply.py 调用, 也可单独运行(手动补发一封):
    python3 send_token_mail.py            # 直接发一封
    python3 send_token_mail.py --print    # 只打印链接, 不发信

配置: config/mail.conf, 私钥: config/token_secret.txt
"""
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.message import EmailMessage

import token_lib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONF_FILE = os.path.join(ROOT, "config", "mail.conf")
SECRET_FILE = os.path.join(ROOT, "config", "token_secret.txt")


def load_conf(path=CONF_FILE):
    conf = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip()
    return conf


def load_secret(path=SECRET_FILE):
    with open(path, encoding="utf-8") as f:
        s = f.read().strip()
    if not s:
        raise RuntimeError("私钥为空: %s" % path)
    return s


def build_url(conf, token):
    host = conf.get("public_host", "127.0.0.1")
    port = conf.get("port", "8756")
    scheme = conf.get("scheme", "http")
    return f"{scheme}://{host}:{port}/?token={token}"


def build_text(conf, token, ttl_min=None):
    """邮件正文。"""
    ttl_min = ttl_min or int(token_lib.TTL / 60)
    url = build_url(conf, token)
    cookie_min = int(token_lib.COOKIE_TTL / 60)
    return (
        f"策略平台访问链接（{ttl_min} 分钟内有效）\n\n"
        f"    {url}\n\n"
        f"· 请在 {ttl_min} 分钟内点击打开，过期后链接失效（需重新发邮件索取）\n"
        f"· 打开后可正常浏览约 {cookie_min} 分钟（浏览器已种 Cookie，超时后需重新索取）\n"
        f"· 想再要一个链接：用 1053075900@qq.com 给本邮箱发一封邮件即可（自动回复）\n"
        f"· 在服务器上通过 SSH 隧道访问（127.0.0.1）无需 token\n\n"
        f"— ETF 策略监控台 · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def send(conf, subject, text, to=None):
    """通过 SMTP 发信。返回 (是否成功, 错误)。"""
    host = conf.get("smtp_host", "smtp.qq.com")
    port = int(conf.get("smtp_port", "465"))
    user = conf["user"]
    code = conf["auth_code"]
    to = to or conf.get("to", user)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(text)

    ctx = ssl.create_default_context()
    last = None
    for attempt in range(1, 4):
        try:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=25) as s:
                s.login(user, code)
                s.send_message(msg)
            return True, None
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"[warn] 第{attempt}次发送失败: {exc}", flush=True)
            time.sleep(3)
    return False, last


def send_link(conf, secret, reason=""):
    """生成 token 并发一封链接邮件。返回 (是否成功, token)。"""
    token = token_lib.make_token(secret)
    ttl_min = int(token_lib.TTL / 60)
    subject = f"[策略平台] 访问链接（{ttl_min}分钟内有效）{datetime.now().strftime('%m-%d %H:%M')}"
    ok, err = send(conf, subject, build_text(conf, token))
    if ok:
        print(f"[ok] 链接已发送到 {conf.get('to')} (token {token[:24]}…){reason}", flush=True)
    else:
        print(f"[error] 发送失败: {err}", flush=True)
    return ok, token


def main():
    conf = load_conf()
    secret = load_secret()

    if "--print" in sys.argv:
        token = token_lib.make_token(secret)
        print(build_text(conf, token))
        print(f"\n(校验: {token_lib.verify(secret, token)})")
        return 0

    ok, _ = send_link(conf, secret, reason=" [手动]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
