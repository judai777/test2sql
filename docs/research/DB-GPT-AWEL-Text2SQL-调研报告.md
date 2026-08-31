# DB-GPT 调研报告：AWEL 编排思想与 Text2SQL 微流水线

> 调研对象：DB-GPT（`D:\DB-GPT`，monorepo，main 分支，v0.8.x 代码结构）
> 调研目的：为「Text-to-SQL Agent」项目立项做技术储备
> 调研重点：① AWEL 的 DAG 编排思想；② ChatData/Text2SQL 微流水线各步骤；③ 为什么强调"工作流优先"而不是自由 ReAct

---

## TL;DR

1. **AWEL 是"代码即 DAG"的声明式编排 DSL**：用 Python 上下文管理器（`with DAG(...) as dag`）+ `>>` 运算符重载定义静态有向无环图，元类自动装配依赖注入，流式（AsyncIterator）是一等公民，执行器在 asyncio 上做依赖递归 + 分支裁剪。
2. **Text2SQL 的"微流水线"就是一条由 AWEL 算子串起来的固定管线**：`场景路由 → schema 召回（双层向量检索）→ 提示词组装（模板 + 约束 + 展示白名单）→ LLM 生成 SQL（流式）→ 结构化解析（sqlparse/JSON）→ 执行（读写分类/超时/行数限制）→ 图表渲染（chart-view 白名单）`。每一步都是确定性代码，LLM 只负责其中"生成"一环。
3. **"工作流优先"不是否定 agent，而是分工**：官方在 `docs/docs/awel/why_use_awel.md` 里明确表态——多 agent 自动编排的能力受限于模型，确定性管线任务不需要动用 LLM 来决定流程；因此**生产级管线用 AWEL 固定编排，开放式问题交给 agent 自动编排**，口号是 "AWEL and agents are all you need"。DB-GPT 实际是双轨制，且两者双向嵌套（agent 可作为 DAG 节点，AWEL flow/应用可作为 agent 的资源）。
4. **对本项目的核心启示**：Text2SQL 这种"容错率低、步骤清晰、需要流式和安全护栏"的场景，正确姿势是**固定微流水线 + 有限的校验/修复循环 + 确定性安全层**，把 ReAct 留给 schema 探索这类真正开放的问题。

---

## 一、代码地图：这个仓库长什么样

DB-GPT 是 monorepo，`packages/` 下分包：

| 包 | 角色 | 与本调研的关系 |
|---|---|---|
| `dbgpt-core` | 核心抽象：AWEL、LLM 接口、RAG、agent 框架 | AWEL 框架全在 `src/dbgpt/core/awel/` |
| `dbgpt-app` | 应用层：场景（scene）、OpenAPI、内置技能 | Text2SQL 场景在 `src/dbgpt_app/scene/` |
| `dbgpt-ext` | 扩展：数据源连接器、DB schema RAG | schema 召回在 `src/dbgpt_ext/rag/` |
| `dbgpt-serve` | 服务层：flow/agent/datasource/conversation 的 REST 服务 | AWEL flow 持久化在 `src/dbgpt_serve/flow/` |
| `dbgpt-client` / `dbgpt-sandbox` / `dbgpt-accelerator` | 客户端 SDK / 沙箱 / 加速 | 沙箱用于 agent 代码执行 |

> ⚠️ **版本注意事项**：网上大量资料（包括 DB-GPT 自己 0.3/0.4 时代的博客）讲的是旧版 AWEL（`TaskExecutor`/`EventLoop`/`ContextTrigger`/`bind()` 连接参数）。这些在当前代码库中**已全部移除或改名**（用 `git log --all -S` 验证过）：执行职责归 `DefaultWorkflowRunner`，`RequestTrigger` 改名 `RequestHttpTrigger`，连接只靠 `>>`/`<<`。本文以当前 main 代码为准。根目录另有一份现成的 `DB-GPT-Core-Code-Design-Analysis.md` 可作包结构速览。

---

## 二、AWEL：DAG 编排思想

### 2.1 设计动机：三层架构，对抗不确定性

官方文档 `docs/docs/awel/awel.md` 给出三层设计：

1. **Operator 层**——LLM 应用的基本原子（检索、向量化、模型交互、prompt 处理）；
2. **AgentFrame 层**——基于算子的链式计算（filter/join/map/reduce），承担流程编排；
3. **DSL 层**——结构化表示，原文的理由非常直白：

