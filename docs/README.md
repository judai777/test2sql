# Test2SQL 文档主索引

> 约定建立于 2026-08-29：**所有设计决策、功能变更、调研材料统一保存在 `docs/` 下**；每新增或修改文档，必须同步更新本索引的「文档清单」。

## 目录结构

```
docs/
├── README.md      ← 主索引（本文件，唯一的入口与登记处）
├── PRD.md         ← 产品需求（做什么/给谁做/验收标准）
├── design.md      ← 总设计文档（当前有效架构的权威描述）
├── decisions/     ← 决策记录 ADR：一次重大设计决策一个文件
├── changes/       ← 变更记录 CHG：一次功能点更改一个文件
├── handoffs/      ← 会话交接 HO：跨会话工作状态的入库快照
├── research/      ← 调研报告（只读输入，落定后不再修改）
└── archify/       ← 架构可视化（archify 产出的自包含交互 HTML，自用）
```

## 命名与模板

### 决策记录 `decisions/ADR-NNN-短标题.md`
```markdown
# ADR-NNN：标题
- 状态：提议 / 已接受 / 已废弃 / 被 ADR-MMM 取代
- 日期：YYYY-MM-DD
- 背景：为什么现在要做这个决策
- 决策：最终选择（一句话可执行）
- 备选与否决理由：列出至少一个被否决的选项及原因
- 影响面：涉及哪些模块 / 哪些文档需要回写
```

### 变更记录 `changes/CHG-NNN-短标题.md`
```markdown
# CHG-NNN：标题
- 日期：YYYY-MM-DD
- 动机：为什么改
- 变更内容：功能点层面改了什么（新增/修改/删除）
- 影响面：涉及模块 + 需回写的文档
- 关联决策：ADR-XXX（如有）
```

## 维护约定

1. **设计决策**（架构、选型、范围裁剪）一经拍板 → 当天写 ADR，编号递增、废弃不删除不重用。
2. **功能点更改**（新增/修改/删除）→ 写 CHG；若变更推翻既有决策，同时把对应 ADR 标注「被取代」并新建 ADR。
3. `design.md` 与 ADR/CHG 冲突时，**以最新 ADR/CHG 为准**，并尽快回写 design.md 使其收敛。
4. research/ 为只读输入：调研结论进入项目只能通过 ADR 引用，不直接改调研文件。
5. 每次写完任何文档 → 更新本文件「文档清单」表格。

## 文档清单

