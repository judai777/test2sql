-- 在 seed.py 完成批量插入后创建（先建索引会拖慢装载）
CREATE INDEX idx_trades_account  ON trades(account_id);
CREATE INDEX idx_trades_product  ON trades(product_id);
CREATE INDEX idx_trades_date     ON trades(trade_date);
CREATE INDEX idx_orders_account  ON orders(account_id);
CREATE INDEX idx_quotes_date     ON quotes_daily(trade_date);
CREATE INDEX idx_holdings_acct   ON holdings(account_id);
CREATE INDEX idx_settle_acct     ON settlements(account_id);
CREATE INDEX idx_margin_acct     ON margin_trades(account_id);
CREATE INDEX idx_alerts_acct     ON risk_alerts(account_id);
CREATE INDEX idx_customers_branch ON customers(branch_id);
CREATE INDEX idx_accounts_customer ON accounts(customer_id);
