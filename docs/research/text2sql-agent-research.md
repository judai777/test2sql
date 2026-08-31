# nanobot Agent 架构调研报告

> 目的：为 Text-to-SQL（NL2SQL）Agent 项目做技术储备。本文基于对 nanobot 源码的逐文件精读，
> 所有结论均落到具体文件与行号，可直接按图索骥。
> 调研范围：主循环、单轮 "LLM 决策 → 工具执行 → 观察回填"、工具注册与 schema 生成、
> Dream 记忆写入/检索时机、最大步数与错误兜底。

---

## 0. 结论速览（TL;DR）

| 问题 | 答案 |
|---|---|
| 主循环在哪 | **两层循环**：外层 `AgentLoop`（`nanobot/agent/loop.py`）负责"消息 → 会话 → turn 生命周期"；内层 `AgentRunner`（`nanobot/agent/runner.py`）负责"单个 turn 内 LLM ↔ 工具迭代" |
| 核心迭代语句 | `runner.py:508` `for iteration in range(spec.max_iterations)`（`AgentRunner._run_core`） |
| 决策判定 | `providers/base.py:583` `LLMResponse.should_execute_tools`：有 tool_calls 且 `finish_reason ∈ {tool_calls, function_call, stop}` |
| 观察回填 | `runner.py:604-618`：结果按 `tool_call` 顺序 zip 成 `{"role": "tool", "tool_call_id", "name", "content"}` append 进 messages |
| 工具注册 | `Tool` ABC（`tools/base.py:159`）+ `@tool_parameters` 装饰器 + `ToolRegistry`（`tools/registry.py`）+ `ToolLoader` pkgutil 扫描 / entry_points 插件（`tools/loader.py`） |
| schema 生成 | `Tool.to_schema()`（`base.py:306`）输出 OpenAI function-calling 格式；`tools/schema.py` 提供 JSON Schema 片段类，默认 `additionalProperties=False` 严格校验 |
| Dream 写入 | 双通道：① 上下文超预算时 LLM 摘要进 `history.jsonl`（`Consolidator.maybe_consolidate_by_tokens`）；② 定时 cron（默认每 2h）Dream run 编辑 `MEMORY.md/SOUL.md/USER.md` + git 提交 |
| Dream 检索 | `ContextBuilder.build_system_prompt`（`agent/context.py:94`）注入 MEMORY.md + "dream cursor 水位线之后"的未消化历史；cursor 防重复注入 |
| 最大步数 | 默认 **200**（`config/schema.py:130` `max_tool_iterations`）；耗尽后走 `finalize_on_max_iterations`：一次无工具的总结请求，失败再退到模板文案 |
| 错误兜底 | 五层：provider 重试(1/2/4s 退避+failover) → 响应级(空响应/截断/畸形 tool call) → 工具级(错误即观察+重试提示) → turn 级(checkpoint 恢复) → run 级(hook on_finally) |

---

## 1. 全景：一条消息的生命周期

```
Channel(Telegram/WebUI/CLI...)
   │  InboundMessage
   ▼
MessageBus (nanobot/bus/queue.py)
   │
   ▼
AgentLoop.run()                      loop.py:1236   ← 外层循环：消费总线
   │  asyncio.create_task(_dispatch)  loop.py:1351  ← 每条消息一个 task
   ▼
AgentLoop._dispatch()                loop.py:1371  ← 同会话串行锁 + 并发门
   │  6 个 turn stage                 loop.py:1680-1688
   │  restore → compact → command → build → run → save → respond
   ▼
AgentLoop._run_turn → _run_agent_loop  loop.py:1978 / 930
   │  组装 AgentRunSpec                loop.py:1158-1194
   ▼
AgentRunner.run()                    runner.py:411  ← hook 包裹
   └─ AgentRunner._run_core()        runner.py:465
        for iteration in range(max_iterations)      runner.py:508
        ┌──────────────────────────────────────────┐
        │ 1. prepare_for_model   (上下文治理)        │ runner.py:514
        │ 2. _request_model      (LLM 决策)          │ runner.py:530
        │ 3. should_execute_tools?                   │ runner.py:557
        │    ├─ 是: append assistant(tool_calls)     │ runner.py:562-572
        │    │      execute_tool_calls (并发执行)     │ runner.py:587
        │    │      append role="tool" 观察回填       │ runner.py:604-618
        │    │      drain 注入 → continue 下一轮      │ runner.py:645-653
        │    └─ 否: 最终回答 → break                  │ runner.py:749-862
        └──────────────────────────────────────────┘
   ▼
OutboundMessage → Channel
```