> "making it more deterministic to write large model applications around data, **avoiding the uncertainty of writing in natural language**" —— 用确定性编程代替"自然语言编程"的不确定性。

`docs/docs/getting-started/concepts/awel.md` 总结的四个卖点：**Declarative DAGs**（定义做什么，不定义怎么连线）、**Reusable operators**、**Stream-native**（流式一等公民）、**Visual editor**（AWEL Flow 可视化编排）。

### 2.2 核心抽象（`packages/dbgpt-core/src/dbgpt/core/awel/`）

| 抽象 | 位置 | 角色 |
|---|---|---|
| `DAG` / `DAGNode` | `dag/base.py` | 纯结构容器：`node_map`、惰性计算的 `root_nodes`/`leaf_nodes`/`trigger_nodes`；DAG 级变量 |
| `DependencyMixin` | `dag/base.py` | 重载 `>>` / `<<` 运算符登记上下游（只记拓扑，不携带数据语义） |
| `DAGVar` | `dag/base.py` | 全局 DAG 栈（同步用 `threading.local`，异步用 `contextvars`）——**任何地方实例化节点都隐式挂到当前 DAG**，这是"代码即编排"的关键 |
| `BaseOperator` | `operators/base.py` | 可执行节点基类；`streaming_operator`/`incremental_output`/`output_format("SSE")` 声明数据形态；`call()` 阻塞取终值、`call_stream()` 返回 AsyncIterator |
| `BaseOperatorMeta`（元类） | `operators/base.py` | 构造时自动从 `DAGVar`/SystemApp 注入 dag/executor/runner 等缺省值，用户零样板 |
| `MapOperator` / `JoinOperator` / `ReduceStreamOperator` / `BranchOperator` / `BranchJoinOperator` / `InputOperator` | `operators/common_operator.py` | 常用算子：单入变换 / 多父汇聚 / 流归并 / 条件路由 / 分支汇合（取第一个非空）/ 输入源 |
| `StreamifyAbsOperator` / `UnstreamifyAbsOperator` / `TransformStreamAbsOperator` | `operators/stream_operator.py` | 批量⇄流式、流到流的形态转换算子 |
| `TaskOutput[T]` | `task/base.py`, `task/task_impl.py` | **双形态数据**：`SimpleTaskOutput`（非流）/ `SimpleStreamTaskOutput`（AsyncIterator，惰性 map、reduce 才消费）；哨兵值 `EMPTY_DATA`/`SKIP_DATA` |
| `TaskContext` / `InputContext` | `task/` | 节点单次运行状态；`map()`（对每个父输出并发 map，内部 `asyncio.gather`）、`map_all()`（Join 用）、`predicate_map()`（Branch 用） |
| `Trigger` 家族 | `trigger/` | `HttpTrigger`（动态注册 FastAPI 路由，把 HTTP 请求接进 DAG）、`IteratorTrigger`（进程内批量并发触发，带限流/重试/超时） |
| `DefaultWorkflowRunner` | `runner/local_runner.py` | 唯一执行器：从叶子节点反向收集子图 → DFS 递归执行 → 分支跳过裁剪 |

**关键设计点：连接是纯拓扑的**。`>>` 只登记 upstream/downstream；数据怎么流由两端算子的**流形态**自动决定：非流→非流走 `map`，流→流走惰性 `transform_stream`，流→非流必须经过 `Unstreamify/ReduceStreamOperator`。

### 2.3 编排表达：命令式构建，声明式外观

```python
# examples/awel/simple_dag_example.py
with DAG("simple_dag_example") as dag:          # ① 上下文管理器：进入时压入 DAGVar 栈
    trigger = HttpTrigger("/examples/hello", request_body=TriggerReqBody)
    map_node = RequestHandleOperator()          # ② 构造即挂图（元类 + DAGVar 捕获）
    trigger >> map_node                         # ③ 运算符重载连边

# examples/awel/data_analyst_assistant.py —— 分支 + Join 的完整 copilot
trigger >> prompt_task >> history_prompt_build_task >> model_request_build_task
(branch_task >> llm_task >> model_parse_task >> result_join_task)          # 分支 A
(branch_task >> streaming_llm_task >> openai_format_stream_task >> result_join_task)  # 分支 B
```

本质上这是**命令式构建 + 声明式外观**：图在 Python 执行 `with` 块时静态成型，运行期只沿静态边走。参数化靠算子构造函数（配合 `functools.partial`）、`task_name` 命名（Branch 按 name 路由）、以及 DAG 变量系统（`DAGVariables` + 运行期 `VariablesPlaceHolder` 解析，支持 secret 类别）。

