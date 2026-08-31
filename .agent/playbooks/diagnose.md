# Playbook: diagnose（缺陷诊断）

- 触发：测试红 / 行为异常 / 报错 / 用户报"坏了、不对、慢了"。
- 依据：ADR-006；能力底座为全局 `diagnosing-bugs` 技能，其核心纪律本 playbook 直接继承——**先建最小反馈回路，再谈修复**。

## 步骤

1. **复现（做不出复现不得动手改）**
   - 最小复现命令：`pytest tests/... -k <name>` 单测，或 `scripts/smoke_tools.py` 单项。
   - 把"坏"变成一个可重复的 pass/fail 信号：红在**这个 bug** 上，而不是红在一堆无关失败里。
2. **定位（AGENTS.md §0）**
   - `codegraph explore "<英文符号关键词>"` 取调用链 + 影响面（blast radius）；禁止盲 grep / 盲读文件。
   - 影响面里的测试文件 = 步骤 5 回归范围的最小集合。
3. **假设**
   - 列出 ≥ 1 个根因假设，按证据强度排序；逐个用最小实验证实/证伪。
   - 常见嫌疑清单：协议顺序（先声明 tool_calls 后回填）、缓存（pytest 重写缓存/旧 pyc）、metadata 与 schema.sql 不同步、浮点/时区、Windows 路径。
4. **修复（红线不可破，AGENTS.md §4）**
   - 最小 diff；安全护栏（只读/黑名单/LIMIT/超时）不可绕过或放松；
   - 错误即观察协议不可改为抛异常；
   - 改 `db/schema.sql` 必须同步 `tools/metadata.py`。
5. **回归（双绿才算修好）**
   - `pytest` 全绿 + 涉及工具过 `scripts/smoke_tools.py`；
   - M5 起 golden 相关改动加 `pytest -m eval`。
6. **落档**
   - 缺陷与修复写入对应 CHG 的"过程中修复的缺陷"节（CHG-002 §过程修复 是范例）；
   - 若修复推翻了设计决策 → 新建 ADR 并标注取代关系，禁止静默改设计。

## 禁令

- 无反馈回路的"试着改改看"。
- 为了让测试变绿而放松断言或护栏。
- 修完不落档。