架构动机（`.agent/design.md`）：**"core stays small; extend at the edges"** ——
`loop.py` + `runner.py` 是被刻意保护的核心路径，能力扩展一律走 tools/channels/skills/MCP。

---

## 2. 主循环：外层 AgentLoop 与内层 AgentRunner 的分工

### 2.1 外层 `AgentLoop`（`nanobot/agent/loop.py`，2352 行）

- **消费循环** `run()`（`loop.py:1236`）：`while self._running` 里
  `await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)`（`loop.py:1244`）。
  1 秒超时的空隙用来做**空闲会话压缩扫描**（`_check_expired_sessions_if_due`，`loop.py:1224-1234`），
  而不是白等——这是把"维护任务搭车在消费循环上"的实用模式。
- **并发模型**（`loop.py:1351-1357`）：每条消息 `asyncio.create_task(self._dispatch(msg))`；
  同一 `session_key` 用 `asyncio.Lock` 串行（`_get_session_lock`，`loop.py:2346`），
  跨会话自然并发；全局并发由 `NANOBOT_MAX_CONCURRENT_REQUESTS` 信号量门控（`loop.py:432-436`）。
- **turn 内注入队列**（`loop.py:1314-1348`）：会话正忙时，新消息不排队开新 turn，
  而是放进 `_pending_queues[key]`（容量 20），由运行中的 turn 在工具间隙"插话"——
  对应 runner 里的 `injection_callback`。
- **turn 生命周期 = 显式 stage 管线**（`_process_message`，`loop.py:1570`；stage 调度 `1680-1688`）：

  | stage | 函数 | 职责 |
  |---|---|---|
  | restore | `_restore_turn` `loop.py:1749` | 恢复 checkpoint/中断、按 session policy 裁剪工具（`disabled_tools` → 重建受限 `ToolRegistry`，1771-1777） |
  | compact | `_compact_session` `loop.py:1805` | `AutoCompact.prepare_session` 取历史摘要 |
  | command | `_dispatch_command` `loop.py:1813` | 斜杠命令短路（跳过 build/save） |
  | build | `_build_turn` `loop.py:1862` | 解析模型运行时、token 压力 consolidation、读历史、**提前持久化用户消息**、拼 `initial_messages` |
  | run | `_run_turn` `loop.py:1978` | 调 `AgentRunner`，拿 `AgentRunResult` |
  | save | `_persist_turn` `loop.py:2016` | `_save_turn` 增量写会话、清 checkpoint、后台再触发一次 consolidation |
  | respond | `_prepare_outbound` `loop.py:2063` | 组装 OutboundMessage |

### 2.2 内层 `AgentRunner`（`nanobot/agent/runner.py`，1415 行）

- 定位：**"Run a tool-capable LLM loop without product-layer concerns"**（`runner.py:139`）——
  不碰 channel/会话持久化，只吃 `AgentRunSpec`（`runner.py:94-119`）吐 `AgentRunResult`。
  这个解耦让 subagent（`agent/subagent.py`）和 Dream 复用同一个执行引擎。
