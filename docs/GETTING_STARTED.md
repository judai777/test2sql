# Test2SQL 启动指南

> 所有命令均在本机验证过（2026-08-29）。Windows + Git Bash 环境为准，CMD 差异见各节备注。
> 项目约定（代码定位用 codegraph、文档纪律、修改红线）见根目录 [AGENTS.md](../AGENTS.md)。

---

## 1. 前置条件

| 项 | 要求 | 本机现状 |
|---|---|---|
| Python | 3.11+ | conda 环境 `t2s`（3.11.16），位于 `D:\Anaconda\envs\t2s` |
| API Key | 智谱（或其他 OpenAI 兼容供应商） | 已配置于 `.env`（勿提交，已在 .gitignore） |
| 可选工具 | codegraph（代码定位） | 已安装并建索引 |

## 2. 从零搭建（新机器才需要）

```bash
# ① 创建环境（uv 下载受阻时的 conda 方案，见 CHG-001）
conda create -n t2s python=3.11 -y
D:\Anaconda\envs\t2s\python.exe -m pip install -r requirements.txt -r requirements-dev.txt

# ② 配置：复制模板填入你的 API Key（唯一必填项 T2S_LLM_API_KEY）
cp .env.example .env

# ③ 生成业务库（12 表 / 219 万行合成数据，约 1~2 分钟）
D:\Anaconda\envs\t2s\python.exe db\seed.py

# ④ 生成口径/样例种子（幂等，可重复跑）
PYTHONPATH=src D:\Anaconda\envs\t2s\python.exe scripts\seed_memory.py

# ⑤ 代码索引（代理协作用）
codegraph init
```

> CMD 下设置环境变量：`set PYTHONPATH=src`（Git Bash 用 `PYTHONPATH=src` 前缀）。

## 3. 三种启动方式

### Web 界面（推荐，业务人员视角）

```bash
PYTHONPATH=src python -m t2s.ui.web            # 默认 http://127.0.0.1:8000
PYTHONPATH=src python -m t2s.ui.web --port 9000
```

浏览器打开后输入问题（如 `上个月各营业部的成交额排行`），返回：结论 + SQL + 结果表格 + 条形图 + 步数/token 元信息。`/api/audit` 可查审计近表。

### 交互式 REPL（多轮会话，开发视角）

```bash
PYTHONPATH=src python -m t2s.ui.repl
# 问题> 上个月各营业部的成交额排行
# 问题> 再按产品类型拆分          ← 跨轮指代
# 输入 exit 退出
```

### 一次性命令（脚本/验收）

```bash
PYTHONPATH=src python -m t2s.ui.repl -q "客户总数是多少"
```

## 4. 测试与评测

```bash
# 单元测试（97 例，秒级，零 LLM 成本——真机冒烟已有计费门禁）
D:\Anaconda\envs\t2s\python.exe -m pytest

# 金标离线校验（50 条金标 SQL 可执行性，零 LLM 成本）
PYTHONPATH=src python -m eval.runner --verify-golden

# 真实评测（计费！glm-4.7 全量约 40 分钟 / ~50 万 token）
PYTHONPATH=src python -m eval.runner                    # 记忆开启组
PYTHONPATH=src python -m eval.runner --no-memory --tag ablation   # 消融对照组
PYTHONPATH=src python -m eval.runner --limit 5          # 冒烟 5 条
PYTHONPATH=src python -m eval.runner --rejudge <报告.json>   # 离线重判（零 LLM）

# 真机冒烟进回归（显式计费门禁，默认跳过）
T2S_REAL_EVAL=1 python -m pytest -m eval
```

报告落 `eval/reports/report-<tag>-<时间戳>.json`；当前基线与结论见 `eval/reports/SUMMARY.md`（78%，Δ≈0）。

## 5. 日常运维

| 操作 | 命令 |
|---|---|
| 重建业务库（数据完全确定性可重现） | `python db/seed.py`（`--scale 0.1` 可缩量） |
| 查看审计（谁/何时/问了什么/执行 SQL） | Web `/api/audit`，或查 `db/memory.db` 的 `audit_log` 表 |
| 清空会话/审计/记忆 | 删除 `db/memory.db` 后重跑 `scripts/seed_memory.py` |
| 删除误沉淀的样例 | `db/memory.db` 的 `qa_pairs` 表删行（或调 `MemoryService.forget_pair`） |
| 代码索引同步 | `codegraph status` / `codegraph sync`（文件变更自动同步，通常无需手动） |
| 架构图 | 打开 `docs/archify/test2sql-architecture.html`（自包含交互图，含三视图/导出） |

## 6. 故障排查

| 症状 | 原因与处置 |
|---|---|
| `HTTP 429 code 1305 该模型当前访问量过大` | 智谱 flash 免费档晚高峰拥堵。换付费档（`.env` 改 `T2S_LLM_MODEL=glm-4.7`）或错峰；退避已配 2/4/8/16s |
| `getaddrinfo failed` | DNS 瞬断，重试即可；评测中表现为单案 `未产出 SQL`（基础设施失败 ≠ 任务失败） |
| Git Bash 里 curl/日志中文乱码 | 控制台 GBK 显示问题，浏览器/文件内容正常（UTF-8） |
| pytest 变慢且产生 API 费用 | 检查是否设置了 `T2S_REAL_EVAL=1`；Web/路由单测已指向无效端口，不会外呼 |
| `sqlite3.ProgrammingError: SQL objects created in a thread...` | 已修复（open_db check_same_thread=False，CHG-009）；若复现检查是否绕过 `open_db` 直连 |
| 改了 `db/schema.sql` 后工具对不上 | 必须同步 `src/t2s/tools/metadata.py`（AGENTS.md §4 红线），然后 `pytest` |
| Web 端口被占 | `--port 9000` 换端口 |

## 7. 项目地图（30 秒版）

```
src/t2s/
├── router/      路由层：危险拦截 → 意图分类 → 权限 → 编排入口 RouterService
├── manager/     管理层（零 LLM）：上下文窗口装配 + 记忆检索/沉淀
├── executor/    执行层（唯一 LLM）：ReAct 引擎 + 四层防护 + 提示词
├── tools/       五件套（search/get_schema/validate/execute/ask_user）+ 12 表元数据
├── llm/ storage/ models/ utils/ ui/   基础设施与驱动器
eval/            golden 50 评测 + 运行器
docs/            PRD → design → ADR/CHG → handoffs（主索引 README.md）
```

深入阅读顺序建议：`AGENTS.md` → `docs/design.md` → `docs/decisions/ADR-001` → `executor/engine.py` → `eval/reports/SUMMARY.md`。
