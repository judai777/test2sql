"""M5 评测运行器：golden 集执行 + 结果集比对 + 记忆消融（PRD FR-9 / NFR-4）。

用法（--verify-golden 外均需 .env 配置真实 LLM）:
  PYTHONPATH=src python -m eval.runner --verify-golden     # 只验证金标 SQL 可执行
  PYTHONPATH=src python -m eval.runner --limit 5           # 真实冒烟
  PYTHONPATH=src python -m eval.runner                     # 全量 50 条
  PYTHONPATH=src python -m eval.runner --no-memory         # 消融：关闭记忆注入
报告: eval/reports/report-<tag>.json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = ROOT / "eval" / "golden.jsonl"
REPORT_DIR = ROOT / "eval" / "reports"

_TIER_NAMES = {"single": "单表", "join": "多表JOIN", "agg": "聚合",
               "time": "时间口径", "ambiguity": "歧义澄清"}

_EVAL_ASK = "（评测模式）请基于最常见的业务口径执行，并在回答开头声明所做假设。"


@dataclass
class EvalCase:
    id: str
    tier: str
    question: str
    golden_sql: str | None = None
    judge: str = "result_match"   # result_match | valid_sql


@dataclass
class CaseResult:
    id: str
    tier: str
    question: str
    agent_sql: str | None = None
    passed: bool = False
    valid: bool = False           # 产出了可执行的只读 SELECT
    error: str = ""
    steps: int = 0
    tokens: int = 0
    elapsed_ms: int = 0
    detail: str = ""


def load_cases(path: Path = GOLDEN_PATH) -> list[EvalCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        cases.append(EvalCase(id=obj["id"], tier=obj["tier"], question=obj["question"],
                              golden_sql=obj.get("golden_sql"), judge=obj.get("judge", "result_match")))
    return cases


# ---------- 结果集比对 ----------

def normalize_cell(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    return str(v)


def has_order_by(sql: str) -> bool:
    return re.search(r"\border\s+by\b", sql, re.IGNORECASE) is not None


def run_query(conn: sqlite3.Connection, sql: str, limit: int = 200) -> tuple[list[str], list[tuple]]:
    """只读执行；无 ORDER BY/LIMIT 的语句包裹 LIMIT 防全量拉取。返回 (列名, 行)。"""
    if not re.search(r"\border\s+by\b|\blimit\b", sql, re.IGNORECASE):
        sql = f"SELECT * FROM ({sql}) AS __eval LIMIT {limit}"
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description or []]
    return cols, cur.fetchmany(limit)


def _project(cols: list[str], rows: list[tuple], wanted: list[str]) -> list[tuple] | None:
    """按金标列名投影 agent 结果（列序无关、信息超集友好）；列缺失返回 None。"""
    lower = {c.lower(): i for i, c in enumerate(cols)}
    idx = [lower.get(w.lower()) for w in wanted]
    if any(i is None for i in idx):
        return None
    return [tuple(row[i] for i in idx) for row in rows]  # type: ignore[index]


def _norm_row(row, k: float = 1.0) -> tuple:
    out = []
    for v in row:
        if v is None:
            out.append(None)
        elif isinstance(v, bool):
            out.append(int(v))
        elif isinstance(v, (int, float)):
            out.append(round(float(v) * k, 2))
        else:
            out.append(str(v))
    return tuple(out)


def _row_close(gr: tuple, ar: tuple, k: float) -> bool:
    """逐格比较：数值容差 max(0.011, 0.1%×金标)；字符串精确。"""
    for gv, av in zip(gr, ar):
        if gv is None or av is None:
            if gv is not av:
                return False
        elif isinstance(gv, bool) or isinstance(av, bool):
            if int(gv) != int(av):
                return False
        elif isinstance(gv, (int, float)) and isinstance(av, (int, float)):
            if abs(float(av) * k - float(gv)) > max(0.011, abs(float(gv)) * 0.001):
                return False
        elif str(gv) != str(av):
            return False
    return True


def _match(golden: list[tuple], agent: list[tuple], ordered: bool) -> tuple[bool, str]:
    g = [_norm_row(r) for r in golden]
    a = [_norm_row(r) for r in agent]
    if (g == a) if ordered else (Counter(g) == Counter(a)):
        return True, ""
    # 量纲因子：agent 可能做单位换算（元→万/亿）。k 限定 10 的幂（真实换算只可能是
    # 10 的幂），且要求 ≥2 行（单值结果没有换算证据，防止任意比例假阳性，CHG-008）。
    if len(golden) >= 2:
        k0 = 1.0
        for gr, ar in zip(golden, agent):
            for gv, av in zip(gr, ar):
                if isinstance(gv, (int, float)) and isinstance(av, (int, float)) \
                        and not isinstance(gv, bool) and not isinstance(av, bool) \
                        and av and gv:
                    k0 = abs(float(gv) / float(av))
                    break
            if k0 != 1.0:
                break
        if k0 != 1.0:
            exp = round(__import__("math").log10(k0))
            for e in (exp - 1, exp, exp + 1):
                if e < -6 or e > 12:
                    continue
                k = 10.0 ** e
                if ordered:
                    if all(_row_close(gv, av, k) for gv, av in zip(golden, agent)) \
                            and len(golden) == len(agent):
                        return True, f"量纲因子 1e{e} 下一致"
                elif all(
                    any(_row_close(gv, av, k) for av in agent) and
                    sum(1 for av in agent if _row_close(gv, av, k)) == 1
                    for gv in golden
                ) and len(golden) == len(agent):
                    return True, f"量纲因子 1e{e} 下一致"
    return False, (f"有序不一致（金标 {len(golden)} 行 / 实际 {len(agent)} 行）" if ordered
                   else f"结果集不一致（金标 {len(golden)} 行 / 实际 {len(agent)} 行）")


def _type_sig(v) -> str:
    if v is None:
        return "0"
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return "n"
    return "s"


def _fuzzy_project(golden_cols: list[str], golden_rows: list[tuple],
                   agent_cols: list[str], agent_rows: list[tuple]) -> list[tuple] | None:
    """类型签名贪婪对齐（别名/列超集容错，CHG-014）：金标列依序匹配类型兼容的未用 agent 列。"""
    if not golden_rows or not agent_rows or len(agent_cols) < len(golden_cols):
        return None
    first_g, first_a = golden_rows[0], agent_rows[0]
    used: set[int] = set()
    mapping: list[int] = []
    for gi in range(len(golden_cols)):
        gt = _type_sig(first_g[gi])
        found = None
        for ai in range(len(agent_cols)):
            if ai in used:
                continue
            if _type_sig(first_a[ai]) == gt:
                found = ai
                break
        if found is None:
            return None
        used.add(found)
        mapping.append(found)
    return [tuple(r[i] for i in mapping) for r in agent_rows]


def compare(golden_cols: list[str], golden_rows: list[tuple],
            agent_cols: list[str], agent_rows: list[tuple], ordered: bool) -> tuple[bool, str]:
    """结果集比对：金标列投影对齐 → 数值容差归一 → 行多集合（或有序）匹配 → 量纲因子
    → 类型签名模糊对齐（别名/列超集容错，CHG-014）。"""
    projected = None
    if len(agent_cols) >= len(golden_cols):
        projected = _project(agent_cols, agent_rows, golden_cols)
        if projected is not None:
            ok, detail = _match(golden_rows, projected, ordered)
            if ok:
                return True, detail
    fuzzy = _fuzzy_project(golden_cols, golden_rows, agent_cols, agent_rows)
    if fuzzy is not None:
        ok, detail = _match(golden_rows, fuzzy, ordered)
        if ok:
            return True, "类型对齐下一致"
    return _match(golden_rows, agent_rows, ordered)


def judge_case(case: EvalCase, answer, conn: sqlite3.Connection) -> CaseResult:
    result = CaseResult(id=case.id, tier=case.tier, question=case.question,
                        agent_sql=answer.sql, steps=len(answer.steps),
                        tokens=answer.total_tokens, elapsed_ms=answer.elapsed_ms)
    sql = answer.sql
    if not sql:
        result.error = "未产出 SQL"
        if case.judge == "valid_sql" and answer.stop_reason == "final" and answer.content:
            result.passed, result.detail = True, "无 SQL 但完成澄清/说明"
        return result
    try:
        agent_cols, agent_rows = run_query(conn, sql)
    except sqlite3.Error as e:
        result.error = f"执行失败: {e}"
        return result
    result.valid = True
    if case.judge == "valid_sql":
        result.passed = True
        result.detail = "产出可执行 SQL"
        return result
    if not case.golden_sql:
        result.error = "金标缺失"
        return result
    golden_cols, golden_rows = run_query(conn, case.golden_sql)
    passed, detail = compare(golden_cols, golden_rows, agent_cols, agent_rows,
                             ordered=has_order_by(case.golden_sql))
    result.passed, result.detail = passed, detail
    return result


# ---------- 汇总 ----------

def summarize(results: list[CaseResult]) -> dict:
    total = len(results)
    passed = sum(r.passed for r in results)
    by_tier: dict[str, dict] = {}
    for tier in dict.fromkeys(r.tier for r in results):
        rs = [r for r in results if r.tier == tier]
        by_tier[tier] = {
            "label": _TIER_NAMES.get(tier, tier),
            "total": len(rs),
            "passed": sum(r.passed for r in rs),
            "accuracy": round(sum(r.passed for r in rs) / len(rs), 4) if rs else 0.0,
        }
    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "grammar_valid": round(sum(r.valid for r in results) / total, 4) if total else 0.0,
        "avg_steps": round(sum(r.steps for r in results) / total, 2) if total else 0.0,
        "avg_tokens": round(sum(r.tokens for r in results) / total) if total else 0,
        "by_tier": by_tier,
    }


def render_summary(summary: dict, tag: str) -> str:
    lines = [f"\n===== 评测报告（{tag}）=====",
             f"准确率(结果集比对): {summary['accuracy']:.1%}  ({summary['passed']}/{summary['total']})",
             f"语法有效率: {summary['grammar_valid']:.1%}  平均步数: {summary['avg_steps']}  "
             f"平均token: {summary['avg_tokens']}"]
    for tier, info in summary["by_tier"].items():
        lines.append(f"  [{info['label']:<6}] {info['passed']}/{info['total']}  "
                     f"({info['accuracy']:.0%})")
    return "\n".join(lines)


# ---------- 运行 ----------

def run_eval(cases: list[EvalCase], service, ctx, judge_conn: sqlite3.Connection,
             memory_store=None) -> list[CaseResult]:
    """逐案执行；memory_store 传入时，评测新增的样例在结束后回收（不污染用户记忆）。"""
    before_ids = {p.id for p in memory_store.all_pairs()} if memory_store else set()
    results = []
    for i, case in enumerate(cases, 1):
        t0 = time.perf_counter()
        try:
            answer = service.handle(case.question, session_id=f"eval-{case.id}", ctx=ctx)
        except Exception as e:  # noqa: BLE001 —— 单案失败不中断评测
            answer = type("Ans", (), {"sql": None, "content": "", "stop_reason": "runner_error",
                                      "steps": [], "total_tokens": 0,
                                      "elapsed_ms": round((time.perf_counter() - t0) * 1000)})()
            results.append(CaseResult(id=case.id, tier=case.tier, question=case.question,
                                      error=f"服务异常: {e}"))
            continue
        result = judge_case(case, answer, judge_conn)
        results.append(result)
        mark = "✓" if result.passed else "✗"
        print(f"  [{i:>2}/{len(cases)}] {mark} {case.id} {_TIER_NAMES.get(case.tier, case.tier)}"
              f"  {result.detail or result.error}"[:110])
    if memory_store:
        for p in memory_store.all_pairs():
            if p.id not in before_ids:
                memory_store.remove_pair(p.id)
    return results


def build_real_service(cfg, use_memory: bool):
    """组装真实服务栈（需 .env）。评测的会话/审计写 eval 专用库，不污染用户记忆库。"""
    from t2s.executor import ReActEngine
    from t2s.llm import EmbeddingClient, LLMClient
    from t2s.manager import MemoryService
    from t2s.router import RouterService
    from t2s.storage import AuditLog, MemoryStore, SessionStore, open_db
    from t2s.tools import ToolContext, build_registry

    llm = LLMClient(cfg.llm)
    engine = ReActEngine(llm, build_registry())
    eval_dir = ROOT / "eval" / "reports"
    eval_dir.mkdir(parents=True, exist_ok=True)
    eval_conn = open_db(eval_dir / "eval-memory.db")
    memory = None
    memory_store = None
    if use_memory:
        embedder = EmbeddingClient(cfg.embedding) if cfg.embedding.enabled else None
        memory_store = MemoryStore(open_db(cfg.tools.memory_db_path))
        memory = MemoryService(memory_store, embedder)
    service = RouterService(llm, engine, SessionStore(eval_conn), AuditLog(eval_conn),
                            memory=memory)
    ctx = ToolContext(db_path=cfg.tools.db_path,
                      sql_timeout_s=cfg.tools.sql_timeout_s,
                      sql_row_limit=cfg.tools.sql_row_limit,
                      ask_user=lambda q, o: _EVAL_ASK)
    return service, ctx, memory_store


def verify_golden(db_path: Path) -> int:
    """离线校验：每条金标 SQL 必须在业务库上可执行。"""
    conn = connect_ro(db_path)
    cases = load_cases()
    errors = 0
    for case in cases:
        if not case.golden_sql:
            print(f"  - {case.id} ({_TIER_NAMES.get(case.tier)}) 无金标（valid_sql 档）")
            continue
        try:
            rows = run_query(conn, case.golden_sql)
            print(f"  ✓ {case.id} {_TIER_NAMES.get(case.tier)}  {len(rows)} 行")
        except sqlite3.Error as e:
            errors += 1
            print(f"  ✗ {case.id}  金标 SQL 执行失败: {e}")
    print(f"\n金标校验完成：{len(cases) - errors}/{len(cases)} 可执行")
    return errors


def connect_ro(db_path: Path) -> sqlite3.Connection:
    from urllib.parse import quote
    return sqlite3.connect(f"file:{quote(db_path.resolve().as_posix())}?mode=ro", uri=True)


def rejudge(report_path: Path, db_path: Path) -> dict:
    """离线重判：用报告中保存的 agent_sql 按升级后的比对器重新判定（不耗 LLM）。"""
    data = json.loads(report_path.read_text(encoding="utf-8"))
    cases = {c.id: c for c in load_cases()}
    conn = connect_ro(db_path)
    results = []
    for c in data["cases"]:
        case = cases.get(c["id"])
        if case is None:
            continue
        answer = type("Ans", (), {"sql": c.get("agent_sql"), "content": " ",
                                  "stop_reason": "final", "steps": [],
                                  "total_tokens": c.get("tokens", 0),
                                  "elapsed_ms": c.get("elapsed_ms", 0)})()
        results.append(judge_case(case, answer, conn))
    summary = summarize(results)
    summary["tag"] = f"{data.get('tag', 'run')}-rejudged"
    summary["cases"] = [asdict(r) for r in results]
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Test2SQL golden 评测")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟）")
    ap.add_argument("--tier", action="append", default=[], help="只跑指定档（可多次）")
    ap.add_argument("--only", action="append", default=[], help="只跑指定 id（可多次，用于基础设施失败重试）")
    ap.add_argument("--no-memory", action="store_true", help="消融：关闭记忆注入")
    ap.add_argument("--tag", default="", help="报告文件名标签")
    ap.add_argument("--verify-golden", action="store_true", help="只离线校验金标 SQL")
    ap.add_argument("--rejudge", default="", help="离线重判指定报告 JSON")
    args = ap.parse_args()

    from t2s.config import AppConfig
    cfg = AppConfig.load()

    if args.verify_golden:
        return 1 if verify_golden(cfg.tools.db_path) else 0

    if args.rejudge:
        summary = rejudge(Path(args.rejudge), cfg.tools.db_path)
        REPORT_DIR.mkdir(exist_ok=True)
        out = REPORT_DIR / f"report-{summary['tag']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(render_summary(summary, summary["tag"]))
        print(f"报告已写入: {out}")
        return 0

    if not cfg.llm.api_key:
        print("未配置 T2S_LLM_API_KEY：真实评测需要 .env。")
        return 1

    cases = load_cases()
    if args.tier:
        cases = [c for c in cases if c.tier in set(args.tier)]
    if args.only:
        cases = [c for c in cases if c.id in set(args.only)]
    if args.limit:
        cases = cases[:args.limit]

    service, ctx, memory_store = build_real_service(cfg, use_memory=not args.no_memory)
    conn = connect_ro(cfg.tools.db_path)
    t0 = time.perf_counter()
    results = run_eval(cases, service, ctx, conn, memory_store=memory_store)
    wall = round(time.perf_counter() - t0)

    summary = summarize(results)
    tag = args.tag or ("no-memory" if args.no_memory else "full")
    summary["tag"], summary["wall_s"] = tag, wall
    summary["cases"] = [asdict(r) for r in results]

    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"report-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render_summary(summary, tag))
    print(f"报告已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