- `AgentLoop._run_agent_loop`（`loop.py:930`）组装 spec 时注入所有回调：
  `max_iterations=self.max_iterations`（1162）、`concurrent_tools=True`（1165）、
  `checkpoint_callback`（写 runtime checkpoint）、`injection_callback`（drain 注入队列）、
  `continuation_callback`（sustained goal 续跑提示，`loop.py:1124-1133`）。

> 面试要点：为什么分两层？——外层管**会话级串行/路由/持久化/恢复**，
> 内层管**纯 LLM↔工具迭代**。副产物是：任何"另一个 agent 进程"（subagent、Dream、heartbeat）
> 都能以不同 tools/max_iterations/hook 组合复用内层，而不用复制主循环。

---

## 3. 单轮 "LLM 决策 → 工具执行 → 观察回填"

核心在 `AgentRunner._run_core`（`runner.py:465`），一次迭代的完整链路：

### 3.1 决策前：上下文治理（`runner.py:508-549`）

```python
for iteration in range(spec.max_iterations):          # runner.py:508
    messages_for_model = self.context_governor.prepare_for_model(...)  # 514
    response = await self._request_model(...)                          # 530
```

- `ContextGovernor`（`nanobot/agent/context_governance.py`）生成**模型视图的消息副本**：
  压缩/占位化旧工具结果（microcompact）、修复非法消息序列。
  关键不变量：**绝不原地改持久化历史**——治理产生的"合成编辑"不能移动调用方保存新 turn 时的追加边界（`runner.py:509-513` 注释明说）。
- `_request_model`（`runner.py:930`）根据 `hook.wants_streaming()` 选
  `chat_stream_with_retry` / `chat_with_retry`，并统一包一层墙钟超时（见 §6）。

### 3.2 决策判定（`runner.py:557`，定义 `providers/base.py:583-587`）

```python
def should_execute_tools(self) -> bool:
    # has_tool_calls 且 finish_reason ∈ ("tool_calls", "function_call", "stop")
    # 拒绝在 refusal / content_filter / error 下执行 gateway 注入的调用
```
若响应带了 tool_calls 但 finish_reason 不允许执行，runner 只打 warning 忽略（`runner.py:655-660`）——
**"带工具调用的拒绝回复不应该真的执行工具"** 是一个容易被忽视的安全/一致性细节。

### 3.3 执行：assistant 消息先行，工具并发分批（`runner.py:562-595`）

1. 先把 assistant 消息（含 tool_calls）append 进 messages（`runner.py:562-572`）——
   **顺序不能反**：OpenAI/Anthropic 协议要求 tool 结果前必须有对应 assistant tool_calls 声明。
2. `execute_tool_calls(...)`（`runner.py:587` → `tools/execution.py:46`）：
   - `_partition_tool_batches`（`execution.py:261-285`）按 `tool.concurrency_safe`
     （= `read_only and not exclusive`，`tools/base.py:195-197`）分批：
     可并发的一批 `asyncio.gather`，写操作独占一批串行——**既拿并发又保写序**。
   - 每个调用走 `_execute_tool_call`（`execution.py:89`）：
     `prepare_call` 解析/矫正/校验参数 → `hook.before_execute_tool` →
     `tool.execute(**params)` → 异常或 `ToolResult.error` 统一转错误观察。

### 3.4 观察回填（`runner.py:604-618`）

```python
for tool_call, result in zip(response.tool_calls, results):
    tool_message = {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "name": tool_call.name,
        "content": self.context_governor.normalize_tool_result(...),  # 截断/占位
    }
    messages.append(tool_message)
```

- 回填后立刻 `_emit_checkpoint(phase="tools_completed")`（`runner.py:628-642`），
  把进度写进会话 sidecar（`SessionManager.save_runtime_checkpoint`，`session/manager.py:1322`），
  取消/崩溃后可恢复部分上下文（`loop.py:1443-1458`）。
- 然后 drain 一次注入队列（`runner.py:645-649`）→ `continue` 进入下一轮 LLM 调用。
  **每轮工具执行后都会把控制权交回 LLM**——这就是 ReAct 循环的"O"。

