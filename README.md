# Test2SQL

面向证券/银行业务人员的自然语言取数 Agent：业务人员用中文提问，返回 SQL、表格与图表，替代"向数据组提数工单"的流程。

三层架构：路由层（意图/权限/会话）→ 管理层（确定性治理，零 LLM）→ 执行层（计划无关 ReAct 引擎，唯一 LLM 所在）。

## 文档

所有设计决策、调研、变更记录统一在 **[docs/README.md](docs/README.md)**（主索引）：

- **启动指南：[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**（从零搭建/启动/评测/排查）
- 总设计：`docs/design.md`；产品需求：`docs/PRD.md`
- 决策记录：`docs/decisions/ADR-NNN-*.md`
- 调研报告：`docs/research/`
- 代理协作约定：**[AGENTS.md](AGENTS.md)**（代码定位优先用 codegraph；工作流 `.agent/playbooks/`：init-project / handoff / diagnose）

## 快速开始

环境：conda `t2s`（Python 3.11.16）。uv 已安装但其托管 Python 下载受网络阻断，故走 conda 备选（见 CHG-001）。

```bash
D:\Anaconda\envs\t2s\python.exe -m pip install -r requirements-dev.txt   # 或按 pyproject.toml 手装
D:\Anaconda\envs\t2s\python.exe db\seed.py    # 合成证券业务库（约 250 万行，首次几分钟）
D:\Anaconda\envs\t2s\python.exe -m pytest     # 测试
```

## 里程碑

M0 环境与数据 ✅ → M1 LLM 客户端+工具契约 ✅ → M2 ReAct 引擎+四层防护 ✅ → M3 路由+会话+审计 ✅ → M4 记忆三层 ✅ → M5 评测体系 ✅ → M6 Web+架构图 ✅

## Web 启动

```bash
PYTHONPATH=src python -m t2s.ui.web          # http://127.0.0.1:8000
```

评测基线：golden 50 准确率 78%（记忆开/关 Δ≈0，详见 docs/changes/CHG-008）。
