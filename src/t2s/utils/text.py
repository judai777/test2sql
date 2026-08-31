"""跨模块复用的纯函数（ADR-005 utils：不 import 项目内其他模块）。"""
from __future__ import annotations

import re

_CJK = r"[\u4e00-\u9fff]"


def query_terms(query: str) -> list[str]:
    """检索分词：ASCII 词 + 中文整段 + 中文二元组。"""
    terms = [w.lower() for w in re.findall(r"[a-zA-Z_]{2,}", query)]
    for seg in re.findall(rf"{_CJK}{{2,}}", query):
        terms.append(seg)
        terms.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return terms


def bigram_score(query: str, corpus: str) -> int:
    """出现次数计分：语义检索不可用时的降级打分。"""
    return sum(corpus.count(term) for term in query_terms(query))
