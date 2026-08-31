-- Test2SQL demo 业务库：证券场景，12 张表
-- 结构参考真实券商业务，数据全部由 faker/random 合成（合规：不含任何真实客户数据）
-- 注释同时是 get_schema 工具的元数据来源，改动表结构必须同步此处注释

-- 营业部
CREATE TABLE branches (
  branch_id    INTEGER PRIMARY KEY,
  branch_name  TEXT NOT NULL,              -- 营业部名称，如"北京望京证券营业部"
  city         TEXT NOT NULL,
  manager_name TEXT,
  opened_date  TEXT                        -- YYYY-MM-DD
);

-- 客户主档
CREATE TABLE customers (
  customer_id   INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,             -- 合成中文姓名
  gender        TEXT CHECK (gender IN ('M','F')),
  birth_date    TEXT,
  phone         TEXT,                      -- 合成号段，非真实号码
  risk_level    TEXT CHECK (risk_level IN ('C1','C2','C3','C4','C5')),  -- 适当性风险等级
  occupation    TEXT,
  city          TEXT,
  register_date TEXT,
  branch_id     INTEGER REFERENCES branches(branch_id)
);

-- 资金账户（一个客户可开多个）
CREATE TABLE accounts (
  account_id   INTEGER PRIMARY KEY,
  customer_id  INTEGER NOT NULL REFERENCES customers(customer_id),
  branch_id    INTEGER REFERENCES branches(branch_id),
  account_type TEXT CHECK (account_type IN ('普通','信用','期权')),
  status       TEXT CHECK (status IN ('正常','冻结','销户')),
  open_date    TEXT,
  cash_balance REAL,                       -- 元，合成数据
  market_value REAL                          -- 持仓市值快照，元
);

-- 证券产品主档
CREATE TABLE products (
  product_id   INTEGER PRIMARY KEY,
  symbol       TEXT UNIQUE NOT NULL,       -- 6 位代码，如 600519
  name         TEXT NOT NULL,
  product_type TEXT CHECK (product_type IN ('股票','债券','基金','ETF')),
  industry     TEXT,                       -- 申万一级行业（简化）
  exchange     TEXT CHECK (exchange IN ('SSE','SZSE')),
  list_date    TEXT,
  is_active    INTEGER DEFAULT 1
);

-- 委托记录
CREATE TABLE orders (
  order_id   INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(account_id),
  product_id INTEGER NOT NULL REFERENCES products(product_id),
  side       TEXT CHECK (side IN ('B','S')),            -- B=买 S=卖
  order_type TEXT CHECK (order_type IN ('限价','市价')),
  price      REAL,
  quantity   INTEGER,                      -- 股/份，100 的整数倍
  status     TEXT CHECK (status IN ('已成交','部分成交','已撤单','已报待成')),
  order_time TEXT                          -- YYYY-MM-DD HH:MM:SS
);

-- 成交流水（核心大表，百万行级）
CREATE TABLE trades (
  trade_id  INTEGER PRIMARY KEY,
  order_id  INTEGER NOT NULL REFERENCES orders(order_id),
  account_id INTEGER NOT NULL REFERENCES accounts(account_id),
  product_id INTEGER NOT NULL REFERENCES products(product_id),
  side      TEXT CHECK (side IN ('B','S')),
  price     REAL NOT NULL,
  quantity  INTEGER NOT NULL,
  amount    REAL NOT NULL,                 -- 成交金额 = price * quantity
  fee       REAL,                          -- 佣金等费用
  trade_date TEXT NOT NULL,                -- YYYY-MM-DD
  trade_time TEXT                          -- HH:MM:SS
);

-- 日行情
CREATE TABLE quotes_daily (
  quote_id   INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(product_id),
  trade_date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  volume   INTEGER,                        -- 成交量（手）
  turnover REAL,                           -- 成交额（元）
  UNIQUE (product_id, trade_date)
);

-- 持仓快照（月末）
CREATE TABLE holdings (
  holding_id    INTEGER PRIMARY KEY,
  account_id    INTEGER NOT NULL REFERENCES accounts(account_id),
  product_id    INTEGER NOT NULL REFERENCES products(product_id),
  snapshot_date TEXT NOT NULL,             -- 月末日期
  quantity      INTEGER,
  cost_price    REAL,
  market_value  REAL,
  UNIQUE (account_id, product_id, snapshot_date)
);

-- 清算交收（账户日汇总）
CREATE TABLE settlements (
  settle_id  INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(account_id),
  trade_date TEXT NOT NULL,
  buy_amount  REAL,
  sell_amount REAL,
  fee_total   REAL,
  net_amount  REAL                          -- 净入金 = 卖出 - 买入 - 费用
);

-- 两融业务流水
CREATE TABLE margin_trades (
  margin_id   INTEGER PRIMARY KEY,
  account_id  INTEGER NOT NULL REFERENCES accounts(account_id),
  product_id  INTEGER NOT NULL REFERENCES products(product_id),
  margin_type TEXT CHECK (margin_type IN ('融资','融券')),
  amount      REAL,                        -- 发生金额
  balance     REAL,                        -- 期末负债/券余额
  trade_date  TEXT NOT NULL
);

-- 基金净值
CREATE TABLE fund_nav (
  nav_id    INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(product_id),
  nav_date  TEXT NOT NULL,
  nav       REAL,                          -- 单位净值
  accum_nav REAL,                          -- 累计净值
  UNIQUE (product_id, nav_date)
);

-- 风控预警
CREATE TABLE risk_alerts (
  alert_id   INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(account_id),
  alert_type TEXT CHECK (alert_type IN ('集中度超限','异常交易','两融平仓预警','适当性不匹配')),
  level      TEXT CHECK (level IN ('低','中','高')),
  alert_date TEXT,
  detail     TEXT,
  handled    INTEGER DEFAULT 0             -- 0=未处置 1=已处置
);