### 2.4 动态分发：静态图 + 运行期"跳过"裁剪

AWEL **没有**运行期增删节点的能力，动态性体现在：

- `BranchOperator._do_run`（`common_operator.py`）运行时用 `asyncio.gather` 并发评估所有分支谓词，不满足的下游节点名写进 `task_ctx.metadata["skip_node_names"]`；
- runner 侧递归标记跳过集合，规则很细：`can_skip_in_branch()==False` 的节点（如 `BranchJoinOperator`）不可跳过；**多父节点只有全部父被跳过才跳过**；
- 被跳过的节点仍然会"执行"，但直接置 `TaskState.SKIP` 并输出 `SKIP_DATA`，保证下游总能拿到占位输出继续走。

### 2.5 流式如何端到端贯穿

流是 AWEL 区别于一般 workflow 框架的最大卖点，贯穿四层：

1. **算子声明层**：`streaming_operator=True`；
2. **数据层**：`TaskOutput.is_stream` 分派流/非流实现，流的 map 是惰性 async 生成器；
3. **执行层**：流式调用时 runner 不在结束时触发 `after_dag_end` 清理，而是把收尾动作交给触发器的 `finally`/`BackgroundTasks`（否则流没消费完上下文就被清了）；
4. **传输层**：`HttpTrigger` 把 `call_stream()` 包成 FastAPI `StreamingResponse`（SSE），叶子算子用 `output_format="SSE"` 控制格式。

### 2.6 执行模型：asyncio 上的 DFS

`DefaultWorkflowRunner.execute_workflow`（`runner/local_runner.py`）：

- `JobManager.build_from_end_node(node, call_data)` 从叶子**反向递归收集子图**，把 call_data 分配给根节点；
- `_execute_node` **依次 await 全部上游 → 取输出 → 执行当前节点**（递归 DFS；多父汇聚语义上等价于 join，实现上是顺序 await，代码注释自认 "TODO: run in parallel"）；
- 真正的并发发生在细粒度：`InputContext.map` 的 `asyncio.gather`、分支谓词并发评估、`IteratorTrigger` 批量触发（信号量限流 + 重试 + 超时）；
- 阻塞函数一律 `blocking_func_to_async` 丢线程池；全程 `root_tracer.start_span(...)` 打观测 span（**可观测性是内建的，不是外挂**）。

### 2.7 Flow 服务化：代码编排与可视化编排同源

这是 AWEL 最值得借鉴的工程闭环：

- 每个算子类可声明 `metadata: ViewMetadata`（label/参数/输入输出端口 `IOField`），即**算子自带 UI 描述**（`awel/flow/base.py`）；
- `FlowFactory.build()`（`awel/flow/flow_factory.py`）把前端 react-flow 画布导出的 nodes/edges JSON **编译成和手写代码完全相同的 `DAG` 对象**（拓扑排序 → 实例化资源 → 实例化算子 → `>>` 连边）；
- `dbgpt_serve/flow/service/service.py` 负责 flow 的保存（JSON 存 DB + 注册路由）、编辑（先 build 验证，失败回滚）、重启恢复、执行；`HttpTriggerManager` 支持 flow 部署/下线时**动态装卸 FastAPI 路由**；
- 对外暴露 OpenAI 兼容入口 `POST /v2/chat/completions`（`dbgpt_app/openapi/api_v2.py`），`chat_mode=chat_flow` 时把任意 flow 当作一个 chat model 来对话——**flow 被服务化成了"模型"**。

### 2.8 一个生产级实例：模型缓存分支 DAG

`dbgpt_app/scene/operators/app_operator.py` 的 `build_cached_chat_operator`，把 2.4 的分支裁剪用得非常典型：

```
                    -> llm_task -> save_cache_task ->
                   /                                \
input_task -> cache_check_branch_task               ---> join_task
                   \                               /
                    -> cache_task ---------------- ->
```

```python
input_task >> cache_check_branch_task
cache_check_branch_task >> llm_task >> save_cache_task >> join_task
cache_check_branch_task >> cache_task >> join_task      # BranchJoinOperator 取第一个非空
```

同一张图里流式与非流式只是换算子（`StreamingLLMOperator` vs `LLMOperator`），结构不变——**形态与拓扑解耦**的好处。

---

## 三、Text2SQL 微流水线逐步拆解

