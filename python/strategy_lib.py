# -*- coding: utf-8 -*-
"""
两个策略的计算库(与回测脚本算法完全一致, 供 dashboard 实时调用):
  策略1: RSI(14)×动量(20) 共识 + 确认2周 (V1) —— 512800 银行 vs 159949 创业板50
         周五信号次周生效; 新状态(满仓/各半)须被信号连续两周提出才切换
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

START_MOM = "2017-09-01"   # 策略1 回测起点(对齐 共识+确认2周 文档)
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


def rsi_from_ret(ret, n):
    gain = ret.clip(lower=0)
    loss = -ret.clip(upper=0)
    avg_gain = gain.rolling(n, min_periods=n).mean()
    avg_loss = loss.rolling(n, min_periods=n).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


# ==================== 策略1: RSI+动量 共识 + 确认2周 (V1) ====================
# 数据源: 基金累计净值(始终按累计净值口径)
# 规则: RSI(14) 与 动量(20) 两个信号一致 → 提出"满仓该标的"; 分歧 → 提出"各持一半";
#       新状态(银行/创业板/各半)须被信号连续两周提出才执行切换; 永不空仓
def consensus_result():
    bank = load("512800_基金净值.csv")
    cyb = load("159949_基金净值.csv")
    b = bank.assign(
        mom20=bank["close"].pct_change(20),
        rsi14=rsi_from_ret(bank["close"].pct_change().fillna(0), 14),
    )
    c = cyb.assign(
        mom20=cyb["close"].pct_change(20),
        rsi14=rsi_from_ret(cyb["close"].pct_change().fillna(0), 14),
    )
    df = b.merge(c, on="date", suffixes=("_bank", "_cyb")).set_index("date").sort_index()
    df = df[df.index >= pd.Timestamp(START_MOM)]
    df["week"] = df.index.to_period("W-FRI")
    df["ret_bank"] = df["close_bank"].pct_change().fillna(0.0)
    df["ret_cyb"] = df["close_cyb"].pct_change().fillna(0.0)

    # 周五收盘比较 RSI(14) 与 动量(20) -> 信号延迟一周生效
    wk = df[["rsi14_bank", "rsi14_cyb", "mom20_bank", "mom20_cyb"]].resample("W-FRI").last()
    wk = wk[wk.index <= df.index[-1]]  # 只保留完整周
    sig_rsi = pd.Series(np.where(wk["rsi14_bank"] > wk["rsi14_cyb"], "bank", "cyb"), index=wk.index)
    sig_mom = pd.Series(np.where(wk["mom20_bank"] > wk["mom20_cyb"], "bank", "cyb"), index=wk.index)
    sig_rsi.index = sig_rsi.index.to_period("W-FRI")
    sig_mom.index = sig_mom.index.to_period("W-FRI")

    # 每周提出状态: 一致 -> 该标的, 分歧 -> mix(各半)
    stw = pd.Series(np.where(sig_rsi.values == sig_mom.values, sig_rsi.values,
                             np.full(len(sig_rsi), "mix")), index=sig_rsi.index)

    # ---- 确认2周状态机(对齐 共识+确认2周策略文档) ----
    # cur 初始 = cyb; 新状态须被信号连续两周提出才切换; 上一周提出的状态记 prev
    _out, _cur, _prev = [], "cyb", None
    for _w in stw.index:
        _prop = stw[_w]
        if _prop != _cur and _prop == _prev:   # 新状态连续两周被提出
            _cur = _prop
        _prev = _prop
        _out.append(_cur)
    stw_v1 = pd.Series(_out, index=stw.index)
    t = df["week"].map(stw_v1.shift(1)).ffill().fillna("cyb")   # 当周持仓(延迟一周生效)

    # 日收益: 银行 -> 银行日收益; 创业板 -> 创业板日收益; 各半 -> 0.5×(银行+创业板)
    r_b = df["ret_bank"].values
    r_c = df["ret_cyb"].values
    not_mix = (t != "mix").values
    held = np.where(t == "bank", r_b, r_c)
    ret_strat = pd.Series(np.where(not_mix, held, 0.5 * (r_b + r_c)), index=df.index).fillna(0)
    state = t.copy()   # bank/cyb/mix
    # 不复利: 每天收益率 = 当天持有基金的收益率, 累计 = 简单累加
    nav = (1 + ret_strat.cumsum())
    nav_bank = (1 + df["ret_bank"].cumsum())
    nav_cyb = (1 + df["ret_cyb"].cumsum())

    # 绩效(简单口径: 累计=Σ日收益, 年化=线性)
    total = nav.iloc[-1] - 1
    ann = total / len(df) * 252
    mdd = (nav / nav.cummax() - 1).min()
    sharpe = ret_strat.mean() / ret_strat.std() * math.sqrt(252) if ret_strat.std() > 0 else 0.0
    switches = int((state != state.shift()).sum())   # 持仓状态变化次数(与文档一致)

    # ---- 当前信号(提案 / 当前持仓 / 上一周) ----
    def state_name(s):
        return {"bank": "银行ETF(512800)", "cyb": "创业板50ETF(159949)", "mix": "各持一半(50/50)"}[s]

    def state_of(row):
        rsi = "bank" if row["rsi14_bank"] > row["rsi14_cyb"] else "cyb"
        mom = "bank" if row["mom20_bank"] > row["mom20_cyb"] else "cyb"
        return rsi if rsi == mom else "mix"

    # wkl = 完整周信号行(周五); iloc[-1] 最新完整周, iloc[-2] 上一完整周
    wkl = wk.dropna()
    last = wkl.iloc[-1]
    prev_row = wkl.iloc[-2]
    cur_fri = wkl.index[-1]      # 最新完整周周五(提案日)
    prev_fri = wkl.index[-2]     # 上一完整周周五
    # 状态机周输出 -> 每交易日实际持仓 state; 每周五的实际持仓 = 上周状态机输出(延迟一周)
    wstate = state.resample("W-FRI").last()
    cur_hold = state.iloc[-1]                # 最新交易日实际持仓(自 stw_v1 上一输出生效)
    cur_hold_fri = state.index[-1]           # 用于周归属
    # 当前持仓状态 cur_hold 的起始日: 状态序列中最后一次等于 cur_hold 的连续段的起点
    _sv = state.values
    _start_i = 0
    for _i in range(len(_sv) - 1, 0, -1):
        if _sv[_i] != _sv[_i - 1]:
            _start_i = _i
            break
    cur_start = str(state.index[_start_i].date())
    # 最新周五提案(下一周候选; 与当前持仓比较判断是否"连续两周")
    prop_now = state_of(last)
    # prop_now 已被连续提出的周数(截至最新完整周; 含本周)。>=2 → 下一周将切换
    _s2 = list(stw.values)
    _streak2 = 1
    for _i in range(len(_s2) - 2, -1, -1):
        if _s2[_i] == _s2[-1]:
            _streak2 += 1
        else:
            break
    # 上一完整周的实际持仓 = wstate 在 prev_fri(若该周是完整周)
    prev_hold_code = wstate.get(prev_fri)
    if prev_hold_code is None or (isinstance(prev_hold_code, float) and np.isnan(prev_hold_code)):
        prev_hold_code = "cyb"
    cur_signal = {
        "friday": str(cur_fri.date()),
        "rsi_bank": round(float(last["rsi14_bank"]), 1),
        "rsi_cyb": round(float(last["rsi14_cyb"]), 1),
        "mom_bank": round(float(last["mom20_bank"]) * 100, 2),
        "mom_cyb": round(float(last["mom20_cyb"]) * 100, 2),
        "rsi_win": state_name("bank" if last["rsi14_bank"] > last["rsi14_cyb"] else "cyb"),
        "mom_win": state_name("bank" if last["mom20_bank"] > last["mom20_cyb"] else "cyb"),
        "prop_now": prop_now,
        "prop_now_name": state_name(prop_now),
        "prop_streak": _streak2,   # 提案状态被连续提出的周数(>=2 → 下周切换)
        "hold_now": state_name(cur_hold),
        "hold_now_code": cur_hold,
        "cur_start": cur_start,
        "prev_week": f"{str((prev_fri - pd.Timedelta(days=4)).date())} ~ {str(prev_fri.date())}",
        "prev_hold": state_name(prev_hold_code),
        "prev_hold_code": prev_hold_code,
    }

    dates = [d.strftime("%Y-%m-%d") for d in df.index]

    # 最近20周周收益(每天收益率 = 持有基金收益率, 周内简单相加) + 当周形态
    wret = ret_strat.resample("W-FRI").sum()
    wstate = state.resample("W-FRI").last()
    wret = wret[wret.index <= df.index[-1]]      # 去掉不完整周
    wstate = wstate[wstate.index <= df.index[-1]]
    weekly = [
        {"date": str(d.date()), "ret": round(float(r * 100), 2), "hold": wstate[d]}
        for d, r in wret.tail(20).iloc[::-1].items()
    ]

    # 最近20个交易日收益 + 当日形态
    daily = [
        {"date": str(d.date()), "ret": round(float(r * 100), 2), "hold": state[d]}
        for d, r in ret_strat.tail(20).iloc[::-1].items()
    ]

    return {
        "name": "RSI+动量 共识 + 确认2周",
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
        "holdings": state.tolist(),
        "weekly": weekly,
        "daily": daily,
        "ret": [round(float(v), 6) for v in ret_strat],   # 策略日收益(年化统计用)
    }


# ==================== 策略2: 滚500·95%分位通道 ====================
def channel_result():
    df = load("510880_基金净值.csv", keep_extra=True)
    # 日收益率 = 累计净值 close-to-close(始终按累计净值, 不含分红再投)
    ret = df["close"].pct_change().fillna(0.0).values
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
    # 不复利: 累计 = 简单累加
    nav = 1 + np.cumsum(r_strat)
    nav_bh = 1 + np.cumsum(ret)

    vis = df["date"] >= pd.Timestamp(VIS_CHAN)
    v0 = int(np.argmax(vis.values))
    nav_s = nav[v0:]
    d0 = df["date"].iloc[v0]
    days = (df["date"].iloc[-1] - d0).days
    n_vis = int(vis.sum())

    def metric(nav_series, r_series):
        total = nav_series[-1] - nav_series[0]               # Σ 区间日收益(简单)
        ann = total / n_vis * 252 if n_vis > 0 else 0.0      # 线性年化
        mdd = (pd.Series(nav_series) / pd.Series(nav_series).cummax() - 1).min()
        sharpe = r_series.mean() / r_series.std() * math.sqrt(252) if r_series.std() > 0 else 0.0
        return round(float(total * 100), 2), round(float(ann * 100), 2), \
            round(float(mdd * 100), 2), round(float(sharpe), 2)

    rv = pd.Series(r_strat[vis.values])
    t, a, m, s = metric(nav_s, rv)
    t_bh, a_bh, m_bh, s_bh = metric(nav_bh[v0:], pd.Series(ret[vis.values]))
    avg_pos = float(held[vis.values].mean() * 100)
    cash = float((held[vis.values] == 0).sum() / vis.sum() * 100)

    # 买卖点(信号切换日, 含触发轨道价)
    diff = np.diff(np.concatenate([[0], sig]))
    buys = [{"date": df["date"].iloc[i].strftime("%Y-%m-%d"), "value": round(float(y[i]), 4),
             "band": round(float(lo[i]), 4)}
            for i in np.where(diff == 1)[0] if df["date"].iloc[i] >= pd.Timestamp(VIS_CHAN)]
    sells = [{"date": df["date"].iloc[i].strftime("%Y-%m-%d"), "value": round(float(y[i]), 4),
              "band": round(float(up[i]), 4)}
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

    # 最近20周周收益(每天收益率 = 持有基金收益率, 周内简单相加)
    r_ser = pd.Series(r_strat, index=df["date"])
    wret = r_ser.resample("W-FRI").sum()
    wret = wret[wret.index <= df["date"].iloc[-1]]  # 去掉不完整周
    weekly = [{"date": str(d.date()), "ret": round(float(r * 100), 2)}
              for d, r in wret.tail(20).iloc[::-1].items()]

    # 最近20个交易日收益
    daily = [{"date": str(d.date()), "ret": round(float(r * 100), 2)}
             for d, r in r_ser.tail(20).iloc[::-1].items()]

    return {
        "name": "滚500·95%分位通道",
        "dates": [df["date"].iloc[j].strftime("%Y-%m-%d") for j in vis_idx],
        "all_dates": [d.strftime("%Y-%m-%d") for d in df["date"]],
        "ret": [round(float(v), 6) for v in r_strat],   # 策略日收益(全历史, 年化统计用)
        "held": [int(v) for v in held],                 # 全历史持仓标记(0/1)
        "nav_full": [round(float(v), 4) for v in y],    # 全历史累计净值
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


# ==================== 年度收益率 ====================
def annual_returns(r1=None, r2=None) -> dict:
    """两个策略的历年(自然年)收益率 —— 与平台累计口径一致: 当年每天收益率相加(不复利)。
    即: 每年收益 = Σ 当年日收益; 策略1 的"各半"日收益 = 0.5×(银行日收益+创业板日收益)。
    策略1 从 2017-09-01 起; 策略2 从数据起点(2006-11)起(2019 前为展示外区间, 含暖机期)。"""
    if r1 is None:
        r1 = consensus_result()
    if r2 is None:
        r2 = channel_result()
    out = {}
    for key, r in (("strategy1", r1), ("strategy2", r2)):
        dates = r["dates"] if key == "strategy1" else r["all_dates"]
        s = pd.Series(r["ret"], index=pd.to_datetime(dates))
        g = s.groupby(s.index.year).sum()
        out[key] = [{"year": int(y), "ret": round(float(v) * 100, 2)} for y, v in g.items()]
    return out


if __name__ == "__main__":
    r1 = consensus_result()
    r2 = channel_result()
    print(r1["name"], r1["metrics"], r1["signal"])
    print(r2["name"], r2["metrics"], r2["current"])
    a = annual_returns()
    print("\n年度收益率:")
    for k, rows in a.items():
        print(k, rows)
