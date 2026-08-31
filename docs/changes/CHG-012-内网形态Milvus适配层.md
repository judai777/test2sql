# CHG-012：内网形态适配——Milvus 适配层与本机模拟基础设施

- 日期：2026-08-29
- 动机：用户提出私域并发与 Milvus 选型问题；约束澄清——**无法接入公司内网，本机 Windows 无 Docker、WSL 无发行版**，只能本机模拟内网环境。
- 类型：新增（部署形态适配层，运行时可选）

## 事实约束（决策依据）

| 事实 | 来源 |
|---|---|
| Milvus 官方不支持 Windows 原生运行 | Milvus 官方部署文档 |
| Milvus Lite（嵌入式）仅 Linux/macOS | milvus-lite 平台支持矩阵 |
| Docker 未安装；WSL 框架在但无发行版 | 本机探测 |
| pymilvus 客户端跨平台 | 实测安装 3.0.1 成功 |

## 变更内容

| 文件 | 说明 |
|---|---|
| `src/t2s/storage/milvus_store.py` | **MilvusVectorStore**：pymilvus MilvusClient 适配层（同一 API 覆盖 Lite 与 Standalone）；**构造即探活**（list_collections fail-fast），连不上抛 `MilvusUnavailableError`，上层捕获后降级 SQLite 自建向量（ADR-007）；按用途分集合 qa_vectors / saved_vectors |
| `deploy/docker-compose-milvus.yml` | Milvus standalone 官方三容器形态（etcd/minio/milvus），含国内镜像加速备注 |
| `tests/test_milvus.py` | 门禁测试：`T2S_MILVUS_URI` 设置才跑集成；离线探针测试常驻（验证降级信号） |
| `docs/decisions/ADR-008` | **D7 增补**：向量库企业形态 + 三条部署路径 + 本机模拟内网的完整映射（LLM/Embedding/Reranker/向量库/关系库五层） |

## 三条部署路径（按内网模拟忠实度）

- **A. WSL2 + docker-ce（免 Docker Desktop，最忠实）**：`wsl --install -d Ubuntu` → WSL 内装 docker-ce → compose 起 Milvus → Windows 侧 `localhost:19530` 直连。网络边界/gRPC/连接串与生产一致，代码零差异。代价：一次系统级安装（约 2GB，可能需重启）。
- **B. 接口换牌（Windows 原生向量库）**：Qdrant 官方 Windows 单二进制，或 PostgreSQL+pgvector（金融主流）。VectorStore 接口化后同为一层适配——"Milvus 是部署选型，接口是架构承诺"。
- **C. 零服务模拟（现状已具备）**：SQLite JSON 向量 + 余弦；Milvus 适配层与门禁测试已落库，环境就绪一键启用。

## 验证结果

- `pytest`：121 passed 全绿不变（Milvus 为可选增强）；门禁行为实测——无服务时探针正确抛降级信号、集成测试正确跳过。

## 关联

ADR-007（SQLite 自建向量，范围由 ADR-008 D5/D7 部分修订）、ADR-008 D7、CHG-010（Reranker 接口）。
