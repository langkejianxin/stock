#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每小时把"带当前小时 token 的平台访问链接"发送到指定邮箱。

token = md5(私钥 + 当前小时)   例: md5(a + "2026091321")
链接形如: http://<公网地址>:<端口>/?token=<token>

配置: config/mail.conf(key=value), 私钥: config/token_secret.txt
用法: python3 send_token_mail.py            # 正常发送
      python3 send_token_mail.py --print    # 只打印链接, 不发信(调试)
"""
import hashlib
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage

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


def hour_key(dt=None):
    return (dt or datetime.now()).strftime("%Y%m%d%H")


def make_token(secret, dt=None):
    return hashlib.md5((secret + hour_key(dt)).encode("utf-8")).hexdigest()


def build_text(conf, token):
    host = conf.get("public_host", "127.0.0.1")
    port = conf.get("port", "8756")
    scheme = conf.get("scheme", "http")
    url = f"{scheme}://{host}:{port}/?token={token}"
    now = datetime.now()
    nxt = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    return (
        f"策略平台访问链接\n\n"
        f"    {url}\n\n"
        f"· 本链接有效期至 {nxt.strftime('%Y-%m-%d %H:00')}（{hour_key()} 这一小时）\n"
        f"· 下一个整点会自动收到新链接；旧链接随即失效\n"
        f"· 点击后 1 小时内可正常浏览（Cookie 已种下，页面内请求无需再带 token）\n"
        f"· 在服务器上通过 SSH 隧道访问（127.0.0.1）无需 token\n\n"
        f"— ETF 策略监控台 · {now.strftime('%Y-%m-%d %H:%M')}"
    )


def send(conf, subject, text):
    host = conf.get("smtp_host", "smtp.qq.com")
    port = int(conf.get("smtp_port", "465"))
    user = conf["user"]
    code = conf["auth_code"]
    to = conf.get("to", user)

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


def main():
    conf = load_conf()
    secret = load_secret()
    token = make_token(secret)
    text = build_text(conf, token)

    if "--print" in sys.argv:
        print(text)
        return 0

    subject = f"[策略平台] 访问链接 {datetime.now().strftime('%m-%d %H:00')}"
    ok, err = send(conf, subject, text)
    if ok:
        print(f"[ok] 已发送到 {conf.get('to')}  (token {token[:8]}…, {hour_key()})", flush=True)
        return 0
    print(f"[error] 发送失败: {err}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