> 说明：仓库中没有 "micro-pipeline" 字面量；它实际指 `scene` 场景管线——**由 BaseChat 骨架 + AWEL 算子拼成的固定步骤链**。当前版本场景归类：`chat_with_db_execute`（Chat Data，生成并自动执行 SQL）、`chat_with_db_qa`（Chat DB，专业问答不执行）、`chat_dashboard`（图表看板）、`chat_excel`（Excel 分析）。旧 API 中 `chat_data` 模式直接重定向到 `ChatWithDbExecute`（`api_v1.py:313`）。

### 3.0 总览：一条主链路

```
用户输入(db_name + question)
   │
   ▼
ChatFactory.get_implementation()          # 按 chat_scene 反射实例化场景类
   │
   ▼
BaseChat.stream_call()                    # ── 微流水线骨架 ─────────────────
   ├─ ① generate_input_values()           # 子类覆写：schema 召回（阻塞函数进线程池）
   ├─ ② AppChatComposerOperator.call()    # AWEL 子 DAG：历史窗口裁剪 + 模板填充 → ModelRequest
   ├─ ③ build_cached_chat_operator()      # AWEL 分支 DAG：缓存命中? → 否则 StreamingLLMOperator
   ├─ ④ parse_model_stream_resp_ex()      # 逐 chunk 流式解析（边生成边推给前端）
   ├─ ⑤ do_action()                       # 产出后处理：执行 SQL / 生成图表数据
   └─ ⑥ parse_view_response()             # 渲染：chart-view XML / dashboard JSON
   │
   ▼
StorageConversation.end_current_round()   # 会话与 view 消息持久化（全程 root_tracer span）
```

### 3.1 入口与场景路由

- `ChatScene` 枚举（`scene/base.py`）定义场景元数据（code/名称/参数类型）；`ChatFactory`（`scene/chat_factory.py`）用 `BaseChat.__subclasses__()` 反射 + `chat_scene` 匹配找到实现类，懒加载导入。
- 每个场景 = 4 个文件的约定：`chat.py`（场景类）+ `prompt.py`（模板注册进 `CFG.prompt_template_registry`）+ `out_parser.py`（输出解析/渲染）+ `config.py`（场景级配置，如 `schema_retrieve_top_k`、`schema_max_tokens`、`max_num_results`）。

### 3.2 Schema 召回：对表结构做 RAG

这是 DB-GPT 最值得抄的一环，代码在 `dbgpt-serve/datasource/service/db_summary_client.py` + `dbgpt-ext/rag/{summary,assembler,retriever}`。**离线索引 + 在线召回 + 降级兜底**三段式：

**离线索引**（`DBSummaryClient.db_summary_embedding`，首次连接/刷新时触发，带 per-db 互斥锁防并发写坏向量库）：

- `RdbmsSummary._parse_table_summary_with_metadata`（`dbgpt_ext/rag/summary/rdbms_db_summary.py`）把每张表序列化为两段文本：

```
table_name: t_order
table_comment: 订单表
index_keys: idx_user(user_name)
--table-field-separator--
"order_id" BIGINT COMMENT "订单ID", "user_name" VARCHAR COMMENT "用户名", ...
```

- 字段串按 embedding 模型维度（默认 512）**分片**（`_split_columns_str`），宽表标记 `separated=1`；
- 落入**两个向量库**：`{dbname}_profile`（表级）与 `{dbname}_profile_field`（字段级分片）。

**在线召回**（`DBSchemaRetriever`，`dbgpt_ext/rag/retriever/db_schema.py`）：

1. 用用户问题对**表级向量库**做 top_k 相似检索；
2. 命中宽表（`separated=1`）的，再用 metadata filter（`table_name`）去**字段级向量库**做二次召回，把相关字段拼回该表（并发度 3）；
3. `_deserialize_table_chunk` 把表名+字段重新组装成 `CREATE TABLE ...` 语句文本——**让 LLM 看到的是它最熟悉的 DDL 形态**。

**降级兜底**（`ChatWithDbAutoExecute.generate_input_values`，`scene/chat_db/auto_execute/chat.py:50`）：向量检索抛异常时退回 `database.table_simple_info()`（全库表结构简表），再按 `schema_max_tokens` 截断。**召回失败不阻塞主流程**——这是生产意识。

### 3.3 提示词组装：模板 + 约束 + 白名单

`scene/chat_db/auto_execute/prompt.py` 的模板六条约束值得逐条品味（中文版同样存在，按 `CFG.LANGUAGE` 选择）：

