# Playbook: handoff（会话收尾交接）

- 触发：会话即将结束 / 上下文将满 / 用户说"收工 / 交接"。
- 依据：ADR-006；能力底座为全局 `handoff` 技能，本 playbook 做仓库级绑定。
- 吸收全局技能的三条原则：**不重复已有工件**（PRD/ADR/CHG/代码，只引用路径）；**脱敏**（.env、API key、任何敏感值）；写明 **suggested playbooks**（下一会话该走哪些流程）。

## 步骤

1. **本轮完成事项**：逐条列出，每条必须对应 CHG/ADR 编号；若做了事但没落档，**先补档再交接**。
2. **变更文件清单**：新增/修改/删除；注明测试与冒烟状态（pytest / smoke 结果）。
3. **下一步**：下一个可执行动作，具体到命令（如 `uv run python db/seed.py` 级别的粒度）。
4. **阻塞与未决**：对照 PRD §11 Open Questions 标注新增/关闭。
5. **写入 `docs/handoffs/HO-NNN-YYYY-MM-DD.md`**（编号查目录递增；标题一句话概括交接主题）。
6. **登记 `docs/README.md` 主索引**。

## 覆盖声明

本仓库交接文档**必须入库**（覆盖全局技能"存临时目录"的默认）——理由：面试可追溯性，交接链本身就是工程素养证据（ADR-006）。

## 模板骨架

```markdown
# HO-NNN：<一句话主题>
- 日期 / 交出方 / 接入方（下一个代理会话）
- 本轮完成（CHG/ADR 编号引用）
- 变更文件与验证状态
- 下一步（命令级）
- 阻塞与未决
- Suggested playbooks（如：M2 引擎开发 → init-project + diagnose 备用）
```
