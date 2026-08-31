# -*- coding: utf-8 -*-
"""
两个策略的计算库(与回测脚本算法完全一致, 供 dashboard 实时调用):
  策略1: 动量(20日) 日频轮动 —— 512800 银行 vs 159949 创业板50, 周五信号次周生效
  策略2: 滚500·95%分位通道 —— 510880 红利ETF, 累计净值 vs 滚动500日回归通道上下轨
数据源: 本地 *_基金净值.csv(由 fetch_nav.py 每日更新)
"""
import math
import os

# 项目根目录 = python/ 的上一级; 数据统一放 data/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

import numpy as np
import pandas as pd

START_MOM = "2017-08-03"   # 策略1 回测起点(对齐文档)
VIS_CHAN = "2019-01-01"    # 策略2 展示起点(对齐文档)


def load(path, keep_extra=False):
    df = pd.read_csv(os.path.join(DATA_DIR, path))
    df.columns = [str(c).strip() for c in df.columns]
    rename = {}
    for c in df.columns:
        if c in ("净值日期", "日期", "date", "Date"):
            rename[c] = "date"
        elif c in ("累计净值", "收盘", "close", "Close"):
            rename[c] = "close"
    df = df.rename(columns=rename)
    if "close" not in df.columns:
        df = df.rename(columns={"单位净值": "close"})
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    if keep_extra:
        df["unit"] = pd.to_numeric(df.get("单位净值"), errors="coerce")
        df["growth"] = pd.to_numeric(df.get("日增长率"), errors="coerce")
    return df


# ==================== 策略1: 动量(20日) 轮动 ====================
# 数据源: 场内前复权价格(腾讯 fqkline, fetch_stock.py 维护) —— 周五收盘执行口径
def momentum_result():
    bank = load("512800_股票.csv")
    cyb = load("159949_股票.csv")
    b = bank.assign(mom20=bank["close"].pct_change(20))
    c = cyb.assign(mom20=cyb["close"].pct_change(20))
    df = b.merge(c, on="date", suffixes=("_bank", "_cyb")).set_index("date").sort_index()
    df = df[df.index >= pd.Timestamp(START_MOM)]
    df["week"] = df.index.to_period("W-FRI")
    df["ret_bank"] = df["close_bank"].pct_change().fillna(0.0)
    df["ret_cyb"] = df["close_cyb"].pct_change().fillna(0.0)

    # 周五收盘比较 20 日动量 -> 信号延迟一周生效
    wk = df[["mom20_bank", "mom20_cyb"]].resample("W-FRI").last()
    wk = wk[wk.index <= df.index[-1]]  # 只保留完整周(周五标签不晚于数据末日, 避免把不完整周当信号)
    sig_weekly = pd.Series(
        np.where(wk["mom20_bank"] > wk["mom20_cyb"], "bank", "cyb"), index=wk.index
    )
    sig_weekly.index = sig_weekly.index.to_period("W-FRI")
    target = df["week"].map(sig_weekly.shift(1)).fillna("cyb")

    ret_strat = pd.Series(
        np.where(target == "bank", df["ret_bank"], df["ret_cyb"]), index=df.index
    )
    nav = (1 + ret_strat).cumprod()
    nav_bank = (1 + df["ret_bank"]).cumprod()
    nav_cyb = (1 + df["ret_cyb"]).cumprod()

    # 绩效(对齐 compare_all.stats)
    total = nav.iloc[-1] - 1
    ann = nav.iloc[-1] ** (365.25 / (nav.index[-1] - nav.index[0]).days) - 1
    mdd = (nav / nav.cummax() - 1).min()
    sharpe = ret_strat.mean() / ret_strat.std() * math.sqrt(252) if ret_strat.std() > 0 else 0.0
    switches = int((target != target.shift()).sum() - 1)

    # 当前信号: 最新完整周五的比较结果 = 本周(周一生效)应持有的标的
    wkl = wk.dropna()
    last = wkl.iloc[-1]
    prev = wkl.iloc[-2]
    cur_target = "bank" if last["mom20_bank"] > last["mom20_cyb"] else "cyb"
    prev_fri = wkl.index[-1]  # 上一完整周的周五(08-28): prev 信号的生效周
    cur_signal = {
        "friday": str(wkl.index[-1].date()),
        "mom_bank": round(float(last["mom20_bank"]) * 100, 2),
        "mom_cyb": round(float(last["mom20_cyb"]) * 100, 2),
        "hold_now": "创业板50ETF(159949)" if cur_target == "cyb" else "银行ETF(512800)",
        "cur_start": str((wkl.index[-1] + pd.Timedelta(days=3)).date()),  # 本周一(信号次周生效)
        "prev_week": f"{str((prev_fri - pd.Timedelta(days=4)).date())} ~ {str(prev_fri.date())}",
        "prev_hold": "创业板50ETF(159949)" if prev["mom20_bank"] <= prev["mom20_cyb"] else "银行ETF(512800)",
    }

    dates = [d.strftime("%Y-%m-%d") for d in df.index]

    # 最近20周周收益(按策略实际持仓复利) + 当周持有标的
    wret = (1 + ret_strat).resample("W-FRI").prod() - 1
    whold = target.resample("W-FRI").last()
    wret = wret[wret.index <= df.index[-1]]      # 去掉不完整周
    whold = whold[whold.index <= df.index[-1]]
    weekly = [
        {"date": str(d.date()), "ret": round(float(r * 100), 2),
         "hold": "bank" if whold[d] == "bank" else "cyb"}
        for d, r in wret.tail(20).iloc[::-1].items()
    ]

    # 最近20个交易日收益 + 当日持有
    daily = [
        {"date": str(d.date()), "ret": round(float(r * 100), 2),
         "hold": "bank" if target[d] == "bank" else "cyb"}
        for d, r in ret_strat.tail(20).iloc[::-1].items()
    ]

    return {
        "name": "动量(20日) 轮动",
        "dates": dates,
        "strategy": [round(float(v * 100), 2) for v in nav],          # 累计收益率%
        "bank": [round(float(v * 100), 2) for v in nav_bank],
        "cyb": [round(float(v * 100), 2) for v in nav_cyb],
        "metrics": {
            "total": round(float(total * 100), 2),
            "ann": round(float(ann * 100), 2),
            "mdd": round(float(mdd * 100), 2),
            "sharpe": round(float(sharpe), 2),
            "switches": switches,
            "period": f"{df.index[0].date()} ~ {df.index[-1].date()}",
        },
        "signal": cur_signal,
        "holdings": [("bank" if t == "bank" else "cyb") for t in target],
        "weekly": weekly,
        "daily": daily,
    }