### 3.5 终止：无工具调用 → 最终回答（`runner.py:749-862`）

`finalize_content` 清洗 → 空响应重试 / length 续写 / error 分支（见 §6）→
append assistant 消息 + `checkpoint("final_response")` → `break`。
`for-else` 结构保证正常 break 不触发 max_iterations 兜底（`runner.py:863`）。

### 3.6 turn 结束的增量持久化（`loop.py:2125` `_save_turn`）

- 只保存 `messages[skip:]` 的**本轮新增消息**；
- 丢弃"没有对应 assistant tool_calls 声明"的孤儿 tool 结果（`loop.py:2177-2192`，注释：
  *Undeclared tool results corrupt future provider requests*）；
- 超过 `max_tool_result_chars`（默认 16000，`config/schema.py:132`）的工具结果截断；
- 空内容的 assistant 消息直接跳过（"they poison session context"，`loop.py:2176`）。

---

## 4. 工具注册与 schema 生成

### 4.1 工具基类契约（`nanobot/agent/tools/base.py`）

- `Tool` ABC（`base.py:159`）要求三个抽象属性：`name` / `description` / `parameters`（JSON Schema dict），
  外加抽象 `execute(**kwargs)`。**schema 即代码**：`parameters` 就是发给 LLM 的 function schema，
  也是本地校验的依据（同一份 schema 同时用于 `cast_params` + `validate_params`）。
- `to_schema()`（`base.py:306-315`）输出 OpenAI function-calling 格式：

  ```python
  {"type": "function",
   "function": {"name": ..., "description": ..., "parameters": <JSON Schema>}}
  ```

- `@tool_parameters({...})` 类装饰器（`base.py:318-350`）：把 schema 冻结在类上，
  每次访问返回 `deepcopy`（防调用方改坏共享 schema），并动态把 `parameters` 从
  `__abstractmethods__` 里摘掉——一个"用装饰器补齐抽象属性"的 Python 技巧。
- `ToolResult(str)`（`base.py:144-156`）：字符串子类 + `is_error` 标志，
  工具输出天然可回填，错误状态可被执行层识别。

### 4.2 schema 片段类（`nanobot/agent/tools/schema.py`）

- `StringSchema / IntegerSchema / NumberSchema / BooleanSchema / ArraySchema / ObjectSchema`
  实现 `to_json_schema()`；`Schema.validate_json_schema_value`（`base.py:51`）是唯一的
  递归校验入口（required/enum/bounds/additionalProperties/min-max 全覆盖）。
- `tool_parameters_schema()`（`schema.py:217-235`）组根 object，**默认 `additionalProperties=False`**——
  LLM 拼错参数名会在执行前被校验拦下，而不是静默忽略。
- `Tool._cast_value`（`base.py:258-295`）做**宽容矫正**：`"42"→42`、`"true"→True`、
  字符串→string。先 cast 后 validate，兼容"LLM 把参数当字符串传"这一高频失败模式。

### 4.3 注册中心与 schema 派发（`nanobot/agent/tools/registry.py`）

- `ToolRegistry`（`registry.py:19`）：`dict[str, Tool]` + **定义缓存**
  `_cached_definitions`（register/unregister 时失效）。
- `get_definitions()`（`registry.py:86-108`）：**builtin 排前、`mcp_` 前缀排后，组内按名排序**——
  稳定顺序让 provider 侧的 prompt cache 命中率最大化（注释明说 cache-friendly）。
- `prepare_call`（`registry.py:110-147`）是执行前统一协议：
  1. 查不到工具 → 错误观察 + "Did you mean 'xxx'?"（大小写/标点归一后的唯一匹配建议，53-69）
  2. 参数是 JSON 字符串 → 尝试 `json.loads`（`_coerce_argument_value`，150-168）
  3. `{"arguments": {...}}` 包装 → 解包（176-185）
  4. `cast_params` → `validate_params`，错误组装成**给模型看**的修复提示。
