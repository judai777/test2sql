# CHG-013：M9 落地——skill 目录化、coder 子 agent（MCP）与递归预算

- 日期：2026-08-29
- 动机：执行 ADR-008 D1（v2 迭代第三步）：双 agent 架构。
- 类型：新增（含两个通用缺陷修复）

## 变更内容

| 文件 | 说明 |
|---|---|
| `executor/skills/data-analysis.md` / `coder.md` | **skill 即资产**：Markdown + 极简 frontmatter（name/description/tools 白名单/max_steps 预算）+ 提示词正文。改 skill 即改 agent 行为，无需动引擎 |
| `executor/skill_registry.py` | Skill 加载器（免 YAML 依赖；重名 fail-fast） |
| `executor/delegate.py` | **CoderDelegation + DelegateSqlTask 工具**：主 agent 把"SQL 编写与执行"委派给 coder；**递归预算防护**——子 agent 步数 = min(skill 上限, 父剩余步数 − 1)，墙钟不超过父 turn 剩余时间；**HIL 透传**——coder 的 ask_user 经服务端 ctx 走父 turn 用户通道 |
| `tools/mcp_server.py` | **MCP 服务端**：get_schema/validate_sql/execute_sql/ask_user 经 fastmcp 暴露（复用同一套 Tool 实现=单一事实来源，护栏随实现走）；错误语义经 JSON 信封跨协议保留；`python -m t2s.tools.mcp_server` 可作 stdio 服务供外部 MCP client（如 Claude Code）直连 |
| `tools/mcp_bridge.py` | **MCP 桥接注册中心**：fastmcp 内存传输（同进程走完整 MCP 协议，生产可换 stdio/SSE 代码不变），coder 子 agent 的工具面由此构建（工具白名单天然收窄：无 search_schema 探索工具） |
| `executor/engine.py` | `system_prompt` 覆盖参数（skill 注入 agent 身份，few-shot 记忆块仍运行时追加）；ctx.extra 透传 budget/guard/turn_deadline 供委派工具做递归预算 |

**双 agent 协作流**（ADR-008 D1 兑现）：
```
data-analysis 主循环（12 步预算，全工具 + 委派权）
   └─ delegate_sql_task（任务 + schema 信息）
        └─ coder 子 agent（6 步预算，仅 SQL 工具，经 MCP 调库）
             └─ 信息不足 → ask_user → 父 turn 用户通道（HIL）
   父预算内扣减 · 墙钟共享 · 结论由主 agent 组织
```

## 过程中修复的缺陷

1. **可选参数显式 null 校验失败**（通用缺陷）：MCP 往返把可选 `options` 传成 null，参数校验按声明类型（array）判错。修复：**显式 null 的可选参数视同省略**（LLM 高频行为，工具契约层修复，全部工具受益）。
2. **pyproject TOML 损坏**：批量文本替换在 TOML 数组内插入裸行——教训：结构化文件（TOML/JSON/YAML）禁用文本替换，须结构化读写。

## 验证结果

- `pytest`：**128 passed, 2 skipped**（+7：skill 加载与工具面收窄断言、MCP 往返成功/护栏语义跨协议保留、委派全流程（子 agent 身份=skill 提示词）、递归预算拒绝、HIL 透传）。
- 架构承诺兑现：引擎零改动支撑双 agent（计划无关）；护栏/审计/防护在委派场景全部继承。

## 关联

ADR-008 D1/D6；nanobot 调研（子 agent 独立预算模式、MCP 工具先例）；M7/M8（coder 的工具与记忆底座）。
