# ADR-006：代理协作 Playbook 套件（handoff / init-project / diagnose）

- 状态：**已接受**
- 日期：2026-08-29
- 提出人：用户（"考虑添加 handoff、init-project、diagnose 这些插件功能"）

## 背景

代理会话天然易失：上下文一满，工作状态就断。环境中虽有全局技能 `handoff` 与 `diagnosing-bugs`（mattpocock 套件），但它们是通用版——不知道本仓库的文档纪律与红线（handoff 甚至默认存系统临时目录，不入库）。插件功能要"插件化"更要"仓库化"：以仓库内、跨代理可读的 playbook 形式存在，不锁死在某个 CLI 的技能加载器上。

## 决策

新增 `.agent/playbooks/` 三个工作流，并在 AGENTS.md 声明触发约定：

| Playbook | 触发 | 作用 |
|---|---|---|
| `init-project.md` | 新会话处理本仓库的第一步 | ≤2 分钟恢复上下文：AGENTS.md → 主索引 → 最新 handoff → codegraph status → pytest 基线 → 输出状态卡 |
| `handoff.md` | 会话收尾 / 上下文将满 / 用户说收工 | 生成交接文档入库 `docs/handoffs/HO-NNN-*.md`：完成项（引用 CHG/ADR 编号）、下一步、阻塞项 |
| `diagnose.md` | 测试红 / 行为异常 / 报错 | 绑定全局 diagnosing-bugs 的"先建最小反馈回路"纪律 + 本仓库红线（codegraph 定位、护栏不可放松、修复必须 pytest+冒烟双绿、落档 CHG） |

**与全局技能的关系**：全局 `handoff` / `diagnosing-bugs` 仍是能力底座；playbook 负责仓库级绑定——尤其覆盖全局 handoff 的"存临时目录"默认：**本仓库交接文档必须入库**（面试可追溯性）。

## 备选与否决理由

- **装成某 CLI 专属技能（如 .claude/skills/）**：锁死单一工具，换代理失效；playbook 是纯 markdown，任何代理都能读。
- **只依赖全局技能**：不携带仓库纪律，handoff 不入库，diagnose 不知道护栏红线。

## 影响面

- 新增 `.agent/playbooks/`（3 文件）、`docs/handoffs/`（目录）；AGENTS.md 增加工作流章节；主索引登记。