- `execute`（`registry.py:187-201`）：任何异常/错误结果都包成
  `ToolResult.error(msg + "\n\n[Analyze the error above and try a different approach.]")`——
  **错误不抛出、不终止 turn，而是作为观察回填让模型自纠错**。

### 4.4 自动发现（`nanobot/agent/tools/loader.py`）

- `discover()`（`loader.py:36-66`）：`pkgutil.iter_modules` 扫 `nanobot/agent/tools/` 包，
  import 每个模块后收集"非抽象、可发现、非下划线"的 `Tool` 子类（跳过 base/schema/registry 等基础设施模块，`loader.py:20-23`）。
- 外部插件：`entry_points(group="nanobot.tools")`（`loader.py:68-90`）；
  插件与 builtin 重名时让位并告警（`loader.py:107-117`），旧版"Error: 前缀"错误契约用
  `_LegacyErrorPrefixTool` 包装兼容（`loader.py:127`）。
- 工厂模式：`Tool.enabled(ctx)` 决定开关、`Tool.create(ctx)` 注入 `ToolContext`
  （config/bus/subagent_manager 等，`base.py:215-221`；组装见 `loop.py:620-647`）。

> 面试追问预测："schema 是手写还是从函数签名反射生成的？"——nanobot 选择**手写 JSON Schema +
> 类型矫正**而非 pydantic 反射：显式、无魔法（与 `.agent/design.md` "Explicit over magical" 一致），
> 代价是样板代码，收益是 schema/校验/文档三合一且完全可控。

---

## 5. Dream 记忆：写入/检索时机

存储分两层，**互不混用**：

| 层 | 载体 | 归属 |
|---|---|---|
| 会话历史 | `session/*.jsonl` + `last_archived` 水位 + `_last_summary` | `SessionManager`（`session/manager.py`） |
| 跨会话长期记忆 | `memory/history.jsonl`（经验流）、`MEMORY.md/SOUL.md/USER.md`（固化知识）、`.dream_cursor`（水位） | `MemoryStore`（`agent/memory.py:56`） |

### 5.1 写入路径 A：上下文压力触发的 LLM 摘要（"写经验流"）

- 触发点两处：turn 的 **build 阶段同步**一次（`loop.py:1876-1880`）+ **save 阶段后台**一次
  （`loop.py:2045-2051`），都调 `Consolidator.maybe_consolidate_by_tokens`（`memory.py:1093-1173`）。
- 条件：全量回放 prompt 的估算 token > `context_window - max_tokens - 1024`（`memory.py:1052-1058`）。
- 动作：取固定边界（保留尾部 `MIN_COMPACTED_REPLAY_MESSAGES = 8` 条并对齐 user 消息，
  `memory.py:990-1005` + `session/manager.py:43`）→ `MemoryArchiver.archive_session`
  用 LLM 生成摘要 → `append_history` 写入 `history.jsonl`（带 session_key 标记）→
  推进 `session.last_archived`，摘要存 `session.metadata["_last_summary"]`。
- **降级兜底**：LLM 调用失败 / 超长 / 返回工具调用 / 空摘要，一律 `raw_archive`
  原样转储（`memory.py:715-735, 843-861`）——宁可存原文也不丢上下文。
- `append_history`（`memory.py:263-314`）：线程锁保证 cursor 严格单调、`strip_think` 防模板泄漏、
  64KB 单条硬上限；`history.jsonl` 重写用 tmp+fsync+rename+目录 fsync 的原子写（`memory.py:493-516`）。

### 5.2 写入路径 B：定时 Dream run（"固化知识"）

- **排程**：gateway 启动时注册系统 cron 任务 `id="dream"`（`cli/gateway_runtime.py:827-842`），
  默认每 2 小时（`config/schema.py:53-59` `DreamConfig.interval_h=2`）；另有 `/dream` 命令手动触发
  （`command/builtin.py:420-497`）。两者逻辑一致：
