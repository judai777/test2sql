# Playbook: init-project（新会话冷启动）

- 触发：新会话处理本仓库的第一步；或用户说"init / 继续项目 / 熟悉一下"。
- 目标：≤ 2 分钟恢复完整工作上下文，输出一页**状态卡**。
- 依据：ADR-006。

## 步骤

1. **读 `AGENTS.md`**——红线与命令（§0 codegraph 优先、§2 文档纪律、§3 环境、§4 修改红线）。
2. **读 `docs/README.md` 主索引**——重点：最近 2 份 CHG、最新 ADR、挂起项（如 archify 状态）。
3. **读 `docs/handoffs/` 最新一份 HO**（若有）——接手未竟事项，不重做已完成的事。
4. **`codegraph status`** 确认索引新鲜（有待同步先 `codegraph sync`）；需要结构感时 `codegraph files`。
5. **跑基线**：`D:\Anaconda\envs\t2s\python.exe -m pytest -q` 确认全绿（当前基线 35 用例）。

## 输出：状态卡

- 当前里程碑（M0✅ M1✅ M2 待启动 …）
- 最近完成（CHG 编号一览）
- 下一步任务（具体到命令）
- 阻塞与未决问题（对照 PRD §11 Open Questions）
- 索引新鲜度与测试基线状态

## 禁令

- 跳过 AGENTS.md 直接动手。
- 在没看主索引的情况下新建文档（编号会撞车）。
