#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每分钟检查一次收件箱: 只要收到来自"触发邮箱"的邮件, 就回复一个带 token 的访问链接。

工作方式:
  1. IMAP 登录(config/mail.conf 的 user/auth_code)
  2. 读状态文件 data/mail_check_state.json 里的 last_uid
  3. 找出 UID > last_uid 的新邮件, 看发件人是否 == trigger_from
  4. 命中 -> 发一封 10 分钟有效的链接邮件到 to
  5. 更新 last_uid(即使没命中也要前移, 避免重复扫描)

首次运行只会记录基线 UID(不回复历史邮件), 避免一上线就把旧邮件全部回一遍。

用法:
    python3 check_mail_and_reply.py            # 正常检查
    python3 check_mail_and_reply.py --init     # 强制重建基线(忽略历史邮件)
    python3 check_mail_and_reply.py --dry-run  # 只报告, 不发邮件、不写状态
"""
import email
import imaplib
import json
import os
import sys
from email.header import decode_header
from email.utils import parseaddr

import send_token_mail as stm
import token_lib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_FILE = os.path.join(ROOT, "data", "mail_check_state.json")
MAILBOX = "INBOX"


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(last_uid):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_uid": int(last_uid)}, f)


def decode(s):
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def max_uid(M):
    typ, data = M.uid("search", None, "ALL")
    uids = (data[0] or b"").split()
    return int(uids[-1]) if uids else 0


def new_uids(M, after):
    """返回 UID > after 的邮件 UID 列表。"""
    typ, data = M.uid("search", None, "UID", "%d:*" % (after + 1))
    raw = (data[0] or b"").split()
    return [int(u) for u in raw if int(u) > after]


def header_of(M, uid, fields="FROM SUBJECT DATE"):
    typ, data = M.uid("fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (%s)])" % fields)
    for part in data or []:
        if isinstance(part, tuple) and len(part) > 1:
            return email.message_from_bytes(part[1])
    return None


def main():
    dry = "--dry-run" in sys.argv
    init = "--init" in sys.argv

    conf = stm.load_conf()
    secret = stm.load_secret()
    trigger = [a.strip().lower() for a in
               conf.get("trigger_from", "1053075900@qq.com").split(",") if a.strip()]
    to_addr = conf.get("to", conf["user"])

    state = {} if init else load_state()
    last_uid = int(state.get("last_uid", 0))

    M = imaplib.IMAP4_SSL(conf.get("imap_host", "imap.qq.com"),
                          int(conf.get("imap_port", "993")))
    try:
        M.login(conf["user"], conf["auth_code"])
        M.select(MAILBOX)
        mx = max_uid(M)

        if last_uid == 0:
            if not dry:
                save_state(mx)
            print(f"[init] 建立基线 last_uid={mx}(不回复已有邮件)", flush=True)
            return 0

        if mx <= last_uid:
            print(f"[idle] 无新邮件 (last_uid={last_uid})", flush=True)
            return 0

        uids = new_uids(M, last_uid)
        print(f"[scan] 新邮件 {len(uids)} 封: {uids}", flush=True)
        hits = []
        for uid in uids:
            msg = header_of(M, uid)
            if msg is None:
                continue
            frm = parseaddr(msg.get("From", ""))[1].lower()
            subj = decode(msg.get("Subject", ""))
            date = msg.get("Date", "")
            if frm in trigger:
                hits.append((uid, frm, subj))
                print(f"[hit ] UID={uid} 来自={frm} 主题={subj!r} 时间={date}", flush=True)
            else:
                print(f"[skip] UID={uid} 来自={frm} 主题={subj!r}", flush=True)

        if hits:
            if dry:
                print(f"[dry ] 命中 {len(hits)} 封, 本应发送链接到 {to_addr}", flush=True)
            else:
                ok, _ = stm.send_link(conf, secret, reason=f" [触发: UID={hits[0][0]}]")
                if not ok:
                    print("[error] 链接发送失败, 状态不前移(下轮重试)", flush=True)
                    return 1
        if not dry:
            save_state(mx)
            print(f"[done] last_uid -> {mx}", flush=True)
        return 0
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