- **输入**（`MemoryStore.build_dream_prompt`，`memory.py:565-591`）：
  dream prompt 模板（`nanobot/templates/agent/dream.md`）
  + cursor 之后未消化的 history entry（最多 20 条）
  + **三个记忆文件的当前真实内容内嵌**（`memory.py:593-613`，单文件 8KB 上限）——
  注释明说这是为了"模型编辑的是真实文件而非陈旧心智模型，消除一类幻觉审计记录"。
- **执行**：`agent.process_direct(prompt, session_key="dream:<ts>", ephemeral=True,
  tools=build_dream_tools())`（`gateway_runtime.py:585-592`）——
  走同一个 `AgentRunner`，但用**受限工具白名单**（`build_dream_tools`，`memory.py:625-666`：
  Read/Edit/Write/ApplyPatch，只允许写 MEMORY.md/SOUL.md/USER.md 和 skills/ 目录）。
- **完成判定与水位推进**（`gateway_runtime.py:596-615`）：只有
  `resp.metadata["_stop_reason"] == "completed"`（`memory.py:669-676`）才
  `set_last_dream_cursor(last_cursor)`；错误/max_iterations/超时都**不推进**——
  下一轮 Dream 重消化同一批 entry，天然幂等。
- **审计落地**：finally 里 `_commit_dream_changes`（`gateway_runtime.py:152-163`）git auto-commit，
  commit message 由**真实 working-tree diff** 生成（`dream_content_diff`，`memory.py:615-623`），
  刻意不采信模型的自述；随后 `compact_history()`（只删已被 Dream 消化的旧 entry，`memory.py:425-453`）
  和 `prune_dream_sessions`（保留最近 10 个 dream 会话）。

### 5.3 检索时机：`ContextBuilder.build_system_prompt`（`agent/context.py:94-171`）

每个 turn 构建系统提示时（build 阶段）：

1. `MEMORY.md` 全文注入为 `# Memory`（`context.py:123-126`）——**固化知识，永久在场**；
2. `read_recent_history_for_prompt(since_cursor=get_last_dream_cursor())`
   （`context.py:141-146` → `memory.py:404-423`）：只取**尚未被 Dream 消化**的 entry
   注入 `# Recent History`（上限 50 条 / 8000 tokens，`context.py:84-85`）。

   → **dream cursor 同时是写入水位线和检索去重水位线**：已固化为 MEMORY.md 的内容不再以
   原始流水形式重复注入，上下文不随时间膨胀。
3. 会话内近期对话走另一条通道：`Session.get_history`（`session/manager.py:330-449`），
   从 `min(last_archived, 固定 8 条窗口起点)` 开始回放，带 token 预算截断
   （`_replay_token_budget`，`loop.py:917-928`）。
4. 空闲会话 proactive 压缩：主循环 1s 空隙扫描 → `AutoCompact.check_expired`
   （`autocompact.py:57-80`），TTL 默认 15 分钟（`config/schema.py:148-153`）→
   `compact_idle_session`（`memory.py:1175-1238`）。

> 一句话总结（面试版）：**"经验流先落 history.jsonl（带自增 cursor），Dream 定时把未消化批次
> 固化进 MEMORY.md 并推进 cursor；prompt 构建时按 cursor 水位做增量检索，固化过的不再注入。"**

---

## 6. 最大步数与错误兜底

### 6.1 最大步数：`max_iterations` + 强制收尾

- 默认 **200**（`config/schema.py:130`），经 `AgentLoop.__init__`（`loop.py:323-325`）
  传入 `AgentRunSpec.max_iterations`。
