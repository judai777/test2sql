"""Skill 注册与加载（ADR-008 D1）：agent = skill 配置（prompt + 工具白名单 + 预算）。

skill 文件为 Markdown + 极简 frontmatter（--- 分隔的 key: value），避免引入 YAML 依赖
（全自建轻量核约束）。文件即资产：改 skill 即改 agent 行为，无需动引擎。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    tools: tuple[str, ...]
    max_steps: int
    system_prompt: str


def load_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"skill 文件缺少 frontmatter: {path}")
    _, front, body = text.split("---", 2)
    meta: dict[str, str] = {}
    for line in front.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    try:
        return Skill(
            name=meta["name"],
            description=meta.get("description", ""),
            tools=tuple(t.strip() for t in meta.get("tools", "").split(",") if t.strip()),
            max_steps=int(meta.get("max_steps", "8")),
            system_prompt=body.strip(),
        )
    except KeyError as e:
        raise ValueError(f"skill frontmatter 缺少必填字段 {e}: {path}") from e


def load_skills(directory: Path = SKILLS_DIR) -> dict[str, Skill]:
    """加载目录下全部 *.md skill；name 冲突即报错（fail-fast）。"""
    skills: dict[str, Skill] = {}
    for path in sorted(Path(directory).glob("*.md")):
        skill = load_skill(path)
        if skill.name in skills:
            raise ValueError(f"skill 重名: {skill.name}")
        skills[skill.name] = skill
    return skills
