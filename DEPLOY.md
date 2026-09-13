# ETF 策略监控台 · 远程部署说明

> 服务器：`root@8.138.182.173`（阿里云 Alibaba Cloud Linux 3）
> 部署时间：2026-09-13（同日改为「按需索取链接」模式）
> 访问地址：**http://8.138.182.173:8756**

---

## 1. 怎么访问（本机免认证 + 公网 token）

| 访问来源 | 要求 |
|---|---|
| **`127.0.0.1`**（服务器本机 / SSH 隧道直连） | **免认证**，直接打开 |
| **公网 IP / 域名** | **必须带 token**：`http://8.138.182.173:8756/?token=<ts>-<签名>` |

### 日常用法：发一封邮件，换一个链接（不再有定时垃圾邮件）

```
你：用 1053075900@qq.com 给 3453714269@qq.com 发一封邮件（内容随便）
        ↓  （服务器每分钟检查一次收件箱）
平台：自动回一封邮件到 1053075900@qq.com，里面是带 token 的链接
        ↓
你：点开链接 → 正常浏览（10 分钟内需点开，点开后可用约 2 小时）
```

要点：

- **链接 10 分钟内有效**（过了就点不开，重新发一封邮件再要一个即可）
- 点开后种 Cookie，浏览会话约 **2 小时**，期间页面内请求无需再带 token
- 只有来自 **`trigger_from`**（默认 `1053075900@qq.com`）的邮件才会触发，其他邮件忽略
- **上线时只记录基线**，不会把历史邮件全部回一遍；之后每封新邮件都会回应一次

```bash
# 想看当前链接(不发信, 直接打印)
ssh root@8.138.182.173 '/usr/bin/python3.8 /opt/stock/python/send_token_mail.py --print'

# 手动补发一封链接邮件
ssh root@8.138.182.173 '/usr/bin/python3.8 /opt/stock/python/send_token_mail.py'

# 手动跑一次"检查邮件"逻辑
ssh root@8.138.182.173 '/usr/bin/python3.8 /opt/stock/python/check_mail_and_reply.py'

# 看检查日志
ssh root@8.138.182.173 'tail -20 /opt/stock/logs/mail.log'
```

**未授权访问看到什么**：取决于 `STOCK_DENY_MODE`（当前服务器设为 `drop`）
→ **直接断开 TCP 连接**，连 HTTP 响应都不给；默认模式则是空 `404`（零字节、无提示、无 `Server` 头）。

---

## 2. 服务器上的目录结构

```
/opt/stock/
├── python/
│   ├── dashboard.py            # Web 服务（token 认证, 监听 0.0.0.0:8756）
│   ├── token_lib.py            # token 生成/校验（10 分钟 TTL, Cookie 2 小时）
│   ├── send_token_mail.py      # 生成并发送"带链接"的邮件（也可手动运行）
│   ├── check_mail_and_reply.py # ★ 每分钟检查收件箱, 命中触发邮箱就回链接
│   ├── dashboard.html          # 前端页面（ECharts 本地化）
│   ├── strategy_lib.py         # 策略1 共识+确认2周 / 策略2 滚动通道
│   ├── fetch_nav.py            # 基金净值增量抓取（东方财富 F10）
│   ├── daily_signal.py         # 今日信号生成
│   ├── run_daily.sh            # 每日管线（抓净值 → 出信号）
│   └── echarts.min.js
├── data/                       # 4 只基金净值 CSV + mail_check_state.json(邮件检查水位)
├── config/
│   ├── mail.conf               # ★ 邮箱/SMTP/IMAP/触发邮箱 (600, 含授权码, 不入库)
│   ├── mail.conf.example       # 配置模板(可入库)
│   └── token_secret.txt        # ★ token 私钥 (600, 不入库)
├── logs/                       # dashboard.log / daily.log / mail.log
├── output/                     # 今日信号.txt
└── systemd/                    # 单元文件备份(已安装到 /etc/systemd/system/)
```

Python 环境：**/usr/bin/python3.8** + pandas 2.0.3 + numpy 1.24.4
（服务器自带 python3.6 太旧；已通过 `dnf module install python38` 安装，pip 走阿里云镜像）

---

## 3. token 与按需邮件

### 3.1 token 规则

```
token = "<ts>-<md5(私钥 + ts)>"
    ts  = 生成时刻的 Unix 时间戳(秒)
    有效期 = 10 分钟（超时即失效）
    Cookie = 点开后 2 小时（浏览会话）
```

- 私钥：`config/token_secret.txt`（自动生成，权限 600）
- 校验用恒定时间比较（防时序攻击）；带未来时间戳的 token 一律拒绝
- 私钥泄露 = 可伪造 token。更换私钥（会使所有旧链接立即作废）：

```bash
ssh root@8.138.182.173 'rm /opt/stock/config/token_secret.txt && \
  cd /opt/stock/python && /usr/bin/python3.8 dashboard.py --gen-secret && \
  systemctl restart stock-dashboard.service'
```

可在 `python/token_lib.py` 顶部调整 `TTL`（链接有效期）与 `COOKIE_TTL`（会话时长）。

### 3.2 按需触发（stock-mail-check.timer）

