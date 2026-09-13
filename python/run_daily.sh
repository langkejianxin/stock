#!/bin/bash
# 每日数据更新管线: 抓基金净值 -> 输出今日信号
# 由 launchd(com.stock.nav.daily) 每个交易日 20:30 触发; 也可手动运行
# 两策略均按基金累计净值计算(策略1 共识 / 策略2 通道), 无需场内行情
# 仪表盘(dashboard)每次刷新实时从 data/ 计算, 无需在此生成图表
# 任一步失败即中止(避免用陈旧数据生成误导信号)
set -e
ROOT=/Users/langkejianxin/Applications/stock
PY=/opt/homebrew/bin/python3.9
cd "$ROOT" || exit 1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 开始 ====="
echo "--- 抓基金净值(两策略均用累计净值) ---"
"$PY" python/fetch_nav.py
echo "--- 今日信号 ---"
"$PY" python/daily_signal.py
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 结束 ====="
