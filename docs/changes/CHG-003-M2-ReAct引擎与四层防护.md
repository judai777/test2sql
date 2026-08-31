# CHG-003：M2 落地——ReAct 引擎核心与四层死循环防护

- 日期：2026-08-29
- 动机：执行 `docs/design.md` §10 里程碑 M2（项目心脏），PRD FR-3 / FR-4 / FR-5。
- 类型：新增

## 变更内容

| 文件 | 说明 |
|---|---|
| `src/t2s/models/task.py` | 输入输出协议：TaskRequest / Budget（四层防护参数化）/ TraceStep / Answer。首个落位 ADR-005 `models/` 约定的模块 |
| `src/t2s/executor/engine.py` | 计划无关 ReAct 引擎：决策→工具→观察回填→循环；无工具调用即终止；步数耗尽/墙钟/打转强停 → 无工具强制总结；同 SQL 第 3 次报错 → 诚实失败（含转人工建议） |
| `src/t2s/executor/guard.py` | LoopGuard：②连续重复/ABAB 振荡检测（warn 跳过执行、警告即观察；累计强停）③同 SQL 错误回喂限次 ④墙钟（perf_counter 时钟域） |
| `src/t2s/executor/prompts.py` | 系统提示词（六条硬约束改写自 DB-GPT 模板，调研报告 §3.3）+ 总结请求 + 兜底文案；few-shot 注入口预留（M4） |
| `src/t2s/ui/repl.py` | 裸 REPL 驱动器（单轮冒烟；M3 升级多轮）——ADR-005 首个 `ui/` 模块，零业务逻辑 |
| `tests/test_guard.py`（8 例） | 防护单元：连续重复 warn→stop、参数差异不算重复、ABAB 振荡、SQL 回喂限次、墙钟 |
| `tests/test_engine.py`（9 例） | 引擎行为（ScriptLLM 假 LLM，零网络）：happy path、**先声明后回填不变量**、步数耗尽总结、打转警告/强停、诚实失败、墙钟、拒绝不执行、token 累计 |

**三个引擎不变量全部有测试钉住**：先声明后回填（协议合法）、错误即观察（异常不逃逸）、拒绝不执行（带 tool_calls 但 finish_reason 不允许时不碰工具）。

## 过程中修复的缺陷（diagnose playbook 实战记录）

1. **Windows `time.time()` 粒度 ~15.6ms**：超时护栏/测试在 1ms 级查询上永远不触发（同一时钟 tick 内 `>deadline` 不成立）。护栏、引擎、SQL 超时全部改用单调高精度 `time.perf_counter()`。
2. **SQLite COUNT 的 B-tree 计数捷径**：`COUNT(*)` 笛卡尔积不产生逐行 VM 指令，progress handler 不触发；测试改用 SUM 强制逐行求值。
3. **测试场景踩中自家振荡检测**：两次相同坏 SQL 之间插入完全相同的 search（es→ss→es→ss→es）正好构成 ABAB 振荡，第 3 次坏 SQL 被振荡检测跳过，错误计数到不了 3——护栏行为正确，测试改为不同参数的 search。

## 验证结果（M2 完成判据）

- `pytest`：**52 passed**（M1 的 35 + M2 的 17）。
- "故意制造死循环可被四层分别拦截"：单测覆盖 ①步数耗尽 ②连续重复/振荡 ③同 SQL 三错 ④墙钟，四条路径各自触发并给出对应 stop_reason。
- ⏳ **"REPL 真实 LLM 跑通单轮取数"待用户填入 `.env` 的 API key 后验收**（引擎逻辑已被 ScriptLLM 全面验证，剩余的只是真实模型兼容性）：
  ```
  PYTHONPATH=src python -m t2s.ui.repl -q "上个月各营业部的成交额排行"
  ```

## 关联

ADR-001 D2/D6；ADR-005（models/executor/ui 首次实践）；PRD FR-3/4/5。
