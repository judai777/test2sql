# CHG-009：M6 落地——Web 单页、结果集渲染与 archify 架构图交付

- 日期：2026-08-29
- 动机：执行 `docs/design.md` §10 里程碑 M6（最后一个里程碑），PRD FR-10 / US-8。
- 类型：新增

## 变更内容

| 文件 | 说明 |
|---|---|
| `src/t2s/ui/web.py` | FastAPI 单页应用：`POST /api/ask`（问答）、`GET /api/audit`（审计近表，US-7）、`GET /`（内嵌单页）。零 CDN 依赖，图表用纯 CSS 条形图（首列维度+次列数值启发式，借鉴 DB-GPT data_loader） |
| `src/t2s/models/task.py` | `Answer.result` 新增：结果集样本 {columns, rows≤50} |
| `src/t2s/executor/engine.py` | execute_sql 成功后捕获结果样本进 Answer（UI 渲染数据源） |
| `src/t2s/storage/database.py` | `open_db` 加 `check_same_thread=False`（FastAPI 线程池跨线程访问；写入串行性由单用户 MVP 语义保证） |
| `docs/archify/architecture.json` + `test2sql-architecture.html` | **archify 架构图解冻并交付**：向量检索更名自建（ADR-007）、路由/管理层改 tag 表达、全部布局碰撞清零（通过 showcase 质量门禁），726KB 自包含交互 HTML |
| `tests/test_web.py`（6 例） | 端点形状/危险拦截（零 LLM）/审计落库/单页/结果样本捕获 |

**范围决策（PRD 未决问题 #1 的 MVP 裁剪）**：Web 端 ask_user 一期降级为固定文案（"按最常见口径执行并声明假设"）——两段式交互澄清留二期。理由：同步 HTTP 往返无法承载阻塞式澄清，且引擎的 ask_user 降级设计本就保证功能不中断。

**测试成本防护**：Web 单测发现 key 已配置时会真实调用 LLM（64s/次）——测试配置指向无效端口 + 零重试走 error 分支；`pytest -m eval` 真机冒烟同步加 `T2S_REAL_EVAL=1` 门禁。

## 验证结果（M6 完成判据）

- `pytest`：**97 passed, 1 skipped**（+8）。
- archify `deliver` 通过 showcase 质量门禁（布局/标签/线宽全绿），产出 `docs/archify/test2sql-architecture.html`。
- Web 手工验收方式：`PYTHONPATH=src python -m t2s.ui.web` → 浏览器 `http://127.0.0.1:8000` → 输入取数问题 → SQL/表格/条形图/审计四件套呈现。

## 过程中修复的缺陷

1. TestClient 工作线程访问主线程 sqlite 连接 → `check_same_thread=False`。
2. archify via 语义规则（首末段必须严格沿声明边方向、via 须落在边中心线、量纲类标签须 ≥2 行证据）——五轮迭代全部按验证器诊断修正。

## 关联

PRD FR-10 / US-8 / 未决问题 #1；ADR-005（ui 层零逻辑）；ADR-007（图上向量组件更名）。
