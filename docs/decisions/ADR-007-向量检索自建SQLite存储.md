# ADR-007：向量检索选型——自建 SQLite 向量存储（否决 chromadb）

- 状态：**已接受**
- 日期：2026-08-29
- 关联：ADR-001 D7（全自建轻量核）、CHG-005（M4 记忆系统）

## 背景

M4 记忆系统需要语义检索（样例库 few-shot / 口径库召回）。原设计（design.md §8）指定 Chroma。实施时发现两个现实问题：

1. **重依赖**：chromadb 拖入 onnxruntime 等约 200MB 依赖，且部分版本与 pydantic v2 存在兼容摩擦；
2. **网络阻断**：chromadb 默认嵌入模型（MiniLM onnx）需从境外 CDN 首次下载——本机 GitHub 直连已实测不稳定（uv Python 下载、git clone 均被卡）。

而本项目的向量量级：12 张表元数据 + 数十条样例 + 个位数口径文档，**合计 < 1k 向量**。

## 决策

1. **向量存储自建**：`qa_pairs.embedding` / `metrics_docs.embedding` 以 JSON 存 SQLite（`storage/memory_store.py`），检索在 Python 内做余弦（`manager/memory.py::_cosine`）。
2. **嵌入走 OpenAI 兼容 /embeddings API**（`llm/embeddings.py`），独立配置 `T2S_EMBEDDING_*`（base_url/key/model 可与主 LLM 不同供应商）。
3. **降级链**：未配置嵌入模型 / API 失败 / 行无向量 → 关键词 bigram 出现次数打分兜底（`utils/text.py`）；双通道都不命中 → 返回空 → 不注入 few-shot，主流程绝不阻塞。
4. **接口保持可插拔**：MemoryService 不感知存储实现，量级增长时可整体替换为专用向量库而不动上层。

## 备选与否决理由

- **chromadb**：见背景；且"为一个 <1k 向量的场景引入 200MB 依赖"违背全自建轻量核的量级判断——复杂度是负债。
- **sqlite-vec 扩展**：需要加载平台二进制扩展，Windows 环境多一层失败面；纯 Python 余弦在本量级毫秒级完成。
- **不建语义检索，只用关键词**：语义泛化（"成交额排行"≈"营业部交易规模"）是 few-shot 检索的核心价值，也是 Vanna 方案的灵魂，砍掉等于砍掉 M5 消融实验的自变量。

## 影响面

- 新增 `llm/embeddings.py`、`utils/text.py`（关键词打分从 tools 下沉复用）、`storage/memory_store.py`、`manager/memory.py`。
- design.md §6 "Chroma" 表述以本 ADR 为准；评测（M5）的消融实验直接受益于降级链（关语义=纯关键词通道）。
