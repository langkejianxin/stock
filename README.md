# ETF 策略监控台

双 ETF 策略的本地监控平台：实时计算两个策略的最新收益率、信号与收益曲线，浏览器访问即可查看。

> 📄 详细的信号/执行时点、数据源与回测口径见 **[策略说明.md](策略说明.md)**。

## 策略

| 策略 | 标的 | 逻辑 |
|---|---|---|
| 动量(20日) 轮动 | 512800 银行 / 159949 创业板50 | 每周五比较 20 日动量，持有强者，次周一生效，**周五收盘执行**，永不空仓 |
| 滚500·95%分位通道 | 510880 红利ETF | 滚动 500 日线性回归通道，95% 分位上下轨，上轨卖、下轨买，**当日收盘判定、次日执行** |

## 目录结构

```
stock/
├── python/            # 平台代码
│   ├── dashboard.py   #   仪表盘 Web 服务(端口 8756, 零第三方依赖)
│   ├── dashboard.html #   前端页面(ECharts, 本地化离线可用)
│   ├── echarts.min.js #   图表库(已本地化)
│   ├── strategy_lib.py#   两个策略的实时计算(与回测算法一致)
│   ├── fetch_nav.py   #   每日净值增量抓取(东方财富公开接口)
│   ├── daily_signal.py#   输出当日交易信号
│   └── run_daily.sh   #   每日管线: 抓净值 -> 出信号
├── config/            # launchd 配置(定时抓取 + 仪表盘常驻服务)
├── data/              # 基金净值 CSV(每日更新, 不入库)
├── logs/              # 运行日志(不入库)
└── output/            # 生成物: 今日信号.txt(不入库)
```

## 使用

```bash
# 启动仪表盘(launchd 已常驻时无需手动)
/opt/homebrew/bin/python3.9 python/dashboard.py        # 访问 http://127.0.0.1:8756

# 手动更新数据 + 信号
bash python/run_daily.sh

# 定时任务(launchd, 已安装)
#   com.stock.nav.daily  —— 每交易日 20:30 抓净值、写信号
#   com.stock.dashboard  —— 仪表盘常驻(开机自启/崩溃自愈)
launchctl list | grep stock
```

## 说明

- 数据源:
  - 策略1(动量20轮动): 场内**前复权价格**(腾讯行情, `fetch_stock.py` 维护 `data/512800_股票.csv`、`data/159949_股票.csv`)，**周五收盘执行**口径
  - 策略2(滚500通道): 基金净值(东方财富 F10, `fetch_nav.py` 维护 `data/510880_基金净值.csv`)
- 仪表盘每次刷新实时从 `data/` 重算(约 0.3s)，数据以 CSV 为唯一事实来源
- 页面右上角「抓取并刷新」按钮 = 先增量抓取最新净值与行情 → 再重算并刷新图表；每 5 分钟自动刷新仅重算不抓取
- 环境: `/opt/homebrew/bin/python3.9`(pandas/numpy/openpyxl)；系统 python3 无依赖不可用
- ⚠️ 仅供策略研究，不构成投资建议
