# -*- coding: utf-8 -*-
"""
动量(20日)轮动: 周五收盘价执行 vs 周一开盘价执行 对比回测(512800 银行 / 159949 创业板50)
数据: 场内前复权行情(腾讯 fqkline) data/{code}_股票.csv
口径:
  A 周五收盘执行: 信号周五收盘算出后立即按新标的周五收盘换仓 → 新标吃满 周五收盘→下周五收盘
  B 周一开盘执行: 旧标持有到周一开盘再换新标 → 旧标吃周末缺口(周五收盘→周一开盘), 新标从周一盘中起
  (参考)净值口径: 同一信号按基金累计净值 close-to-close 复利(原回测方式)
信号与策略一致: mom20=前复权收盘 20 日变化率, 周五比较, 次周生效, 永不空仓
"""
import math
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
START = "2017-08-03"


def load_stock(code):
    df = pd.read_csv(os.path.join(DATA, f"{code}_股票.csv"), encoding="utf-8-sig")
    df["日期"] = pd.to_datetime(df["日期"])
    for c in ("开盘", "收盘"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["日期", "开盘", "收盘"]].dropna().sort_values("日期").reset_index(drop=True)


def load_nav(code):
    df = pd.read_csv(os.path.join(DATA, f"{code}_基金净值.csv"), encoding="utf-8-sig")
    df["日期"] = pd.to_datetime(df["净值日期"])
    df["累计净值"] = pd.to_numeric(df["累计净值"], errors="coerce")
    return df[["日期", "累计净值"]].dropna().sort_values("日期").reset_index(drop=True)


# ---------- 数据 ----------
bank = load_stock("512800")
cyb = load_stock("159949")
print(f"行情区间: 512800 {bank['日期'].iloc[0].date()}~{bank['日期'].iloc[-1].date()}"
      f" ({len(bank)} 条), 159949 {cyb['日期'].iloc[0].date()}~{cyb['日期'].iloc[-1].date()}"
      f" ({len(cyb)} 条)")

b = bank.assign(mom20=bank["收盘"].pct_change(20))
c = cyb.assign(mom20=cyb["收盘"].pct_change(20))
df = b.merge(c, on="日期", suffixes=("_b", "_c")).set_index("日期").sort_index()
df = df[df.index >= pd.Timestamp(START)]
df["week"] = df.index.to_period("W-FRI")

# 周五信号 -> 次周生效
wk = df[["mom20_b", "mom20_c"]].resample("W-FRI").last()
sig = pd.Series(np.where(wk["mom20_b"] > wk["mom20_c"], "b", "c"), index=wk.index)
sig.index = sig.index.to_period("W-FRI")
target = df["week"].map(sig.shift(1)).fillna("c")
t = target.values

close_b = df["收盘_b"].values
close_c = df["收盘_c"].values
open_b = df["开盘_b"].values
open_c = df["开盘_c"].values
rcb = np.concatenate([[0.0], close_b[1:] / close_b[:-1] - 1])
rcc = np.concatenate([[0.0], close_c[1:] / close_c[:-1] - 1])

n = len(df)
retA = np.zeros(n)
retB = np.zeros(n)
for i in range(n):
    held = t[i]
    retA[i] = rcb[i] if held == "b" else rcc[i]          # A: 新标周五收盘买入, 全天 close-to-close
    if i == 0:
        retB[i] = 0.0
    elif t[i] != t[i - 1]:                                # B: 换仓日拆分
        old, new = t[i - 1], t[i]
        gap = (open_b[i] / close_b[i - 1] if old == "b" else open_c[i] / close_c[i - 1]) - 1
        intra = (close_b[i] / open_b[i] if new == "b" else close_c[i] / open_c[i]) - 1
        retB[i] = (1 + gap) * (1 + intra) - 1
    else:
        retB[i] = rcb[i] if held == "b" else rcc[i]      # 非换仓日: 正常 close-to-close

# 参考: 同一信号按净值 close-to-close(原回测口径), 对齐到行情交易日
nb = load_nav("512800").set_index("日期")["累计净值"]
nc = load_nav("159949").set_index("日期")["累计净值"]
nav_m = pd.concat([nb, nc], axis=1, keys=("b", "c")).reindex(df.index)
rn = np.where(t == "b", nav_m["b"].pct_change().fillna(0).values,
              nav_m["c"].pct_change().fillna(0).values)
retN = np.where(np.isnan(rn), 0.0, rn)

navA = np.cumprod(1 + retA)
navB = np.cumprod(1 + retB)
navN = np.cumprod(1 + retN)
dates = df.index


def stats(nav, ret):
    total = nav[-1] - 1
    days = (dates[-1] - dates[0]).days
    ann = nav[-1] ** (365.25 / days) - 1 if days > 0 else 0.0
    mdd = (pd.Series(nav) / pd.Series(nav).cummax() - 1).min()
    sh = ret.mean() / ret.std() * math.sqrt(252) if ret.std() > 0 else 0.0
    return total * 100, ann * 100, mdd * 100, sh


switches = int(np.sum(t[1:] != t[:-1]))

rows = []
for name, nav, ret in (("A 周五收盘执行", navA, retA), ("B 周一开盘执行", navB, retB), ("参考 净值口径", navN, retN)):
    tot, ann, mdd, sh = stats(nav, ret)
    rows.append((name, tot, ann, mdd, sh))
    print(f"{name:<14} 累计 {tot:+9.2f}%  年化 {ann:+7.2f}%  回撤 {mdd:7.2f}%  夏普 {sh:.2f}")
print(f"换仓次数: {switches} (区间 {dates[0].date()} ~ {dates[-1].date()}, {len(df)} 个交易日)")

# 换仓日的缺口归属差异累计
diff = navB / navA - 1
print(f"\nB 相对 A 的净值比(>1 表示 B 更优): 期末 {diff[-1]*100:+.2f}%")
gap_winA = gap_winB = 0
for i in range(1, n):
    if t[i] != t[i - 1]:
        old, new = t[i - 1], t[i]
        gap_old = (open_b[i] / close_b[i - 1] if old == "b" else open_c[i] / close_c[i - 1]) - 1
        gap_new = (open_b[i] / close_b[i - 1] if new == "b" else open_c[i] / close_c[i - 1]) - 1
        if gap_new > gap_old:
            gap_winA += 1      # 新标周末缺口更大 -> A 占优
        else:
            gap_winB += 1      # 旧标周末缺口更大 -> B 占优
print(f"换仓周周末缺口: 新标缺口更大(A占优) {gap_winA} 次, 旧标缺口更大(B占优) {gap_winB} 次")

with open(os.path.join(ROOT, "output", "执行价对比.csv"), "w", encoding="utf-8-sig", newline="") as f:
    import csv
    w = csv.writer(f)
    w.writerow(["口径", "累计收益%", "年化%", "最大回撤%", "夏普"])
    for r in rows:
        w.writerow([r[0], f"{r[1]:.2f}", f"{r[2]:.2f}", f"{r[3]:.2f}", f"{r[4]:.2f}"])
print("\n已保存 output/执行价对比.csv")