| 项 | 值 |
|---|---|
| 频率 | **每分钟**（`OnCalendar=*:*:00`） |
| 收件 | IMAP `imap.qq.com:993`，账号 `3453714269@qq.com` |
| 触发条件 | 新邮件的发件人 ∈ `trigger_from`（默认 `1053075900@qq.com`） |
| 动作 | 回一封带 token 链接的邮件到 `to` |
| 水位记录 | `data/mail_check_state.json` 里的 `last_uid`（避免重复处理） |
| 日志 | `/opt/stock/logs/mail.log` |

```bash
systemctl list-timers stock-mail-check.timer       # 看下次检查时间
systemctl start stock-mail-check.service           # 立即检查一次
journalctl -u stock-mail-check.service -n 20       # 看执行历史
tail -20 /opt/stock/logs/mail.log                  # 看业务日志
```

**首次基线**：如果 `mail_check_state.json` 不存在，脚本只记录当前最大 UID 并退出
（不回复任何历史邮件）。想重建基线：`check_mail_and_reply.py --init`。

邮件里的链接地址由 `config/mail.conf` 的 `public_host` / `port` / `scheme` 决定 ——
**以后有域名了改这几行即可**（`public_host = stock.example.com`）。

### 3.3 换发件邮箱 / 多个触发邮箱

编辑 `config/mail.conf`（改完立即生效，无需重启服务）：

```ini
user = 新发件邮箱@qq.com
auth_code = 新授权码
imap_host = imap.qq.com
trigger_from = 1053075900@qq.com,another@qq.com   # 逗号分隔可多个
to = 1053075900@qq.com
```

> ⚠️ QQ 邮箱的 **IMAP 与 SMTP 用的是同一个授权码**，需在 QQ 邮箱设置里把
> 「POP3/IMAP/SMTP 服务」都开启并生成授权码。

---

## 4. 服务管理（systemd）

| 单元 | 作用 |
|---|---|
| `stock-dashboard.service` | 平台 Web 服务（开机自启、崩溃自愈） |
| `stock-mail-check.timer` | **每分钟**检查触发邮件 → 回访问链接 |
| `stock-nav-daily.timer` | **周一~周五 20:30** 抓净值 + 出信号 |

```bash
systemctl status stock-dashboard.service
systemctl list-timers --all | grep stock

systemctl restart stock-dashboard.service
systemctl start   stock-mail-check.service     # 立即检查邮件
systemctl start   stock-nav-daily.service      # 立即抓净值+出信号

tail -f /opt/stock/logs/dashboard.log
tail -f /opt/stock/logs/mail.log
```

> 定时器用的是 **systemd timer 而非 crontab**，所以 `crontab -l` 里看不到它们
> （服务器上原有的 `1 * * * * systemctl start mysqld` 与本平台无关）。
> systemd timer 的好处：有日志、开机自启、可配补跑、可设依赖与超时。

### 拒绝模式（可选）

```bash
# 当前服务器已启用 drop(直接断连, 最隐蔽)
cat /etc/systemd/system/stock-dashboard.service.d/override.conf
#   [Service]
#   Environment=STOCK_DENY_MODE=drop
```
可选值：`drop`（断连） / `404`（空 404，默认）。注意 **drop-in 必须带 `[Service]` 段头**，
否则 systemd 会忽略该配置（踩过这个坑）。

---

## 5. 日常维护

```bash
# 抓取最新净值（也可用页面右上角"抓取并刷新"按钮）
ssh root@8.138.182.173 '/usr/bin/python3.8 /opt/stock/python/fetch_nav.py'

# 查看今日信号
ssh root@8.138.182.173 'cat /opt/stock/output/今日信号.txt'

# 看被拒绝的访问来源
ssh root@8.138.182.173 'grep deny /opt/stock/logs/dashboard.log | tail'
```

---

## 6. 安全说明

1. 公网防线 = **token**（`<ts>-md5(私钥+ts)`，10 分钟有效）。链接等于钥匙，别转发。
2. 未授权访问**不给任何提示**（drop 断连 / 空 404、无 `Server` 头）。
3. 邮箱授权码只存服务器 `config/mail.conf`（600），**已加入 `.gitignore`**，不会进仓库。
4. 当前是 **HTTP 明文**；如担心链路安全，用 SSH 隧道：`ssh -L 8756:127.0.0.1:8756 root@8.138.182.173`。
5. 建议在**阿里云安全组**再加一层：入方向只允许你的来源 IP 访问 8756。

---

## 7. 从本地重新部署

```bash
# 本地打包（注意排除真实凭据与运行态）
cd /Users/langkejianxin/Downloads/stock/deploy
tar czf /tmp/stock_deploy.tar.gz --exclude='config/mail.conf' \
    --exclude='config/token_secret.txt' --exclude='data/mail_check_state.json' stock
scp /tmp/stock_deploy.tar.gz root@8.138.182.173:/tmp/

# 服务器端（只覆盖代码, 保留凭据与数据）
ssh root@8.138.182.173
tar xzf /tmp/stock_deploy.tar.gz -C /tmp/
cp -f /tmp/stock/python/*.py /opt/stock/python/
cp -f /tmp/stock/systemd/* /opt/stock/systemd/
cp -f /tmp/stock/systemd/* /etc/systemd/system/
chmod +x /opt/stock/python/run_daily.sh
systemctl daemon-reload
systemctl restart stock-dashboard.service
systemctl enable --now stock-mail-check.timer
```