# ==================== 策略2: 滚500·95%分位通道 ====================
def channel_result():
    df = load("510880_基金净值.csv", keep_extra=True)
    # 日收益率 = 官方日增长率(含分红/拆分), 缺失用单位净值 pct_change 补齐(对齐 gen_red_html)
    ret = df["growth"] / 100
    ret = ret.fillna(df["unit"].pct_change()).fillna(0.0).values
    y = df["close"].values            # 通道基于累计净值
    n = len(df)
    RW = 500

    # 滚动500日线性回归 + 95%分位带宽(对齐 gen_red_html.roll_chan)
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

    # 信号: 当天收盘判定 -> 次日执行(对齐 chan_signals 的 shift(1))
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
    held = np.concatenate([[0], sig[:-1]])          # 当日实际持仓(延迟一天)

    r_strat = ret * held
    nav = np.cumprod(1 + r_strat)
    nav_bh = np.cumprod(1 + ret)

    vis = df["date"] >= pd.Timestamp(VIS_CHAN)
    v0 = int(np.argmax(vis.values))
    nav_s = nav[v0:]
    d0 = df["date"].iloc[v0]
    days = (df["date"].iloc[-1] - d0).days

    def metric(nav_series, r_series):
        total = nav_series[-1] / nav_series[0] - 1
        ann = (nav_series[-1] / nav_series[0]) ** (365.25 / days) - 1 if days > 0 else 0.0
        mdd = (pd.Series(nav_series) / pd.Series(nav_series).cummax() - 1).min()
        sharpe = r_series.mean() / r_series.std() * math.sqrt(252) if r_series.std() > 0 else 0.0
        return round(float(total * 100), 2), round(float(ann * 100), 2), \
            round(float(mdd * 100), 2), round(float(sharpe), 2)

    rv = pd.Series(r_strat[vis.values])
    t, a, m, s = metric(nav_s, rv)
    t_bh, a_bh, m_bh, s_bh = metric(nav_bh[v0:], pd.Series(ret[vis.values]))
    avg_pos = float(held[vis.values].mean() * 100)
    cash = float((held[vis.values] == 0).sum() / vis.sum() * 100)

    # 买卖点(信号切换日)
    diff = np.diff(np.concatenate([[0], sig]))
    buys = [{"date": df["date"].iloc[i].strftime("%Y-%m-%d"), "value": round(float(y[i]), 4)}
            for i in np.where(diff == 1)[0] if df["date"].iloc[i] >= pd.Timestamp(VIS_CHAN)]
    sells = [{"date": df["date"].iloc[i].strftime("%Y-%m-%d"), "value": round(float(y[i]), 4)}
             for i in np.where(diff == -1)[0] if df["date"].iloc[i] >= pd.Timestamp(VIS_CHAN)]

    # 当前状态(今天收盘判定, 明天执行)
    i = n - 1
    cur = y[i]
    m_, u, l = mid[i], up[i], lo[i]
    chan_pos = round((cur - l) / max(u - l, 1e-12) * 100, 1) if m_ == m_ else None
    holding = bool(sig[i])
    if holding and cur >= u:
        action = "卖出(已触上轨, 次日执行)"
    elif not holding and cur <= l:
        action = "买入(已触下轨, 次日执行)"
    elif holding:
        action = "持有"
    else:
        action = "空仓"

    vis_idx = np.where(vis.values)[0]

    # 最近20周周收益(策略按持仓复利)
    r_ser = pd.Series(r_strat, index=df["date"])
    wret = (1 + r_ser).resample("W-FRI").prod() - 1
    wret = wret[wret.index <= df["date"].iloc[-1]]  # 去掉不完整周
    weekly = [{"date": str(d.date()), "ret": round(float(r * 100), 2)}
              for d, r in wret.tail(20).iloc[::-1].items()]

    # 最近20个交易日收益
    daily = [{"date": str(d.date()), "ret": round(float(r * 100), 2)}
             for d, r in r_ser.tail(20).iloc[::-1].items()]

    return {
        "name": "滚500·95%分位通道",
        "dates": [df["date"].iloc[j].strftime("%Y-%m-%d") for j in vis_idx],
        "nav": [round(float(y[j]), 4) for j in vis_idx],
        "mid": [round(float(mid[j]), 4) for j in vis_idx],
        "up": [round(float(up[j]), 4) for j in vis_idx],
        "lo": [round(float(lo[j]), 4) for j in vis_idx],
        "strategy_nav": [round(float(v * 100), 2) for v in nav_s],
        "bh_nav": [round(float(v * 100), 2) for v in nav_bh[v0:]],
        "metrics": {
            "total": t, "ann": a, "mdd": m, "sharpe": s,
            "avg_pos": round(avg_pos, 1), "cash": round(cash, 1),
            "buys": len(buys), "sells": len(sells),
            "period": f"{d0.date()} ~ {df['date'].iloc[-1].date()}",
            "bh": {"total": t_bh, "ann": a_bh, "mdd": m_bh, "sharpe": s_bh},
        },
        "buys": buys,
        "sells": sells,
        "weekly": weekly,
        "daily": daily,
        "current": {
            "date": str(df["date"].iloc[-1].date()),
            "nav": round(float(cur), 4),
            "mid": round(float(m_), 4) if m_ == m_ else None,
            "up": round(float(u), 4) if u == u else None,
            "lo": round(float(l), 4) if l == l else None,
            "chan_pos": chan_pos,
            "holding": holding,
            "action": action,
        },
    }


if __name__ == "__main__":
    r1 = momentum_result()
    r2 = channel_result()
    print(r1["name"], r1["metrics"], r1["signal"])
    print(r2["name"], r2["metrics"], r2["current"])
