"""M0：合成证券业务库。

用法:
    uv run python db/seed.py [--scale 1.0]

产出 db/securities.db（默认约 250 万行）。确定性随机（固定种子），可重复重建。
合规：所有数据合成，不含任何真实客户信息；电话号为合成号段。
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

DB_PATH = Path(__file__).resolve().parent / "securities.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
INDEX_PATH = Path(__file__).resolve().parent / "indexes.sql"

random.seed(20260829)
Faker.seed(20260829)
fake = Faker("zh_CN")

CAL_START = date(2024, 9, 2)
CAL_END = date(2026, 8, 28)

INDUSTRIES = [
    "银行", "非银金融", "食品饮料", "医药生物", "电子", "计算机", "通信",
    "电力设备", "机械设备", "汽车", "有色金属", "房地产", "传媒", "化工",
    "交通运输", "公用事业", "家用电器", "国防军工", "农林牧渔", "钢铁",
]
CITIES = ["北京", "上海", "深圳", "广州", "杭州", "成都", "武汉", "南京", "西安", "重庆"]
NAME_WORDS = ["华信", "恒瑞", "蓝海", "中科", "天成", "金桥", "云智", "长风", "启元", "盛世",
              "瑞丰", "鼎立", "科创", "远景", "宏图", "新元", "力合", "博达", "星辰", "万象"]


def trading_days() -> list[date]:
    days, d = [], CAL_START
    while d <= CAL_END:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def month_ends() -> list[str]:
    out, y, m = [], CAL_START.year, CAL_START.month
    while (y, m) <= (CAL_END.year, CAL_END.month):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        last = date(ny, nm, 1) - timedelta(days=1)
        if CAL_START <= last <= CAL_END:
            out.append(last.isoformat())
        y, m = ny, nm
    return out


def bulk(conn: sqlite3.Connection, sql: str, rows, label: str) -> int:
    t0, n, buf, batch = time.perf_counter(), 0, [], 20000
    for row in rows:
        buf.append(row)
        if len(buf) >= batch:
            conn.executemany(sql, buf)
            n += len(buf)
            buf.clear()
            rate = n / max(time.perf_counter() - t0, 1e-6)
            print(f"  {label}: {n:>9,} rows ({rate:>8,.0f}/s)", end="\r", flush=True)
    if buf:
        conn.executemany(sql, buf)
        n += len(buf)
    conn.commit()
    print(f"  {label}: {n:>9,} rows in {time.perf_counter() - t0:6.1f}s")
    return n


def rand_date(days: list[date]) -> str:
    return random.choice(days).isoformat()


def rand_time() -> str:
    return f"{random.randint(9, 14):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"


def seed(scale: float) -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    days = trading_days()
    recent_250 = days[-250:]
    snapshots = month_ends()
    n_branch = 30
    n_customer = int(15_000 * scale)
    n_account = int(25_000 * scale)
    n_product = 3_000
    n_order = int(450_000 * scale)
    n_trade = int(1_000_000 * scale)
    n_settle = int(120_000 * scale)
    n_margin = int(50_000 * scale)
    n_alert = int(15_000 * scale)

    print(f"[1/9] branches / products / customers / accounts")
    bulk(conn, "INSERT INTO branches VALUES (?,?,?,?,?)",
         ((i, f"{fake.city()}{fake.street_name()}证券营业部", random.choice(CITIES),
           fake.name(), rand_date(days)) for i in range(1, n_branch + 1)), "branches")

    symbols: set[str] = set()
    while len(symbols) < n_product:
        symbols.add(random.choice("603") + f"{random.randint(0, 99999):05d}")
    sym_list = sorted(symbols)
    types = random.choices(["股票", "债券", "基金", "ETF"], weights=[70, 10, 12, 8], k=n_product)
    base_price = [round(random.uniform(5, 300), 2) for _ in range(n_product)]
    fund_ids = [i for i in range(1, n_product + 1) if types[i - 1] in ("基金", "ETF")][:500]

    def products():
        for i in range(1, n_product + 1):
            t = types[i - 1]
            yield (i, sym_list[i - 1], f"{random.choice(NAME_WORDS)}{t}{random.randint(1, 99):02d}",
                   t, random.choice(INDUSTRIES), "SSE" if sym_list[i - 1][0] == "6" else "SZSE",
                   rand_date(days), 1 if random.random() < 0.95 else 0)
    bulk(conn, "INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", products(), "products")

    def customers():
        for i in range(1, n_customer + 1):
            yield (i, fake.name(), random.choice("MF"),
                   date(random.randint(1960, 2005), random.randint(1, 12), random.randint(1, 28)).isoformat(),
                   "199" + f"{random.randint(0, 99999999):08d}",
                   random.choices(["C1", "C2", "C3", "C4", "C5"], weights=[5, 20, 40, 25, 10])[0],
                   random.choice(["教师", "医生", "工程师", "公务员", "个体经营", "企业职员", "退休", "自由职业"]),
                   random.choice(CITIES), rand_date(days), random.randint(1, n_branch))
    bulk(conn, "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?)", customers(), "customers")

    def accounts():
        for i in range(1, n_account + 1):
            yield (i, random.randint(1, n_customer), random.randint(1, n_branch),
                   random.choices(["普通", "信用", "期权"], weights=[80, 15, 5])[0],
                   random.choices(["正常", "冻结", "销户"], weights=[90, 6, 4])[0],
                   rand_date(days), round(random.uniform(0, 2_000_000), 2),
                   round(random.uniform(0, 5_000_000), 2))
    bulk(conn, "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", accounts(), "accounts")

    print("[2/9] orders -> trades（成交与委托保持账户/产品/方向一致）")
    order_meta_acct: list[int] = []
    order_meta_prod: list[int] = []
    order_meta_side: list[str] = []
    order_meta_price: list[float] = []
    filled_ids: list[int] = []

    def orders():
        for i in range(1, n_order + 1):
            acct = random.randint(1, n_account)
            prod = random.randint(1, n_product)
            side = random.choice("BS")
            price = round(base_price[prod - 1] * random.uniform(0.9, 1.1), 2)
            qty = 100 * random.randint(1, 100)
            status = random.choices(["已成交", "部分成交", "已撤单", "已报待成"], weights=[82, 6, 9, 3])[0]
            order_meta_acct.append(acct)
            order_meta_prod.append(prod)
            order_meta_side.append(side)
            order_meta_price.append(price)
            if status in ("已成交", "部分成交"):
                filled_ids.append(i)
            yield (i, acct, prod, side, random.choices(["限价", "市价"], weights=[75, 25])[0],
                   price, qty, status, f"{rand_date(days)} {rand_time()}")
    bulk(conn, "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", orders(), "orders")

    def trades():
        for i in range(1, n_trade + 1):
            oid = random.choice(filled_ids)
            price = round(order_meta_price[oid - 1] * random.uniform(0.997, 1.003), 2)
            qty = 100 * random.randint(1, 100)
            amount = round(price * qty, 2)
            d = rand_date(recent_250)
            yield (i, oid, order_meta_acct[oid - 1], order_meta_prod[oid - 1],
                   order_meta_side[oid - 1], price, qty, amount,
                   round(amount * 0.0003 + 5, 2), d, rand_time())
    bulk(conn, "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)", trades(), "trades")
    del order_meta_acct, order_meta_prod, order_meta_side, order_meta_price, filled_ids

    print("[3/9] quotes_daily（近 250 个交易日随机游走）")
    def quotes():
        qid = 0
        for prod in range(1, n_product + 1):
            if random.random() < 0.34:      # 2/3 产品有行情 → 约 50 万行
                p = base_price[prod - 1]
                for d in recent_250:
                    o = round(p * random.uniform(0.98, 1.02), 2)
                    c = round(o * random.uniform(0.95, 1.05), 2)
                    h = round(max(o, c) * random.uniform(1.0, 1.03), 2)
                    l = round(min(o, c) * random.uniform(0.97, 1.0), 2)
                    p = c
                    qid += 1
                    vol = random.randint(100, 500_000)
                    yield (qid, prod, d.isoformat(), o, h, l, c, vol, round((o + c + h + l) / 4 * vol, 2))
    bulk(conn, "INSERT INTO quotes_daily VALUES (?,?,?,?,?,?,?,?,?)", quotes(), "quotes_daily")

    print("[4/9] holdings（月末快照，去重）")
    def holdings():
        hid, seen = 0, set()
        for snap in snapshots:
            k = max(1, int(50_000 * scale / len(snapshots)))
            for _ in range(k):
                key = (random.randint(1, n_account), random.randint(1, n_product), snap)
                if key in seen:
                    continue
                seen.add(key)
                hid += 1
                qty = 100 * random.randint(1, 200)
                cost = round(random.uniform(5, 300), 2)
                yield (hid, key[0], key[1], snap, qty, cost, round(qty * cost * random.uniform(0.85, 1.35), 2))
                if hid >= int(50_000 * scale):
                    return
    bulk(conn, "INSERT INTO holdings VALUES (?,?,?,?,?,?,?)", holdings(), "holdings")

    print("[5/9] settlements")
    def settlements():
        for i in range(1, n_settle + 1):
            buy = round(random.uniform(0, 500_000), 2)
            sell = round(random.uniform(0, 500_000), 2)
            fee = round(buy * 0.0003 + sell * 0.0003 + 10, 2)
            yield (i, random.randint(1, n_account), rand_date(days),
                   buy, sell, fee, round(sell - buy - fee, 2))
    bulk(conn, "INSERT INTO settlements VALUES (?,?,?,?,?,?,?)", settlements(), "settlements")

    print("[6/9] margin_trades")
    def margins():
        for i in range(1, n_margin + 1):
            amt = round(random.uniform(10_000, 1_000_000), 2)
            yield (i, random.randint(1, n_account), random.randint(1, n_product),
                   random.choice(["融资", "融券"]), amt, round(amt * random.uniform(0.2, 1.5), 2),
                   rand_date(days))
    bulk(conn, "INSERT INTO margin_trades VALUES (?,?,?,?,?,?,?)", margins(), "margin_trades")

    print("[7/9] fund_nav（500 只基金 × 近 400 交易日）")
    nav_days = days[-400:]
    def navs():
        nid = 0
        for prod in fund_ids:
            nav = round(random.uniform(0.8, 4.0), 4)
            for d in nav_days:
                nav = round(max(nav * random.uniform(0.985, 1.015), 0.2), 4)
                nid += 1
                yield (nid, prod, d.isoformat(), nav, round(nav * random.uniform(1.0, 1.6), 4))
    bulk(conn, "INSERT INTO fund_nav VALUES (?,?,?,?,?)", navs(), "fund_nav")

    print("[8/9] risk_alerts")
    def alerts():
        for i in range(1, n_alert + 1):
            yield (i, random.randint(1, n_account),
                   random.choice(["集中度超限", "异常交易", "两融平仓预警", "适当性不匹配"]),
                   random.choices(["低", "中", "高"], weights=[50, 35, 15])[0],
                   rand_date(days), fake.sentence(nb_words=8)[:80], 1 if random.random() < 0.6 else 0)
    bulk(conn, "INSERT INTO risk_alerts VALUES (?,?,?,?,?,?,?)", alerts(), "risk_alerts")

    print("[9/9] 建索引 + 统计")
    conn.executescript(INDEX_PATH.read_text(encoding="utf-8"))
    conn.commit()

    print("\n===== 验证 =====")
    total = 0
    for (tbl,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        (cnt,) = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        total += cnt
        print(f"  {tbl:<16} {cnt:>10,}")
    print(f"  {'TOTAL':<16} {total:>10,}")

    t0 = time.perf_counter()
    row = conn.execute(
        "SELECT b.branch_name, ROUND(SUM(t.amount)/1e8, 2) AS yiyuan "
        "FROM trades t JOIN accounts a ON a.account_id=t.account_id "
        "JOIN branches b ON b.branch_id=a.branch_id "
        "WHERE t.trade_date >= '2026-08-01' "
        "GROUP BY b.branch_name ORDER BY yiyuan DESC LIMIT 5").fetchall()
    dt = time.perf_counter() - t0
    print(f"\n  分析查询（近一月各营业部成交额 Top5，{(dt)*1000:.0f} ms）：")
    for r in row:
        print(f"    {r[0]}  {r[1]} 亿")

    size_mb = DB_PATH.stat().st_size / 1e6
    print(f"\n  库文件: {DB_PATH}  ({size_mb:,.0f} MB)")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0, help="行数缩放系数")
    seed(ap.parse_args().scale)
