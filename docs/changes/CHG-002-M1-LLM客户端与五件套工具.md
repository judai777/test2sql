# CHG-002：M1 落地——LLM 客户端 + 工具契约 + 五件套工具

- 日期：2026-08-29
- 动机：执行 `docs/design.md` §10 里程碑 M1。
- 类型：新增

## 变更内容

**新增文件**

| 文件 | 说明 |
|---|---|
| `src/t2s/config.py` | .env 加载（自建 15 行 loader，不引 dotenv）+ LLMConfig/ToolConfig/AppConfig |
| `src/t2s/llm/types.py` | ChatMessage/ToolCall/LLMResponse 强类型；`should_execute_tools` 不变量（nanobot §3.2） |
| `src/t2s/llm/client.py` | OpenAI 兼容客户端：重试 1/2/4s 退避 + Retry-After、408/429/5xx 可重试、4xx 硬失败、SSE 流式、MockTransport 注入口 |
| `src/t2s/tools/base.py` | Tool ABC / ToolResult（str 子类 + is_error）/ ToolContext；schema 即代码：cast→validate、additionalProperties=False |
| `src/t2s/tools/registry.py` | 注册中心：稳定排序 definitions、未知工具模糊建议、JSON 字符串/`{"arguments":{}}` 包装兼容、异常→错误观察永不逃逸 |
| `src/t2s/tools/metadata.py` | 12 表元数据（列注释/枚举/外键/用途）——get_schema 与 search_schema 的单一事实来源 |
| `src/t2s/tools/sql_tools.py` | search_schema / get_schema / validate_sql / execute_sql |
| `src/t2s/tools/ask_user.py` | 口径歧义澄清工具（无终端时降级为"按假设继续"） |
| `tests/`（5 文件） | 35 个用例：契约行为、SQL 护栏、schema 工具、LLM 重试/流式（全 MockTransport） |
| `scripts/smoke_tools.py` | 对真实库（219 万行）的九项手动冒烟 |
| `requirements.txt` / `requirements-dev.txt` / `.env.example` | 依赖与配置样板 |

**安全护栏（execute_sql，ADR-001 D4/D6 的实现）**
1. 写操作/DDL 关键字黑名单 → 硬边界拒绝（提示"不可重试"）；
2. sqlglot 限定单条只读语句（SELECT/UNION/WITH）；
3. 表 + 列存在性白盒校验（DB-GPT 缺环补齐，本项目增量）；
4. SQLite `mode=ro` 只读连接（护栏漏网也写不进）；
5. 无 LIMIT 自动包裹 `SELECT * FROM (...) LIMIT 100` + 行数上限；
6. progress handler 墙钟中断（默认 30s，ToolContext 可配）。

**search_schema 打分**：查询切 CJK 二元组 + ASCII 词，按出现次数计分、purpose 三倍权重（修正了"是否命中"打分把主表挤出 top-3 的问题）。

## 过程中修复的缺陷

1. `test_llm_client.py` 字符串字面量笔误（`'{"bad': }`）导致语法错误。
2. 超时测试在 8 行迷你库上永远触发不了 progress handler → 改用四表笛卡尔积保证工作量；另发现 pytest 断言重写缓存吃掉了测试修改（清 `__pycache__` 后复现正常）。
3. search_schema 首版打分逻辑如上修正。

## 验证结果（M1 完成判据）

- `pytest`：**35 passed**。
- 真实库冒烟九项全过：语义检索命中、100 万行 trades 完整 DDL + 行数 + 样例、三表 JOIN 实查、幻觉表拦截、写操作拦截、无 LIMIT 自动包裹、ask_user 降级。

## 关联

ADR-001（D2/D4/D6/D7）；后续 M2（ReAct 引擎）将直接消费 `registry.definitions()` 与 `should_execute_tools`。