```
1. 生成语法正确的 {dialect} SQL；不需要 SQL 就直接回答       ← 意图分流
2. 默认 LIMIT {top_k} 行                                    ← 资源保护
3. 只准用给定表结构里的表；生成不了就说信息不足，禁止捏造        ← 反幻觉
4. 别弄错表和列的对应关系                                     ← 常见错误预防
5. 检查 SQL 正确性并优化性能                                  ← 自检前置到 prompt
6. 从 {display_type} 列表里选渲染方式，选不出就用 'Table'      ← 展示白名单
```

- 注入变量：`db_name`、`table_info`（召回结果）、`dialect`（SQL 方言）、`top_k`、`display_type`（`_generate_numbered_list()` 生成的 9 种 antv 图表白名单：line/pie/table/scatter/bubble/donut/area/heatmap/vector）；
- 输出格式强约束：`{response}` 注入 JSON schema（`thoughts` / `direct_response` / `sql` / `display_type`），并要求 "Ensure the response is correct json and can be parsed by Python json.loads"；
- few-shot：`example.py` 里 `ExampleSelector`（ONE_SHOT 模式）备了两条中文问答→JSON 的示例，展示"直接查询"与"跨表 JOIN"两种典型模式；
- 历史与记忆由 AWEL 子 DAG 处理（`AppChatComposerOperator._build_composer_dag`）：按 `BufferWindow`（保留首尾 N 轮）或 `TokenBuffer`（按 token 预算裁剪）配置裁剪历史后填进 `chat_history` 占位符。
- **温度被刻意压低**：`PROMPT_TEMPERATURE = 0.5`，注释里明确解释了为什么——Text2SQL 要的是可预测，不是创造力。

### 3.4 SQL 生成与解析

- 生成：`StreamingLLMOperator` 走模型集群（SMMF），逐 chunk 流出，前端边看"思考"边等 SQL；
- 解析（`DbChatOutputParser.parse_prompt_response`，`out_parser.py:50`）是**两级容错**：
  1. 先用 `sqlparse` 判断整段输出是不是纯 SQL（兼容社区微调的"直接吐 SQL"模型）——是则直接包装成 `SqlAction`；
  2. 否则 `json.loads` 提取 `{sql, thoughts, display_type, direct_response}`；解析失败降级为把原文放进 `thoughts` 至少能展示。
- `direct_response` 字段是意图分流的产物：问"你们有几张表"这类不需要 SQL 的问题时，模型直接回答、不走执行。

### 3.5 校验与修复：约束前置，错误后置

当前 v1 场景管线里**没有**"生成→校验→重写"的自动修复循环，策略是三层防线：

| 层 | 机制 | 位置 |
|---|---|---|
| Prompt 层 | 6 条约束 + 自检要求 + 低温度 | `auto_execute/prompt.py` |
| 解析层 | `sqlparse` 类型识别、JSON 两级容错 | `auto_execute/out_parser.py` |
| 执行层 | 执行异常被捕获 → 渲染错误视图（SQL + 空表 + 红字错误），**不重试**，把错误还给用户/会话 | `out_parser.py:157` |

非流式路径有一次通用重试：`BaseChat._no_streaming_call_with_retry` 挂了 `@async_retry` 装饰器（重试次数/并发度可配）。

真正的"修复循环"出现在 **agent 路线**：`api_v1/subagent/react_tools.py` 的 `code_interpreter` 有 `_try_repair_truncated_code`（截断代码自动补全→重新编译→通过才替换）；`sql_query` 工具有**只读白名单**（INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE 一律拒绝）。这印证了设计哲学：**修复与安全这类"确定性可判定的检查"用代码做，不该指望 LLM 自己守规矩**。

### 3.6 SQL 执行：读写分类与保护

`RDBMSBaseConnector.run()`（`dbgpt-core/datasource/rdbms/base.py:620`）：

```python
parsed, ttype, sql_type, table_name = self.__sql_parse(command)
if ttype == sqlparse.tokens.DML:
    if sql_type == "SELECT":      return self._query(command, fetch)
    else:                          # 写操作：执行后转 SELECT 取回受影响行
        self._write(command)
        select_sql = self.convert_sql_write_to_select(command)
        return self._query(select_sql)
else:                              # DDL 由配置开关控制
```