- `for-else`（`runner.py:508 / 863-895`）：循环耗尽进入 else 分支：
  1. 先 drain 剩余注入消息（不让用户插话丢失）；
  2. `finalize_on_max_iterations=True` 时调 `_try_finalize_after_max_iterations`
     （`runner.py:1218-1264`）：**发一次不带 tools 的请求**，提示模型"预算已尽，请总结已知信息作答"；
  3. 总结请求失败/仍返回工具调用/空白 → 退到模板文案
     `nanobot/templates/agent/max_iterations_message.md`（`_max_iterations_fallback`，`runner.py:1319-1329`）。
  设计要点：**超步数也要给用户一个有内容的答案**，而不是抛异常。

### 6.2 五层兜底一览

| 层 | 机制 | 位置 |
|---|---|---|
| ① provider 网络/限流 | `_run_with_retry`：退避 `_CHAT_RETRY_DELAYS=(1,2,4)`s、解析 `Retry-After` 头/文案、`persistent` 模式长重试；`FallbackProvider` 按 `fallback_models` 预设整链切换 | `providers/base.py:606, 1478-1550, 1572-`；`providers/factory.py:269-284` |
| ② 请求墙钟 | `NANOBOT_LLM_TIMEOUT_S` 默认 300s（流式外层 `max(300, 2×timeout)`）；超时转 `finish_reason="error"` 响应而不是抛异常 | `runner.py:1296-1309, 1043-1066` |
| ③ 响应内容 | 空响应重试 `_MAX_EMPTY_RETRIES=2` → 仍空走无工具 finalization retry；`length` 截断续写 `_MAX_LENGTH_RECOVERIES=3`（拼接已生成部分+续写提示）；error/欠费给专用文案并持久化占位 assistant 消息防历史断裂；**畸形 tool call**（name 非字符串）剥离→重试→退化为 no-tools 请求（否则会永久卡死会话回放） | `runner.py:76-79, 668-730, 789-827, 1127-1163, 1411-1415` |
| ④ 工具执行 | 异常/`ToolResult.error` 一律转成观察回填 + `[Analyze the error above...]` 自纠错提示；重复外部查询/越界计数升级；SSRF 是不可重试的硬边界 | `tools/execution.py:19, 89-192, 213-249`；`tools/registry.py:187-201` |
| ⑤ turn/run 生命周期 | 每个工具阶段写 runtime checkpoint（sidecar 原子替换），取消后恢复部分上下文；`_dispatch` 捕获异常 `delivery.fail`；pending queue 剩余消息重新发布回总线防丢；hook `on_error/after_run/on_finally` 保证收尾 | `loop.py:1422-1493, 2267-2270`；`session/manager.py:1322`；`runner.py:419-463` |

### 6.3 值得复刻的三个细节

1. **错误是观察，不是异常**：整个工具层没有任何异常逃逸到主循环——
   所有失败都以 `role="tool"` 文本回填，交给 LLM 决定重试/换路/承认失败。
2. **恢复点先于正确性妥协**：checkpoint 只在 `awaiting_tools / tools_completed / final_response`
   三个安全边界写（`runner.py:573, 628, 840`），且 sidecar 带版本+基数校验防错位
   （`session/manager.py:1369-1407`）。
3. **降级链完整**：LLM 摘要失败→raw dump；malformed tool call→no-tools 请求；
   max_iterations→无工具总结→模板文案。每层兜底都有"再兜底"。

---

## 7. 迁移到 Text-to-SQL Agent 的落点建议

结合以上机制，你的项目最小骨架可以直接对应过来：

