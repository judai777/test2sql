"""配置加载：.env（可选）→ 环境变量 → pydantic 校验。

不引第三方 dotenv 依赖，自建 15 行加载器（全自建轻量核约束，见 ADR-001 D7）。
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


def load_env(path: str | Path = ".env") -> None:
    """把 KEY=VALUE 行写入 os.environ（已存在的环境变量优先，不被覆盖）。"""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


class LLMConfig(BaseModel):
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    timeout_s: float = 60.0
    temperature: float = 0.2
    retry_delays: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)  # 429/5xx 退避；智谱 flash 高峰拥堵实测需容忍 ~30s


class EmbeddingConfig(BaseModel):
    """向量检索配置（ADR-007）：未配置 model 时记忆检索降级为关键词打分。"""

    base_url: str = ""       # 空则回退 LLM 的 base_url
    api_key: str = ""        # 空则回退 LLM 的 api_key
    model: str = ""          # 空 = 禁用语义检索
    timeout_s: float = 30.0

    @property
    def enabled(self) -> bool:
        return bool(self.model)


class ToolConfig(BaseModel):
    db_path: Path = Path("db/securities.db")          # 业务库（只读）
    memory_db_path: Path = Path("db/memory.db")       # 系统库：会话/审计/记忆（M3/M4）
    sql_timeout_s: float = 30.0
    sql_row_limit: int = 100


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    tools: ToolConfig = Field(default_factory=ToolConfig)

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> "AppConfig":
        load_env(env_file)
        env = os.environ
        llm = LLMConfig(
            base_url=env.get("T2S_LLM_BASE_URL", LLMConfig().base_url),
            api_key=env.get("T2S_LLM_API_KEY", ""),
            model=env.get("T2S_LLM_MODEL", LLMConfig().model),
            timeout_s=float(env.get("T2S_LLM_TIMEOUT_S", 60)),
            temperature=float(env.get("T2S_LLM_TEMPERATURE", 0.2)),
        )
        embedding = EmbeddingConfig(
            base_url=env.get("T2S_EMBEDDING_BASE_URL", llm.base_url),
            api_key=env.get("T2S_EMBEDDING_API_KEY", llm.api_key),
            model=env.get("T2S_EMBEDDING_MODEL", ""),
            timeout_s=float(env.get("T2S_EMBEDDING_TIMEOUT_S", 30)),
        )
        return cls(
            llm=llm,
            embedding=embedding,
            tools=ToolConfig(
                db_path=Path(env.get("T2S_DB_PATH", "db/securities.db")),
                memory_db_path=Path(env.get("T2S_MEMORY_DB_PATH", "db/memory.db")),
                sql_timeout_s=float(env.get("T2S_SQL_TIMEOUT_S", 30)),
                sql_row_limit=int(env.get("T2S_SQL_ROW_LIMIT", 100)),
            ),
        )
