# -*- coding: utf-8 -*-
"""
每日抓取多只 ETF 基金净值(单位净值/累计净值/日增长率) -> 本地 CSV

数据源: 东方财富基金 F10 历史净值接口(免登录, 无需 moomoo OpenD 网关)
  https://api.fund.eastmoney.com/f10/lsjz?fundCode=<代码>&pageIndex=N&pageSize=20

特性:
  - 增量更新: 已有本地 CSV 时只抓取最新几页, 按日期去重合并(幂等, 可重复运行)
  - 输出: {基金代码}_基金净值.csv (列: 净值日期/单位净值/累计净值/日增长率/申购状态/赎回状态/分红送配)

用法:
  /opt/homebrew/bin/python3.9 fetch_nav.py            # 抓默认四只
  /opt/homebrew/bin/python3.9 fetch_nav.py 510880     # 只抓一只
"""

import csv
import json
import os
import sys
import time
import urllib.request

# 项目根目录 = python/ 的上一级; 数据统一放 data/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

FUND_CODES = ["510880", "512800", "159949", "510300"]  # 红利/银行/创业板50/沪深300 ETF
BASE_URL = "https://api.fund.eastmoney.com/f10/lsjz"
PAGE_SIZE = 20  # 该接口单页上限 20 条
SLEEP = 0.15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://fundf10.eastmoney.com/",
}

OUT_COLS = ["净值日期", "单位净值", "累计净值", "日增长率", "申购状态", "赎回状态", "分红送配"]


from typing import Optional


def fetch_pages(code: str, stop_at: Optional[str] = None) -> list[dict]:
    """抓取该基金净值记录(接口返回倒序, 最新在前)。
    stop_at: 本地已有最新日期; 抓到的页内最旧日期 <= stop_at 即停止(增量)。"""
    rows: list[dict] = []
    page = 1
    while True:
        url = f"{BASE_URL}?fundCode={code}&pageIndex={page}&pageSize={PAGE_SIZE}"
        req = urllib.request.Request(url, headers=HEADERS)
        data = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] {code} 第{page}页失败({attempt + 1}/3): {exc}", flush=True)
                time.sleep(2)
        if data is None:
            raise RuntimeError(f"{code} 第{page}页连续失败")
        if data.get("ErrCode") != 0:
            raise RuntimeError(f"{code} 接口错误: {data}")

        batch = (data.get("Data") or {}).get("LSJZList") or []
        rows.extend(batch)
        if not batch or len(batch) < PAGE_SIZE:
            break
        oldest_this_page = batch[-1]["FSRQ"]  # 页内最旧(接口倒序)
        if stop_at and oldest_this_page <= stop_at:
            break
        page += 1
        time.sleep(SLEEP)
    return rows


def merge_and_save(code: str, rows: list[dict]) -> int:
    csv_path = os.path.join(DATA_DIR, f"{code}_基金净值.csv")
    local = {}
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                local[r["净值日期"]] = r

    added = 0
    for r in rows:  # 接口倒序, 逐条并入
        if r["FSRQ"] not in local:
            local[r["FSRQ"]] = {
                "净值日期": r["FSRQ"],
                "单位净值": r.get("DWJZ", ""),
                "累计净值": r.get("LJJZ", ""),
                "日增长率": r.get("JZZZL", ""),
                "申购状态": r.get("SGZT", ""),
                "赎回状态": r.get("SHZT", ""),
                "分红送配": r.get("FHFCZ", ""),
            }
            added += 1

    ordered = sorted(local.values(), key=lambda r: r["净值日期"])
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLS)
        writer.writeheader()
        writer.writerows(ordered)
    return added, ordered[-1]["净值日期"] if ordered else None


def main() -> int:
    codes = sys.argv[1:] or FUND_CODES
    for code in codes:
        csv_path = os.path.join(DATA_DIR, f"{code}_基金净值.csv")
        stop_at = None
        if os.path.exists(csv_path):
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            if rows:
                stop_at = max(r["净值日期"] for r in rows)
        try:
            fetched = fetch_pages(code, stop_at)
            added, latest = merge_and_save(code, fetched)
            print(f"{code}: 新增 {added} 条, 最新 {latest} -> {csv_path}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {code}: {exc}", flush=True)
            return 1
        time.sleep(SLEEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
