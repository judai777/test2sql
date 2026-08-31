"""口径库/样例库种子数据（PRD FR-8：口径库人工维护入口）。

用法: PYTHONPATH=src python scripts/seed_memory.py
幂等：按 title/question upsert。样例库正式数据应由真实使用沉淀，这里仅放两条演示对。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from t2s.config import AppConfig
from t2s.llm import EmbeddingClient
from t2s.manager import MemoryService
from t2s.storage import MemoryStore, open_db

METRICS = [
    ("月活跃客户", "月活跃客户 = 自然月内有过成交（trades 表有记录）的客户数，"
                 "不含仅登录或仅查询未交易的用户。数据源：trades.trade_date。"),
    ("收益率口径", "客户收益率 =（期末市值 + 期间卖出金额 − 期初市值 − 期间买入金额）/ 期初市值。"
                 "涉及 holdings 月末快照与 settlements 汇总；未定义区间时必须向用户澄清。"),
    ("成交额口径", "成交额 = trades.amount 求和（已含买卖双向）；营业部维度经 accounts.branch_id 关联 branches。"),
]

DEMO_PAIRS = [
    ("各营业部近一月成交额排行",
     "SELECT b.branch_name, ROUND(SUM(t.amount) / 100000000, 2) AS yi\n"
     "FROM trades t\n"
     "JOIN accounts a ON a.account_id = t.account_id\n"
     "JOIN branches b ON b.branch_id = a.branch_id\n"
     "WHERE t.trade_date >= date('now', '-1 month')\n"
     "GROUP BY b.branch_name ORDER BY yi DESC LIMIT 20"),
    ("C4 级客户有多少",
     "SELECT COUNT(*) AS c4_customers\n"
     "FROM customers\n"
     "WHERE risk_level = 'C4'"),
]


def main() -> None:
    cfg = AppConfig.load()
    conn = open_db(cfg.tools.memory_db_path)
    embedder = EmbeddingClient(cfg.embedding) if cfg.embedding.enabled else None
    memory = MemoryService(MemoryStore(conn), embedder)
    for title, content in METRICS:
        memory.store.add_metric(title, content,
                                memory._embed_query(f"{title} {content}") if embedder else None)
    for question, sql in DEMO_PAIRS:
        memory.record_success(question, sql)
    # 种子数据直接进正式库（确认制下免确认，便于演示）
    for p in memory.store.candidate_pairs():
        if p.question in {q for q, _ in DEMO_PAIRS}:
            memory.store.confirm_pair(p.id)
    print(f"已写入口径 {len(METRICS)} 条、演示问答 {len(DEMO_PAIRS)} 条（confirmed）"
          f"（语义检索：{'开启 ' + cfg.embedding.model if embedder else '未配置，关键词降级'}）")


if __name__ == "__main__":
    main()
