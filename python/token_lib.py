# -*- coding: utf-8 -*-
"""访问 token 的生成与校验(供 dashboard.py / check_mail_and_reply.py 共用)。

token = "<ts>-<md5(私钥 + ts)>"
    ts      = 生成时刻的 Unix 时间戳(秒)
    有效期  = TTL 秒(默认 600 = 10 分钟)

校验通过后 dashboard 会种 Cookie(浏览会话), 时长 COOKIE_TTL(默认 600 = 10 分钟,
与链接有效期一致): 即"点开链接后再浏览 10 分钟", 超时需重新发邮件索取新链接。
"""
import hashlib
import hmac
import time

TTL = 600            # 链接(token)有效期: 10 分钟
COOKIE_TTL = 600     # 点击链接后的浏览会话时长: 10 分钟
CLOCK_SKEW = 60      # 允许的时钟偏差(秒)


def make_token(secret, ts=None):
    """生成 token。ts 省略则用当前时间。"""
    ts = int(ts if ts is not None else time.time())
    sig = hashlib.md5((secret + str(ts)).encode("utf-8")).hexdigest()
    return "%d-%s" % (ts, sig)


def verify(secret, tok, ttl=TTL, now=None):
    """校验 token: 签名正确 + 未过期(且不是来自未来)。恒定时间比较。"""
    if not secret or not tok or "-" not in str(tok):
        return False
    ts_s, _, sig = str(tok).partition("-")
    try:
        ts = int(ts_s)
    except ValueError:
        return False
    now = now if now is not None else time.time()
    if ts > now + CLOCK_SKEW:          # 未来时间
        return False
    if now - ts > ttl:                 # 已过期
        return False
    expect = hashlib.md5((secret + ts_s).encode("utf-8")).hexdigest()
    return hmac.compare_digest(sig, expect)


def age(tok, now=None):
    """token 已存在多少秒(用于日志); 无法解析返回 None。"""
    try:
        ts = int(str(tok).partition("-")[0])
    except ValueError:
        return None
    return (now if now is not None else time.time()) - ts


if __name__ == "__main__":
    import sys
    s = sys.argv[1] if len(sys.argv) > 1 else "demo-secret"
    t = make_token(s)
    print("secret :", s)
    print("token  :", t)
    print("verify :", verify(s, t))
    print("过期后 :", verify(s, t, ttl=-1))
