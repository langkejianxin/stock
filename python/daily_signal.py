# -*- coding: utf-8 -*-
"""
当前交易信号(读取本地 CSV 最新数据, 复刻两个策略的精确算法):
  策略1: RSI(14)×动量(20) 共识 + 确认2周 —— 512800 银行ETF vs 159949 创业板50ETF
         周五比较、次周生效; 新状态(满仓/各半)须被信号连续两周提出才切换
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


# ============ 策略1: RSI+动量 共识 + 确认2周 (V1) ============
def signal_consensus():
    bank = load("512800_基金净值.csv")   # 基金累计净值
    cyb = load("159949_基金净值.csv")

    def rsi_from_ret(ret, n):
        gain = ret.clip(lower=0)
        loss = -ret.clip(upper=0)
        rs = gain.rolling(n, min_periods=n).mean() / loss.rolling(n, min_periods=n).mean()
        return 100 - 100 / (1 + rs)

    b = bank.assign(mom20=bank["close"].pct_change(20),
                    rsi14=rsi_from_ret(bank["close"].pct_change().fillna(0), 14))
    c = cyb.assign(mom20=cyb["close"].pct_change(20),
                   rsi14=rsi_from_ret(cyb["close"].pct_change().fillna(0), 14))
    m = b.merge(c, on="date", suffixes=("_bank", "_cyb")).set_index("date")
    m = m[m.index >= pd.Timestamp("2017-09-01")]          # 对齐策略1回测起点
    wk = m[["rsi14_bank", "rsi14_cyb", "mom20_bank", "mom20_cyb"]].resample("W-FRI").last().dropna()
    wk = wk[wk.index <= m.index[-1]]  # 只保留完整周(避免把不完整周当信号)
    def state_of(row):
        rsi = "bank" if row["rsi14_bank"] > row["rsi14_cyb"] else "cyb"
        mom = "bank" if row["mom20_bank"] > row["mom20_cyb"] else "cyb"
        return rsi if rsi == mom else "mix"

    # ---- 确认2周状态机(对齐 strategy_lib.consensus_result / 策略文档) ----
    sig_rsi = (wk["rsi14_bank"] > wk["rsi14_cyb"]).map({True: "bank", False: "cyb"})
    sig_mom = (wk["mom20_bank"] > wk["mom20_cyb"]).map({True: "bank", False: "cyb"})
    sig_rsi.index = sig_rsi.index.to_period("W-FRI")
    sig_mom.index = sig_mom.index.to_period("W-FRI")
    stw = pd.Series(np.where(sig_rsi.values == sig_mom.values, sig_rsi.values,
                             np.full(len(sig_rsi), "mix")), index=sig_rsi.index)
    _out, _cur, _prev = [], "cyb", None
    for _w in stw.index:
        _prop = stw[_w]
        if _prop != _cur and _prop == _prev:   # 新状态连续两周被提出才切换
            _cur = _prop
        _prev = _prop
        _out.append(_cur)
    stw_v1 = pd.Series(_out, index=stw.index)
    # 逐日实际持仓(延迟一周生效, 与 strategy_lib 一致)
    m["week"] = m.index.to_period("W-FRI")
    t = m["week"].map(stw_v1.shift(1)).ffill().fillna("cyb")
    cur_hold = t.iloc[-1]                        # 最新交易日实际持仓
    # 当前持仓状态段的起始交易日
    _sv = t.values
    _start_i = 0
    for _i in range(len(_sv) - 1, 0, -1):
        if _sv[_i] != _sv[_i - 1]:
            _start_i = _i
            break
    cur_start = str(t.index[_start_i].date())

    last = wk.iloc[-1]
    prev = wk.iloc[-2]
    fri = wk.index[-1].date()
    prev_fri_d = wk.index[-2]
    prev_week_range = f"{str((prev_fri_d - pd.Timedelta(days=4)).date())} ~ {str(prev_fri_d.date())}"

    rsi_win = "银行ETF(512800)" if last["rsi14_bank"] > last["rsi14_cyb"] else "创业板50ETF(159949)"
    mom_win = "银行ETF(512800)" if last["mom20_bank"] > last["mom20_cyb"] else "创业板50ETF(159949)"
    prop_now = state_of(last)                    # 本周提案(与当前持仓比较, 判断是否连续两周)
    prop_name = {"bank": "银行ETF(512800)", "cyb": "创业板50ETF(159949)", "mix": "各持一半(50/50)"}[prop_now]
    hold_now = {"bank": "银行ETF(512800)", "cyb": "创业板50ETF(159949)", "mix": "各持一半(50/50)"}[cur_hold]
    # 提案连续提出周数
    _s2 = list(stw.values)
    _streak2 = 1
    for _i in range(len(_s2) - 2, -1, -1):
        if _s2[_i] == _s2[-1]:
            _streak2 += 1
        else:
            break
    confirm_note = (f"提案 {prop_name} 已连续提出 {_streak2} 周"
                    + (" → 下一周将切换" if _streak2 >= 2 and prop_now != cur_hold else
                       " → 仍待确认(须再连续一周)" if prop_now != cur_hold else
                       " → 与当前一致, 无需切换"))

    # 上一完整周(prev_fri)当天的实际持仓(从逐日 t 取)
    _prev_hold = t.asof(pd.Timestamp(prev_fri_d))
    _prev_hold_name = {"bank": "银行ETF(512800)", "cyb": "创业板50ETF(159949)", "mix": "各持一半(50/50)"}[_prev_hold]
    lines = [
        "━━━ 策略1: RSI+动量 共识 + 确认2周 ━━━",
        f"  最新信号({fri} 周五收盘):",
        f"    RSI(14) : 银行 {last['rsi14_bank']:.1f} vs 创业板 {last['rsi14_cyb']:.1f} → {rsi_win}胜",
        f"    动量(20): 银行 {last['mom20_bank']*100:+.2f}% vs 创业板 {last['mom20_cyb']*100:+.2f}% → {mom_win}胜",
        f"  共识提案: {prop_name} · 当前应持有: {hold_now}({cur_start} 起生效)",
        f"  {confirm_note}",
        f"  上一完整周({prev_week_range})持有: {_prev_hold_name}",
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
    out += signal_consensus()
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