- `run_to_df` 把结果转 pandas DataFrame；`query_ex` 支持**执行超时**（按方言 `SET SESSION MAX_EXECUTION_TIME` / `statement_timeout` / `ob_query_timeout`），超时后恢复原值；
- 行数保护在 prompt 约束（`LIMIT {top_k}`）+ `run_to_df` 全量转 DataFrame 之间——**DB-GPT 把第一道防线放在提示词里**，这也是可以商榷的地方（见第五章）。

### 3.7 图表可视化：两条路

**单图（ChatData 路径）**：模型从 9 种展示白名单里选 `display_type` → `parse_view_response` 执行 SQL 得 DataFrame → 特判 `response_vector_chart`（向量列自动 PCA 降维到散点，失败降级表格）→ 把 `{type, sql, data}` 序列化成 `<chart-view content="...">` XML 塞进 view 消息，前端按类型渲染。执行失败时错误信息连同空表一起渲染——**UI 永远有确定性的结构可渲染**。

**多图看板（ChatDashboard 路径，`scene/chat_dashboard/`）**：

1. prompt 要求"至少 4 最多 8 个分析维度"，一次产出 `[{thoughts, showcase, sql, title}]` 数组，图表类型限定在 dashboard.json 模板的 `supported_chart_type`（Table/LineChart/BarChart/PieChart/IndicatorValue）；
2. `ChatDashboardOutputParser` 三级容错解析 JSON（直接 loads → 清洗 markdown 围栏 → 正则兜底抽取）；
3. `do_action` 逐条执行 SQL（单条失败 `logger.warning` 跳过，**不让一张图挂掉整个看板**）→ `ChartData(chart_uid, chart_name, chart_type, chart_desc, chart_sql, column_name, values)` → `ReportData`；
4. `DashboardDataLoader.get_chart_values_by_data` 用启发式把行式结果转成图表友好的 `ValueItem(name, type, value)`：优先选字符串列做维度、日期次之、含 "id" 的列再次之；三者也都没有时按列求和。

### 3.8 四个场景对比

| | ChatWithDbExecute (Chat Data) | ChatWithDbQA (Chat DB) | ChatDashboard | ChatExcel |
|---|---|---|---|---|
| 产出 | SQL + 执行结果 + 单图 | 专业元数据问答（不执行） | 4–8 张图的看板 | Excel 问答 |
| schema 来源 | 同一套 DBSchemaRetriever | 同上（图库 top_k=全部表） | 同上 | ExcelLearning 学习出的 DuckDB schema |
| 执行 | `run_to_df` | 无 | 逐条 `query_ex` | DuckDB 内存库 |
| 输出协议 | `{sql, thoughts, display_type, direct_response}` | 自然语言+表格 | `[{showcase, sql, title, thoughts}]` | `<api-call>` XML |
| 失败语义 | 错误视图 | 错误视图 | 单图失败跳过 | 解析失败 raise（fail-fast） |

---

## 四、为什么"工作流优先"而不是自由 ReAct

### 4.1 官方论述（`docs/docs/awel/why_use_awel.md`，全文核心）

> "We currently also see that the **auto-orchestration capabilities of multi-agents are greatly limited by the model's capabilities**, and at the same time, **for scenarios that require determinism**. For instance, tasks like pipeline work **do not need to utilize the auto-orchestration capabilities of large models**. Therefore, in DB-GPT, the integration of AWEL with agents can satisfy the implementation of a **production-level pipeline** and the **auto-orchestration of agents systems that address open-ended problems**."
>
> **"AWEL and agents are all you need."**

拆开是三个论点：① agent 自动编排的可靠性天花板 = 模型能力天花板；② 管线类任务要确定性，不该花 LLM 的钱和延迟去决定"下一步做什么"；③ 两者互补——生产管线归 AWEL，开放问题归 agent。

### 4.2 从代码反推的完整理由

| 维度 | 固定微流水线 | 自由 ReAct |
|---|---|---|
| **延迟/成本** | 1 次 LLM 调用（schema 召回是向量检索，毫秒级） | 思考-行动-观察循环，N 倍 token 与轮次 |
| **确定性** | 步骤固定，失败点可枚举，prompt 里就能针对性下约束 | 每一步路径都可能漂移，回归测试无法覆盖 |
| **流式体验** | AWEL 流式算子端到端，边生成边渲染 | 中间步骤（工具调用）难以优雅流式 |
| **可观测** | 每个 AWEL 节点固定打 span，tracing 即拓扑 | 需要额外从循环里还原执行轨迹 |
| **安全** | 执行入口唯一（`run()`），读写分类、超时、行数限制都在确定位置 | 工具面暴露多，攻击面大，需白名单+沙箱+步数上限兜底 |
| **演进成本** | 换模型/换方言只改模板与算子 | prompt 优化在循环里互相耦合 |

