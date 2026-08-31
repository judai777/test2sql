"""业务库元数据：12 张表的结构、注释与枚举——get_schema / search_schema 的数据源。

单一事实来源。修改 db/schema.sql 时必须同步此处（列注释会进入 LLM 提示词，
是 Text2SQL 准确率的第一杠杆，见 DB-GPT 调研报告 §3.2）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    name: str
    ctype: str
    comment: str
    enum: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableMeta:
    name: str
    purpose: str
    columns: tuple[Column, ...]
    fks: tuple[tuple[str, str, str], ...] = ()  # (列, 引用表, 引用列)
    uniques: tuple[str, ...] = ()

    @property
    def column_names(self) -> frozenset[str]:
        return frozenset(c.name.lower() for c in self.columns)

    @property
    def search_corpus(self) -> str:
        parts = [self.name, self.purpose]
        parts += [c.name for c in self.columns]
        parts += [c.comment for c in self.columns]
        parts += [v for c in self.columns for v in c.enum]
        return " ".join(parts).lower()


def _c(name: str, ctype: str, comment: str, enum: tuple[str, ...] = ()) -> Column:
    return Column(name, ctype, comment, enum)


TABLES: tuple[TableMeta, ...] = (
    TableMeta(
        name="branches",
        purpose="营业部主档：券商分支机构，可作为地域/组织维度聚合成交与客户数据",
        columns=(
            _c("branch_id", "INTEGER PRIMARY KEY", "营业部ID"),
            _c("branch_name", "TEXT NOT NULL", "营业部全名"),
            _c("city", "TEXT NOT NULL", "所在城市"),
            _c("manager_name", "TEXT", "负责人姓名"),
            _c("opened_date", "TEXT", "开业日期 YYYY-MM-DD"),
        ),
    ),
    TableMeta(
        name="customers",
        purpose="客户主档：自然人客户信息与适当性风险等级（C1 最低 C5 最高）",
        columns=(
            _c("customer_id", "INTEGER PRIMARY KEY", "客户唯一ID"),
            _c("name", "TEXT NOT NULL", "客户姓名（合成数据）"),
            _c("gender", "TEXT", "性别", ("M", "F")),
            _c("birth_date", "TEXT", "出生日期 YYYY-MM-DD"),
            _c("phone", "TEXT", "联系电话（合成号段，非真实）"),
            _c("risk_level", "TEXT", "适当性风险等级", ("C1", "C2", "C3", "C4", "C5")),
            _c("occupation", "TEXT", "职业"),
            _c("city", "TEXT", "常住城市"),
            _c("register_date", "TEXT", "注册日期"),
            _c("branch_id", "INTEGER", "开户营业部"),
        ),
        fks=(("branch_id", "branches", "branch_id"),),
    ),
    TableMeta(
        name="accounts",
        purpose="资金账户：客户在营业部开立的交易账户，含账户类型与资产快照",
        columns=(
            _c("account_id", "INTEGER PRIMARY KEY", "账户ID"),
            _c("customer_id", "INTEGER NOT NULL", "所属客户"),
            _c("branch_id", "INTEGER", "开户营业部"),
            _c("account_type", "TEXT", "账户类型", ("普通", "信用", "期权")),
            _c("status", "TEXT", "账户状态", ("正常", "冻结", "销户")),
            _c("open_date", "TEXT", "开户日期"),
            _c("cash_balance", "REAL", "资金余额（元）"),
            _c("market_value", "REAL", "持仓市值快照（元）"),
        ),
        fks=(
            ("customer_id", "customers", "customer_id"),
            ("branch_id", "branches", "branch_id"),
        ),
    ),
    TableMeta(
        name="products",
        purpose="证券产品主档：股票/债券/基金/ETF 的代码、名称、行业与交易所",
        columns=(
            _c("product_id", "INTEGER PRIMARY KEY", "产品ID"),
            _c("symbol", "TEXT UNIQUE NOT NULL", "6位证券代码，如 600519"),
            _c("name", "TEXT NOT NULL", "产品名称"),
            _c("product_type", "TEXT", "产品类型", ("股票", "债券", "基金", "ETF")),
            _c("industry", "TEXT", "所属行业（申万一级，简化）"),
            _c("exchange", "TEXT", "交易所", ("SSE", "SZSE")),
            _c("list_date", "TEXT", "上市日期"),
            _c("is_active", "INTEGER", "是否活跃 1/0"),
        ),
    ),
    TableMeta(
        name="orders",
        purpose="委托记录：客户下单流水，含买卖方向、委托价格与状态",
        columns=(
            _c("order_id", "INTEGER PRIMARY KEY", "委托ID"),
            _c("account_id", "INTEGER NOT NULL", "账户ID"),
            _c("product_id", "INTEGER NOT NULL", "产品ID"),
            _c("side", "TEXT", "买卖方向", ("B", "S")),
            _c("order_type", "TEXT", "委托类型", ("限价", "市价")),
            _c("price", "REAL", "委托价格（元）"),
            _c("quantity", "INTEGER", "委托数量（股，100 的整数倍）"),
            _c("status", "TEXT", "委托状态", ("已成交", "部分成交", "已撤单", "已报待成")),
            _c("order_time", "TEXT", "委托时间 YYYY-MM-DD HH:MM:SS"),
        ),
        fks=(
            ("account_id", "accounts", "account_id"),
            ("product_id", "products", "product_id"),
        ),
    ),
    TableMeta(
        name="trades",
        purpose="成交流水（核心大表，百万行）：每笔成交的价格、数量、金额与费用",
        columns=(
            _c("trade_id", "INTEGER PRIMARY KEY", "成交ID"),
            _c("order_id", "INTEGER NOT NULL", "关联委托ID"),
            _c("account_id", "INTEGER NOT NULL", "账户ID"),
            _c("product_id", "INTEGER NOT NULL", "产品ID"),
            _c("side", "TEXT", "买卖方向", ("B", "S")),
            _c("price", "REAL NOT NULL", "成交价格（元）"),
            _c("quantity", "INTEGER NOT NULL", "成交数量（股）"),
            _c("amount", "REAL NOT NULL", "成交金额（元）= 价格 × 数量"),
            _c("fee", "REAL", "费用（佣金等，元）"),
            _c("trade_date", "TEXT NOT NULL", "成交日期 YYYY-MM-DD"),
            _c("trade_time", "TEXT", "成交时间 HH:MM:SS"),
        ),
        fks=(
            ("order_id", "orders", "order_id"),
            ("account_id", "accounts", "account_id"),
            ("product_id", "products", "product_id"),
        ),
    ),
    TableMeta(
        name="quotes_daily",
        purpose="日行情：每只证券每个交易日的 OHLC、成交量与成交额",
        columns=(
            _c("quote_id", "INTEGER PRIMARY KEY", "行情ID"),
            _c("product_id", "INTEGER NOT NULL", "产品ID"),
            _c("trade_date", "TEXT NOT NULL", "交易日 YYYY-MM-DD"),
            _c("open", "REAL", "开盘价"),
            _c("high", "REAL", "最高价"),
            _c("low", "REAL", "最低价"),
            _c("close", "REAL", "收盘价"),
            _c("volume", "INTEGER", "成交量（手）"),
            _c("turnover", "REAL", "成交额（元）"),
        ),
        fks=(("product_id", "products", "product_id"),),
        uniques=("(product_id, trade_date)",),
    ),
    TableMeta(
        name="holdings",
        purpose="持仓快照：月末时点各账户的证券持仓数量、成本与市值",
        columns=(
            _c("holding_id", "INTEGER PRIMARY KEY", "持仓ID"),
            _c("account_id", "INTEGER NOT NULL", "账户ID"),
            _c("product_id", "INTEGER NOT NULL", "产品ID"),
            _c("snapshot_date", "TEXT NOT NULL", "快照日期（月末）"),
            _c("quantity", "INTEGER", "持仓数量（股）"),
            _c("cost_price", "REAL", "成本价（元）"),
            _c("market_value", "REAL", "市值（元）"),
        ),
        fks=(
            ("account_id", "accounts", "account_id"),
            ("product_id", "products", "product_id"),
        ),
        uniques=("(account_id, product_id, snapshot_date)",),
    ),
    TableMeta(
        name="settlements",
        purpose="清算交收：账户维度的日度买卖金额与净入金汇总",
        columns=(
            _c("settle_id", "INTEGER PRIMARY KEY", "清算ID"),
            _c("account_id", "INTEGER NOT NULL", "账户ID"),
            _c("trade_date", "TEXT NOT NULL", "交易日"),
            _c("buy_amount", "REAL", "买入金额（元）"),
            _c("sell_amount", "REAL", "卖出金额（元）"),
            _c("fee_total", "REAL", "费用合计（元）"),
            _c("net_amount", "REAL", "净入金（元）= 卖出 − 买入 − 费用"),
        ),
        fks=(("account_id", "accounts", "account_id"),),
    ),
    TableMeta(
        name="margin_trades",
        purpose="两融业务流水：融资买入与融券卖出的发生额与期末负债",
        columns=(
            _c("margin_id", "INTEGER PRIMARY KEY", "流水ID"),
            _c("account_id", "INTEGER NOT NULL", "账户ID"),
            _c("product_id", "INTEGER NOT NULL", "产品ID"),
            _c("margin_type", "TEXT", "业务类型", ("融资", "融券")),
            _c("amount", "REAL", "发生金额（元）"),
            _c("balance", "REAL", "期末负债/券余额（元）"),
            _c("trade_date", "TEXT NOT NULL", "日期"),
        ),
        fks=(
            ("account_id", "accounts", "account_id"),
            ("product_id", "products", "product_id"),
        ),
    ),
    TableMeta(
        name="fund_nav",
        purpose="基金净值：每只基金每个交易日的单位净值与累计净值",
        columns=(
            _c("nav_id", "INTEGER PRIMARY KEY", "净值ID"),
            _c("product_id", "INTEGER NOT NULL", "基金产品ID"),
            _c("nav_date", "TEXT NOT NULL", "净值日期"),
            _c("nav", "REAL", "单位净值"),
            _c("accum_nav", "REAL", "累计净值"),
        ),
        fks=(("product_id", "products", "product_id"),),
        uniques=("(product_id, nav_date)",),
    ),
    TableMeta(
        name="risk_alerts",
        purpose="风控预警：集中度超限、异常交易、两融平仓预警与适当性不匹配事件",
        columns=(
            _c("alert_id", "INTEGER PRIMARY KEY", "预警ID"),
            _c("account_id", "INTEGER NOT NULL", "账户ID"),
            _c("alert_type", "TEXT", "预警类型", ("集中度超限", "异常交易", "两融平仓预警", "适当性不匹配")),
            _c("level", "TEXT", "级别", ("低", "中", "高")),
            _c("alert_date", "TEXT", "预警日期"),
            _c("detail", "TEXT", "详情描述"),
            _c("handled", "INTEGER", "是否已处置 1/0"),
        ),
        fks=(("account_id", "accounts", "account_id"),),
    ),
)

BY_NAME: dict[str, TableMeta] = {t.name: t for t in TABLES}


def render_ddl(meta: TableMeta) -> str:
    lines = [f"CREATE TABLE {meta.name} ("]
    width = max(len(c.name) for c in meta.columns)
    for c in meta.columns:
        enum_note = f"（取值: {'/'.join(c.enum)}）" if c.enum else ""
        lines.append(f"  {c.name:<{width}} {c.ctype},  -- {c.comment}{enum_note}")
    lines.append(");")
    if meta.fks:
        lines.append("-- 外键: " + "; ".join(f"{a} -> {b}.{c}" for a, b, c in meta.fks))
    if meta.uniques:
        lines.append("-- 唯一约束: " + "; ".join(meta.uniques))
    return "\n".join(lines)
