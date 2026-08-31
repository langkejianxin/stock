# -*- coding: utf-8 -*-
"""
当前交易信号(读取本地 CSV 最新数据, 复刻两个策略的精确算法):
  策略1: 动量(20日) 轮动  —— 512800 银行ETF vs 159949 创业板50ETF, 周五比较、次周生效
  策略2: 滚500·95%分位通道 —— 510880 红利ETF, 每日判定 累计净值 相对上下轨

用法: /opt/homebrew/bin/python3.9 signal.py
输出: 终端 + 今日信号.txt
"""

import numpy as np
import os
import pandas as pd

# 项目根目录 = python/ 的上一级; 数据在 data/, 输出到 output/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "output")


def load(path):
    df = pd.read_csv(os.path.join(DATA_DIR, path))
    df.columns = [str(c).strip() for c in df.columns]
    rename = {}
    for c in df.columns:
        if c in ("日期", "交易日期", "净值日期", "date", "Date"):
            rename[c] = "date"
        elif c in ("累计净值", "收盘", "收盘价", "close", "Close"):
            rename[c] = "close"
    df = df.rename(columns=rename)
    if "close" not in df.columns:
        df = df.rename(columns={"单位净值": "close"})
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df[["date", "close"]].dropna().sort_values("date").reset_index(drop=True)


def fmt(x):
    return f"{x:.4f}" if x == x else "NaN"


# ============ 策略1: 动量(20日) 轮动 ============
def signal_momentum():
    bank = load("512800_股票.csv")   # 场内前复权价(周五收盘执行口径)
    cyb = load("159949_股票.csv")
    b = bank.assign(mom20=bank["close"].pct_change(20))
    c = cyb.assign(mom20=cyb["close"].pct_change(20))
    m = b.merge(c, on="date", suffixes=("_bank", "_cyb")).set_index("date")
    wk = m[["mom20_bank", "mom20_cyb"]].resample("W-FRI").last().dropna()
    wk = wk[wk.index <= m.index[-1]]  # 只保留完整周(避免把不完整周当信号)

    last = wk.iloc[-1]
    fri = wk.index[-1].date()
    mom_b, mom_c = last["mom20_bank"], last["mom20_cyb"]
    target = "银行ETF(512800)" if mom_b > mom_c else "创业板50ETF(159949)"
    # 上一完整周的信号(数据里上一周的持仓)
    prev = wk.iloc[-2]
    prev_fri = wk.index[-2].date()
    prev_target = "银行ETF(512800)" if prev["mom20_bank"] > prev["mom20_cyb"] else "创业板50ETF(159949)"

    prev_fri_d = wk.index[-1]  # 上一完整周的周五(prev 信号的生效周)
    cur_start = str((wk.index[-1] + pd.Timedelta(days=3)).date())   # 本周一(当前信号生效日)
    prev_week_range = f"{str((prev_fri_d - pd.Timedelta(days=4)).date())} ~ {str(prev_fri_d.date())}"

    lines = [
        "━━━ 策略1: 动量(20日) 轮动 ━━━",
        f"  最新信号({fri} 周五收盘)  20日动量: 银行 {mom_b*100:+.2f}%  vs  创业板 {mom_c*100:+.2f}%",
        f"  → 当前应持有: {target}({cur_start} 起生效)",
        f"  上一完整周({prev_week_range})持有: {prev_target}",
    ]
    return lines


# ============ 策略2: 滚500·95%分位通道 ============
def signal_channel():
    df = load("510880_基金净值.csv")
    y = df["close"].values
    n = len(df)
    RW = 500
    mid = np.full(n, np.nan)
    up = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    for i in range(n):
        if i < 60:
            continue
        d = y[max(0, i - RW):i]
        if len(d) < 60:
            continue
        tt = np.arange(len(d))
        A = np.vstack([tt, np.ones(len(tt))]).T
        bb, aa = np.linalg.lstsq(A, d, rcond=None)[0]
        now = aa + bb * (len(d) - 1)
        resid = d - (aa + bb * tt)
        mid[i] = now
        ql, qu = np.quantile(resid, [0.025, 0.975])
        lo[i] = now + ql
        up[i] = now + qu

    # 持仓状态重放(与 gen_red_html.chan_signals 一致)
    pos = 0
    sig = np.zeros(n, dtype=int)
    for i in range(n):
        if i < 60 or np.isnan(lo[i]):
            continue
        if pos == 0 and y[i] <= lo[i]:
            pos = 1
        elif pos == 1 and y[i] >= up[i]:
            pos = 0
        sig[i] = pos

    i = n - 1
    last_date = df["date"].iloc[i].date()
    cur, m, u, l = y[i], mid[i], up[i], lo[i]
    # 当前位置分位(相对最近500日残差)
    resid_now = cur - m
    lines = [
        "━━━ 策略2: 滚500·95%分位通道 (510880 红利ETF) ━━━",
        f"  最新累计净值({last_date}) = {fmt(cur)}",
        f"  中轨 = {fmt(m)}   上轨 = {fmt(u)}   下轨 = {fmt(l)}",
    ]
    if m == m:
        above_lo = (cur - l) / max(u - l, 1e-12) * 100
        lines.append(f"  通道内位置: {above_lo:.1f}% (0%=下轨, 100%=上轨)")
    if sig[i] == 1:
        lines.append("  当前策略状态: 持仓(满仓)")
        if cur >= u:
            lines.append("  ⚠ 已达上轨 → 卖出信号(次日执行)")
        else:
            lines.append("  → 建议: 继续持有, 跌破下轨前不动")
    else:
        lines.append("  当前策略状态: 空仓")
        if cur <= l:
            lines.append("  ⚠ 已触下轨 → 买入信号(次日执行)")
        else:
            lines.append("  → 建议: 空仓等待, 跌至下轨再买")
    return lines


def main():
    out = ["# 今日交易信号", f"生成时间: {pd.Timestamp.now():%Y-%m-%d %H:%M}"]
    out += signal_momentum()
    out += [""]
    out += signal_channel()
    out += ["", "⚠ 仅供策略研究, 不构成投资建议。信号次日生效, 请以收盘净值为准。"]

    text = "\n".join(out)
    print(text)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "今日信号.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print("\n已写入 今日信号.txt")


if __name__ == "__main__":
    main()
