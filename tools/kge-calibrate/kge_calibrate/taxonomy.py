"""错因分类表（只读）：docs/mapping/error-taxonomy.md 里由 check_error_taxonomy.py 从 YAML 生成的映射表（机器可读部分）。

本工具纯标准库，不读 YAML；只解析 md 里 BEGIN/END GENERATED 标记之间的第一张表（学科、分组、学科编码、原名称、映射到、状态、备选/说明），
映射表与 YAML 的一致性由 make taxonomy-check 保证。阅读模块的下辖标签（“下辖标签：`a`、`b`”）继承所属大类的映射（同 YAML 的约定）。
七个跨科大类与迁移 0001 的 platform.cross_subject_error 枚举逐字一致；每个编码的映射与 YAML 逐条一致（测试核对）。
“状态”列只收三种写法：明确 / 待定 / 待定（敏感）。只有“明确”的编码参与检验与候选；待定与敏感编码（如 xinli_xiguan，
完整方案红线“不作心理诊断”）的错因行在读入时整行丢弃，不进任何计数。表里出现不认识的学科一律拒绝（不静默跳过）。
不改分类表、不提议新编码（spec“不做”）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .inputs import InputRefused, read_limited

TAXONOMY_PATH = Path(__file__).resolve().parents[3] / "docs/mapping/error-taxonomy.md"
MAX_TAXONOMY_BYTES = 1024 * 1024
BEGIN, END = "<!-- BEGIN GENERATED: mapping -->", "<!-- END GENERATED: mapping -->"
# 迁移 0001 platform.cross_subject_error 的取值与顺序（测试逐字核对）
CROSS_SUBJECT = ("reading_the_question", "locating_information", "thinking_method", "computation_or_grammar",
                 "expression_convention", "strategy_and_time", "knowledge")
# 映射表“学科”列 → 平台学科编码（YAML subjects.<编码>.label）
SUBJECT_LABELS = {"语文 · 文砺": "wenli", "数学 · 阿鸦": "aya", "道法 · 知行": "zhixing", "英语 · 开口成章": "echo"}
CODE = re.compile(r"^[a-z0-9_\-]{1,48}$")          # 契约 subject_category / 迁移 0005 的 CHECK
_TICK = re.compile(r"`([^`]+)`")


CLEAR, DISPUTED, SENSITIVE = "clear", "disputed", "sensitive"
_HEADER = ("学科", "分组", "学科编码", "原名称", "映射到", "状态", "备选 / 说明")
_SEPARATOR = re.compile(r"^:?-{3,}:?$")


def _status(cell: str) -> str:
    """“状态”列：✅ 明确 → clear；⚠️ 待定（敏感）→ sensitive；⚠️ 待定 → disputed；其他写法一律拒绝"""
    if cell == "✅ 明确":
        return CLEAR
    if cell == "⚠️ 待定（敏感）":
        return SENSITIVE
    if cell == "⚠️ 待定":
        return DISPUTED
    raise InputRefused("错因分类表有一行的“状态”列不认识")


@dataclass(frozen=True)
class Taxonomy:
    codes: Mapping[str, Mapping[str, str]]   # 学科 → {学科错因编码 → 跨科大类}（全部编码：用来校验输入）
    status: Mapping[str, Mapping[str, str]] = field(default_factory=dict)   # 学科 → {编码 → clear / disputed / sensitive}

    def subject_codes(self, subject: str) -> tuple[str, ...]:
        """参与检验与候选的编码：只取映射明确（clear）的；待定与敏感（如 xinli_xiguan）一律排除"""
        st = self.status.get(subject, {})
        return tuple(sorted(c for c in self.codes.get(subject, {}) if st.get(c) == CLEAR))

    def excluded_codes(self, subject: str) -> tuple[str, ...]:
        st = self.status.get(subject, {})
        return tuple(sorted(c for c in self.codes.get(subject, {}) if st.get(c) != CLEAR))


def parse_taxonomy(text: str) -> Taxonomy:
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < i:
        raise InputRefused("错因分类表缺生成的映射表（BEGIN/END GENERATED 标记）")
    codes: dict[str, dict[str, str]] = {}
    status: dict[str, dict[str, str]] = {}
    for line in text[i + len(BEGIN):j].splitlines():
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) != 7:
            continue                                   # 空行、按大类汇总的第二张表（3 列）
        if tuple(cols) == _HEADER or all(_SEPARATOR.fullmatch(c) for c in cols):
            continue                                   # 表头、分隔行
        if cols[0] not in SUBJECT_LABELS:
            raise InputRefused("错因分类表有一行的学科不认识（不能静默跳过）")
        subject = SUBJECT_LABELS[cols[0]]
        st = _status(cols[5])
        code = _TICK.fullmatch(cols[2])
        target = _TICK.findall(cols[4])
        if code is None or len(target) != 1 or target[0] not in CROSS_SUBJECT or not CODE.fullmatch(code.group(1)):
            raise InputRefused("错因分类表有一行的学科编码或映射到的大类不合法")
        own = codes.setdefault(subject, {})
        tags = _TICK.findall(cols[6].split("下辖标签：", 1)[1].split("；", 1)[0]) if "下辖标签：" in cols[6] else []
        for c in [code.group(1), *tags]:
            if not CODE.fullmatch(c) or c in own:
                raise InputRefused("错因分类表的学科编码不合法或在学科内重复")
            own[c] = target[0]
            status.setdefault(subject, {})[c] = st         # 下辖标签继承所属大类的状态
    if not codes:
        raise InputRefused("错因分类表里没有可用的映射行")
    return Taxonomy({s: dict(sorted(v.items())) for s, v in sorted(codes.items())},
                    {s: dict(sorted(v.items())) for s, v in sorted(status.items())})


def load_taxonomy(path: str | Path = TAXONOMY_PATH) -> Taxonomy:
    data = read_limited(path, MAX_TAXONOMY_BYTES, "错因分类表")
    try:
        return parse_taxonomy(data.decode("utf-8"))
    except UnicodeDecodeError:
        raise InputRefused("错因分类表不是 UTF-8") from None