| 文档 | 类型 | 一句话说明 | 状态 |
|---|---|---|---|
| `../AGENTS.md`（根） | 代理约定 | **优先使用 codegraph 定位代码** + 文档纪律 + 环境命令 + 修改红线 + 工作流 | 生效中 |
| `GETTING_STARTED.md` | 启动指南 | 从零搭建 / 三种启动方式 / 测试评测 / 运维 / 故障排查（全命令已验证） | v1.0 |
| `../.agent/playbooks/`（根） | 工作流 | init-project / handoff / diagnose 三个代理工作流（ADR-006） | 生效中 |
| `PRD.md` | 产品需求 | 背景与替代叙事、目标/非目标、用户故事与验收、FR/NFR、评测指标、风险（ADR-004） | v1.0 |
| `design.md` | 总设计 | 三层架构 + ReAct 引擎 + 工具五件套 + 记忆三层 + 四层防护 + 评测体系 + 里程碑 M0~M6 | 有效 |
| `decisions/ADR-001-架构基线九大决策.md` | ADR | grill-me 九轮拷打收敛的架构基线（含两次质疑复核记录） | 已接受 |
| `decisions/ADR-002-文档体系与决策记录约定.md` | ADR | docs/ 五区文档体系、ADR/CHG 模板与冲突裁决顺序 | 已接受 |
| `decisions/ADR-003-接入CodeGraph代码定位.md` | ADR | CodeGraph CLI v1.6.0 接入 + AGENTS.md 优先级声明 + 中文查询不命中的实测限制 | 已接受 |
| `decisions/ADR-004-引入PRD产品需求文档层.md` | ADR | 文档层级定稿：research → PRD → design → ADR/CHG | 已接受 |
| `decisions/ADR-005-模块化工程结构约定.md` | ADR | 目标目录树（models/llm/tools/storage/manager/executor/router/ui/utils）+ 单向依赖 + 封装规则 | 已接受（M2 起强制） |
| `decisions/ADR-006-代理协作Playbook套件.md` | ADR | handoff / init-project / diagnose 三工作流的仓库化（覆盖全局技能默认） | 已接受 |
| `decisions/ADR-007-向量检索自建SQLite存储.md` | ADR | 否决 chromadb：SQLite 存向量 + OpenAI 兼容 Embedding + 关键词降级三层链 | 已接受 |
| `decisions/ADR-008-v2架构迭代决策.md` | ADR | v2 迭代全记录：双 agent+MCP、确认制记忆、表格字段复用、混合检索关键词主导、内网 cross-encoder 重排（修正 ADR-007 范围）、权限硬约束 | 已接受 |
| `research/text2sql-agent-research.md` | 调研 | nanobot 源码精读：主循环/工具契约/Dream 记忆/五层兜底 | 输入 |
| `research/DB-GPT-AWEL-Text2SQL-调研报告.md` | 调研 | DB-GPT AWEL 编排思想与 Text2SQL 微流水线逐步拆解 | 输入 |
| `changes/CHG-001-M0环境与百万行证券库.md` | CHG | M0 完成：conda t2s（3.11.16）+ 12 表证券库 + 219 万行合成数据（204MB），含 uv→conda 偏差记录 | 已落地 |
| `changes/CHG-002-M1-LLM客户端与五件套工具.md` | CHG | M1 完成：自建 LLM 客户端 + Tool 契约 + 五件套工具，pytest 35 通过 + 真实库冒烟九项全过 | 已落地 |
| `changes/CHG-003-M2-ReAct引擎与四层防护.md` | CHG | M2 完成：计划无关 ReAct 引擎 + 四层防护（17 新测试，总 52 通过），含 Windows 时钟粒度缺陷实录；真实 LLM REPL 验收待 .env key | 已落地（待真机验收） |
| `changes/CHG-004-M3-路由层与会话审计.md` | CHG | M3 完成：危险拦截+意图分流+多轮会话+审计必录（11 新测试，总 63 通过）；含 ADR-005 依赖方向修正与时钟域混用缺陷实录 | 已落地 |
| `changes/CHG-005-M4-记忆三层.md` | CHG | M4 完成：样例库/口径库 + 余弦/关键词双通道检索 + few-shot 注入 + 双通过沉淀闭环（16 新测试，总 79 通过）；含 Chroma→自建选型变更 | 已落地 |
| `changes/CHG-006-M5-评测体系.md` | CHG | M5 完成：golden 50（五档）+ 结果集比对 + 消融框架；金标 50/50 真实库可执行（9 新测试，总 88 通过）；真实准确率待 .env 真机评测 | 已落地（待真机跑数） |
| `changes/CHG-007-真机接入与限流调优.md` | CHG | 真机验收通过（glm-4.7 付费档）：单轮取数 39.4s 完整闭环 + 评测冒烟 8/8 单表 100%（均步 7.0 / 7.4k token）；flash 免费档晚高峰不可用结论与调优记录 | 已验收 |
| `changes/CHG-008-首轮评测与消融实验.md` | CHG | 首轮全量实验：基线 78%（记忆开）vs 78%（关，Δ≈0 诚实结论）；比对器 v2 升级（列投影/容差/量纲因子）修正 18pp 误判；few-shot 抄答案副作用发现与修复 | 已落地 |
| `changes/CHG-009-M6-Web与架构图交付.md` | CHG | M6 完成：FastAPI 单页（问答/审计/CSS 图表零 CDN）+ Answer.result + archify 图交付（showcase 门禁通过）；Web 澄清降级裁剪 | 已落地 |
| `changes/CHG-010-M7-混合检索与权限硬约束.md` | CHG | M7 完成（v2 迭代第一步）：RRF 混合检索（关键词 0.7 主导）+ schema_usage 复用统计 + 启发式重排接口化 + 权限硬约束白名单（14 新测试，总 111 通过） | 已落地 |
| `changes/CHG-011-M8-确认制记忆与异步摘要.md` | CHG | M8 完成（v2 迭代第二步）：确认制（候选/正式库）+ 结果表格记忆 + 异步滚动摘要 + Web 记忆管理端点（10 新测试，总 121 通过）；含 status 遮蔽缺陷实录 | 已落地 |
| `changes/CHG-012-内网形态Milvus适配层.md` | CHG | 内网形态适配：MilvusVectorStore（fail-fast 探针）+ standalone compose + 三条部署路径（WSL-docker / Windows 原生换牌 / 零服务模拟）；Windows 无 Docker 约束实录 | 已落地（Milvus 运行时可选） |
| `changes/CHG-013-M9-skill目录化与coder子agent.md` | CHG | M9 完成（v2 迭代第三步）：skill 目录化（data-analysis/coder）+ coder 子 agent 经 MCP 调库 + 递归预算防护 + HIL 透传（7 新测试，总 128 通过）；含可选参数 null 契约修复 | 已落地 |
| `changes/CHG-014-v2回归修复.md` | CHG | v2 回归 70% 归因修复：LIMIT 注入破坏 ORDER BY（根因）、get_schema 行数诱导与打转、比对器类型签名模糊对齐（4 新测试，总 132 通过）；修复后回归进行中 | 已落地 |
| `handoffs/HO-001-M0-M6全量交付.md` | HO | 首份交接：M0~M6 全绿、有效数字、下一步议程、环境备忘 | 最新 |
| `archify/` | 可视化 | **已交付**：`test2sql-architecture.html`（自包含交互架构图，showcase 门禁通过）+ IR 源 `architecture.json` | 已交付 |