### 4.3 ReAct 并没有缺席：DB-GPT 是双轨制

调研确认 agent 能力是完整存在的，只是被定位成"开放式问题"的承接方：

- `dbgpt-core/src/dbgpt/agent/expand/react_agent.py`：`ReActAgent`（LOOP 运行模式，强制 `Thought/Action/Action Input/Observation` 步进制，终止只能 `Action: terminate`）；
- 生产入口 `POST /v1/chat/react-agent`（`api_v1/agentic_data_api.py`）：SSE 事件协议（step.start/step.chunk/step.output/final），工具含 `sql_query`、`code_interpreter`、`shell_interpreter`、`knowledge_retrieve`、`dispatch_parallel_tasks`（并行子 agent，带独立会话、步数预算 15、超时 600s、只读 MCP 过滤）；
- plan-and-execute：`PlannerAgent` + `AutoPlanChatManager`（`agent/core/plan/`）；
- 四种应用构建模式（v0.6 起，`docs/docs/application/apps/app_manage.md`）：多 agent 自动规划 / **任务流编排** / 单 agent / 原生应用——本身就是"自由度光谱"的产品化。

### 4.4 双向嵌套：工作流与 agent 不是对立而是互补

- **agent 作为 DAG 节点**：`agent/core/plan/awel/agent_operator.py` 的 `AWELAgentOperator`（带 ViewMetadata，可在 flow 画布拖拽）；`AWELAgent.fixed_subgoal` 可以把某个 agent 的子目标**钉死**——在 AWEL 编排里 agent 只允许按既定流程解题（源码里还有一段中文注释解释 flow 启动点的匹配逻辑）；
- **AWEL flow / App 作为 agent 的资源**：`ResourceType` 枚举里有 `AWELFlow`、`App`；`GptAppResource` 把一个已发布的应用（flow 应用或 agent 应用）当作另一个 agent 的工具调用；
- **技能即约束**：`skills/` 目录的 SKILL.md（如 `csv-data-analysis`）把 agent 的自由度收敛到"必须调用确定性脚本 → 输出标记块 → 注入前端模板"，甚至明文规定 "Never write or modify any JavaScript chart rendering code yourself"；
- 官方知识问答甚至反向示范了一次"从固定 RAG 管线迁到 ReAct"（`docs/docs/design/agentic_rag_principles.md`）——结论不是"agent 万能"，而是：单次检索能搞定的走管线，需要**迭代检索/多工具/可溯源引用**的才值得上循环。

### 4.5 一句话回答"为什么工作流优先"

> Text2SQL 的每一步都有"正确答案"可依（schema 是既定的、SQL 语法是可判定的、图表类型是白名单的），**凡是能用确定性代码表达的步骤，就不该用模型的不确定性去换**；模型只出现在真正需要"理解与生成"的那一格。Agent 循环留给"该问哪张表都还不知道"的探索型场景。

---

## 五、对我们的 Text-to-SQL Agent 项目的启示

1. **先定管线，再谈 agent**。照抄 BaseChat 骨架：`schema 召回 → prompt 组装 → 生成 → 解析 → 执行 → 渲染` 六步固定，每步一个纯函数/算子，天然可单测、可 tracing、可替换。
2. **schema 召回照抄"双层向量索引"方案**：表级 chunk（表名+注释+索引+字段摘要）+ 字段级分片（宽表二次召回），召回结果组装成 `CREATE TABLE` 文本喂给 LLM；务必做**全量简表降级路径**。
3. **把约束写进 prompt，把校验写进代码**。DB-GPT 的六条约束模板可直接改造复用；解析用 sqlparse + JSON 两级容错；建议在它基础上**补上它没做完的一环**：执行前用 `EXPLAIN`/语法解析做白盒校验，失败把数据库报错回填给 LLM 做一次受限重试（1–2 次），这就是"校验/修复循环"的最小实现，比自由 ReAct 便宜且可控。
4. **只读保护放在执行入口**，不要依赖模型自觉（学 `sql_query` 工具的关键字黑名单 + `run()` 的读写分类 + 超时设置）。
5. **输出协议白名单化**：SQL、图表类型、展示方式都收敛到枚举值，前端永远拿确定结构渲染；LLM 的自由度只留在 `thoughts` 字段。
6. **编排框架可以自建轻量版**：AWEL 的精髓是「上下文管理器 + `>>` 重载 + 元类注入 + 流/非流双形态 TaskOutput」，两百行就能抄出骨架；如果项目需要可视化编排或 OpenAI 兼容服务化，再补 FlowFactory 那层。
7. **预留 agent 化逃生舱**：当用户问题本质是"探索"（不知道查哪张表、要跨多轮取证）时，提供 ReAct 路线（工具：`sql_query` 只读 + `schema_search`），并把它的每一步 Action/Observation 记录成可回放的 trace——DB-GPT v0.8 的"对话回放/步数预算/引用白名单"就是为此准备的护栏。

