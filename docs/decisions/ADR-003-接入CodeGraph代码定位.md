# ADR-003：接入 CodeGraph 作为代码定位工具

- 状态：**已接受**
- 日期：2026-08-29
- 提出人：用户（"我想接入codegraph，给项目代码生成地图。方便你快速定位代码。你需要在agent.md中说明：优先使用codegraph来定位代码"）

## 背景

项目代码量进入持续增长期（M1 后 18 个源文件/测试文件），代理靠 grep + 盲读文件定位代码的调用次数与上下文成本高。上游实测（README 基准）：接入代码图谱后定位型工具调用中位数下降 88%，文件读取降为 0。

## 决策

1. 安装 CodeGraph CLI（`colbymchenry/codegraph` v1.6.0，npm 全局装 `@colbymchenry/codegraph`，Rust 内核 + SQLite 本地索引，100% 本地无 API key）。
2. 项目内执行 `codegraph init`：生成 `.codegraph/` 索引（18 文件 / 245 节点 / 554 边，932ms），文件 watcher 自动同步默认开启。
3. 新增根目录 **`AGENTS.md`**（跨代理标准文件名；即用户所说的 agent.md），声明硬约定：**优先使用 codegraph 来定位代码**，并给出查询命令规范。
4. 查询规范：用符号名/英文关键词（`codegraph explore "ToolRegistry execute error"`）；实测中文自然语言长句查不中——此限制写入 AGENTS.md。

## 备选与否决理由

- **纯 grep + Read**：现状可用但每轮"发现"都要烧调用与 token，且无调用关系/影响面视图。
- **archify**：两者不冲突——archify 解决"架构设计的可视化表达"，CodeGraph 解决"代码实体的索引检索"；archify 组件图另行挂起中（见 docs/README.md）。

## 影响面

- 新增：`AGENTS.md`、`.codegraph/`（本地索引，已加入 .gitignore）。
- 所有代理（含 ZCode 自身）在本仓库工作时的代码定位行为受 AGENTS.md §0 约束。
- 文档纪律不变：后续决策仍按 ADR-002 流程落档。
