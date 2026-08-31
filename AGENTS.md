# AGENTS.md — Test2SQL 代理协作约定

本文件约束所有在此仓库工作的 AI 代理（ZCode / Claude Code / Codex 等）。

## 0. 代码定位（最高优先级约定）

**优先使用 codegraph 来定位代码**；未查询 codegraph 前，禁止全文 grep 或盲目整读文件。

```bash
codegraph explore "ToolRegistry execute error observation"  # 首选：源码+调用路径+影响面一步到位
codegraph query <关键词>          # 符号搜索（--kind/--limit/--json）
codegraph callers <符号>          # 谁调用了它
codegraph callees <符号>          # 它调用了谁
codegraph impact <符号>           # 改动影响半径
codegraph node <符号|文件>        # 单符号源码 / 带行号读文件
codegraph status                  # 索引统计与待同步项
```

- **查询用符号名或英文关键词**（如 `ToolRegistry execute error`）；中文自然语言长句查不中（索引匹配的是符号与代码文本）。
- 索引随文件改动自动同步（watcher）；批量生成/修改文件后立即查询时先 `codegraph sync`。
- `explore` 返回的是逐字源码，等同已 Read 过该文件，不要再重复读。

## 1. 项目速览

- 定位：面向证券/银行业务人员的自然语言取数 Agent（Text2SQL）。
- 三层架构：**路由层**（意图/权限/会话）→ **管理层**（确定性治理，零 LLM）→ **执行层**（唯一 LLM，计划无关 ReAct 引擎）。
- 代码：`src/t2s/`（`config.py`、`llm/`、`tools/`）+ `tests/` + `scripts/smoke_tools.py`。
- 模块化结构（ADR-005，M2 起强制）：`models/`（纯数据模型）· `llm/` · `tools/` · `storage/`（DB/向量唯一出入口，manager 与 executor 均可访问）· `manager/` · `executor/`（含 prompts.py）· `router/` · `ui/`（只渲染零逻辑）· `utils/`。依赖单向：ui→router→manager→executor→{tools,llm}。
- 权威设计：`docs/design.md`；产品需求：`docs/PRD.md`；调研底稿：`docs/research/`。

## 2. 文档纪律（硬约定）

- 一切**设计决策**（架构/选型/范围裁剪）→ 当天写 `docs/decisions/ADR-NNN-*.md`。
- 一切**功能点更改** → 写 `docs/changes/CHG-NNN-*.md`。
- 文档层级：`research/`（输入）→ **PRD**（做什么，`docs/PRD.md`）→ **design.md**（怎么做）→ ADR/CHG（变更）。
- 冲突裁决：最新 ADR/CHG > PRD ≈ design.md > research；写完任何文档必须登记 `docs/README.md` 主索引。

## 3. 环境与命令

- Python：conda `t2s`（3.11.16）→ `D:\Anaconda\envs\t2s\python.exe`。
- 测试：`D:\Anaconda\envs\t2s\python.exe -m pytest`——改任何工具/校验逻辑后必跑。
- 评测：`PYTHONPATH=src python -m eval.runner`（需 .env；`--verify-golden` 离线校验金标；`--no-memory` 消融）——改 prompt/工具/护栏后必跑回归。
- demo 库：`db/securities.db`（219 万行合成数据，只读使用；重建：`db/seed.py`）。
- LLM：`.env`（参考 `.env.example`），OpenAI 兼容协议。
- 手动冒烟：`PYTHONPATH=src python scripts/smoke_tools.py`。

## 4. 修改红线

- 改 `db/schema.sql` 必须同步 `src/t2s/tools/metadata.py`（get_schema / search_schema 的数据源）。
- `execute_sql` 的安全护栏（只读连接 / 写操作黑名单 / LIMIT 强制 / 超时中断）不可绕过或放松。
- 工具失败必须"错误即观察"回填，不允许向引擎抛异常。

## 5. 工作流 Playbooks（ADR-006，位于 `.agent/playbooks/`）

| Playbook | 触发时机 |
|---|---|
| `init-project.md` | **新会话处理本仓库的第一步**：恢复上下文并输出状态卡 |
| `handoff.md` | 会话收尾 / 上下文将满 / 用户说收工：交接文档入库 `docs/handoffs/` |
| `diagnose.md` | 测试红 / 行为异常 / 报错：先建最小反馈回路再修复，双绿后落档 |

触发即按 playbook 步骤执行；环境内的全局技能（handoff / diagnosing-bugs）是能力底座，playbook 是仓库级绑定。