---

## 六、关键文件索引

**AWEL 框架**（`packages/dbgpt-core/src/dbgpt/core/awel/`）

| 文件 | 内容 |
|---|---|
| `dag/base.py` | DAG/DAGNode/DependencyMixin(`>>`)/DAGVar/DAGContext/DAGVariables |
| `operators/base.py` | BaseOperator/元类自动装配/call 与 call_stream/WorkflowRunner 接口 |
| `operators/common_operator.py` | Map/Join/ReduceStream/Branch/BranchJoin/Input/Trigger |
| `operators/stream_operator.py` | Streamify/Unstreamify/TransformStream |
| `task/base.py` + `task/task_impl.py` | TaskOutput 双形态/TaskContext/InputContext(map/map_all/reduce/predicate_map) |
| `runner/local_runner.py` + `runner/job_manager.py` | DFS 执行/反向收集子图/分支 SKIP 裁剪 |
| `trigger/http_trigger.py` 等 | HTTP 触发器/动态路由装卸/批量触发 |
| `flow/base.py` + `flow/flow_factory.py` | 算子 UI 元数据/画布 JSON→DAG 编译 |
| `packages/dbgpt-serve/src/dbgpt_serve/flow/service/service.py` | flow 保存/编辑/恢复/执行/调试 |
| `packages/dbgpt-app/src/dbgpt_app/openapi/api_v2.py` | OpenAI 兼容 chat_flow 入口 |

**Text2SQL 微流水线**（`packages/dbgpt-app/src/dbgpt_app/scene/`）

| 文件 | 步骤 |
|---|---|
| `base_chat.py` | 微流水线骨架（stream_call/_build_model_request/do_action） |
| `chat_factory.py` + `base.py` | 场景路由/ChatScene 枚举 |
| `chat_db/auto_execute/{chat,prompt,out_parser,example}.py` | Chat Data 全链路 |
| `chat_db/professional_qa/*.py` | Chat DB 问答 |
| `chat_dashboard/{chat,prompt,out_parser,data_loader}.py` + `data_preparation/report_schma.py` | 看板多图链路 |
| `chat_data/chat_excel/` | Excel 分析 |
| `operators/app_operator.py` | AWEL 组装算子/缓存分支 DAG |
| `packages/dbgpt-serve/src/dbgpt_serve/datasource/service/db_summary_client.py` | schema 索引与召回客户端 |
| `packages/dbgpt-ext/src/dbgpt_ext/rag/{summary/rdbms_db_summary.py, assembler/db_schema.py, retriever/db_schema.py}` | 表结构序列化/组装/双层召回 |
| `packages/dbgpt-core/src/dbgpt/datasource/rdbms/base.py` | SQL 执行/读写分类/超时 |

**设计文档与 agent 路线**

| 文件 | 内容 |
|---|---|
| `docs/docs/awel/why_use_awel.md` | "工作流优先"的官方论述（必读） |
| `docs/docs/awel/awel.md` | 三层架构/DSL 动机 |
| `docs/docs/getting-started/concepts/awel.md` | 四卖点速览 |
| `docs/docs/agents/introduction/introduction.md` | "可控 agentic workflow"定位 |
| `docs/docs/design/agentic_rag_principles.md` | 固定 RAG vs Agentic RAG 的取舍 |
| `packages/dbgpt-core/src/dbgpt/agent/expand/react_agent.py` + `api_v1/agentic_data_api.py` + `api_v1/subagent/react_tools.py` | ReAct 实现与生产护栏 |
| `packages/dbgpt-core/src/dbgpt/agent/core/plan/awel/` | agent 与 AWEL 双向嵌套 |
| `skills/` | SKILL.md 技能即约束的实例 |
