# -*- coding: utf-8 -*-
"""
抓取 A 股日线行情(前复权) -> 本地 CSV
=====================================
数据源: 腾讯财经 fqkline 接口(免登录, 前复权=分红再投资, 与基金累计净值可比)
  https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh601398,day,<开始>,<结束>,800,qfq

特性:
  - 前复权收盘价: 直接用于动量/收益计算(含分红), 与原策略用基金累计净值口径一致
  - 同时保存不复权收盘价(列"收盘不复权"): 供"纯价格动量"敏感性检验(对比分红对信号的影响)
  - 按年份分段抓取(接口单次上限约 800 根), 增量合并: 已有本地 CSV 时只补齐缺失日期
  - 输出: data/{code}_股票.csv (列: 日期/开盘/收盘/最高/最低/成交量/收盘不复权)

用法:
  /opt/homebrew/bin/python3.9 fetch_stock.py            # 抓默认 601398 工商银行
  /opt/homebrew/bin/python3.9 fetch_stock.py 600519     # 抓其它股票
"""

import csv
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
OUT_COLS = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "收盘不复权"]


def fetch_range(code: str, start: str, end: str, market: str = "sh") -> list[list]:
    """抓取一段日线, 返回 [[日期,开,收,高,低,量,收不复权], ...](前复权 + 不复权)。"""
    # 两次请求: qfq 拿前复权(写 OHLCV+收盘), 空 fq 拿不复权(只补 收盘不复权 列)
    out = {}
    for fq in ("qfq", ""):
        fq_part = fq + "," if fq else ","
        param = f"{market}{code},day,{start},{end},800,{fq_part}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        node = (data.get("data") or {}).get(f"{market}{code}")
        if not isinstance(node, dict):
            raise RuntimeError(f"{code} 接口返回异常: {data.get('msg')}")
        kl = node.get("qfqday") or node.get("day") or []
        for r in kl:
            row = out.setdefault(r[0], [None] * 7)
            row[0] = r[0]
            if fq:                              # 前复权: 写 OHLCV + 收盘(红利再投资口径)
                row[1], row[2], row[3], row[4], row[5] = r[1], r[2], r[3], r[4], r[5]
            else:                               # 不复权: 只补第6列(纯价格信号用)
                row[6] = r[2]
        time.sleep(0.3)
    # 剔除缺失列的行(某天只有一种口径的极端情况)
    return [r for r in out.values() if all(x is not None for x in r)]


def market_of(code: str) -> str:
    return "sh" if code.startswith(("6", "9")) else "sz"


START_YEAR = 2005   # 全量重抓起点(前复权价格会随新分红整体重算, 必须覆盖旧数据)


def update_stock(code: str) -> tuple[int, str]:
    """全量抓取并覆盖合并(前复权随新分红整体重算, 不做按日期增量), 返回 (条数, 最新日期)。"""
    csv_path = os.path.join(DATA_DIR, f"{code}_股票.csv")
    local = {}
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                local[r["日期"]] = [r["日期"], r["开盘"], r["收盘"], r["最高"], r["最低"],
                                    r["成交量"], r.get("收盘不复权", "")]

    mk = market_of(code)
    rows: dict[str, list] = dict(local)
    added = 0
    year = START_YEAR
    end_year = int(time.strftime("%Y"))
    while year <= end_year:
        seg = fetch_range(code, f"{year}-01-01", f"{year}-12-31", mk)
        for r in seg:
            rows[r[0]] = r          # 覆盖: 前复权随新分红重算
            added += 1
        year += 1
        time.sleep(0.4)

    ordered = sorted(rows.values(), key=lambda r: r[0])
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(OUT_COLS)
        w.writerows(ordered)
    return added, ordered[-1][0] if ordered else ""


def main() -> int:
    code = sys.argv[1] if len(sys.argv) > 1 else "601398"
    try:
        added, latest = update_stock(code)
        print(f"{code}: 新增 {added} 条, 最新 {latest} -> data/{code}_股票.csv", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {code}: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