| Text-to-SQL 需求 | nanobot 对应物 | 直接借鉴的文件 |
|---|---|---|
| 主循环骨架 | `AgentRunner._run_core` 的 for-loop + should_execute_tools 判定 | `runner.py:465-908` |
| `execute_sql` 工具 | `Tool` 子类：`read_only=True`（SELECT 可并发）、超时/行数上限、只读账号连接 | `tools/base.py:159`；schema 用 `tools/schema.py:217` |
| schema 描述工具（get_schema/list_tables） | 同上；把 DDL/表关系描述作为工具结果回填，而非塞 system prompt（省 token、按需拉取） | `tools/execution.py` |
| SQL 报错自纠错 | 错误回填 + retry hint 模式：`SQLSyntaxError...` 作为 tool 观察，模型改写重试 | `tools/registry.py:187-201` |
| 步数/成本护栏 | `max_iterations`（Text-to-SQL 建议 8~15 即可）+ finalize-on-budget 总结 | `runner.py:863-895, 1218` |
| 结果截断 | `max_tool_result_chars` + `normalize_tool_result`（防大结果集撑爆上下文） | `context_governance.py`；`loop.py:2193-2205` |
| 成功 (question, SQL) 样例沉淀 | history.jsonl + cursor 水位线模式的简化版：追加成功对，prompt 时按相似度/水位注入 few-shot | `memory.py:263-314, 404-423, 565-591` |
| 危险 SQL 拦截 | workspace violation / SSRF 硬边界的"分类+不可重试提示"模式 | `tools/execution.py:22-49, 213-249` |

**面试追问快答**（基于本文行号均可展开）：

- Q: 工具并发安全怎么保证？→ `read_only and not exclusive` 分批，写操作独占串行（`execution.py:261`）。
- Q: 上下文超长怎么办？→ 三级：turn 内 microcompact（governance）、跨 turn 摘要（consolidator）、
  空闲 TTL 压缩（autocompact）。
- Q: 记忆会不会重复注入？→ dream cursor 双重水位线（写入消化 + 检索去重）。
- Q: agent 卡死怎么办？→ 每步 200 上限 + 300s 墙钟 + 步数耗尽强制总结 + checkpoint 可取消恢复。
- Q: 为什么错误不抛异常？→ 错误即观察，让 LLM 在循环内自纠错，turn 保持连贯。

---

## 8. 附录：文件索引

| 文件 | 职责 |
|---|---|
| `nanobot/agent/loop.py` | AgentLoop：总线消费、会话串行、turn 六阶段、增量持久化、checkpoint、注入队列 |
| `nanobot/agent/runner.py` | AgentRunner：与产品无关的 LLM↔工具迭代核心 + 全部响应级兜底 |
| `nanobot/agent/tools/base.py` | Tool ABC、ToolResult、schema 校验、@tool_parameters |
| `nanobot/agent/tools/schema.py` | JSON Schema 片段类与 `tool_parameters_schema` |
| `nanobot/agent/tools/registry.py` | 注册表、定义缓存、prepare_call 执行前协议 |
| `nanobot/agent/tools/execution.py` | 并发分批执行、错误分类（SSRF/越界）、观察生成 |
| `nanobot/agent/tools/loader.py` | pkgutil + entry_points 工具发现 |
| `nanobot/agent/memory.py` | MemoryStore（MEMORY.md/history.jsonl/cursor）、MemoryArchiver、Consolidator、Dream 工具构建 |
| `nanobot/agent/context.py` | ContextBuilder：系统提示组装（memory/最近历史/skills 注入点） |
| `nanobot/agent/autocompact.py` | 空闲会话 TTL 压缩调度 |
| `nanobot/session/manager.py` | Session/SessionManager：get_history 回放、原子保存、runtime checkpoint sidecar |
| `nanobot/providers/base.py` | LLMProvider 契约、LLMResponse/ToolCallRequest、重试与 Retry-After 解析 |
| `nanobot/providers/factory.py` | make_provider + FallbackProvider 主备链 |
| `nanobot/cli/gateway_runtime.py` | Dream/heartbeat 系统任务注册与执行、git 审计提交 |
| `nanobot/command/builtin.py` | `/dream` 等内置命令 |
| `nanobot/config/schema.py` | AgentDefaults（max_tool_iterations=200 等）、DreamConfig |
| `nanobot/templates/agent/` | identity/dream/max_iterations_message 等提示词模板（改模板=改行为） |
| `.agent/design.md` | 架构约束：核心保持小、显式优于魔法、宁可重复不过早抽象 |
